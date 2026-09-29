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
import { scoreChecklist, type ChecklistScore } from '../checks/code/checklist.ts';
import { runDevilAdvocate } from '../checks/llm/devilAdvocate.ts';
import { resolveFinding } from '../checks/llm/resolve.ts';
import { ExtractedFacts } from '../checks/llm/schemas.ts';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../prompts/render.ts';
import type { RoleName } from '../providers/config.ts';
import type { LlmClient } from '../providers/llm.ts';
import { Bible } from '../schemas/bible.ts';
import { Concept, ConceptSet } from '../schemas/concept.ts';
import type { Finding } from '../schemas/finding.ts';
import { Logline } from '../schemas/logline.ts';
import { SeasonPlan } from '../schemas/season.ts';
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
  ctx.pipeline.begin(step);
  try {
    const run = await RUNNERS[step]!(ctx, opts);
    return { ...run, step, status: ctx.pipeline.finish(step) };
  } catch (err) {
    ctx.pipeline.abort(step);
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
    const plan = need(memory.currentPlan(), 'план сезона');
    const starts = cardBlocks(plan.episodes.map((e) => e.ep)).map((b) => b[0]!);
    if (!starts.includes(opts.block)) throw new PipelineError(`Нет блока карточек, начинающегося с ${opts.block}-й серии`);
    const eps = cardBlocks(plan.episodes.map((e) => e.ep)).find((b) => b[0] === opts.block)!;
    const cards = new Set(memory.cards().map((c) => c.ep));
    if (eps.some((ep) => !cards.has(ep))) throw new PipelineError(`В блоке ${eps[0]}–${eps.at(-1)} есть серии без карточек`);
    const blockers = memory.findingsOf('episode_cards', 'open').filter((f) => f.severity === 'blocker' && f.episode !== null && eps.includes(f.episode));
    if (blockers.length) throw new PipelineError(`Нельзя утвердить серии ${eps[0]}–${eps.at(-1)}: открытых блокирующих замечаний — ${blockers.length}`);
    const approved = new Set(((memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []));
    approved.add(opts.block);
    memory.saveArtifact('card_blocks', { approved: [...approved].sort((a, b) => a - b) });
    if (starts.every((b) => approved.has(b))) pipeline.approve(step);
    return;
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
    let { data: bible } = await ctx.llm.completeJson(Bible, {
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
    return audit(ctx, 'bible', 'architect');
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
    }
    ctx.memory.saveArtifact('card_blocks', { approved: [...approved] });

    const cards = ctx.memory.cards();
    ctx.memory.saveArtifact('episode_cards', { episodes: cards.map((c) => c.ep) });
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
    const writer = ctx.llm.resolve('writer').provider;
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
      const { data } = await ctx.llm.completeJson(Script, {
        role: 'writer',
        projectId: ctx.projectId,
        step: 'scripts',
        request: build(),
        shrink: () => {
          if (level >= MAX_COMPRESSION) return build();
          level = (level + 1) as CompressionLevel;
          return build();
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
      all.push(...found);
    }
    ctx.memory.saveArtifact('scripts', { episodes: ctx.memory.scripts().map((s) => s.ep) });
    return { findings: all };
  },

  async season_plan(ctx) {
    const bible = need(ctx.memory.currentBible(), 'библия');
    const f = ctx.kit.frame;
    const { data: plan } = await ctx.llm.completeJson(SeasonPlan, {
      role: 'architect_heavy',
      projectId: ctx.projectId,
      step: 'season_plan',
      request: {
        task: 'season_plan',
        cacheablePrefix: [ctx.kbText, `# Библия сериала\n${json(bible)}`],
        system: renderPrompt(ctx.kb, 'architect_heavy/season_plan', {
          episodes: f.episodes,
          anchor_ids: Object.keys(f.anchors).join(', '),
          tolerance: f.tolerance,
          schema: schemaText(SeasonPlan),
        }),
        messages: [{ role: 'user', content: 'Составь план сезона. Только JSON.' }],
      },
    });
    ctx.memory.importPlan(plan);
    return audit(ctx, 'season_plan', 'architect_heavy');
  },
};

/**
 * Code checks first, then the devil's advocate from another family; blocker and major
 * findings go through author reply and judge. Everything lands in the findings table.
 */
async function audit(ctx: Ctx, step: 'bible' | 'season_plan', authorRole: RoleName): Promise<Omit<StepRun, 'step' | 'status'>> {
  let bible = need(ctx.memory.currentBible(), 'библия');
  const plan = step === 'season_plan' ? ctx.memory.currentPlan() : undefined;
  const authorProvider = ctx.llm.resolve(authorRole).provider;

  const code = runCodeChecks({ kit: ctx.kit, bible, plan });
  const findings: Finding[] = [...code.findings];
  let checklist: ChecklistScore | undefined;
  if (plan) {
    checklist = scoreChecklist(ctx.kit.checklist, ctx.kit.rules, code);
    if (checklist.finding) findings.push(checklist.finding);
  }

  const auditRes = await runDevilAdvocate(
    { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step },
    { bible, plan, authorProvider },
  );
  ctx.memory.saveFindings([...findings, ...auditRes.findings], step);

  const outcome: Finding[] = [...findings];
  for (const f of auditRes.findings) {
    if (ctx.autoResolve === false || f.severity === 'minor') {
      outcome.push(f);
      continue;
    }
    const res = await resolveFinding(
      { llm: ctx.llm, kb: ctx.kb, kit: ctx.kit, projectId: ctx.projectId, step, authorRole: 'architect', authorProvider, judgeRole: 'critic_of_architect' },
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
