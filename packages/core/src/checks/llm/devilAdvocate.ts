import type { GenreKit, Kb, ReviewMethod } from '@aiw/kb';
import type { ZodType } from 'zod';
import type { Authors, ProviderName, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { Bible } from '../../schemas/bible.ts';
import { LEVEL_SEVERITY, type Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../../prompts/render.ts';
import { makeFinding } from '../code/finding.ts';
import { concurrencyFromEnv, mapLimit } from '../../pipeline/mapLimit.ts';
import { FindingDraft, FindingDrafts, ReviewDraft, ReviewDrafts } from './schemas.ts';
import { lineHasFragment, locateQuote } from './quote.ts';
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
  /** Model calls in flight at once; by default AIW_CONCURRENCY or 4. */
  concurrency?: number;
}

export interface AuditResult {
  findings: Finding[];
  /** Drafts dropped because their quote is not in the audited text. */
  dropped: number;
  /** A few of the dropped quotes, to see what the model cites wrongly. */
  droppedSamples: string[];
  calls: number;
  /** Providers that really answered (the reserve may have stood in): a judge of the audit must differ. */
  providers: ProviderName[];
}

const MAX_DROPPED_SAMPLES = 10;

const normalize = (s: string) => s.replace(/[«»"“”„]/gu, '"').replace(/[ёЁ]/gu, 'е').replace(/\s+/gu, ' ').trim().toLowerCase();

/**
 * Episode of a quote inside a plan block: the "Серия N." line it comes from.
 * When the same words stand in several episodes, the one the auditor named wins.
 */
function episodeOfQuote(block: AuditBlock, fragment: string, named?: number): number | undefined {
  const eps = block.text
    .split('\n')
    .filter((l) => lineHasFragment(l, fragment))
    .map((l) => /^Серия (\d+)\./u.exec(l))
    .filter((m): m is RegExpExecArray => !!m)
    .map((m) => Number(m[1]));
  return named !== undefined && eps.includes(named) ? named : eps[0];
}

/** The episode of a draft: from the quote's line, or the model's number if it is inside the block. */
function episodeOf(block: AuditBlock, fragment: string, named?: number): number | undefined {
  const inBlock = (ep: number | undefined) =>
    ep !== undefined && (!block.episodes || (ep >= block.episodes[0] && ep <= block.episodes[1])) ? ep : undefined;
  return block.episodes ? (episodeOfQuote(block, fragment, named) ?? inBlock(named)) : named;
}

/** Where a quote was found: in the audited block, or in the bible/facts shown next to it. */
function locate(block: AuditBlock, facts: string, quote: string): { fragment: string; inBlock: boolean } | undefined {
  const inBlock = locateQuote(block.text, quote);
  if (inBlock) return { fragment: inBlock, inBlock: true };
  const inFacts = locateQuote(facts, quote);
  return inFacts ? { fragment: inFacts, inBlock: false } : undefined;
}

/** The episode of a finding whose quote comes from the bible: the model's number, if it is inside the block. */
function episodeFromFacts(block: AuditBlock, named?: number): number | undefined {
  if (named === undefined) return undefined;
  return !block.episodes || (named >= block.episodes[0] && named <= block.episodes[1]) ? named : undefined;
}

function reviewFinding(d: ReviewDraft, block: AuditBlock, facts: string, category: Category): Finding | undefined {
  const at = locate(block, facts, d.quote);
  if (!at) return undefined;
  const f = makeFinding({
    check: `review.${category.id}`,
    controller: 'logic',
    severity: LEVEL_SEVERITY[d.level],
    // The model names the hole type among the category's; code falls back to the first.
    holeType: d.holeType !== undefined && category.hole_types.includes(d.holeType) ? d.holeType : category.hole_types[0],
    episode: at.inBlock ? episodeOf(block, at.fragment, d.episode) : episodeFromFacts(block, d.episode),
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

function personaFinding(d: FindingDraft, block: AuditBlock, facts: string, check: string): Finding | undefined {
  const at = locate(block, facts, d.quote);
  if (!at) return undefined;
  return makeFinding({
    check,
    controller: 'logic',
    severity: d.severity,
    holeType: d.holeType,
    episode: at.inBlock ? episodeOf(block, at.fragment, d.episode) : episodeFromFacts(block, d.episode),
    quote: d.quote,
    question: d.viewerQuestion.replace(/^Зритель спросит:\s*/u, ''),
    fixes: d.fixes as [string],
  });
}

/** Which review categories to pass: those of this genre, narrowed by the options. */
function pickCategories(review: ReviewMethod, genreId: string, opts: AuditOptions): Category[] {
  return review.categories.filter(
    (c) =>
      (!c.genres.length || c.genres.includes(genreId)) &&
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
  const kbText = knowledgeBlock(deps.kb, deps.kit, { step: 'review' });
  const terms = deps.kit.genre.terms;
  // A plan audit gets the whole bible as its fact base: world rules with their numbers, villains'
  // motives, dates, clues and guns are what most cross-episode holes are made of. A bible audit
  // already reads the bible as its text, so it gets only the facts table.
  const bible = bibleText(target.bible, terms);
  const facts = target.plan ? bible : factsText(target.bible) || 'Фактов пока нет.';
  const review = deps.kb.review;
  let blocks: AuditBlock[] = target.plan
    ? planBlocks(target.plan, opts.blockSize ?? target.plan.episodes.length, terms, deps.kit.frame.hook_types)
    : [{ label: 'библия', text: bible }];
  if (opts.episodes && target.plan) {
    blocks = blocks.filter((b) => opts.episodes!.some((ep) => b.episodes && ep >= b.episodes[0] && ep <= b.episodes[1]));
  }

  const found = new Map<string, Finding>();
  let dropped = 0;
  const droppedSamples: string[] = [];
  const drop = (quote: string) => {
    dropped++;
    if (droppedSamples.length < MAX_DROPPED_SAMPLES) droppedSamples.push(quote);
  };
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
  // One pass per category and block; the passes are independent, so they run a few at a time.
  // The results are applied in order, so the findings come out the same whatever finishes first.
  const passes = pickCategories(review, deps.kit.genre.id, opts).flatMap((category) => {
    const holes = deps.kb.holes.holes.filter((h) => category.hole_types.includes(h.id));
    // The genre guide adds its own questions per hole type, or replaces the catalog's.
    const guide = deps.kit.guide;
    const questions = holes.flatMap((h) => {
      const own = guide?.hole_questions[String(h.id)] ?? [];
      return guide?.replace_hole_questions.includes(String(h.id)) && own.length ? own : [...h.questions, ...own];
    });
    return blocks.map((block) => {
      const notice = category.with_notice_questions
        ? noticeQuestions(target.bible, block, target.plan).slice(0, MAX_NOTICE_QUESTIONS)
        : [];
      const system = renderPrompt(deps.kb, `${role}/devil_advocate`, {
        category: `${category.id}. ${category.name}`,
        hole_types: holes.map((h) => `${h.id} — ${h.name}`).join('; ') || String(category.hole_types[0]),
        hero: deps.kit.genre.terms.hero.nom,
        check: category.check,
        hole_questions: questions.map((q) => `- ${q}`).join('\n') || '- (нет)',
        extra_questions: notice.length ? `\nВопросы по конкретным событиям:\n${notice.map((q) => `- ${q}`).join('\n')}\n` : '',
        six_questions: review.six_questions.map((q, i) => `${i + 1}. ${q}`).join('\n'),
        review_rules: category.rules.map((r) => `- ${r}`).join('\n'),
        patterns: review.patterns.map((p) => `- ${p}`).join('\n'),
        categories: pickCategories(review, deps.kit.genre.id, {}).map((c) => `${c.id} — ${c.name}`).join('; '),
        severities,
        target: block.label,
        text: block.text,
        facts,
        schema: schemaText(ReviewDrafts),
      });
      return { category, block, system };
    });
  });
  const limit = opts.concurrency ?? concurrencyFromEnv();
  const drafts = await mapLimit(passes, limit, (p) => ask(`devil_advocate:${p.category.id}:${p.block.label}`, p.system, ReviewDrafts));
  passes.forEach((p, i) => {
    for (const d of drafts[i]!) {
      const f = reviewFinding(d, p.block, facts, p.category);
      if (f) keep(f);
      else drop(d.quote);
    }
  });

  if (opts.personas !== false) {
    const whole: AuditBlock = target.plan
      ? { label: 'план сезона', text: blocks.map((b) => b.text).join('\n'), episodes: [blocks[0]?.episodes?.[0] ?? 1, blocks.at(-1)?.episodes?.[1] ?? 1] }
      : blocks[0]!;
    const personas = deps.kit.personas.personas.map((p) => ({
      p,
      system: renderPrompt(deps.kb, `${role}/persona`, {
        persona: `${p.name}${p.age ? `, ${p.age} лет` : ''}`,
        focus: p.focus,
        target: whole.label,
        text: whole.text,
        holes: deps.kb.holes.holes.map((h) => `${h.id}. ${h.name}`).join('\n'),
        facts,
        schema: schemaText(FindingDrafts),
      }),
    }));
    const personaDrafts = await mapLimit(personas, limit, ({ p, system }) => ask(`persona:${p.id}`, system, FindingDrafts));
    personas.forEach(({ p }, i) => {
      for (const d of personaDrafts[i]!) {
        const f = personaFinding(d, whole, facts, `persona.${p.id}`);
        if (f) keep(f);
        else drop(d.quote);
      }
    });
  }

  return { findings: [...found.values()], dropped, droppedSamples, calls, providers: [...providers] };
}
