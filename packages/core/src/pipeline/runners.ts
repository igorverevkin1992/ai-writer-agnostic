import { genreKit, METHOD_STEPS, type GenreKit, type Kb, type MethodStep } from '@aiw/kb';
import { z } from 'zod';
import { formatEpisodes, makeFinding } from '../checks/code/finding.ts';
import { checkKnowledge } from '../checks/code/knowledge.ts';
import { checkLegalMarkers, checkLimits, checkScriptMetrics, collectTexts } from '../checks/code/production.ts';
import { runCodeChecks } from '../checks/code/runner.ts';
import { runScriptControllers } from '../checks/llm/controllers.ts';
import { episodeLine } from '../checks/llm/texts.ts';
import type { LlmRequest } from '../providers/types.ts';
import { InvalidOutputError } from '../providers/errors.ts';
import { EpisodeCard } from '../schemas/episodeCard.ts';
import { Script } from '../schemas/script.ts';
import { ContinuityExtract, ledgerLines } from '../schemas/continuity.ts';
import { renderScript } from '../text/script.ts';
import { compressBible, MAX_COMPRESSION, type CompressionLevel } from './compress.ts';
import { checklistFinding, scoreChecklist, type ChecklistScore } from '../checks/code/checklist.ts';
import { judgeChecklist } from '../checks/llm/checklistJudge.ts';
import { runDevilAdvocate } from '../checks/llm/devilAdvocate.ts';
import { runReviewSummary } from '../checks/llm/reviewSummary.ts';
import { resolveFinding } from '../checks/llm/resolve.ts';
import { ExtractedFacts } from '../checks/llm/schemas.ts';
import { linterLimits } from './limits.ts';
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
import { INCOMPLETE_CHECK, Pipeline, PipelineError, type StepStatus } from './machine.ts';
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

function context(deps: StepDeps, step: StepId): Ctx {
  const project = getProject(deps.db, deps.projectId);
  if (!project.genreId) throw new PipelineError('У проекта не выбран жанр');
  const kit = genreKit(deps.kb, project.genreId);
  return {
    ...deps,
    kit,
    memory: new ProjectMemory(deps.db, deps.projectId),
    pipeline: new Pipeline(deps.db, deps.projectId),
    kbText: knowledgeBlock(deps.kb, kit, { step: (METHOD_STEPS as readonly string[]).includes(step) ? (step as MethodStep) : undefined }),
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
  const ctx = context(deps, step);
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
    const outdated = memory.staleEpisodes().cards.filter((ep) => eps.includes(ep));
    if (outdated.length) throw new PipelineError(`В блоке ${eps[0]}–${eps.at(-1)} устаревшие карточки: ${outdated.join(', ')}. Перепишите их`);
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
    const before = memory.latestArtifact('concept_choice');
    memory.saveArtifact('concept_choice', chosen);
    // Another concept: everything built on the old one needs approval again.
    if (before && JSON.stringify(before) !== JSON.stringify(chosen)) pipeline.resetLater(step);
    return;
  }
  if (step === 'scripts') {
    const outdated = memory.staleEpisodes().scripts;
    if (outdated.length) throw new PipelineError(`Устаревшие или непроверенные сценарии: ${outdated.join(', ')}. Перепишите их`);
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
        system: renderPrompt(ctx.kb, 'architect/logline', {
          concept: json(concept),
          hero: ctx.kit.genre.terms.hero.nom,
          wound: ctx.kit.genre.terms.wound.toLowerCase(),
          schema: schemaText(Logline),
        }),
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
        system: renderPrompt(ctx.kb, 'architect/bible', {
          concept: json(concept ?? {}),
          logline: json(logline),
          hero: ctx.kit.genre.terms.hero.nom,
          fix_notes: fixNotes(ctx, 'bible'),
          schema: schemaText(Bible),
        }),
        messages: [{ role: 'user', content: 'Напиши библию. Только JSON.' }],
      },
    });
    // The helper always extracts the facts the scenes rely on: the architect's own list is rarely
    // complete. Both lists merge, deduplicated by id and text.
    const { data: extracted } = await ctx.llm.completeJson(ExtractedFacts, {
      role: 'helper',
      projectId: ctx.projectId,
      step: 'bible',
      request: {
        task: 'extract_facts',
        system: renderPrompt(ctx.kb, 'helper/extract_facts', { bible: json(written.data), schema: schemaText(ExtractedFacts) }),
        messages: [{ role: 'user', content: 'Выпиши факты. Только JSON.' }],
      },
    });
    ctx.memory.importBible(mergeExtracted(written.data, extracted));
    return audit(ctx, 'bible', [written.provider]);
  },

  async episode_cards(ctx, opts) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const plan = need(ctx.memory.currentPlan(), 'план сезона');
    const p = ctx.kit.production;
    const f = ctx.kit.frame;
    const existing = new Set(ctx.memory.cards().map((c) => c.ep));
    // First generation goes in blocks. Asked for particular episodes that already have cards, only
    // those cards are rewritten (with their fix notes), block by block; episodes without a card
    // still get their whole block.
    const wanted = opts.episodes?.filter((ep) => plan.episodes.some((e) => e.ep === ep));
    const groups: number[][] = [];
    for (const block of cardBlocks(plan.episodes.map((e) => e.ep))) {
      if (!wanted) groups.push(block);
      else if (block.some((ep) => wanted.includes(ep) && !existing.has(ep))) groups.push(block);
      else if (block.some((ep) => wanted.includes(ep))) groups.push(block.filter((ep) => wanted.includes(ep)));
    }
    const approved = new Set(((ctx.memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []));

    for (const eps of groups) {
      const blockStart = cardBlocks(plan.episodes.map((e) => e.ep)).find((b) => b.includes(eps[0]!))![0]!;
      const whole = eps.length > 1 && eps.at(-1)! - eps[0]! === eps.length - 1;
      const which = whole ? `${eps[0]}–${eps.at(-1)}` : eps.join(', ');
      const outlines = plan.episodes.filter((e) => eps.includes(e.ep));
      const cards = ctx.memory.cards();
      // TODO(hook_kind): validate card beat `hook_kind` against the frame once the frame lists hook kinds; it does not yet.
      const { data } = await ctx.llm.completeJson(cardsFor(eps), {
        role: 'architect',
        projectId: ctx.projectId,
        step: 'episode_cards',
        request: {
          task: `episode_cards:${whole ? `${eps[0]}-${eps.at(-1)}` : eps.join(',')}`,
          cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`, `# План сезона\n${json(plan)}`],
          system: renderPrompt(ctx.kb, 'architect/episode_cards', {
            which,
            from: eps[0]!,
            to: eps.at(-1)!,
            outlines: outlines.map((e) => episodeLine(e, ctx.kit.genre.terms, f.hook_types)).join('\n'),
            neighbours: neighbourCards(cards, eps),
            hero: ctx.kit.genre.terms.hero.nom,
            duration: f.duration_s.target,
            duration_range: `${f.duration_s.min}–${f.duration_s.max} с`,
            max_line_words: p.script_metrics.max_line_words,
            max_speakers: p.limits.max_speakers_per_scene,
            locations: bible.locations.join(', ') || 'список не задан',
            plants: plantsText(bible, eps),
            nested: nestedText(bible),
            fix_notes: fixNotes(ctx, 'episode_cards', eps),
            schema: schemaText(cardsFor(eps)),
          }),
          messages: [{ role: 'user', content: 'Напиши карточки. Только JSON.' }],
        },
      });
      for (const card of data) {
        ctx.memory.closeObsolete('episode_cards', card.ep, 'Карточка переписана');
        ctx.memory.saveCard(card);
      }
      approved.delete(blockStart);
      // Saved after every block: a run that fails later still leaves a consistent state.
      ctx.memory.saveArtifact('card_blocks', { approved: [...approved].sort((a, b) => a - b) });
      ctx.memory.saveArtifact('episode_cards', { episodes: ctx.memory.cards().map((c) => c.ep) });
    }

    const cards = ctx.memory.cards();
    // Genre code checks that need the cards (beats, golden three, knowledge on cards…), then the
    // step's own checks; a finding both raise is kept once, rule-attributed.
    const findings = uniqueFindings([
      ...cardCodeChecks(ctx, bible, plan, cards),
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
    ]);
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
      const outline = plan?.episodes.find((e) => e.ep === ep);
      // The next episode's first seconds: the cliffhanger of this one must flow into them.
      const nextCard = cards.find((c) => c.ep === ep + 1);
      const nextOutline = plan?.episodes.find((e) => e.ep === ep + 1);
      const nextHook = nextCard?.hook_0_5s ?? (nextOutline ? `обрыв по плану: ${nextOutline.cliffhanger}` : 'это последняя серия сезона');
      const ledger = ctx.memory.continuityDigest(ep);
      // The writer gets the whole bible; it shrinks only when the input is really over the limit.
      let level: CompressionLevel = 0;
      const build = (): LlmRequest => ({
        task: `script:${ep}`,
        cacheablePrefix: [ctx.kbText],
        system: renderPrompt(ctx.kb, 'writer/scripts', {
          ep,
          card: json(card),
          bible: json(compressBible(bible, card, level)),
          previous: previous || 'Это первая серия.',
          outline: plan
            ? plan.episodes.filter((e) => Math.abs(e.ep - ep) <= 2).map((e) => episodeLine(e, ctx.kit.genre.terms, f.hook_types)).join('\n')
            : 'Плана сезона нет.',
          next_hook: nextHook,
          plants: cardPlantsText(bible, card),
          knowledge_mode: outline?.knowledge_mode ? KNOWLEDGE_MODE_TEXT[outline.knowledge_mode] : 'не задан планом',
          ledger: ledger.length ? ledger.join('\n') : 'Пока пусто: это первая написанная серия.',
          fix_notes: notes.length
            ? `\nИсправь замечания к прошлой версии:\n${notes.map((n) => `- «${n.quote}» — ${n.viewerQuestion} Как исправить: ${n.fixes.join(' / ')}`).join('\n')}\n`
            : '',
          duration: card.duration_s,
          chars_per_minute: p.script_metrics.line_chars_per_minute.join('–'),
          max_line_words: p.script_metrics.max_line_words,
          max_overlay_words: p.script_metrics.max_overlay_words,
          max_speakers: p.limits.max_speakers_per_scene,
          ...linterLimits(ctx.kit),
          paywall_ep: f.free,
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
      // The step result changed now: if the checks below fail, the run counts as unfinished.
      ctx.memory.saveArtifact('scripts', { episodes: ctx.memory.scripts().map((s) => s.ep) });
      // Who really wrote it (the reserve may have stood in): later critics must be of another family.
      ctx.memory.saveArtifact(`author:scripts:${ep}`, { provider: writer });
      try {
        // Genre code checks on the script (linter: talking heads, faces, sound, overlap…), then the
        // step's own metrics; duplicates are kept once. All of it goes into the fix loop below.
        const found = uniqueFindings([
          ...runCodeChecks({ kit: ctx.kit, scripts: [script] }).findings,
          ...checkScriptMetrics(script, p, f),
          ...checkLegalMarkers(collectTexts({ scripts: [script] }), ctx.kit.legal),
        ]);
        // The controllers read the card as JSON: the ledger digest rides along in it, so the logic
        // controller sees who holds what and who knows what by this episode.
        const cardForCritics = { ...card, continuity: ledger };
        const model = await runScriptControllers(
          { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step: 'scripts' },
          { script, card: cardForCritics, outline, authorProvider: writer },
        );
        found.push(...model.findings);
        ctx.memory.saveFindings(found, 'scripts');
        all.push(...found);
        await recordContinuity(ctx, bible, script);
      } catch (err) {
        // An unchecked script must be written and checked again, not approved.
        ctx.memory.markOutdated('scripts', [ep]);
        throw err;
      }
    }
    return { findings: all };
  },

  async season_plan(ctx) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const f = ctx.kit.frame;
    const outline = outlineSchema(f);
    const episodes: EpisodeOutline[] = [];
    const deviations: FrameDeviation[] = [];
    const authors = new Set<ProviderName>();
    // Parts written for this very bible survive a failed run: they are not paid for twice.
    const stamp = fingerprint(bible);
    const saved = ctx.memory.latestArtifact(PARTIAL_PLAN) as PartialPlan | undefined;
    const partial: PartialPlan = saved?.bible === stamp ? saved : { bible: stamp, parts: {} };
    // The plan is written in parts of at least MIN_PLAN_PART episodes: one answer for the whole
    // season is too long, and shorter parts lose the season's memory.
    for (const [from, to] of planParts(f.episodes, f.blocks)) {
      const key = `${from}-${to}`;
      let part = partial.parts[key];
      if (!part) {
        const { data, provider } = await ctx.llm.completeJson(planPart(from, to, outline), {
          role: 'architect_heavy',
          projectId: ctx.projectId,
          step: 'season_plan',
          request: {
            task: `season_plan:${key}`,
            cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`],
            system: renderPrompt(ctx.kb, 'architect_heavy/season_plan', {
              episodes: f.episodes,
              from,
              to,
              // The whole table of what is written so far: rhythm, hooks, moods and knowledge are visible.
              written: episodes.length ? episodes.map((e) => episodeLine(e, ctx.kit.genre.terms, f.hook_types)).join('\n') : 'Это начало сезона.',
              hero: ctx.kit.genre.terms.hero.nom,
              anchor_ids: Object.keys(f.anchors).join(', '),
              kaif_lines: Object.keys(f.lines).length ? Object.entries(f.lines).map(([id, name]) => `${id} (${name})`).join(', ') : 'в этом жанре линий нет, оставь пустым',
              villain_block: villainBlock(f),
              hook_types: hookTypesText(f),
              tolerance: f.tolerance,
              fix_notes: fixNotes(ctx, 'season_plan', Array.from({ length: to - from + 1 }, (_, i) => from + i)),
              schema: schemaText(planPart(from, to, outline)),
            }),
            messages: [{ role: 'user', content: `Составь план серий ${from}–${to}. Только JSON.` }],
          },
        });
        part = { ...data, provider };
        partial.parts[key] = part;
        // Saved after every part: a run that fails later resumes from here.
        ctx.memory.saveArtifact(PARTIAL_PLAN, partial);
      }
      episodes.push(...part.episodes);
      deviations.push(...part.deviations);
      authors.add(part.provider);
    }
    const assembled = SeasonPlan.parse({ episodes, deviations });
    const reconciled = await reconcilePlan(ctx, bible, assembled, outline);
    if (reconciled.provider) authors.add(reconciled.provider);
    ctx.memory.importPlan(reconciled.plan);
    // The plan is whole: nothing is left to resume.
    ctx.memory.saveArtifact(PARTIAL_PLAN, { bible: stamp, parts: {} } satisfies PartialPlan);
    // Cards were written from the old plan: they need rewriting and approving again.
    const oldCards = ctx.memory.cards().map((c) => c.ep);
    if (oldCards.length) {
      ctx.memory.markOutdated('cards', oldCards);
      ctx.memory.saveArtifact('card_blocks', { approved: [] });
    }
    return audit(ctx, 'season_plan', [...authors]);
  },
};

/** Artifact that holds the parts of a season plan written so far (resume after a failure). */
export const PARTIAL_PLAN = 'season_plan:partial';

interface PartialPlan {
  /** Fingerprint of the bible the parts were written for. */
  bible: string;
  parts: Record<string, { episodes: EpisodeOutline[]; deviations: FrameDeviation[]; provider: ProviderName }>;
}

/** Stable short hash of a JSON value (djb2), for telling versions apart. */
function fingerprint(value: unknown): string {
  const text = JSON.stringify(value);
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

function hookTypesText(f: GenreKit['frame']): string {
  return Object.keys(f.hook_types).length
    ? `только из словаря жанра: ${Object.entries(f.hook_types).map(([id, name]) => `${id} (${name})`).join(', ')}`
    : 'своими словами (угроза, тайна, победа…)';
}

/**
 * Outline schema for a frame: where the frame lists hook types, `hook_type` is validated against
 * them when the answer is parsed, so a wrong type is fixed by the model's retry, not found as a
 * finding later.
 */
export function outlineSchema(frame: GenreKit['frame']) {
  const types = Object.keys(frame.hook_types);
  return types.length ? EpisodeOutline.extend({ hook_type: z.enum(types as [string, ...string[]]) }) : EpisodeOutline;
}
type OutlineSchema = ReturnType<typeof outlineSchema>;

/** Outlines of exactly these episodes, in order. */
function outlinesFor(eps: number[], outline: OutlineSchema) {
  return z
    .array(outline)
    .length(eps.length)
    .superRefine((list, ctx) => {
      list.forEach((e, i) => {
        if (e.ep !== eps[i]) ctx.addIssue({ code: 'custom', path: [i, 'ep'], message: `Здесь должна быть ${eps[i]}-я серия` });
      });
    });
}

/**
 * Reconciliation: the assembled plan goes through the code checks, and when they raise blocker or
 * major findings, one call of the heavy architect rewrites only the episodes they point at,
 * with the findings and the whole plan in front of it. One round at most; the audit that follows
 * runs the checks again and records what is left.
 */
async function reconcilePlan(ctx: Ctx, bible: Bible, plan: SeasonPlan, outline: OutlineSchema): Promise<{ plan: SeasonPlan; provider?: ProviderName }> {
  const serious = runCodeChecks({ kit: ctx.kit, bible, plan }).findings.filter((x) => x.severity !== 'minor' && x.episode !== undefined);
  if (serious.length === 0) return { plan };
  const f = ctx.kit.frame;
  const eps = [...new Set(serious.map((x) => x.episode!))].sort((a, b) => a - b);
  const schema = outlinesFor(eps, outline);
  let answer: { data: EpisodeOutline[]; provider: ProviderName };
  try {
    answer = await ctx.llm.completeJson(schema, {
      role: 'architect_heavy',
      projectId: ctx.projectId,
      step: 'season_plan',
      request: {
        task: `season_plan_fix:${eps.join(',')}`,
        cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`],
        system: renderPrompt(ctx.kb, 'architect_heavy/season_plan_fix', {
          episodes: formatEpisodes(eps),
          plan: plan.episodes.map((e) => episodeLine(e, ctx.kit.genre.terms, f.hook_types)).join('\n'),
          findings: serious
            .map((x) => `- Серия ${x.episode}: «${x.quote}» — ${x.viewerQuestion} Как исправить: ${x.fixes.join(' / ')}`)
            .join('\n'),
          hero: ctx.kit.genre.terms.hero.nom,
          anchor_ids: Object.keys(f.anchors).join(', '),
          hook_types: hookTypesText(f),
          schema: schemaText(schema),
        }),
        messages: [{ role: 'user', content: `Перепиши серии ${formatEpisodes(eps)}. Только JSON.` }],
      },
    });
  } catch (err) {
    // The extra call is best effort: a malformed answer leaves the plan as assembled, and the
    // audit records the findings for the producer instead of losing the paid parts.
    if (err instanceof InvalidOutputError) return { plan };
    throw err;
  }
  const byEp = new Map(answer.data.map((e) => [e.ep, e]));
  return { plan: SeasonPlan.parse({ episodes: plan.episodes.map((e) => byEp.get(e.ep) ?? e), deviations: plan.deviations }), provider: answer.provider };
}

/** Findings deduplicated by id; the first occurrence wins. */
function uniqueFindings(list: Finding[]): Finding[] {
  const seen = new Set<string>();
  return list.filter((f) => !seen.has(f.id) && seen.add(f.id));
}

/**
 * Code checks the cards add to what the bible and plan already raise: findings about the plan
 * alone belong to the season plan step and are not repeated here.
 */
function cardCodeChecks(ctx: Ctx, bible: Bible, plan: SeasonPlan, cards: EpisodeCard[]): Finding[] {
  const withoutCards = new Set(runCodeChecks({ kit: ctx.kit, bible, plan }).findings.map((f) => f.id));
  return runCodeChecks({ kit: ctx.kit, bible, plan, cards }).findings.filter((f) => !withoutCards.has(f.id));
}

/**
 * «Что исправить» for a step that is written again: its open findings (of these episodes, or of
 * the whole step) and the facts the producer added by hand. Empty on the first run.
 */
function fixNotes(ctx: Ctx, step: 'bible' | 'season_plan' | 'episode_cards', eps?: number[]): string {
  const open = ctx.memory
    .findingsOf(step, 'open')
    .filter((x) => x.check !== INCOMPLETE_CHECK && (!eps || x.episode === null || eps.includes(x.episode)));
  const facts = ctx.memory.factsBy('producer');
  if (!open.length && !facts.length) return '';
  const lines = [
    ...open.map((x) => `- ${x.episode ? `Серия ${x.episode}: ` : ''}«${x.quote}» — ${x.viewerQuestion} Как исправить: ${x.fixes.join(' / ')}`),
    ...facts.map((x) => `- Факт продюсера ${x.id}${x.since_ep ? ` (с ${x.since_ep}-й серии)` : ''}: ${x.text}. Текст должен ему соответствовать.`),
  ];
  return `\nЧто исправить (замечания к прошлой версии и факты продюсера):\n${lines.join('\n')}\n`;
}

/** Villain instructions of the plan prompt: only for a frame with a villain ladder. */
function villainBlock(f: GenreKit['frame']): string {
  if (!f.villains) return '';
  return [
    `- Сдвиг снятия злодея объясняется так же: в \`anchor\` — роль злодея из каркаса (${Object.values(f.villains.roles).join(', ')}).`,
    '- `takedown_rank` — ранг злодея, которого снимают в этой серии; `villains_introduced` — ранги злодеев, впервые появившихся в кадре.',
  ].join('\n');
}

/** Hooks and cliffhangers of the cards next to the rewritten episodes: the new card must fit between them. */
function neighbourCards(cards: EpisodeCard[], eps: number[]): string {
  const near = cards.filter((c) => !eps.includes(c.ep) && eps.some((ep) => Math.abs(ep - c.ep) === 1));
  return near.length ? near.map((c) => `Серия ${c.ep}: начало — ${c.hook_0_5s}; обрыв — ${c.cliffhanger}`).join('\n') : 'соседних карточек ещё нет';
}

const normText = (t: string) => t.replace(/[«»"“”„]/gu, '"').replace(/[ёЁ]/gu, 'е').replace(/[.\s]+$/u, '').replace(/\s+/gu, ' ').trim().toLowerCase();

/** The architect's facts and the helper's, merged: duplicates by id or text are dropped, knowledge follows the kept id. */
function mergeExtracted(bible: Bible, extracted: ExtractedFacts): Bible {
  const facts = [...bible.facts];
  const alias = new Map<string, string>();
  for (const f of extracted.facts) {
    const same = facts.find((x) => x.id === f.id || normText(x.text) === normText(f.text));
    if (same) alias.set(f.id, same.id);
    else facts.push(f);
  }
  const knowledge = [...bible.knowledge];
  const seen = new Set(knowledge.map((k) => `${k.who}\u0000${k.fact}\u0000${k.since_ep}`));
  for (const k of extracted.knowledge) {
    const fact = alias.get(k.fact) ?? k.fact;
    const key = `${k.who}\u0000${fact}\u0000${k.since_ep}`;
    if (seen.has(key) || !facts.some((x) => x.id === fact)) continue;
    seen.add(key);
    knowledge.push({ ...k, fact });
  }
  return Bible.parse({ ...bible, facts, knowledge });
}

const KNOWLEDGE_MODE_TEXT = { suspense: 'саспенс — зритель знает больше героя', mystery: 'тайна — никто не знает', surprise: 'сюрприз — персонаж в кадре знает больше' } as const;

/** Clues and guns the architect may plant or pay off in these episodes, with their episodes. */
function plantsText(bible: Bible, eps: number[]): string {
  const near = (a: number, b?: number | null) => eps.includes(a) || (b != null && eps.includes(b));
  const clues = bible.clues.filter((c) => near(c.planted_ep, c.paid_ep)).map((c) => `${c.id} (улика, заложена в ${c.planted_ep}-й${c.paid_ep ? `, окупается в ${c.paid_ep}-й` : ''}): ${c.text}`);
  const guns = bible.guns.filter((g) => near(g.planted_ep, g.fired_ep)).map((g) => `${g.id} (ружьё, показано в ${g.planted_ep}-й${g.fired_ep ? `, стреляет в ${g.fired_ep}-й` : ''}): ${g.object}`);
  const all = [...clues, ...guns];
  return all.length ? all.join('\n') : 'в библии нет улик и ружей на эти серии — оставь `plants` пустым';
}

/** Nested questions of the bible, for `closes_question` / `opens_question`. */
function nestedText(bible: Bible): string {
  if (!bible.nested.length) return 'в библии нет матрёшки вопросов — оставь `closes_question` и `opens_question` пустыми';
  return [...bible.nested]
    .sort((a, b) => a.level - b.level)
    .map((q) => `уровень ${q.level}: ${q.question} (открыт в ${q.opened_ep}-й${q.closed_ep ? `, закрыт в ${q.closed_ep}-й` : ''})`)
    .join('\n');
}

/** What this card's `plants` point at, with the text the writer must show. */
function cardPlantsText(bible: Bible, card: EpisodeCard): string {
  const items = (card.plants ?? []).map((id) => {
    const clue = bible.clues.find((c) => c.id === id);
    if (clue) return `${id} — улика: ${clue.text}${clue.visual ? ' (визуальная, без комментария)' : ''}`;
    const gun = bible.guns.find((g) => g.id === id);
    if (gun) return `${id} — ружьё: ${gun.object}${gun.metro_visible ? ' (читается без звука)' : ''}`;
    return `${id} — нет в библии: уточни у архитектора`;
  });
  return items.length ? items.join('\n') : 'ничего особого: карточка не называет улик и ружей';
}

/**
 * Continuity ledger: after a script is written, the helper extracts new facts, who learned what
 * and where the objects are now. Facts and knowledge merge into the fact base (deduplicated);
 * the lines go into the ledger the writer and the critics of the next episodes read.
 */
async function recordContinuity(ctx: Ctx, bible: Bible, script: Script): Promise<void> {
  let data: ContinuityExtract;
  try {
    ({ data } = await ctx.llm.completeJson(ContinuityExtract, {
      role: 'helper',
      projectId: ctx.projectId,
      step: 'scripts',
      request: {
        task: `continuity:${script.ep}`,
        system: renderPrompt(ctx.kb, 'helper/continuity', {
          ep: script.ep,
          script: renderScript(script),
          facts: bible.facts.map((x) => `${x.id}: ${x.text}`).join('\n') || 'фактов пока нет',
          schema: schemaText(ContinuityExtract),
        }),
        messages: [{ role: 'user', content: 'Выпиши ведомость. Только JSON.' }],
      },
    }));
  } catch (err) {
    // The ledger is a help, not a gate: a malformed helper answer leaves this episode's lines out.
    if (err instanceof InvalidOutputError) return;
    throw err;
  }
  ctx.memory.mergeFacts(data.facts, data.knowledge, { author: 'agent', note: `Ведомость непрерывности, ${script.ep}-я серия` });
  ctx.memory.recordContinuity(script.ep, ledgerLines(script.ep, data));
}

/** The plan is written in parts of at least this many episodes. */
export const MIN_PLAN_PART = 10;

/**
 * Parts of the season to write the plan in: the frame's blocks (or blocks of CARD_BLOCK when they
 * do not cover the season), with neighbouring blocks merged until a part holds at least
 * `minSize` episodes. A short tail joins the last part, so the last one may be longer.
 */
export function planParts(episodes: number, blocks: [number, number][], minSize = MIN_PLAN_PART): [number, number][] {
  const sorted = [...blocks].sort((a, b) => a[0] - b[0]);
  const covers = sorted.length > 0 && sorted[0]![0] === 1 && sorted.at(-1)![1] === episodes &&
    sorted.every(([a, b], i) => a <= b && (i === 0 || a === sorted[i - 1]![1] + 1));
  const base: [number, number][] = [];
  if (covers) base.push(...sorted);
  else for (let a = 1; a <= episodes; a += CARD_BLOCK) base.push([a, Math.min(a + CARD_BLOCK - 1, episodes)]);

  const out: [number, number][] = [];
  let start: number | undefined;
  for (const [a, b] of base) {
    start ??= a;
    if (b - start + 1 >= minSize) {
      out.push([start, b]);
      start = undefined;
    }
  }
  if (start !== undefined) {
    const end = base.at(-1)![1];
    const last = out.pop();
    out.push(last ? [last[0], end] : [start, end]);
  }
  return out;
}

function planPart(from: number, to: number, outline: OutlineSchema) {
  return z.object({
    episodes: outlinesFor(Array.from({ length: to - from + 1 }, (_, i) => from + i), outline),
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
  // The closing part of the review: tables from the bible, verdict and dangers from the critic.
  ctx.memory.saveArtifact(
    `review:${step}`,
    await runReviewSummary({ llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step }, { bible, plan, authorProvider: authors }, raised),
  );
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
