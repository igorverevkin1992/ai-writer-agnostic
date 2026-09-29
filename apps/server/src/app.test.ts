import { LlmClient, llmCalls, loadGolden, loadModelsConfig, openDb, type Db } from '@aiw/core';
import { FakeProvider, testConfig } from '@aiw/core/testing';
import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { buildApp } from './app.ts';

const kb = loadKb();
const golden = loadGolden();

const concepts = [0, 1, 2].map((i) => ({
  id: `c${i}`,
  title: `Концепция ${i}`,
  premise: 'Муж женился ради крови',
  hook: 'Свадьба',
  genre_formula: 'предательство → маска → месть',
  reference_cases: [],
}));
const logline = {
  heroine: 'Лиза',
  wound: 'Молчание семьи',
  betrayer: 'Герман',
  motive: 'Молодость',
  revenge_goal: 'Разоблачить семью',
  twist_secret: 'Свекровь не стареет',
  stakes: 'Жизнь',
  text_35w: 'Реставратор узнаёт, что муж женился на ней ради крови её рода, и мстит.',
  ad_15w: 'Он женился ради её крови.',
};
const hole = {
  holeType: 3,
  severity: 'blocker',
  quote: 'Лиза сама возвращается в семью, чтобы спасти сестру',
  viewerQuestion: 'Зритель спросит: почему не в полицию?',
  fixes: ['Показать, почему полиция не поможет'],
};

function reply(req: { task?: string }): string {
  const t = req.task ?? '';
  if (t === 'concept') return JSON.stringify(concepts);
  if (t === 'logline') return JSON.stringify(logline);
  if (t === 'bible') return JSON.stringify(golden.bible);
  if (t === 'season_plan') return JSON.stringify(golden.plan);
  if (t.startsWith('devil_advocate:3:план, серии 41–50')) return JSON.stringify([hole]);
  if (t.startsWith('devil_advocate:') || t.startsWith('persona:')) return '[]';
  if (t.startsWith('respond:')) return JSON.stringify({ action: 'cite', fact_id: 'f_dasha_target', explanation: 'Даша' });
  if (t.startsWith('judge:')) return JSON.stringify({ closed: false, reason: 'Не видно в кадре' });
  throw new Error(t);
}

let db: Db;
let app: ReturnType<typeof buildApp>;

beforeEach(() => {
  db = openDb(':memory:');
  const config = testConfig();
  const llm = new LlmClient({ config, db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) } });
  app = buildApp({ kb, db, config: loadModelsConfig(), llm });
});

const post = (url: string, payload?: object) => app.inject({ method: 'POST', url, payload: payload ?? {} });
const get = (url: string) => app.inject({ method: 'GET', url });

describe('server', () => {
  it('answers health check with available genres', async () => {
    expect((await get('/api/health')).json()).toMatchObject({ ok: true, genres: ['revenge_thriller'] });
  });

  it('lists genre packs for new projects', async () => {
    expect((await get('/api/genres')).json()).toEqual([
      expect.objectContaining({ id: 'revenge_thriller', episodes: 60, free: 8, villains: 5, rules: 18 }),
    ]);
  });

  it('runs steps 1–4 through the API, with findings and producer resolution', async () => {
    const created = await post('/api/projects', { title: 'Кровь', genreId: 'revenge_thriller', idea: 'Муж женился на мне ради крови' });
    expect(created.statusCode).toBe(201);
    const id = created.json().id as string;

    expect((await post(`/api/projects/${id}/steps/logline/run`)).json()).toEqual({ error: 'Сначала утвердите или пропустите шаг «Концепции»' });

    expect((await post(`/api/projects/${id}/steps/concept/run`)).json()).toMatchObject({ status: 'draft' });
    expect((await post(`/api/projects/${id}/steps/concept/approve`)).statusCode).toBe(400);
    expect((await post(`/api/projects/${id}/steps/concept/approve`, { choice: 0 })).json()).toMatchObject({ status: 'approved' });
    await post(`/api/projects/${id}/steps/logline/run`);
    await post(`/api/projects/${id}/steps/logline/approve`);
    await post(`/api/projects/${id}/steps/bible/run`);
    await post(`/api/projects/${id}/steps/bible/approve`);

    const plan = await post(`/api/projects/${id}/steps/season_plan/run`);
    expect(plan.json()).toMatchObject({ status: 'needs_fix' });

    const open = (await get(`/api/projects/${id}/findings?status=open&limit=3`)).json() as { id: string; episode: number }[];
    expect(open).toMatchObject([{ episode: 42, severity: 'blocker', verdict: 'Не видно в кадре' }]);

    const bad = await post(`/api/findings/${encodeURIComponent(open[0]!.id)}/resolve`, { projectId: id, factId: 'f_nope' });
    expect(bad.json().error).toMatch(/факта f_nope нет/);

    const ok = await post(`/api/findings/${encodeURIComponent(open[0]!.id)}/resolve`, {
      projectId: id,
      fact: { id: 'f_police', text: 'Следователь Панова ещё не верит Лизе без доказательств', since_ep: 20 },
      knowledge: [{ who: 'Лиза', fact: 'f_police', since_ep: 20 }],
    });
    expect(ok.json()).toMatchObject({ status: 'resolved', resolutionFactId: 'f_police' });

    const state = (await get(`/api/projects/${id}`)).json() as { steps: { step: string; status: string }[] };
    expect(state.steps.find((s) => s.step === 'season_plan')?.status).toBe('draft');
    expect((await post(`/api/projects/${id}/steps/season_plan/approve`)).json()).toMatchObject({ status: 'approved' });
  });

  it('reports errors clearly', async () => {
    expect((await post('/api/projects', { title: 'X', genreId: 'sitcom', idea: 'Y' })).json().error).toMatch(/Жанр «sitcom» не найден/);
    expect((await get('/api/projects/nope')).statusCode).toBe(404);
    expect((await post('/api/projects/nope/steps/concept/run')).statusCode).toBe(404);
  });

  it('reports project costs against the budget', async () => {
    db.insert(llmCalls)
      .values({ projectId: 'p1', step: 'bible', role: 'architect', provider: 'anthropic', model: 'm', costUsd: 1.5, durationMs: 1, status: 'ok' })
      .run();
    expect((await get('/api/projects/p1/costs')).json()).toMatchObject({ totalUsd: 1.5, limitUsd: 300, calls: 1 });
  });
});
