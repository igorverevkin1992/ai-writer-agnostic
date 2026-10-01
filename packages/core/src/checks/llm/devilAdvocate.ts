import type { GenreKit, Kb } from '@aiw/kb';
import type { Authors, ProviderName, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../../prompts/render.ts';
import { makeFinding } from '../code/finding.ts';
import { FindingDraft, FindingDrafts } from './schemas.ts';
import { bibleText, factsText, noticeQuestions, planBlocks, type AuditBlock } from './texts.ts';

const DEFAULT_BLOCK_SIZE = 10;
const MAX_NOTICE_QUESTIONS = 30;

export interface AuditDeps {
  llm: LlmClient;
  kb: Kb;
  kit: GenreKit;
  projectId?: string;
  step?: string;
  /** Auditor role. Its provider must differ from the author's. */
  criticRole?: RoleName;
}

export interface AuditTarget {
  bible: Bible;
  /** When present, the plan is audited in blocks; otherwise the bible is. */
  plan?: SeasonPlan;
  /** Provider that wrote the audited text. */
  authorProvider: Authors;
}

export interface AuditOptions {
  holeTypes?: number[];
  /** Restrict plan audit to blocks covering these episodes. */
  episodes?: number[];
  personas?: boolean;
  blockSize?: number;
}

export interface AuditResult {
  findings: Finding[];
  /** Drafts dropped because their quote is not in the audited text. */
  dropped: number;
  calls: number;
  /** Providers that really answered (the reserve may have stood in): a judge of the audit must differ. */
  providers: ProviderName[];
}

const normalize = (s: string) => s.replace(/[«»"“”„]/gu, '"').replace(/[ёЁ]/gu, 'е').replace(/\s+/gu, ' ').trim().toLowerCase();

/**
 * Episode of a quote inside a plan block: the "Серия N." line it comes from.
 * When the same words stand in several episodes, the one the auditor named wins.
 */
function episodeOfQuote(block: AuditBlock, quote: string, named?: number): number | undefined {
  const q = normalize(quote);
  const eps = block.text
    .split('\n')
    .filter((l) => normalize(l).includes(q))
    .map((l) => /^Серия (\d+)\./u.exec(l))
    .filter((m): m is RegExpExecArray => !!m)
    .map((m) => Number(m[1]));
  return named !== undefined && eps.includes(named) ? named : eps[0];
}

function toFinding(d: FindingDraft, block: AuditBlock, check: string, holeType: number): Finding | undefined {
  if (!normalize(block.text).includes(normalize(d.quote))) return undefined;
  // The model's episode number counts only inside the audited block.
  const inBlock = (ep: number | undefined) =>
    ep !== undefined && (!block.episodes || (ep >= block.episodes[0] && ep <= block.episodes[1])) ? ep : undefined;
  const episode = block.episodes ? (episodeOfQuote(block, d.quote, d.episode) ?? inBlock(d.episode)) : d.episode;
  const question = d.viewerQuestion.replace(/^Зритель спросит:\s*/u, '');
  return makeFinding({
    check,
    controller: 'logic',
    severity: d.severity,
    holeType,
    episode,
    quote: d.quote,
    question,
    fixes: d.fixes as [string] | [string, string],
  });
}

/** Rules of the review categories that cover this hole type, with the category's check. */
function reviewRules(kb: Kb, holeType: number): string {
  return kb.review.categories
    .filter((c) => c.hole_types.includes(holeType))
    .map((c) => `${c.id}. ${c.name}. ${c.check}\n${c.rules.map((r) => `- ${r}`).join('\n')}`)
    .join('\n\n');
}

/**
 * «Адвокат дьявола»: one pass per hole type over each block (the bible, or the plan
 * in blocks of 10 episodes), code-generated «почему никто не заметил?» questions,
 * and one pass per viewer persona. The critic is always from another family.
 */
export async function runDevilAdvocate(deps: AuditDeps, target: AuditTarget, opts: AuditOptions = {}): Promise<AuditResult> {
  const role = deps.criticRole ?? 'critic_of_architect';
  const kbText = knowledgeBlock(deps.kb, deps.kit);
  const facts = factsText(target.bible) || 'Фактов пока нет.';
  let blocks: AuditBlock[] = target.plan
    ? planBlocks(target.plan, opts.blockSize ?? DEFAULT_BLOCK_SIZE)
    : [{ label: 'библия', text: bibleText(target.bible) }];
  if (opts.episodes && target.plan) {
    blocks = blocks.filter((b) => opts.episodes!.some((ep) => b.episodes && ep >= b.episodes[0] && ep <= b.episodes[1]));
  }

  const holes = deps.kb.holes.holes.filter((h) => !opts.holeTypes || opts.holeTypes.includes(h.id));
  const found = new Map<string, Finding>();
  let dropped = 0;
  let calls = 0;
  const providers = new Set<ProviderName>();

  const ask = async (task: string, system: string, block: AuditBlock, holeType: number | null, check: string) => {
    calls++;
    const res = await deps.llm.completeJson(FindingDrafts, {
      role,
      projectId: deps.projectId,
      step: deps.step,
      authorProvider: target.authorProvider,
      request: { task, cacheablePrefix: [kbText], system, messages: [{ role: 'user', content: 'Проверь текст. Ответ — только JSON.' }] },
    });
    providers.add(res.provider);
    for (const d of res.data) {
      const f = toFinding(d, block, check, holeType ?? d.holeType);
      if (!f) {
        dropped++;
        continue;
      }
      const key = `${f.holeType}|${f.episode ?? 0}|${normalize(f.quote)}`;
      if (!found.has(key)) found.set(key, f);
    }
  };

  for (const hole of holes) {
    for (const block of blocks) {
      const notice =
        hole.notice ? noticeQuestions(target.bible, block, target.plan).slice(0, MAX_NOTICE_QUESTIONS) : [];
      const system = renderPrompt(deps.kb, `${role}/devil_advocate`, {
        hole: `${hole.id}. ${hole.name}`,
        // Genre questions (e.g. «почему никто не видит магию?») join the catalog's.
        hole_questions: [...hole.questions, ...(deps.kit.guide?.hole_questions[String(hole.id)] ?? [])].map((q) => `- ${q}`).join('\n'),
        review_rules: reviewRules(deps.kb, hole.id),
        six_questions: deps.kb.review.six_questions.map((q, i) => `${i + 1}. ${q}`).join('\n'),
        extra_questions: notice.length ? `\nВопросы по конкретным событиям:\n${notice.map((q) => `- ${q}`).join('\n')}\n` : '',
        target: block.label,
        text: block.text,
        facts,
        schema: schemaText(FindingDrafts),
      });
      await ask(`devil_advocate:${hole.id}:${block.label}`, system, block, hole.id, `devil_advocate.${hole.id}`);
    }
  }

  if (opts.personas !== false) {
    const whole: AuditBlock = target.plan
      ? { label: 'план сезона', text: blocks.map((b) => b.text).join('\n'), episodes: [blocks[0]?.episodes?.[0] ?? 1, blocks.at(-1)?.episodes?.[1] ?? 1] }
      : blocks[0]!;
    for (const p of deps.kit.personas.personas) {
      const system = renderPrompt(deps.kb, `${role}/persona`, {
        persona: `${p.name}${p.age ? `, ${p.age} лет` : ''}`,
        focus: p.focus,
        target: whole.label,
        text: whole.text,
        holes: deps.kb.holes.holes.map((h) => `${h.id}. ${h.name}`).join('\n'),
        schema: schemaText(FindingDrafts),
      });
      await ask(`persona:${p.id}`, system, whole, null, `persona.${p.id}`);
    }
  }

  return { findings: [...found.values()], dropped, calls, providers: [...providers] };
}
