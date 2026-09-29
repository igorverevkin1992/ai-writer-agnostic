import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { loadGolden } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { SameFamilyError } from '../providers/errors.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { parseModelsConfig } from '../providers/config.ts';
import { sampleConcept, sampleLogline } from '../schemas/samples.ts';
import { Pipeline } from './machine.ts';
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
      return JSON.stringify(golden.plan);
    case 'devil_advocate':
      return JSON.stringify(b === 'библия' ? (script.auditBible?.(a) ?? []) : (script.auditPlan?.(a, b) ?? []));
    case 'persona':
      return '[]';
    case 'respond':
      return JSON.stringify(script.respond ?? { action: 'cite', fact_id: 'f_dasha_target', explanation: 'Бабушка в 10-й серии говорит, что Даша следующая' });
    case 'judge':
      return JSON.stringify({ closed: script.judgeClosed ?? true, reason: 'Ответ виден в кадре' });
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
  holeType: 3,
  severity: 'blocker',
  quote: 'Лиза сама возвращается в семью, чтобы спасти сестру',
  viewerQuestion: 'Зритель спросит: почему она не идёт в полицию?',
  fixes: ['Показать, почему полиция не поможет'],
};
const invented = { ...dasha, quote: 'Лиза улетает в Париж', severity: 'major' };

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

    script.auditBible = (t) => (t === '5' ? [{ ...dasha, holeType: 5, quote: 'Кровь забирают под видом лечения' }] : []);
    const bible = await runStep(deps(), 'bible');
    expect(bible.status).toBe('draft');
    expect(bible.findings).toMatchObject([{ holeType: 5, status: 'resolved', resolutionFactId: 'f_dasha_target', controller: 'logic' }]);
    approveStep(deps(), 'bible');

    script.auditPlan = (t, label) => (t === '3' && label.includes('41–50') ? [dasha, invented] : []);
    const plan = await runStep(deps(), 'season_plan');
    expect(plan.status).toBe('draft');
    expect(plan.checklist?.passed).toBe(true);
    expect(plan.findings).toMatchObject([{ holeType: 3, episode: 42, status: 'resolved' }]);
    approveStep(deps(), 'season_plan');

    const states = new Pipeline(db, projectId).states().slice(0, 4).map((s) => s.status);
    expect(states).toEqual(['approved', 'approved', 'approved', 'approved']);
  });

  it('audits the plan: 11 hole types × 6 blocks of 10 episodes + 5 personas', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    calls = [];
    await runStep(deps(), 'season_plan');
    expect(calls.filter((c) => c.startsWith('devil_advocate:'))).toHaveLength(66);
    expect(calls.filter((c) => c.startsWith('persona:'))).toHaveLength(5);
    expect(calls).toContain('devil_advocate:7:план, серии 51–60');
  });

  it('an unresolved blocker keeps the step in needs_fix and blocks approval', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    script.auditPlan = (t, label) => (t === '3' && label.includes('41–50') ? [dasha] : []);
    script.judgeClosed = false;
    const run = await runStep(deps(), 'season_plan');
    expect(run.status).toBe('needs_fix');
    expect(() => approveStep(deps(), 'season_plan')).toThrow('открытых блокирующих замечаний — 1');
    const stored = new ProjectMemory(db, projectId).findingsOf('season_plan', 'open');
    expect(stored).toMatchObject([{ holeType: 3, verdict: 'Ответ виден в кадре', severity: 'blocker' }]);
  });

  it('a new fact that breaks character knowledge is rejected without asking the judge', async () => {
    await throughLogline();
    await runStep(deps(), 'bible');
    approveStep(deps(), 'bible');
    script.auditPlan = (t, label) => (t === '3' && label.includes('41–50') ? [dasha] : []);
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
