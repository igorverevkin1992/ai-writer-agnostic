import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { GenreKit, Kb } from '@aiw/kb';
import { parse } from 'yaml';
import { z } from 'zod';
import { runCodeChecks } from '../checks/code/runner.ts';
import { scoreChecklist } from '../checks/code/checklist.ts';
import { runDevilAdvocate } from '../checks/llm/devilAdvocate.ts';
import { resolveFinding } from '../checks/llm/resolve.ts';
import { EvalMatches } from '../checks/llm/schemas.ts';
import { FIXTURES_DIR, type ProjectFixture } from '../fixtures.ts';
import { renderPrompt, schemaText } from '../prompts/render.ts';
import { ROLE_NAMES, type RoleName } from '../providers/config.ts';
import { BudgetExceededError } from '../providers/errors.ts';
import type { LlmClient } from '../providers/llm.ts';
import type { Finding } from '../schemas/finding.ts';
import { withHole, type SeededHole } from './seed.ts';

export const ProducerHole = z.object({
  id: z.string().min(1),
  holeType: z.int().min(1).max(11),
  episode: z.int().min(1).optional(),
  description: z.string().min(1),
});
export type ProducerHole = z.infer<typeof ProducerHole>;

export const ProducerHolesFile = z.object({
  holes: z.array(ProducerHole),
  /** The producer's own checklist score of the golden project. */
  checklist_score: z.int().min(0).optional(),
});
export type ProducerHolesFile = z.infer<typeof ProducerHolesFile>;

/** fixtures/golden/<name>/holes.yaml, if the producer has provided it. */
export function loadProducerHoles(name: string): ProducerHolesFile | undefined {
  const path = join(FIXTURES_DIR, 'golden', name, 'holes.yaml');
  if (!existsSync(path)) return undefined;
  return ProducerHolesFile.parse(parse(readFileSync(path, 'utf8')));
}

export interface EvalInput {
  llm: LlmClient;
  kb: Kb;
  kit: GenreKit;
  golden: ProjectFixture;
  producer?: ProducerHolesFile;
  seeded: SeededHole[];
  projectId: string;
  /** all — model audits every seeded hole; missed — only the ones code misses; none. */
  seededModel?: 'all' | 'missed' | 'none';
  /** Architect role answering findings (default architect; heavy for the Fable comparison). */
  architectRole?: RoleName;
}

export interface SeededResult {
  hole: SeededHole;
  code: boolean;
  model: boolean | null;
}

export interface EvalReport {
  findings: Finding[];
  dropped: number;
  matches: EvalMatches['matches'];
  producerHoles: number;
  producerFound: number;
  realFindings: number;
  seeded: SeededResult[];
  checklist: { code: number; unknown: number; producer?: number };
  anchors: { total: number; onPlace: number };
  resolved: { tried: number; closed: number; architect: RoleName };
  /** Set when the budget ran out: the report is partial. */
  stopped?: string;
}

/** Picks a judge role whose provider differs from the given one. */
function judgeFor(llm: LlmClient, notProvider: string): RoleName {
  const role = ROLE_NAMES.find((r) => llm.resolve(r).provider !== notProvider);
  if (!role) throw new Error('Нет роли другого семейства для судьи');
  return role;
}

const near = (a: number | undefined, b: number | undefined) => a === undefined || b === undefined || Math.abs(a - b) <= 1;

/**
 * Runs the auditor on the golden project and on seeded holes, matches its findings
 * with the producer's own list through a judge of another family, and measures
 * how many holes the architect can close.
 */
export async function runAuditEval(input: EvalInput): Promise<EvalReport> {
  const { llm, kb, kit, golden, projectId } = input;
  const critic: RoleName = 'critic_of_architect';
  const criticProvider = llm.resolve(critic).provider;
  const architectRole = input.architectRole ?? 'architect';
  const deps = { llm, kb, kit, projectId, step: 'eval' };

  const code = runCodeChecks({ kit, ...golden });
  const score = scoreChecklist(kit.checklist, kit.rules, code);
  const anchorIds = Object.keys(kit.frame.anchors);
  const misplaced = new Set(code.findings.filter((f) => f.check?.startsWith('season_frame.anchor.')).map((f) => f.check!.split('.')[2]));

  const report: EvalReport = {
    findings: [],
    dropped: 0,
    matches: [],
    producerHoles: input.producer?.holes.length ?? 0,
    producerFound: 0,
    realFindings: 0,
    seeded: [],
    checklist: { code: score.score, unknown: score.unknownPoints, producer: input.producer?.checklist_score },
    anchors: { total: anchorIds.length, onPlace: anchorIds.filter((a) => !misplaced.has(a)).length },
    resolved: { tried: 0, closed: 0, architect: architectRole },
  };

  try {
    // 1. The auditor on the golden bible and plan.
    for (const [target, authorRole] of [
      [{ bible: golden.bible }, 'architect'],
      [{ bible: golden.bible, plan: golden.plan }, 'architect_heavy'],
    ] as const) {
      const res = await runDevilAdvocate(deps, { ...target, authorProvider: llm.resolve(authorRole).provider });
      report.findings.push(...res.findings);
      report.dropped += res.dropped;
    }

    // 2. Match findings with the producer's holes (judge from another family than the auditor).
    if (report.findings.length) {
      const holes = input.producer?.holes ?? [];
      const { data } = await llm.completeJson(EvalMatches, {
        role: judgeFor(llm, criticProvider),
        projectId,
        step: 'eval',
        authorProvider: criticProvider,
        request: {
          task: 'eval_match',
          system: renderPrompt(kb, 'critic_of_writer/eval_match', {
            holes: holes.map((h) => `${h.id} | тип ${h.holeType} | серия ${h.episode ?? '—'} | ${h.description}`).join('\n') || 'Список продюсера не передан.',
            findings: report.findings.map((f) => `${f.id} | тип ${f.holeType} | серия ${f.episode ?? '—'} | «${f.quote}» | ${f.viewerQuestion}`).join('\n'),
            schema: schemaText(EvalMatches),
          }),
          messages: [{ role: 'user', content: 'Сопоставь. Только JSON.' }],
        },
      });
      report.matches = data.matches;
      report.realFindings = data.matches.filter((m) => m.real).length;
      const matched = new Set(data.matches.map((m) => m.hole_id).filter((x): x is string => !!x));
      report.producerFound = holes.filter((h) => matched.has(h.id)).length;
    }

    // 3. Seeded holes: code first, then a targeted model pass.
    for (const hole of input.seeded) {
      const project = withHole(golden, hole);
      const codeFound = runCodeChecks({ kit, ...project }).findings.some((f) => f.holeType === hole.holeType);
      let model: boolean | null = null;
      const mode = input.seededModel ?? 'all';
      if (mode === 'all' || (mode === 'missed' && !codeFound)) {
        // A hole with an episode shows in the plan, even when the patch changes the bible.
        const touchesPlan = hole.episode !== undefined || hole.patch.some((op) => op.path.startsWith('/plan/'));
        const res = await runDevilAdvocate(
          deps,
          touchesPlan
            ? { bible: project.bible, plan: project.plan, authorProvider: llm.resolve('architect_heavy').provider }
            : { bible: project.bible, authorProvider: llm.resolve('architect').provider },
          { holeTypes: [hole.holeType], episodes: hole.episode ? [hole.episode] : undefined, personas: false },
        );
        model = res.findings.some((f) => f.holeType === hole.holeType && near(f.episode, hole.episode));
      }
      report.seeded.push({ hole, code: codeFound, model });
    }

    // 4. The architect answers the real blocker and major findings; a judge decides.
    const real = new Set(report.matches.filter((m) => m.real).map((m) => m.finding_id));
    for (const f of report.findings.filter((x) => real.has(x.id) && x.severity !== 'minor')) {
      report.resolved.tried++;
      const res = await resolveFinding(
        { ...deps, authorRole: architectRole, authorProvider: llm.resolve(architectRole).provider, judgeRole: critic },
        f,
        golden.bible,
        golden.plan,
      );
      if (res.finding.status === 'resolved') report.resolved.closed++;
    }
  } catch (err) {
    if (!(err instanceof BudgetExceededError)) throw err;
    report.stopped = err.message;
  }
  return report;
}

const pct = (a: number, b: number) => (b === 0 ? null : Math.round((a / b) * 100));
const fmt = (p: number | null) => (p === null ? '—' : `${p}%`);
const mark = (p: number | null, min: number) => (p === null ? '—' : p >= min ? '✓' : '✗');

/** Russian Markdown report with the spec thresholds (MVP / v1.0). */
export function renderEvalReport(r: EvalReport, meta: { date: string; project: string; costUsd: number; architectModel: string }): string {
  const recall = pct(r.producerFound, r.producerHoles);
  const precision = pct(r.realFindings, r.matches.length);
  const seededFound = r.seeded.filter((s) => s.code || s.model).length;
  const seededPct = pct(seededFound, r.seeded.length);
  const divergence = r.checklist.producer === undefined ? null : Math.abs(r.checklist.producer - r.checklist.code - r.checklist.unknown);
  const anchorsPct = pct(r.anchors.onPlace, r.anchors.total);
  const types = [...new Set(r.seeded.map((s) => s.hole.holeType))].sort((a, b) => a - b);
  const lines = [
    `# Оценка аудитора — ${meta.date}`,
    '',
    `Проект: ${meta.project}. Архитектор в ответах на замечания: ${r.resolved.architect} (${meta.architectModel}). Потрачено: $${meta.costUsd.toFixed(2)}.`,
    ...(r.stopped ? ['', `**Прогон остановлен по бюджету:** ${r.stopped} Отчёт неполный.`] : []),
    '',
    '## Метрики',
    '',
    '| Метрика | Значение | MVP | v1.0 |',
    '|---|---|---|---|',
    `| Найдено дыр продюсера | ${r.producerHoles ? `${r.producerFound} из ${r.producerHoles} (${fmt(recall)})` : 'нет списка продюсера'} | ≥70% ${mark(recall, 70)} | ≥85% ${mark(recall, 85)} |`,
    `| Доля реальных замечаний | ${r.matches.length ? `${r.realFindings} из ${r.matches.length} (${fmt(precision)})` : '—'} | ≥50% ${mark(precision, 50)} | ≥65% ${mark(precision, 65)} |`,
    `| Найдено посеянных дыр | ${seededFound} из ${r.seeded.length} (${fmt(seededPct)}) | ≥80% ${mark(seededPct, 80)} | ≥90% ${mark(seededPct, 90)} |`,
    `| Расхождение с продюсером по чек-листу | ${divergence === null ? 'нет оценки продюсера' : `${divergence} балл.`} | ≤3 ${divergence === null ? '—' : divergence <= 3 ? '✓' : '✗'} | ≤2 ${divergence === null ? '—' : divergence <= 2 ? '✓' : '✗'} |`,
    '| Сцены «можно снимать после лёгкой правки» | считает `pnpm pilot` | ≥40% | ≥60% |',
    `| Опорные точки на своих номерах | ${r.anchors.onPlace} из ${r.anchors.total} (${fmt(anchorsPct)}) | 100% ${mark(anchorsPct, 100)} | 100% |`,
    '',
    `Чек-лист по коду: ${r.checklist.code} баллов, ещё ${r.checklist.unknown} оценивает только модель-судья.`,
    `Замечаний аудитора: ${r.findings.length}; отброшено без точной цитаты: ${r.dropped}.`,
    `Архитектор ответил на ${r.resolved.tried} реальных замечаний, судья закрыл ${r.resolved.closed}.`,
    '',
    '## Посеянные дыры по типам',
    '',
    '| Тип | Посеяно | Нашёл код | Нашла модель | Вместе |',
    '|---|---|---|---|---|',
    ...types.map((t) => {
      const s = r.seeded.filter((x) => x.hole.holeType === t);
      return `| ${t} | ${s.length} | ${s.filter((x) => x.code).length} | ${s.filter((x) => x.model).length} | ${s.filter((x) => x.code || x.model).length} |`;
    }),
  ];
  const disputed = r.matches.filter((m) => m.disputed);
  if (disputed.length) {
    lines.push('', '## Спорные случаи — нужен взгляд продюсера', '');
    for (const m of disputed) {
      const f = r.findings.find((x) => x.id === m.finding_id);
      lines.push(`- «${f?.quote ?? m.finding_id}» → ${m.hole_id ?? 'не сопоставлено'}: ${m.reason}`);
    }
  }
  const missed = r.seeded.filter((s) => !s.code && !s.model);
  if (missed.length) {
    lines.push('', '## Не найденные посеянные дыры', '');
    for (const s of missed) lines.push(`- ${s.hole.id} [тип ${s.hole.holeType}] ${s.hole.description}`);
  }
  return `${lines.join('\n')}\n`;
}
