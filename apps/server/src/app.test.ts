import { LlmClient, confirmChecklist, llmCalls, projects, loadGolden, loadModelsConfig, openDb, type Db } from '@aiw/core';
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
  level: 'critical',
  quote: 'Лиза сама возвращается в семью, чтобы спасти сестру',
  viewerQuestion: 'Зритель спросит: почему не в полицию?',
  whyNoticed: 'Полиция — первое, о чём подумает зритель',
  fixes: ['Показать, почему полиция не поможет'],
  agentRule: 'Каждое «почему не в полицию» закрывать в кадре',
};

function reply(req: { task?: string; system?: string }): string {
  const t = req.task ?? '';
  if (t === 'concept') return JSON.stringify(concepts);
  if (t === 'logline') return JSON.stringify(logline);
  if (t === 'bible') return JSON.stringify(golden.bible);
  if (t.startsWith('season_plan:')) {
    const [from = 1, to = Infinity] = t.slice('season_plan:'.length).split('-').map(Number);
    const inPart = (ep: number) => ep >= from && ep <= to;
    return JSON.stringify({ episodes: golden.plan.episodes.filter((e) => inPart(e.ep)), deviations: golden.plan.deviations.filter((d) => inPart(d.ep)) });
  }
  if (t === 'checklist_judge') return JSON.stringify(confirmChecklist(req.system ?? ''));
  if (t.startsWith('devil_advocate:А:план сезона')) return JSON.stringify([hole]);
  if (t.startsWith('devil_advocate:') || t.startsWith('persona:')) return '[]';
  if (t.startsWith('respond:')) return JSON.stringify({ action: 'cite', fact_id: 'f_dasha_target', explanation: 'Даша' });
  if (t === 'review_summary') return JSON.stringify({ verdict: 'Сезон почти готов.', dangers: [{ where: 'Серия 1', why: 'Первая серия' }], legal: [] });
  if (t.startsWith('judge:')) return JSON.stringify({ closed: false, reason: 'Не видно в кадре' });
  throw new Error(t);
}

let db: Db;
let app: ReturnType<typeof buildApp>;

beforeEach(() => {
  db = openDb(':memory:');
  const config = testConfig();
  const llm = new LlmClient({ config, db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) } });
  app = buildApp({ kb, db, config: loadModelsConfig(), llm, runsInBackground: false });
});

const post = (url: string, payload?: object) => app.inject({ method: 'POST', url, payload: payload ?? {} });
const get = (url: string) => app.inject({ method: 'GET', url });

describe('server', () => {
  it('answers health check with available genres', async () => {
    expect((await get('/api/health')).json()).toMatchObject({ ok: true, genres: ['revenge_thriller', 'romantasy_revenge'] });
  });

  it('lists genre packs for new projects', async () => {
    expect((await get('/api/genres')).json()).toEqual([
      expect.objectContaining({ id: 'revenge_thriller', episodes: 60, free: 8, villains: 5, rules: 18 }),
      expect.objectContaining({ id: 'romantasy_revenge', title: 'Женское ромэнтэзи с реваншем и наказанием подлеца', episodes: 60, villains: 5, rules: 18 }),
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
    expect((open[0] as unknown as { categoryNames: string[] }).categoryNames[0]).toMatch(/^А — \S/u);

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

  it('serves cards and readable scripts, and polishes a fragment', async () => {
    const id = (await post('/api/projects', { title: 'Кровь', genreId: 'revenge_thriller', idea: 'идея' })).json().id as string;
    expect((await post(`/api/projects/${id}/steps/episode_cards/run`, { episodes: [1] })).json().error).toMatch(/Сначала утвердите/);
    expect((await get(`/api/projects/${id}/scripts/1`)).statusCode).toBe(404);
    expect((await post(`/api/projects/${id}/polish`, { ep: 1, from: 0, to: 0 })).json().error).toMatch(/Сначала утвердите/);
    expect((await get(`/api/projects/${id}/cards`)).json()).toEqual([]);
  });

  it('gives an overview with the one task and exports files', async () => {
    const id = (await post('/api/projects', { title: 'Кровь', genreId: 'revenge_thriller', idea: 'идея' })).json().id as string;
    const ov = (await get(`/api/projects/${id}/overview`)).json();
    expect(ov.next).toMatchObject({ step: 'concept', action: 'run' });
    expect(ov.genre).toMatchObject({ episodes: 60, anchorLabels: { paywall_hook: 'Точка оплаты' } });

    const docx = await get(`/api/projects/${id}/export?format=docx`);
    expect(docx.headers['content-type']).toContain('wordprocessingml');
    expect(docx.headers['content-disposition']).toBe('attachment; filename="krov.docx"');
    expect((await get(`/api/projects/${id}/export?format=pdf`)).statusCode).toBe(400);
    // Earlier steps are not done: the file is given, the export step stays open.
    expect((await get(`/api/projects/${id}/overview`)).json().steps.at(-1).status).toBe('draft');

    for (const step of ['concept', 'logline', 'bible', 'season_plan', 'episode_cards', 'scripts', 'polish']) {
      await post(`/api/projects/${id}/steps/${step}/skip`);
    }
    await get(`/api/projects/${id}/export?format=xlsx`);
    const done = (await get(`/api/projects/${id}/overview`)).json();
    expect(done.steps.at(-1).status).toBe('approved');
    expect(done.next.task).toBe('Всё готово. Скачайте файлы');
    expect((await get('/api/projects')).json().map((p: { id: string }) => p.id)).toContain(id);
  });

  it('reports errors clearly', async () => {
    expect((await post('/api/projects', { title: 'X', genreId: 'sitcom', idea: 'Y' })).json().error).toMatch(/Жанр «sitcom» не найден/);
    expect((await get('/api/projects/nope')).statusCode).toBe(404);
    expect((await post('/api/projects/nope/steps/concept/run')).statusCode).toBe(404);
  });

  it('reports project costs against the budget', async () => {
    db.insert(projects).values({ id: 'p1', title: 'Т' }).run();
    db.insert(llmCalls)
      .values({ projectId: 'p1', step: 'bible', role: 'architect', provider: 'anthropic', model: 'm', costUsd: 1.5, durationMs: 1, status: 'ok' })
      .run();
    expect((await get('/api/projects/p1/costs')).json()).toMatchObject({ totalUsd: 1.5, limitUsd: 300, calls: 1 });
  });

  it('runs a step in the background and shows it in the overview', async () => {
    const bg = buildApp({ kb, db, config: loadModelsConfig(), llm: new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) } }) });
    const id = (await bg.inject({ method: 'POST', url: '/api/projects', payload: { title: 'Т', genreId: 'revenge_thriller', idea: 'И' } })).json().id as string;
    const started = await bg.inject({ method: 'POST', url: `/api/projects/${id}/steps/concept/run`, payload: {} });
    expect(started.statusCode).toBe(202);
    // What cannot start is refused at once, not in the background.
    const early = await bg.inject({ method: 'POST', url: `/api/projects/${id}/steps/logline/run`, payload: {} });
    expect(early.json().error).toMatch(/Сначала утвердите/);
    await new Promise((ok) => setTimeout(ok, 50));
    const overview = (await bg.inject({ method: 'GET', url: `/api/projects/${id}/overview` })).json();
    expect(overview.job).toMatchObject({ step: 'concept', running: false });
    expect(overview.steps[0]).toMatchObject({ step: 'concept', status: 'draft', version: 1 });
  });

  it('rejects malformed input with a readable 400', async () => {
    const id = (await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И' })).json().id as string;
    const bad = await post(`/api/projects/${id}/steps/episode_cards/run`, { episodes: ['один'] });
    expect(bad.statusCode).toBe(400);
    expect(bad.json().error).toMatch(/Неверные данные: episodes/);
    expect((await post(`/api/projects/${id}/polish/choose`, { variant: 'x' })).statusCode).toBe(400);
    expect((await post('/api/projects', { title: 'Т' })).statusCode).toBe(400);
  });

  it('answers only to this computer', async () => {
    expect((await app.inject({ method: 'GET', url: '/api/health', headers: { host: 'evil.example' } })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: '/api/health', headers: { origin: 'https://evil.example' } })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: '/api/health', headers: { origin: 'http://localhost:5173' } })).statusCode).toBe(200);
  });

  it('costs of an unknown project are a 404', async () => {
    expect((await get('/api/projects/nope/costs')).statusCode).toBe(404);
  });
});
