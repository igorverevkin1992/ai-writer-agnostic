import { genreKit, type Kb } from '@aiw/kb';
import { scoreChecklist, type ChecklistScore } from '../checks/code/checklist.ts';
import { runCodeChecks } from '../checks/code/runner.ts';
import { judgeChecklist } from '../checks/llm/checklistJudge.ts';
import { rateShootable, type Shootable } from '../checks/llm/controllers.ts';
import type { Db } from '../db/client.ts';
import type { ProjectFixture } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { createProject } from '../pipeline/project.ts';
import { runStep, skipStep } from '../pipeline/runners.ts';
import { BudgetExceededError } from '../providers/errors.ts';
import type { ProviderName } from '../providers/config.ts';
import type { LlmClient } from '../providers/llm.ts';
import type { Finding } from '../schemas/finding.ts';

export interface PilotReport {
  projectId: string;
  episodes: number[];
  checklist: ChecklistScore;
  /** Scripts with no script-metric findings. */
  metricsPassed: number[];
  metricsFailed: { ep: number; findings: Finding[] }[];
  shootable: { ep: number; verdict: Shootable['verdict']; reason: string }[];
  openBlockers: number;
  stopped?: string;
}

/**
 * Pilot run (M5 acceptance): a project from the golden bible and plan, episode cards and
 * scripts for the first episodes, then the checklist (code + judge), script metrics and
 * a production rating of every script. Gates are skipped: no producer in the loop.
 */
export async function runPilot(
  deps: { db: Db; llm: LlmClient; kb: Kb },
  golden: ProjectFixture,
  opts: { genreId: string; episodes: number; budgetLimitUsd?: number; title?: string },
): Promise<PilotReport> {
  const { db, llm, kb } = deps;
  const kit = genreKit(kb, opts.genreId);
  const projectId = createProject(db, kb, { title: opts.title ?? 'Пилот', genreId: opts.genreId, idea: 'Пилот по эталону', budgetLimitUsd: opts.budgetLimitUsd });
  const memory = new ProjectMemory(db, projectId);
  memory.importBible(golden.bible);
  memory.importPlan(golden.plan);
  const sd = { db, kb, projectId };
  for (const step of ['concept', 'logline', 'bible', 'season_plan'] as const) skipStep(sd, step);
  const episodes = golden.plan.episodes.map((e) => e.ep).filter((ep) => ep <= opts.episodes);

  const report: PilotReport = {
    projectId,
    episodes,
    checklist: scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...golden })),
    metricsPassed: [],
    metricsFailed: [],
    shootable: [],
    openBlockers: 0,
  };
  try {
    await runStep({ ...sd, llm }, 'episode_cards', { episodes });
    skipStep(sd, 'episode_cards');
    await runStep({ ...sd, llm }, 'scripts', { episodes });

    const cards = memory.cards().filter((c) => episodes.includes(c.ep));
    const scripts = memory.scripts().filter((s) => episodes.includes(s.ep));
    const code = runCodeChecks({ kit, bible: memory.currentBible()!, plan: golden.plan, cards, scripts });
    report.checklist = await judgeChecklist(
      { llm, kb, kit, projectId, step: 'pilot' },
      scoreChecklist(kit.checklist, kit.rules, code),
      { bible: memory.currentBible()!, plan: golden.plan, authorProvider: llm.resolve('architect_heavy').provider },
    );
    const metric = memory.findingsOf('scripts', 'open').filter((f) => f.check?.startsWith('script_metrics.'));
    for (const s of scripts) {
      const fs = metric.filter((f) => f.episode === s.ep);
      if (fs.length) report.metricsFailed.push({ ep: s.ep, findings: fs as unknown as Finding[] });
      else report.metricsPassed.push(s.ep);
    }
    for (const s of scripts) {
      const author = (memory.latestArtifact(`author:scripts:${s.ep}`) as { provider: ProviderName } | undefined)?.provider;
      const r = await rateShootable({ llm, kb, kit, projectId, step: 'pilot' }, s, author ?? llm.resolve('writer').provider);
      report.shootable.push({ ep: s.ep, verdict: r.verdict, reason: r.reason });
    }
    report.openBlockers = memory.openFindings().filter((f) => f.severity === 'blocker').length;
  } catch (err) {
    if (!(err instanceof BudgetExceededError)) throw err;
    report.stopped = err.message;
  }
  return report;
}

export function renderPilotReport(r: PilotReport, meta: { date: string; project: string; costUsd: number }): string {
  const c = r.checklist;
  const total = r.metricsPassed.length + r.metricsFailed.length;
  const shootableOk = r.shootable.filter((s) => s.verdict !== 'no').length;
  const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
  const lines = [
    `# Пилот — ${meta.date}`,
    '',
    `Проект: ${meta.project}, серии ${r.episodes[0]}–${r.episodes.at(-1)}. Потрачено: $${meta.costUsd.toFixed(2)}.`,
    ...(r.stopped ? ['', `**Прогон остановлен по бюджету:** ${r.stopped} Отчёт неполный.`] : []),
    '',
    '## Критерий вехи M5',
    '',
    '| Что | Результат | Порог |',
    '|---|---|---|',
    `| Чек-лист | ${c.score} из ${c.total}${c.unknownPoints ? ` (+${c.unknownPoints} не оценено)` : ''} | ≥${c.pass} ${c.score >= c.pass ? '✓' : '✗'} |`,
    `| Метрики сценария пройдены | ${r.metricsPassed.length} из ${total} серий | все ${total > 0 && r.metricsFailed.length === 0 ? '✓' : '✗'} |`,
    `| Сцены «можно снимать после лёгкой правки» | ${shootableOk} из ${r.shootable.length} (${pct(shootableOk, r.shootable.length)}%) | ≥40% ${pct(shootableOk, r.shootable.length) >= 40 ? '✓' : '✗'} |`,
    '',
    `Открытых блокирующих замечаний: ${r.openBlockers}.`,
    '',
    '## Чек-лист по пунктам',
    '',
    '| Пункт | Баллы | Итог |',
    '|---|---|---|',
    ...c.items.map((i) => `| ${i.text} | ${i.points} | ${i.status === 'ok' ? '✓' : i.status === 'fail' ? '✗' : '?'} |`),
  ];
  if (r.metricsFailed.length) {
    lines.push('', '## Метрики, которые не прошли', '');
    for (const m of r.metricsFailed) for (const f of m.findings) lines.push(`- ${m.ep}-я серия: ${f.quote}`);
  }
  if (r.shootable.length) {
    lines.push('', '## Можно ли снимать', '', '| Серия | Оценка | Почему |', '|---|---|---|');
    const label = { yes: 'можно', light_edit: 'после лёгкой правки', no: 'нужна переработка' } as const;
    for (const s of r.shootable) lines.push(`| ${s.ep} | ${label[s.verdict]} | ${s.reason} |`);
  }
  return `${lines.join('\n')}\n`;
}
