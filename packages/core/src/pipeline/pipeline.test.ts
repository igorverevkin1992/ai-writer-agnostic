import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { confirmChecklist } from '../demo.ts';
import { loadGolden } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { SameFamilyError } from '../providers/errors.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { parseModelsConfig } from '../providers/config.ts';
import { EpisodeCard } from '../schemas/episodeCard.ts';
import { sampleCard, sampleConcept, sampleLogline } from '../schemas/samples.ts';
import { dismissByProducer } from './findings.ts';
import { Pipeline, recoverStaleSteps } from './machine.ts';
import { nextTask } from './next.ts';
import { createProject } from './project.ts';
import { approveStep, runStep, skipStep } from './runners.ts';

const kb = loadKb();
const golden = loadGolden();

/** What the fake models answer, by task. */
interface Script {
  auditPlan?: (type: string, label: string) => unknown[];
  auditBible?: (type: string) => unknown[];
  respond?: unknown;
  judgeClosed?: boolean;
}

let db: Db;
let calls: string[];
let script: Script;

/** The golden plan's episodes of one part, e.g. "1-8". */
function planPart(range: string) {
  const [from = 1, to = Infinity] = range.split('-').map(Number);
  const inPart = (ep: number) => ep >= from && ep <= to;
  return { episodes: golden.plan.episodes.filter((e) => inPart(e.ep)), deviations: golden.plan.deviations.filter((d) => inPart(d.ep)) };
}

function reply(req: LlmRequest): string {
  const task = req.task ?? '';
  calls.push(task);
  const [kind, a = '', b = ''] = task.split(':');
  switch (kind) {
    case 'concept':
      return JSON.stringify([0, 1, 2].map((i) => ({ ...sampleConcept, id: `c${i}`, title: `Концепция ${i}` })));
    case 'logline':
      return JSON.stringify(sampleLogline);
    case 'bible':
      return JSON.stringify(golden.bible);
    case 'season_plan':
      return JSON.stringify(planPart(a));
    case 'checklist_judge':
      return JSON.stringify(confirmChecklist(req.system ?? ''));
    case 'devil_advocate':
      return JSON.stringify(b === 'библия' ? (script.auditBible?.(a) ?? []) : (script.auditPlan?.(a, b) ?? []));
    case 'persona':
      return '[]';
    case 'respond':
      return JSON.stringify(script.respond ?? { action: 'cite', fact_id: 'f_dasha_target', explanation: 'Бабушка в 10-й серии говорит, что Даша следующая' });
    case 'judge':
      return JSON.stringify({ closed: script.judgeClosed ?? true, reason: 'Ответ виден в кадре' });
    case 'review_summary':
      return JSON.stringify({ verdict: 'Сезон почти готов.', dangers: [{ where: 'Серия 1', why: 'Первая серия' }], legal: [] });
    default:
      throw new Error(`Неожиданная задача ${task}`);
  }
}

function llm(config = testConfig()) {
  return new LlmClient({
    config,
    db,
    env: {},
    providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) },
  });
}

const dasha = {
  level: 'critical',
  quote: 'Лиза сама возвращается в семью, чтобы спасти сестру',
  viewerQuestion: 'Зритель спросит: почему она не идёт в полицию?',
  whyNoticed: 'Полиция — первое, о чём подумает зритель',
  fixes: ['Показать, почему полиция не поможет', 'Дать Лизе план, ради которого она возвращается'],
  agentRule: 'Каждое «почему не в полицию» закрывать в кадре',
};
const invented = { ...dasha, quote: 'Лиза улетает в Париж', level: 'high' };

let projectId: string;
const deps = () => ({ db, llm: llm(), kb, projectId });

beforeEach(() => {
  db = openDb(':memory:');
  calls = [];
  script = {};
  projectId = createProject(db, kb, { title: 'Кровь', genreId: 'revenge_thriller', idea: 'Муж женился на мне ради крови' });
});

async function throughLogline() {
  await runStep(deps(), 'concept');
  approveStep(deps(), 'concept', { choice: 1 });
  await runStep(deps(), 'logline');
  approveStep(deps(), 'logline');
}

describe('pipeline gates', () => {
  it('does not run a step before the previous one is approved or skipped', async () => {
    await expect(runStep(deps(), 'logline')).rejects.toThrow('Сначала утвердите или пропустите шаг «Концепции»');
    skipStep(deps(), 'concept');
    await expect(runStep(deps(), 'logline')).rejects.toThrow('Нет результата шага: выбранная концепция');
  });

  it('the producer picks one of three concepts', async () => {
    const run = await runStep(deps(), 'concept');
    expect(run.status).toBe('draft');
    expect(() => approveStep(deps(), 'concept')).toThrow('Выберите одну из трёх концепций');
    approveStep(deps(), 'concept', { choice: 2 });
    expect(new ProjectMemory(db, projectId).latestArtifact('concept_choice')).toMatchObject({ id: 'c2' });
  });

  it('a failed run can be retried', async () => {
    const broken = new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', ['не json']) } });
    await expect(runStep({ ...deps(), llm: broken }, 'concept')).rejects.toThrow(/дважды/);
    expect(new Pipeline(db, projectId).get('concept').status).toBe('draft');
    await expect(runStep(deps(), 'concept')).resolves.toMatchObject({ status: 'draft' });
  });
});

describe('steps 1–4 end to end', () => {
  it('idea → concepts → logline → bible → season plan', async () => {
    await throughLogline();

    script.auditBible = (t) => (t === 'В' ? [{ ...dasha, quote: 'Кровь забирают под видом лечения' }] : []);
    const bible = await runStep(deps(), 'bible');
    expect(bible.status).toBe('draft');
    expect(bible.findings).toMatchObject([{ holeType: 1, category: ['В'], level: 'critical', status: 'resolved', resolutionFactId: 'f_dasha_target', controller: 'logic' }]);
    approveStep(deps(), 'bible');

    script.auditPlan = (t) => (t === 'А' ? [dasha, invented] : []);
    const plan = await runStep(deps(), 'season_plan');
    expect(plan.status).toBe('draft');
    expect(plan.checklist?.passed).toBe(true);
    expect(plan.findings).toMatchObject([{ holeType: 3, category: ['А'], episode: 42, status: 'resolved' }]);
    approveStep(deps(), 'season_plan');

    const states = new Pipeline(db, projectId).states().slice(0, 4).map((s) => s.status);
    expect(states).toEqual(['approved', 'approved', 'approved', 'approved']);
  });

  it('audits the whole plan once per review category А–П + 5 personas', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    calls = [];
    await runStep(deps(), 'season_plan');
    expect(calls.filter((c) => c.startsWith('devil_advocate:'))).toHaveLength(15);
    expect(calls.filter((c) => c.startsWith('persona:'))).toHaveLength(5);
    expect(calls).toContain('devil_advocate:Г:план сезона');
    // Then one closing call: verdict and dangers, saved with the code tables.
    expect(calls.filter((c) => c === 'review_summary')).toHaveLength(1);
    const review = new ProjectMemory(db, projectId).latestArtifact('review:season_plan') as { summary?: { verdict: string }; tables: { guns: unknown[] } };
    expect(review.summary?.verdict).toBe('Сезон почти готов.');
    expect(review.tables.guns.length).toBeGreaterThan(0);
  });

  it('an unresolved blocker keeps the step in needs_fix and blocks approval', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    script.auditPlan = (t) => (t === 'А' ? [dasha] : []);
    script.judgeClosed = false;
    const run = await runStep(deps(), 'season_plan');
    expect(run.status).toBe('needs_fix');
    expect(() => approveStep(deps(), 'season_plan')).toThrow('открытых блокирующих замечаний — 1');
    const stored = new ProjectMemory(db, projectId).findingsOf('season_plan', 'open');
    expect(stored).toMatchObject([{ holeType: 3, verdict: 'Ответ виден в кадре', severity: 'blocker', level: 'critical', category: ['А'] }]);
  });

  it('a new fact that breaks character knowledge is rejected without asking the judge', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    script.auditPlan = (t) => (t === 'А' ? [dasha] : []);
    script.respond = {
      action: 'new_fact',
      fact: { id: 'f_police', text: 'Полиция куплена семьёй', since_ep: 40 },
      knowledge: [{ who: 'Лиза', fact: 'f_police', since_ep: 20 }],
      explanation: 'Лиза знает, что полиция куплена',
    };
    calls = [];
    const run = await runStep(deps(), 'season_plan');
    expect(calls.filter((c) => c.startsWith('judge:'))).toHaveLength(0);
    expect(run.status).toBe('needs_fix');
    expect(new ProjectMemory(db, projectId).findingsOf('season_plan', 'open')[0]?.verdict).toMatch(/Новый факт ломает сюжет/);
  });

  it('refuses to audit when the critic is from the author family', async () => {
    const cfg = testConfig();
    const same = parseModelsConfig({ ...cfg, roles: { ...cfg.roles, critic_of_architect: { provider: 'anthropic', model: 'a-big' } } });
    await throughLogline();
    await expect(runStep({ ...deps(), llm: llm(same) }, 'bible')).rejects.toThrow(SameFamilyError);
  });

  it('re-running an earlier step sends later approvals back to draft', async () => {
    await throughLogline();
    await runStep(deps(), 'logline');
    expect(new Pipeline(db, projectId).get('logline').status).toBe('draft');
    await runStep(deps(), 'concept');
    expect(new Pipeline(db, projectId).get('logline').status).toBe('draft');
  });

  it('drops auditor findings whose quote is not in the text', async () => {
    await throughLogline();
    script.auditBible = () => [invented];
    const run = await runStep(deps(), 'bible');
    expect(run.findings).toEqual([]);
  });

  it('puts the knowledge base into the cacheable prefix', async () => {
    const anthropic = new FakeProvider('anthropic', [reply]);
    const client = new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic, google: new FakeProvider('google', [reply]) } });
    await runStep({ ...deps(), llm: client }, 'concept');
    expect(anthropic.calls[0]?.req.cacheablePrefix?.[0]).toMatch(/^# База знаний: Женский психологический триллер/);
    expect(anthropic.calls[0]?.req.cacheablePrefix?.[0]).toContain('R08');
  });
});

describe('review fixes: pipeline', () => {
  async function throughBible() {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
  }

  it('writes the season plan in the frame blocks', async () => {
    await throughBible();
    calls = [];
    await runStep(deps(), 'season_plan');
    expect(calls.filter((c) => c.startsWith('season_plan:'))).toEqual([
      'season_plan:1-8', 'season_plan:9-20', 'season_plan:21-30', 'season_plan:31-40', 'season_plan:41-50', 'season_plan:51-60',
    ]);
    expect(new ProjectMemory(db, projectId).currentPlan()?.episodes).toHaveLength(60);
  });

  it('a run that fails after saving a new result leaves a blocker and asks to run again', async () => {
    await throughBible();
    script.auditPlan = (t) => {
      if (t === 'Г') throw new Error('сеть упала');
      return [];
    };
    await expect(runStep(deps(), 'season_plan')).rejects.toThrow();
    const pipeline = new Pipeline(db, projectId);
    expect(pipeline.get('season_plan').status).toBe('needs_fix');
    expect(() => approveStep(deps(), 'season_plan')).toThrow('открытых блокирующих замечаний — 1');
    expect(nextTask(db, kb, projectId)).toMatchObject({ step: 'season_plan', action: 'run' });

    script.auditPlan = () => [];
    const again = await runStep(deps(), 'season_plan');
    expect(again.status).toBe('draft');
    expect(pipeline.hasIncomplete('season_plan')).toBe(false);
  });

  it('a run that fails before changing anything restores the previous status', async () => {
    await throughLogline();
    const broken = new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', ['не json']) } });
    skipStep(deps(), 'bible');
    await expect(runStep({ ...deps(), llm: broken }, 'logline')).rejects.toThrow(/дважды/);
    const pipeline = new Pipeline(db, projectId);
    expect(pipeline.get('logline').status).toBe('approved');
    expect(pipeline.get('bible').status).toBe('skipped');
  });

  it('one step at a time: no run or skip while the agent is working', async () => {
    await runStep(deps(), 'concept');
    approveStep(deps(), 'concept', { choice: 0 });
    const pipeline = new Pipeline(db, projectId);
    pipeline.begin('logline');
    await expect(runStep(deps(), 'logline')).rejects.toThrow('уже выполняется');
    expect(() => skipStep(deps(), 'logline')).toThrow('ещё выполняется');
    expect(() => pipeline.begin('concept')).toThrow('Агент ещё работает над шагом «Логлайн»');
  });

  it('steps left running by a restart are recovered', async () => {
    await runStep(deps(), 'concept');
    approveStep(deps(), 'concept', { choice: 0 });
    const pipeline = new Pipeline(db, projectId);
    pipeline.begin('logline');
    expect(recoverStaleSteps(db)).toBe(1);
    expect(pipeline.get('logline').status).toBe('draft');

    await runStep(deps(), 'logline');
    pipeline.begin('logline');
    new ProjectMemory(db, projectId).saveArtifact('logline', sampleLogline);
    recoverStaleSteps(db);
    expect(pipeline.get('logline').status).toBe('needs_fix');
    expect(pipeline.hasIncomplete('logline')).toBe(true);
  });

  it('a step is approved only after the previous one', async () => {
    await throughLogline();
    await runStep(deps(), 'concept');
    expect(() => approveStep(deps(), 'logline')).toThrow('Сначала утвердите или пропустите шаг «Концепции»');
  });

  it('findings of the old version that the new run did not raise are closed', async () => {
    await throughBible();
    script.auditPlan = (t) => (t === 'А' ? [dasha] : []);
    script.judgeClosed = false;
    expect((await runStep(deps(), 'season_plan')).status).toBe('needs_fix');
    script.auditPlan = () => [];
    expect((await runStep(deps(), 'season_plan')).status).toBe('draft');
    const memory = new ProjectMemory(db, projectId);
    expect(memory.findingsOf('season_plan', 'open')).toEqual([]);
    expect(memory.findingsOf('season_plan', 'resolved')[0]?.verdict).toBe('Заменено новой версией');
  });

  it('a closed finding cannot be dismissed', async () => {
    await throughBible();
    script.auditPlan = (t) => (t === 'А' ? [dasha] : []);
    await runStep(deps(), 'season_plan');
    const done = new ProjectMemory(db, projectId).findingsOf('season_plan', 'resolved')[0]!;
    expect(() => dismissByProducer(db, projectId, done.id, 'f_dasha_target')).toThrow('Замечание уже закрыто');
  });

  it('a new season plan outdates the old cards and their block approvals', async () => {
    await throughBible();
    await runStep(deps(), 'season_plan');
    const memory = new ProjectMemory(db, projectId);
    memory.saveCard(EpisodeCard.parse({ ...sampleCard, ep: 1 }));
    memory.saveArtifact('card_blocks', { approved: [1] });
    await runStep(deps(), 'season_plan');
    expect(memory.staleEpisodes().cards).toEqual([1]);
    expect(memory.latestArtifact('card_blocks')).toEqual({ approved: [] });
  });

  it('choosing another concept sends later approvals back to draft', async () => {
    await throughLogline();
    approveStep(deps(), 'concept', { choice: 2 });
    expect(new Pipeline(db, projectId).get('logline').status).toBe('draft');
  });

  it('a romantasy project goes through steps 1–4 with its own frame and checks', async () => {
    projectId = createProject(db, kb, { title: 'Наследница', genreId: 'romantasy_revenge', idea: 'Служанка в родовом отеле — тайная наследница' });
    await throughLogline();
    // The thriller bible breaks romantasy rules (the betrayer must be the right hand, 1–3 world rules).
    const bible = await runStep(deps(), 'bible');
    expect(bible.findings.map((f) => f.check)).toEqual(expect.arrayContaining(['betrayer_rank.rank', 'world_rules.count']));
    skipStep(deps(), 'bible');
    calls = [];
    const plan = await runStep(deps(), 'season_plan');
    // The fake answers with the thriller plan: the romantasy frame finds its love and power anchors missing.
    const open = new ProjectMemory(db, projectId).findingsOf('season_plan', 'open').map((f) => f.check);
    expect(open).toContain('season_frame.anchor.love_near_kiss');
    expect(plan.status).toBe('needs_fix');
    expect(calls.filter((c) => c.startsWith('persona:'))).toEqual([
      'persona:romantasy_fan', 'persona:skeptic', 'persona:slavic_mystic', 'persona:lawyer', 'persona:teen_mom',
    ]);
  });
});

