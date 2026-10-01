import type { GenreKit, Kb, ReviewMethod } from '@aiw/kb';
import type { ZodType } from 'zod';
import type { Authors, ProviderName, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { Bible } from '../../schemas/bible.ts';
import { LEVEL_SEVERITY, type Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../../prompts/render.ts';
import { makeFinding } from '../code/finding.ts';
import { FindingDraft, FindingDrafts, ReviewDraft, ReviewDrafts } from './schemas.ts';
import { bibleText, factsText, noticeQuestions, planBlocks, type AuditBlock } from './texts.ts';

const MAX_NOTICE_QUESTIONS = 30;

type Category = ReviewMethod['categories'][number];

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
  /** When present, the plan is audited; otherwise the bible is. */
  plan?: SeasonPlan;
  /** Provider that wrote the audited text. */
  authorProvider: Authors;
}

export interface AuditOptions {
  /** Review categories А–П to pass; by default all of them. */
  categories?: string[];
  /** Or: the categories that cover these hole types of the catalog. */
  holeTypes?: number[];
  /** Restrict plan audit to blocks covering these episodes. */
  episodes?: number[];
  personas?: boolean;
  /** Episodes per pass; by default the whole season at once, so setups and payoffs are seen together. */
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

/** The episode of a draft: from the quote's line, or the model's number if it is inside the block. */
function episodeOf(block: AuditBlock, quote: string, named?: number): number | undefined {
  const inBlock = (ep: number | undefined) =>
    ep !== undefined && (!block.episodes || (ep >= block.episodes[0] && ep <= block.episodes[1])) ? ep : undefined;
  return block.episodes ? (episodeOfQuote(block, quote, named) ?? inBlock(named)) : named;
}

const quoteIsIn = (block: AuditBlock, quote: string) => normalize(block.text).includes(normalize(quote));

function reviewFinding(d: ReviewDraft, block: AuditBlock, category: Category): Finding | undefined {
  if (!quoteIsIn(block, d.quote)) return undefined;
  const f = makeFinding({
    check: `review.${category.id}`,
    controller: 'logic',
    severity: LEVEL_SEVERITY[d.level],
    holeType: category.hole_types[0],
    episode: episodeOf(block, d.quote, d.episode),
    quote: d.quote,
    question: d.viewerQuestion.replace(/^Зритель спросит:\s*/u, ''),
    fixes: d.fixes as [string],
  });
  return {
    ...f,
    category: [...new Set([category.id, ...d.category])],
    level: d.level,
    whyNoticed: d.whyNoticed,
    agentRule: d.agentRule,
    ...(d.episodes ? { episodes: d.episodes } : {}),
    ...(d.doubt ? { doubt: true } : {}),
  };
}

function personaFinding(d: FindingDraft, block: AuditBlock, check: string): Finding | undefined {
  if (!quoteIsIn(block, d.quote)) return undefined;
  return makeFinding({
    check,
    controller: 'logic',
    severity: d.severity,
    holeType: d.holeType,
    episode: episodeOf(block, d.quote, d.episode),
    quote: d.quote,
    question: d.viewerQuestion.replace(/^Зритель спросит:\s*/u, ''),
    fixes: d.fixes as [string],
  });
}

/** Which review categories to pass. */
function pickCategories(review: ReviewMethod, opts: AuditOptions): Category[] {
  return review.categories.filter(
    (c) =>
      (!opts.categories || opts.categories.includes(c.id)) &&
      (!opts.holeTypes || c.hole_types.some((t) => opts.holeTypes!.includes(t))),
  );
}

/**
 * «Адвокат дьявола» by the producer's review method: one pass per category А–П over the whole
 * season (or the bible), with the six questions to every action, the category's rules drawn from
 * holes already found, and the sample holes; then one pass per viewer persona.
 * A hole found in several categories is kept once with all of them. The critic is always
 * from another family than the author.
 */
export async function runDevilAdvocate(deps: AuditDeps, target: AuditTarget, opts: AuditOptions = {}): Promise<AuditResult> {
  const role = deps.criticRole ?? 'critic_of_architect';
  const kbText = knowledgeBlock(deps.kb, deps.kit);
  const facts = factsText(target.bible) || 'Фактов пока нет.';
  const review = deps.kb.review;
  let blocks: AuditBlock[] = target.plan
    ? planBlocks(target.plan, opts.blockSize ?? target.plan.episodes.length)
    : [{ label: 'библия', text: bibleText(target.bible) }];
  if (opts.episodes && target.plan) {
    blocks = blocks.filter((b) => opts.episodes!.some((ep) => b.episodes && ep >= b.episodes[0] && ep <= b.episodes[1]));
  }

  const found = new Map<string, Finding>();
  let dropped = 0;
  let calls = 0;
  const providers = new Set<ProviderName>();
  const keep = (f: Finding) => {
    const key = `${f.episode ?? 0}|${normalize(f.quote)}`;
    const prev = found.get(key);
    if (!prev) found.set(key, f);
    // The same hole seen from another category: one finding with both categories.
    else if (f.category && prev.category) prev.category = [...new Set([...prev.category, ...f.category])];
  };

  const ask = async <T>(task: string, system: string, schema: ZodType<T[]>) => {
    calls++;
    const res = await deps.llm.completeJson(schema, {
      role,
      projectId: deps.projectId,
      step: deps.step,
      authorProvider: target.authorProvider,
      request: { task, cacheablePrefix: [kbText], system, messages: [{ role: 'user', content: 'Проверь текст. Ответ — только JSON.' }] },
    });
    providers.add(res.provider);
    return res.data;
  };

  const severities = Object.entries(review.severities)
    .map(([level, text]) => `- ${level}: ${text}`)
    .join('\n');
  for (const category of pickCategories(review, opts)) {
    const holes = deps.kb.holes.holes.filter((h) => category.hole_types.includes(h.id));
    const questions = [
      ...holes.flatMap((h) => h.questions),
      ...holes.flatMap((h) => deps.kit.guide?.hole_questions[String(h.id)] ?? []),
    ];
    for (const block of blocks) {
      const notice = category.with_notice_questions
        ? noticeQuestions(target.bible, block, target.plan).slice(0, MAX_NOTICE_QUESTIONS)
        : [];
      const system = renderPrompt(deps.kb, `${role}/devil_advocate`, {
        category: `${category.id}. ${category.name}`,
        check: category.check,
        hole_questions: questions.map((q) => `- ${q}`).join('\n') || '- (нет)',
        extra_questions: notice.length ? `\nВопросы по конкретным событиям:\n${notice.map((q) => `- ${q}`).join('\n')}\n` : '',
        six_questions: review.six_questions.map((q, i) => `${i + 1}. ${q}`).join('\n'),
        review_rules: category.rules.map((r) => `- ${r}`).join('\n'),
        patterns: review.patterns.map((p) => `- ${p}`).join('\n'),
        categories: review.categories.map((c) => `${c.id} — ${c.name}`).join('; '),
        severities,
        target: block.label,
        text: block.text,
        facts,
        schema: schemaText(ReviewDrafts),
      });
      for (const d of await ask(`devil_advocate:${category.id}:${block.label}`, system, ReviewDrafts)) {
        const f = reviewFinding(d, block, category);
        if (f) keep(f);
        else dropped++;
      }
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
      for (const d of await ask(`persona:${p.id}`, system, FindingDrafts)) {
        const f = personaFinding(d, whole, `persona.${p.id}`);
        if (f) keep(f);
        else dropped++;
      }
    }
  }

  return { findings: [...found.values()], dropped, calls, providers: [...providers] };
}
