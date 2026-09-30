import { genreKit, type GenreKit, type Kb } from '@aiw/kb';
import { z } from 'zod';
import { makeFinding } from '../checks/code/finding.ts';
import { checkKnowledge } from '../checks/code/knowledge.ts';
import { checkLegalMarkers, checkLimits, checkScriptMetrics, collectTexts } from '../checks/code/production.ts';
import { runCodeChecks } from '../checks/code/runner.ts';
import { runScriptControllers } from '../checks/llm/controllers.ts';
import { episodeLine } from '../checks/llm/texts.ts';
import type { LlmRequest } from '../providers/types.ts';
import { EpisodeCard } from '../schemas/episodeCard.ts';
import { Script } from '../schemas/script.ts';
import { renderScript } from '../text/script.ts';
import { compressBible, MAX_COMPRESSION, type CompressionLevel } from './compress.ts';
import { checklistFinding, scoreChecklist, type ChecklistScore } from '../checks/code/checklist.ts';
import { judgeChecklist } from '../checks/llm/checklistJudge.ts';
import { runDevilAdvocate } from '../checks/llm/devilAdvocate.ts';
import { resolveFinding } from '../checks/llm/resolve.ts';
import { ExtractedFacts } from '../checks/llm/schemas.ts';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../prompts/render.ts';
import type { ProviderName } from '../providers/config.ts';
import type { LlmClient } from '../providers/llm.ts';
import { Bible } from '../schemas/bible.ts';
import { Concept, ConceptSet } from '../schemas/concept.ts';
import type { Finding } from '../schemas/finding.ts';
import { Logline } from '../schemas/logline.ts';
import { EpisodeOutline, FrameDeviation, SeasonPlan } from '../schemas/season.ts';
import type { StepId } from '../steps.ts';
import { Pipeline, PipelineError, type StepStatus } from './machine.ts';
import { getProject } from './project.ts';

export interface StepDeps {
  db: Db;
  llm: LlmClient;
  kb: Kb;
  projectId: string;
  /** Resolve blocker and major audit findings through author reply + judge (default true). */
  autoResolve?: boolean;
}

export interface StepRun {
  step: StepId;
  status: StepStatus;
  findings: Finding[];
  checklist?: ChecklistScore;
}

export interface RunOptions {
  /** Episode cards and scripts: only these episodes (cards: their blocks of 10). */
  episodes?: number[];
  /** Scripts: rewrite taking the open findings into account. */
  fix?: boolean;
}

/** Steps run by the agent. Polish is interactive (see polish.ts); export comes with M6. */
export const RUNNABLE_STEPS: StepId[] = ['concept', 'logline', 'bible', 'season_plan', 'episode_cards', 'scripts'];

/** Episode cards are written and approved in blocks of this many episodes. */
export const CARD_BLOCK = 10;

interface Ctx extends StepDeps {
  kit: GenreKit;
  memory: ProjectMemory;
  pipeline: Pipeline;
  kbText: string;
}

function context(deps: StepDeps): Ctx {
  const project = getProject(deps.db, deps.projectId);
  if (!project.genreId) throw new PipelineError('У проекта не выбран жанр');
  const kit = genreKit(deps.kb, project.genreId);
  return {
    ...deps,
    kit,
    memory: new ProjectMemory(deps.db, deps.projectId),
    pipeline: new Pipeline(deps.db, deps.projectId),
    kbText: knowledgeBlock(deps.kb, kit),
  };
}

function need<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new PipelineError(`Нет результата шага: ${what}`);
  return value;
}

const json = (x: unknown) => JSON.stringify(x, null, 1);

/** Runs one pipeline step: model call, code checks, audit, status. */
export async function runStep(deps: StepDeps, step: StepId, opts: RunOptions = {}): Promise<StepRun> {
  if (!RUNNABLE_STEPS.includes(step)) throw new PipelineError(`Шаг ${step} появится на следующих вехах`);
  const ctx = context(deps);
  const mark = ctx.pipeline.begin(step);
  try {
    const run = await RUNNERS[step]!(ctx, opts);
    ctx.pipeline.clearIncomplete(step);
    return { ...run, step, status: ctx.pipeline.finish(step) };
  } catch (err) {
    ctx.pipeline.abort(step, mark, err instanceof Error ? err.message.slice(0, 200) : undefined);
    throw err;
  }
}

/**
 * Approves a step. The concept step needs the producer's choice of one of three concepts.
 * Episode cards are approved in blocks of 10 (block = first episode of the block);
 * the step is approved when every block is.
 */
export function approveStep(deps: Omit<StepDeps, 'llm'>, step: StepId, opts: { choice?: number; block?: number } = {}): void {
  const pipeline = new Pipeline(deps.db, deps.projectId);
  const memory = new ProjectMemory(deps.db, deps.projectId);
  if (step === 'episode_cards' && opts.block !== undefined) {
    if (pipeline.get(step).status === 'checking') throw new PipelineError('Карточки ещё пишутся. Дождитесь окончания');
    pipeline.assertPreviousDone(step);
    const plan = need(memory.currentPlan(), 'план сезона');
    const starts = cardBlocks(plan.episodes.map((e) => e.ep)).map((b) => b[0]!);
    if (!starts.includes(opts.block)) throw new PipelineError(`Нет блока карточек, начинающегося с ${opts.block}-й серии`);
    const eps = cardBlocks(plan.episodes.map((e) => e.ep)).find((b) => b[0] === opts.block)!;
    const cards = new Set(memory.cards().map((c) => c.ep));
    if (eps.some((ep) => !cards.has(ep))) throw new PipelineError(`В блоке ${eps[0]}–${eps.at(-1)} есть серии без карточек`);
    // Blockers of the block's episodes and those about all cards at once (no episode).
    const blockers = memory
      .findingsOf('episode_cards', 'open')
      .filter((f) => f.severity === 'blocker' && (f.episode === null || eps.includes(f.episode)));
    if (blockers.length) throw new PipelineError(`Нельзя утвердить серии ${eps[0]}–${eps.at(-1)}: открытых блокирующих замечаний — ${blockers.length}`);
    const approved = new Set(((memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []));
    approved.add(opts.block);
    memory.saveArtifact('card_blocks', { approved: [...approved].sort((a, b) => a - b) });
    if (starts.every((b) => approved.has(b))) pipeline.approve(step);
    return;
  }
  if (step === 'episode_cards') {
    const plan = need(memory.currentPlan(), 'план сезона');
    const approved = new Set((memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []);
    const left = cardBlocks(plan.episodes.map((e) => e.ep)).filter((b) => !approved.has(b[0]!));
    if (left.length) throw new PipelineError(`Сначала утвердите блоки карточек: ${left.map((b) => `${b[0]}–${b.at(-1)}`).join(', ')}`);
  }
  if (step === 'concept') {
    const concepts = ConceptSet.parse(need(memory.latestArtifact('concept'), 'концепции'));
    const chosen = opts.choice === undefined ? undefined : concepts[opts.choice];
    if (!chosen) throw new PipelineError('Выберите одну из трёх концепций');
    pipeline.approve(step);
    memory.saveArtifact('concept_choice', chosen);
    return;
  }
  pipeline.approve(step);
}

export function skipStep(deps: Omit<StepDeps, 'llm'>, step: StepId): void {
  new Pipeline(deps.db, deps.projectId).skip(step);
}

type Runner = (ctx: Ctx, opts: RunOptions) => Promise<Omit<StepRun, 'step' | 'status'>>;

/** Episodes split into blocks of CARD_BLOCK. */
export function cardBlocks(eps: number[]): number[][] {
  const sorted = [...eps].sort((a, b) => a - b);
  const out: number[][] = [];
  for (let i = 0; i < sorted.length; i += CARD_BLOCK) out.push(sorted.slice(i, i + CARD_BLOCK));
  return out;
}

function cardsFor(eps: number[]) {
  return z
    .array(EpisodeCard)
    .length(eps.length)
    .superRefine((cards, ctx) => {
      cards.forEach((c, i) => {
        if (c.ep !== eps[i]) ctx.addIssue({ code: 'custom', path: [i, 'ep'], message: `Здесь должна быть карточка ${eps[i]}-й серии` });
      });
    });
}

const RUNNERS: Partial<Record<StepId, Runner>> = {
  async concept(ctx) {
    const idea = need(ctx.memory.latestArtifact('idea') as { text: string } | undefined, 'идея').text;
    const { data } = await ctx.llm.completeJson(ConceptSet, {
      role: 'architect',
      projectId: ctx.projectId,
      step: 'concept',
      request: {
        task: 'concept',
        cacheablePrefix: [ctx.kbText],
        system: renderPrompt(ctx.kb, 'architect/concept', { idea, schema: schemaText(ConceptSet) }),
        messages: [{ role: 'user', content: 'Предложи три концепции. Только JSON.' }],
      },
    });
    ctx.memory.saveArtifact('concept', data);
    return { findings: [] };
  },

  async logline(ctx) {
    const concept = Concept.parse(need(ctx.memory.latestArtifact('concept_choice'), 'выбранная концепция'));
    const { data } = await ctx.llm.completeJson(Logline, {
      role: 'architect',
      projectId: ctx.projectId,
      step: 'logline',
      request: {
        task: 'logline',
        cacheablePrefix: [ctx.kbText],
        system: renderPrompt(ctx.kb, 'architect/logline', { concept: json(concept), schema: schemaText(Logline) }),
        messages: [{ role: 'user', content: 'Сделай логлайн. Только JSON.' }],
      },
    });
    ctx.memory.saveArtifact('logline', data);
    return { findings: [] };
  },

  async bible(ctx) {
    const concept = ctx.memory.latestArtifact('concept_choice');
    const logline = need(ctx.memory.latestArtifact('logline'), 'логлайн');
    const written = await ctx.llm.completeJson(Bible, {
      role: 'architect',
      projectId: ctx.projectId,
      step: 'bible',
      request: {
        task: 'bible',
        cacheablePrefix: [ctx.kbText],
        system: renderPrompt(ctx.kb, 'architect/bible', { concept: json(concept ?? {}), logline: json(logline), schema: schemaText(Bible) }),
        messages: [{ role: 'user', content: 'Напиши библию. Только JSON.' }],
      },
    });
    let bible = written.data;
    if (bible.facts.length === 0) {
      const { data: extracted } = await ctx.llm.completeJson(ExtractedFacts, {
        role: 'helper',
        projectId: ctx.projectId,
        step: 'bible',
        request: {
          task: 'extract_facts',
          system: renderPrompt(ctx.kb, 'helper/extract_facts', { bible: json(bible), schema: schemaText(ExtractedFacts) }),
          messages: [{ role: 'user', content: 'Выпиши факты. Только JSON.' }],
        },
      });
      bible = Bible.parse({ ...bible, facts: extracted.facts, knowledge: [...bible.knowledge, ...extracted.knowledge] });
    }
    ctx.memory.importBible(bible);
    return audit(ctx, 'bible', [written.provider]);
  },

  async episode_cards(ctx, opts) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const plan = need(ctx.memory.currentPlan(), 'план сезона');
    const p = ctx.kit.production;
    const f = ctx.kit.frame;
    let blocks = cardBlocks(plan.episodes.map((e) => e.ep));
    if (opts.episodes) blocks = blocks.filter((b) => b.some((ep) => opts.episodes!.includes(ep)));
    const approved = new Set(((ctx.memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []));

    for (const eps of blocks) {
      const outlines = plan.episodes.filter((e) => eps.includes(e.ep));
      const { data } = await ctx.llm.completeJson(cardsFor(eps), {
        role: 'architect',
        projectId: ctx.projectId,
        step: 'episode_cards',
        request: {
          task: `episode_cards:${eps[0]}-${eps.at(-1)}`,
          cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`, `# План сезона\n${json(plan)}`],
          system: renderPrompt(ctx.kb, 'architect/episode_cards', {
            from: eps[0]!,
            to: eps.at(-1)!,
            outlines: outlines.map(episodeLine).join('\n'),
            duration: f.duration_s.target,
            duration_range: `${f.duration_s.min}–${f.duration_s.max} с`,
            max_line_words: p.script_metrics.max_line_words,
            max_speakers: p.limits.max_speakers_per_scene,
            locations: bible.locations.join(', ') || 'список не задан',
            schema: schemaText(cardsFor(eps)),
          }),
          messages: [{ role: 'user', content: 'Напиши карточки. Только JSON.' }],
        },
      });
      for (const card of data) {
        ctx.memory.closeObsolete('episode_cards', card.ep, 'Карточка переписана');
        ctx.memory.saveCard(card);
      }
      approved.delete(eps[0]!);
      // Saved after every block: a run that fails later still leaves a consistent state.
      ctx.memory.saveArtifact('card_blocks', { approved: [...approved].sort((a, b) => a - b) });
      ctx.memory.saveArtifact('episode_cards', { episodes: ctx.memory.cards().map((c) => c.ep) });
    }

    const cards = ctx.memory.cards();
    const findings = [
      ...checkLimits(bible, cards, p),
      ...checkKnowledge(cards, bible),
      ...checkLegalMarkers(collectTexts({ cards }), ctx.kit.legal),
      ...cards
        .filter((c) => !plan.episodes.some((e) => e.ep === c.ep))
        .map((c) =>
          makeFinding({
            check: 'cards.not_in_plan',
            controller: 'structure',
            severity: 'blocker',
            holeType: 10,
            episode: c.ep,
            quote: `Карточка ${c.ep}-й серии`,
            question: 'откуда эта серия? Её нет в плане.',
            fixes: ['Убрать карточку', 'Добавить серию в план'],
          }),
        ),
    ];
    ctx.memory.saveFindings(findings, 'episode_cards');
    // These checks cover all cards: what they no longer raise is fixed.
    ctx.memory.closeSuperseded('episode_cards', findings.map((x) => x.id), 'Больше не подтверждается');
    return { findings };
  },

  async scripts(ctx, opts) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const plan = ctx.memory.currentPlan();
    const cards = ctx.memory.cards();
    if (cards.length === 0) throw new PipelineError('Нет карточек серий');
    const eps = (opts.episodes ?? cards.map((c) => c.ep)).filter((ep) => cards.some((c) => c.ep === ep)).sort((a, b) => a - b);
    const p = ctx.kit.production;
    const f = ctx.kit.frame;
    const all: Finding[] = [];

    for (const ep of eps) {
      const card = cards.find((c) => c.ep === ep)!;
      const previous = [ep - 2, ep - 1]
        .filter((n) => n >= 1)
        .map((n) => {
          const s = ctx.memory.script(n);
          if (s) return renderScript(s);
          const c = cards.find((x) => x.ep === n);
          return c ? `Серия ${n} (только карточка): ${c.event}. Клиффхэнгер: ${c.cliffhanger}` : '';
        })
        .filter(Boolean)
        .join('\n\n');
      const notes = opts.fix
        ? ctx.memory.findingsOf('scripts', 'open').filter((x) => x.episode === ep)
        : [];
      let level: CompressionLevel = 1;
      const build = (): LlmRequest => ({
        task: `script:${ep}`,
        cacheablePrefix: [ctx.kbText],
        system: renderPrompt(ctx.kb, 'writer/scripts', {
          ep,
          card: json(card),
          bible: json(compressBible(bible, card, level)),
          previous: previous || 'Это первая серия.',
          fix_notes: notes.length
            ? `\nИсправь замечания к прошлой версии:\n${notes.map((n) => `- «${n.quote}» — ${n.viewerQuestion} Как исправить: ${n.fixes.join(' / ')}`).join('\n')}\n`
            : '',
          duration: card.duration_s,
          chars_per_minute: p.script_metrics.line_chars_per_minute.join('–'),
          max_line_words: p.script_metrics.max_line_words,
          max_overlay_words: p.script_metrics.max_overlay_words,
          max_speakers: p.limits.max_speakers_per_scene,
          schema: schemaText(Script),
        }),
        messages: [{ role: 'user', content: 'Напиши сценарий. Только JSON.' }],
      });
      const { data, provider: writer } = await ctx.llm.completeJson(Script, {
        role: 'writer',
        projectId: ctx.projectId,
        step: 'scripts',
        request: build(),
        // Only the system part (with the bible) shrinks; the dialogue, e.g. a retry, stays.
        shrink: (req) => {
          if (level < MAX_COMPRESSION) level = (level + 1) as CompressionLevel;
          return { ...build(), messages: req.messages };
        },
      });
      const script = { ...data, ep };
      ctx.memory.closeObsolete('scripts', ep, 'Сценарий переписан');
      ctx.memory.saveScript(script);

      const found = [
        ...checkScriptMetrics(script, p, f),
        ...checkLegalMarkers(collectTexts({ scripts: [script] }), ctx.kit.legal),
      ];
      const model = await runScriptControllers(
        { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step: 'scripts' },
        { script, card, outline: plan?.episodes.find((e) => e.ep === ep), authorProvider: writer },
      );
      found.push(...model.findings);
      ctx.memory.saveFindings(found, 'scripts');
      ctx.memory.saveArtifact('scripts', { episodes: ctx.memory.scripts().map((s) => s.ep) });
      all.push(...found);
    }
    return { findings: all };
  },

  async season_plan(ctx) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const f = ctx.kit.frame;
    const episodes: EpisodeOutline[] = [];
    const deviations: FrameDeviation[] = [];
    const authors = new Set<ProviderName>();
    // The plan is written in the frame's blocks: one answer for the whole season is too long.
    for (const [from, to] of planParts(f.episodes, f.blocks)) {
      const part = planPart(from, to);
      const { data, provider } = await ctx.llm.completeJson(part, {
        role: 'architect_heavy',
        projectId: ctx.projectId,
        step: 'season_plan',
        request: {
          task: `season_plan:${from}-${to}`,
          cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`],
          system: renderPrompt(ctx.kb, 'architect_heavy/season_plan', {
            episodes: f.episodes,
            from,
            to,
            written: episodes.length ? episodes.map(episodeLine).join('\n') : 'Это начало сезона.',
            anchor_ids: Object.keys(f.anchors).join(', '),
            villain_roles: Object.values(f.villains?.roles ?? {}).join(', ') || 'в жанре нет лестницы злодеев',
            tolerance: f.tolerance,
            schema: schemaText(part),
          }),
          messages: [{ role: 'user', content: `Составь план серий ${from}–${to}. Только JSON.` }],
        },
      });
      episodes.push(...data.episodes);
      deviations.push(...data.deviations);
      authors.add(provider);
    }
    ctx.memory.importPlan(SeasonPlan.parse({ episodes, deviations }));
    return audit(ctx, 'season_plan', [...authors]);
  },
};

/** Parts of the season to write the plan in: the frame's blocks, or blocks of 10. */
export function planParts(episodes: number, blocks: [number, number][]): [number, number][] {
  const sorted = [...blocks].sort((a, b) => a[0] - b[0]);
  const covers = sorted.length > 0 && sorted[0]![0] === 1 && sorted.at(-1)![1] === episodes &&
    sorted.every(([a, b], i) => a <= b && (i === 0 || a === sorted[i - 1]![1] + 1));
  if (covers) return sorted;
  const out: [number, number][] = [];
  for (let a = 1; a <= episodes; a += CARD_BLOCK) out.push([a, Math.min(a + CARD_BLOCK - 1, episodes)]);
  return out;
}

function planPart(from: number, to: number) {
  return z.object({
    episodes: z
      .array(EpisodeOutline)
      .length(to - from + 1)
      .superRefine((list, ctx) => {
        list.forEach((e, i) => {
          if (e.ep !== from + i) ctx.addIssue({ code: 'custom', path: [i, 'ep'], message: `Здесь должна быть ${from + i}-я серия` });
        });
      }),
    deviations: z.array(FrameDeviation).default([]),
  });
}

/** How many audit findings the author and the judge answer automatically in one run. */
const MAX_AUTO_RESOLVE = 20;

/**
 * Code checks first, then the devil's advocate from another family; blocker and major
 * findings go through author reply and judge. Everything lands in the findings table.
 */
async function audit(ctx: Ctx, step: 'bible' | 'season_plan', authors: ProviderName[]): Promise<Omit<StepRun, 'step' | 'status'>> {
  let bible = need(ctx.memory.currentBible(), 'библия');
  const plan = step === 'season_plan' ? ctx.memory.currentPlan() : undefined;

  const code = runCodeChecks({ kit: ctx.kit, bible, plan });
  const findings: Finding[] = [...code.findings];
  let checklist: ChecklistScore | undefined;
  if (plan) {
    checklist = scoreChecklist(ctx.kit.checklist, ctx.kit.rules, code);
    if (checklist.passed === null) {
      // Code could not decide: the judge of another family scores the rest, backed by quotes.
      const judged = await judgeChecklist(
        { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step },
        checklist,
        { bible, plan, authorProvider: authors },
      );
      checklist = { ...judged, finding: checklistFinding(judged, true) };
    }
    if (checklist.finding) findings.push(checklist.finding);
  }

  const auditRes = await runDevilAdvocate(
    { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step },
    { bible, plan, authorProvider: authors },
  );
  const raised = [...findings, ...auditRes.findings];
  ctx.memory.saveFindings(raised, step);
  ctx.memory.closeSuperseded(step, raised.map((f) => f.id), 'Заменено новой версией');

  const outcome: Finding[] = [...findings];
  let answered = 0;
  for (const f of auditRes.findings) {
    const stored = ctx.memory.finding(f.id);
    if (stored && stored.status !== 'open') {
      // Already closed with a fact or dismissed by the producer: no second round.
      outcome.push({ ...f, status: stored.status as Finding['status'], resolutionFactId: stored.resolutionFactId ?? undefined });
      continue;
    }
    if (ctx.autoResolve === false || f.severity === 'minor' || answered >= MAX_AUTO_RESOLVE) {
      outcome.push(f);
      continue;
    }
    answered++;
    const res = await resolveFinding(
      { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step, authorRole: 'architect', authorProvider: authors, judgeRole: 'critic_of_architect' },
      f,
      bible,
      plan,
    );
    if (res.bible !== bible && res.reply?.fact) {
      ctx.memory.addFact(res.reply.fact, res.reply.knowledge, { author: 'agent', note: `Ответ на замечание ${f.id}` });
      bible = res.bible;
    }
    ctx.memory.setFindingOutcome(f.id, { status: res.finding.status, verdict: res.verdict, resolutionFactId: res.finding.resolutionFactId });
    outcome.push(res.finding);
  }
  return { findings: outcome, checklist };
}
