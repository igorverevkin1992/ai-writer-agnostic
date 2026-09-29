import { genreKit, type GenreKit, type Kb } from '@aiw/kb';
import { runCodeChecks } from '../checks/code/runner.ts';
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

/** Steps that exist in this milestone. */
export const RUNNABLE_STEPS: StepId[] = ['concept', 'logline', 'bible', 'season_plan'];

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
export async function runStep(deps: StepDeps, step: StepId): Promise<StepRun> {
  if (!RUNNABLE_STEPS.includes(step)) throw new PipelineError(`Шаг ${step} появится на следующих вехах`);
  const ctx = context(deps);
  ctx.pipeline.begin(step);
  try {
    const run = await RUNNERS[step]!(ctx);
    return { ...run, step, status: ctx.pipeline.finish(step) };
  } catch (err) {
    ctx.pipeline.abort(step);
    throw err;
  }
}

/** Approves a step. The concept step needs the producer's choice of one of three concepts. */
export function approveStep(deps: Omit<StepDeps, 'llm'>, step: StepId, opts: { choice?: number } = {}): void {
  const pipeline = new Pipeline(deps.db, deps.projectId);
  const memory = new ProjectMemory(deps.db, deps.projectId);
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

type Runner = (ctx: Ctx) => Promise<Omit<StepRun, 'step' | 'status'>>;

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
