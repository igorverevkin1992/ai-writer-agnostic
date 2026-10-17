import { EpisodeCard, LlmClient, ProjectMemory, Script, confirmChecklist, llmCalls, projects, revisions, loadGolden, loadModelsConfig, openDb, type Db, type LlmRequest } from '@aiw/core';
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
    expect((await get('/api/health')).json()).toMatchObject({ ok: true, genres: ['revenge_thriller', 'romantasy_revenge', 'slavic_myth', 'vertical_thriller'] });
  });

  it('lists genre packs for new projects', async () => {
    expect((await get('/api/genres')).json()).toEqual([
      expect.objectContaining({ id: 'revenge_thriller', episodes: 60, free: 8, villains: 5, rules: 22 }),
      expect.objectContaining({ id: 'romantasy_revenge', title: 'Женское ромэнтэзи с реваншем и наказанием подлеца', episodes: 60, villains: 5, rules: 22 }),
      expect.objectContaining({ id: 'slavic_myth', title: 'Тёмное славянское фэнтези-миф', episodes: 60, free: 8, villains: 5, rules: 21 }),
      expect.objectContaining({ id: 'vertical_thriller', title: 'Вертикальный триллер, саспенс и хоррор', episodes: 60, free: 10, villains: 0, rules: 26 }),
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
    expect((await get('/api/projects/p1/costs')).json()).toMatchObject({ totalUsd: 1.5, limitUsd: 50, calls: 1 });
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

// ---- Wave 3: the producer's app ----

const card = (ep: number) => ({
  ep,
  duration_s: 90,
  hook_0_5s: 'Рука Лизы сжимает ключ',
  event: 'Лиза открывает гримёрку свекрови',
  twist: 'Внутри её детские фото',
  emotions: ['страх', 'надежда'],
  heroine_action: 'Открывает гримёрку чужим ключом',
  punchline: 'Он знал. С самого начала.',
  cliffhanger: 'Свекровь в дверях с тем же ключом',
  cast: ['Лиза', 'Вера'],
  location: 'Театр: гримёрка',
  sound: 'Шаги за дверью',
  metro_frame: { face: 'Лиза', action: 'сжимает', object: 'ключ' },
  knowledge: { viewer: 'Свекровь следит', heroine: 'Ключ подходит', villain: 'Лиза ищет правду' },
});
const script = (ep: number) => ({
  ep,
  title: `Серия ${ep}`,
  duration_s: 90,
  blocks: [
    { t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. АРХИВ — НОЧЬ. ЛИЗА держит фото.' },
    { t0: 5, t1: 40, kind: 'line', speaker: 'ВЕРА', text: 'Положи на место.' },
    { t0: 40, t1: 45, kind: 'scene', text: 'ИНТ. ФОЙЕ — НОЧЬ. Незнакомец в тени.' },
    { t0: 45, t1: 90, kind: 'line', speaker: 'ЛИЗА', text: 'Кто вы?' },
  ],
});

/** A project with the golden bible and plan in memory and steps 1–4 skipped: ready for cards and scripts. */
async function readyForCards(a: ReturnType<typeof buildApp>, genreId = 'revenge_thriller') {
  const id = (await a.inject({ method: 'POST', url: '/api/projects', payload: { title: 'Кровь', genreId, idea: 'идея' } })).json().id as string;
  const m = new ProjectMemory(db, id);
  m.importBible(golden.bible);
  m.importPlan(golden.plan);
  for (const step of ['concept', 'logline', 'bible', 'season_plan']) await a.inject({ method: 'POST', url: `/api/projects/${id}/steps/${step}/skip`, payload: {} });
  return { id, memory: m };
}

const patch = (url: string, payload: object) => app.inject({ method: 'PATCH', url, payload });
const put = (url: string, payload: object) => app.inject({ method: 'PUT', url, payload });

describe('budget, rename, delete', () => {
  it('a project gets its own budget limit at creation and the producer can raise it', async () => {
    const id = (await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И', budgetLimitUsd: 120 })).json().id as string;
    expect((await get(`/api/projects/${id}/costs`)).json().limitUsd).toBe(120);
    const raised = await patch(`/api/projects/${id}`, { budgetLimitUsd: 250 });
    expect(raised.statusCode).toBe(200);
    expect(raised.json().budget.limitUsd).toBe(250);
    expect((await get(`/api/projects/${id}/overview`)).json().budget.limitUsd).toBe(250);
    // Without its own limit a project takes the one from config/models.yaml.
    const plain = (await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И' })).json().id as string;
    expect((await get(`/api/projects/${plain}/costs`)).json().limitUsd).toBe(50);
    expect((await get('/api/genres')).json()[0].budgetLimitUsd).toBe(50);
  });

  it('refuses a budget that is not a positive number, in Russian', async () => {
    const id = (await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И' })).json().id as string;
    const zero = await patch(`/api/projects/${id}`, { budgetLimitUsd: 0 });
    expect(zero.statusCode).toBe(400);
    expect(zero.json().error).toMatch(/^Неверные данные: budgetLimitUsd — /u);
    const text = await patch(`/api/projects/${id}`, { budgetLimitUsd: 'сто' });
    expect(text.json().error).toBe('Неверные данные: budgetLimitUsd — ожидается число');
    expect((await post(`/api/projects/${id}/steps/episode_cards/run`, { episodes: ['один'] })).json().error).toBe('Неверные данные: episodes.0 — ожидается число');
    expect((await post('/api/projects', { title: 'Т' })).json().error).toBe('Неверные данные: genreId — ожидается текст, поле не заполнено; idea — ожидается текст, поле не заполнено');
  });

  it('renames and deletes a project, with everything it had', async () => {
    const { id } = await readyForCards(app);
    expect((await patch(`/api/projects/${id}`, { title: 'Новое имя' })).json().project.title).toBe('Новое имя');
    expect((await patch(`/api/projects/${id}`, { title: '  ' })).json().error).toBe('Название не может быть пустым');
    expect((await app.inject({ method: 'DELETE', url: `/api/projects/${id}` })).statusCode).toBe(204);
    expect((await get(`/api/projects/${id}`)).statusCode).toBe(404);
    expect((await get('/api/projects')).json().map((p: { id: string }) => p.id)).not.toContain(id);
    expect(db.select().from(projects).all().filter((p) => p.id === id)).toEqual([]);
    expect((await app.inject({ method: 'DELETE', url: `/api/projects/${id}` })).statusCode).toBe(404);
  });
});

describe('frame inputs and hero terms per project', () => {
  it('stores the paywall of a thriller project and resolves the frame with it', async () => {
    const created = await post('/api/projects', {
      title: 'Прима',
      genreId: 'vertical_thriller',
      idea: 'Балерина',
      frameInputs: { paywall: 12 },
      terms: { hero: { nom: 'героиня', gen: 'героини', dat: 'героине' }, wound: 'Травма' },
    });
    expect(created.statusCode).toBe(201);
    const id = created.json().id as string;
    const ov = (await get(`/api/projects/${id}/overview`)).json();
    expect(ov.project.frameInputs).toEqual({ paywall: 12 });
    expect(ov.genre.inputs).toEqual({ paywall: 12 });
    expect(ov.genre.free).toBe(12);
    expect(ov.genre.anchors.paywall_hook).toBe(12);
    expect(ov.genre.anchors.paywall_answer).toEqual([13, 14]);
    expect(ov.genre.terms.wound).toBe('Травма');
    // The genre pack itself keeps its defaults.
    expect((await get('/api/genres')).json().find((g: { id: string }) => g.id === 'vertical_thriller')).toMatchObject({ inputs: { paywall: 10 }, free: 10 });
    // Changed while there is no season plan yet; a bad value is refused with a readable message.
    expect((await patch(`/api/projects/${id}`, { frameInputs: { paywall: 9 } })).json().project.frameInputs).toEqual({ paywall: 9 });
    expect((await get(`/api/projects/${id}/overview`)).json().genre.anchors.paywall_hook).toBe(9);
    expect((await patch(`/api/projects/${id}`, { frameInputs: { paywall: 70 } })).json().error).toMatch(/не сходится/u);
    expect((await patch(`/api/projects/${id}`, { frameInputs: { midpoint: 30 } })).json().error).toMatch(/не знает параметра «midpoint»/u);
  });

  it('refuses inputs the frame does not have at creation', async () => {
    const bad = await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И', frameInputs: { paywall: 9 } });
    expect(bad.statusCode).toBe(400);
    expect(bad.json().error).toMatch(/нет параметров/u);
  });
});

describe('stopping a step', () => {
  it('refuses to stop what is not running', async () => {
    const id = (await post('/api/projects', { title: 'Т', genreId: 'revenge_thriller', idea: 'И' })).json().id as string;
    const res = await post(`/api/projects/${id}/steps/episode_cards/cancel`);
    expect(res.statusCode).toBe(400);
    expect(res.json().error).toBe('Шаг «Карточки серий» сейчас не выполняется');
  });

  it('stops a background run after the current block and marks the step as interrupted', async () => {
    // The model answers the first block only when the test lets it, so the stop can be asked meanwhile.
    let release: () => void = () => {};
    const gate = new Promise<void>((ok) => (release = ok));
    const slow = {
      name: 'anthropic' as const,
      calls: [] as string[],
      async complete(req: LlmRequest, target: { model: string }) {
        slow.calls.push(req.task ?? '');
        const m = /^episode_cards:(\d+)-(\d+)$/u.exec(req.task ?? '');
        if (!m) throw new Error(req.task);
        if (m[1] === '1') await gate;
        const eps = Array.from({ length: Number(m[2]) - Number(m[1]) + 1 }, (_, i) => Number(m[1]) + i);
        return { text: JSON.stringify(eps.map(card)), model: target.model, usage: { inputTokens: 1000, outputTokens: 500, cacheReadTokens: 0, cacheWriteTokens: 0 } };
      },
      async countTokens() {
        return 1000;
      },
    };
    const bg = buildApp({ kb, db, config: loadModelsConfig(), llm: new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: slow, google: new FakeProvider('google', [reply]) } }) });
    const { id, memory } = await readyForCards(bg);
    const started = await bg.inject({ method: 'POST', url: `/api/projects/${id}/steps/episode_cards/run`, payload: { episodes: [1, 11, 21] } });
    expect(started.statusCode).toBe(202);
    await new Promise((ok) => setTimeout(ok, 20));
    let ov = (await bg.inject({ method: 'GET', url: `/api/projects/${id}/overview` })).json();
    expect(ov.job).toMatchObject({ step: 'episode_cards', running: true, progress: { unit: 'block', done: 0, total: 3, current: 10 } });
    const stop = await bg.inject({ method: 'POST', url: `/api/projects/${id}/steps/episode_cards/cancel`, payload: {} });
    expect(stop.json()).toMatchObject({ cancelling: true });
    release();
    for (let i = 0; i < 100 && (ov = (await bg.inject({ method: 'GET', url: `/api/projects/${id}/overview` })).json()).job.running; i++) {
      await new Promise((ok) => setTimeout(ok, 10));
    }
    expect(ov.job).toMatchObject({ running: false, stopped: true, error: 'Остановлено продюсером после серии 10', progress: { done: 1, total: 3 } });
    expect(ov.job.progress.costUsd).toBeGreaterThan(0);
    // The first block was paid for and is kept; the second never started.
    expect(slow.calls).toEqual(['episode_cards:1-10']);
    expect(memory.cards().map((c) => c.ep)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(ov.steps.find((s: { step: string }) => s.step === 'episode_cards')).toMatchObject({ status: 'needs_fix' });
    const open = (await bg.inject({ method: 'GET', url: `/api/projects/${id}/findings?status=open&step=episode_cards` })).json() as { quote: string }[];
    expect(open.map((f) => f.quote)).toContain('Шаг «Карточки серий» прервался и проверен не до конца: Остановлено продюсером после серии 10');
    expect(ov.next).toMatchObject({ action: 'run', episodes: expect.arrayContaining([11, 12, 60]) });
  });
});

describe('hand edits', () => {
  it('changes a fact and propagates it: dependent cards go stale, the revision is logged', async () => {
    const { id, memory } = await readyForCards(app);
    // Episode 3 of the golden plan acts on f_betrayal.
    memory.saveCard(EpisodeCard.parse(card(3)));
    memory.saveCard(EpisodeCard.parse(card(4)));
    const bad = await patch(`/api/projects/${id}/facts/f_nope`, { text: 'x' });
    expect(bad.json().error).toBe('Факта f_nope нет в базе');
    const res = await patch(`/api/projects/${id}/facts/f_betrayal`, { text: 'Герман женился на Лизе по плану матери', since_ep: 2, note: 'Так точнее' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ affectedEpisodes: expect.arrayContaining([3]), staleCards: [3] });
    expect(res.json().affectedEpisodes).not.toContain(4);
    const ov = (await get(`/api/projects/${id}/overview`)).json();
    expect(ov.bible.facts.find((f: { id: string }) => f.id === 'f_betrayal')).toEqual({ id: 'f_betrayal', text: 'Герман женился на Лизе по плану матери', since_ep: 2 });
    expect(ov.stale.cards).toEqual([3]);
    const log = db.select().from(revisions).all().filter((r) => r.projectId === id);
    expect(log).toMatchObject([{ entity: 'fact', entityId: 'f_betrayal', author: 'producer', note: 'Так точнее' }]);
    expect((await patch(`/api/projects/${id}/facts/f_betrayal`, { since_ep: 'два' })).json().error).toBe('Неверные данные: since_ep — ожидается число');
  });

  it('changes a timeline event and re-checks chronology', async () => {
    const { id } = await readyForCards(app);
    const event = golden.bible.timeline.events[0]!;
    const res = await patch(`/api/projects/${id}/events/${event.id}`, { year: event.year + 1 });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ revisionId: expect.any(Number) });
    expect((await get(`/api/projects/${id}/overview`)).json().bible.timeline.events.find((e: { id: string }) => e.id === event.id).year).toBe(event.year + 1);
    expect((await patch(`/api/projects/${id}/events/e_nope`, { year: 2000 })).json().error).toBe('События e_nope нет в хронологии');
  });

  it('a card rewritten by hand is validated, saved, code-checked and sends its block back to approval', async () => {
    const { id, memory } = await readyForCards(app);
    for (const ep of golden.plan.episodes.map((e) => e.ep)) memory.saveCard(EpisodeCard.parse(card(ep)));
    memory.saveArtifact('episode_cards', { episodes: [1] });
    memory.saveArtifact('card_blocks', { approved: [1, 11] });
    expect((await put(`/api/projects/${id}/cards/1`, { ...card(1), cast: 'Лиза' })).json().error).toBe('Неверные данные: cast — ожидается список');
    expect((await put(`/api/projects/${id}/cards/99`, card(99))).json().error).toBe('Карточки 99-й серии нет');
    const many = { ...card(1), cast: ['Лиза', 'Вера', 'Герман', 'Анна', 'Кира', 'Даша', 'Панова'] };
    const res = await put(`/api/projects/${id}/cards/1`, many);
    expect(res.statusCode).toBe(200);
    expect(res.json().card.cast).toHaveLength(7);
    expect(res.json().findings.length).toBeGreaterThan(0);
    expect((await get(`/api/projects/${id}/overview`)).json().cardBlocks).toEqual([11]);
    expect(db.select().from(revisions).all().filter((r) => r.projectId === id)).toMatchObject([{ entity: 'card', entityId: '1', author: 'producer' }]);
  });

  it('a script rewritten by hand is validated, saved and linted without the model', async () => {
    const { id, memory } = await readyForCards(app);
    memory.saveCard(EpisodeCard.parse(card(1)));
    memory.saveScript(Script.parse(script(1)));
    memory.saveArtifact('scripts', { episodes: [1] });
    await post(`/api/projects/${id}/steps/episode_cards/skip`);
    expect((await put(`/api/projects/${id}/scripts/2`, script(2))).json().error).toBe('Сценария 2-й серии нет');
    expect((await put(`/api/projects/${id}/scripts/1`, { ...script(1), blocks: 'нет' })).json().error).toBe('Неверные данные: blocks — ожидается список');
    const long = { ...script(1), blocks: [{ t0: 0, t1: 90, kind: 'line', speaker: 'ЛИЗА', text: Array.from({ length: 40 }, () => 'слово').join(' ') }] };
    const res = await put(`/api/projects/${id}/scripts/1`, long);
    expect(res.statusCode).toBe(200);
    expect(res.json().text).toContain('ЛИЗА');
    expect(res.json().findings.length).toBeGreaterThan(0);
    expect((await get(`/api/projects/${id}/scripts/1`)).json().script.blocks).toHaveLength(1);
    expect(db.select().from(revisions).all().filter((r) => r.projectId === id)).toMatchObject([{ entity: 'script', entityId: '1', author: 'producer' }]);
  });

  it('«это не дыра» takes a reason without a fact and keeps it as the producer\'s contribution', async () => {
    const id = (await post('/api/projects', { title: 'Кровь', genreId: 'revenge_thriller', idea: 'идея' })).json().id as string;
    await post(`/api/projects/${id}/steps/concept/run`);
    await post(`/api/projects/${id}/steps/concept/approve`, { choice: 0 });
    for (const step of ['logline', 'bible']) {
      await post(`/api/projects/${id}/steps/${step}/run`);
      await post(`/api/projects/${id}/steps/${step}/approve`);
    }
    await post(`/api/projects/${id}/steps/season_plan/run`);
    const open = (await get(`/api/projects/${id}/findings?status=open&limit=1`)).json() as { id: string }[];
    const fid = encodeURIComponent(open[0]!.id);
    expect((await post(`/api/findings/${fid}/dismiss`, { projectId: id })).json().error).toMatch(/укажите факт из базы или напишите, почему/u);
    expect((await post(`/api/findings/${fid}/dismiss`, { projectId: id, factId: 'f_nope' })).json().error).toBe('Факта f_nope нет в базе');
    const done = await post(`/api/findings/${fid}/dismiss`, { projectId: id, reason: 'Полиция в этом городе куплена — показано в 2-й серии' });
    expect(done.json()).toMatchObject({ status: 'dismissed', verdict: 'Отклонено продюсером: Полиция в этом городе куплена — показано в 2-й серии', resolutionFactId: null });
    expect(db.select().from(revisions).all().filter((r) => r.projectId === id)).toMatchObject([
      { entity: 'finding', entityId: open[0]!.id, author: 'producer', note: 'Полиция в этом городе куплена — показано в 2-й серии' },
    ]);
    expect((await get(`/api/projects/${id}`)).json().steps.find((s: { step: string }) => s.step === 'season_plan').status).toBe('draft');
  });
});

describe('approval and export of a whole season', () => {
  it('scripts are approved only when every card has a current script', async () => {
    const { id, memory } = await readyForCards(app);
    memory.saveCard(EpisodeCard.parse(card(1)));
    memory.saveCard(EpisodeCard.parse(card(2)));
    await post(`/api/projects/${id}/steps/episode_cards/skip`);
    memory.saveScript(Script.parse(script(1)));
    memory.saveArtifact('scripts', { episodes: [1] });
    const refused = await post(`/api/projects/${id}/steps/scripts/approve`);
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error).toBe('Нельзя утвердить «Сценарии»: нет готовых сценариев у 1 серий (2)');
    memory.saveScript(Script.parse(script(2)));
    memory.markOutdated('scripts', [2]);
    expect((await post(`/api/projects/${id}/steps/scripts/approve`)).json().error).toBe('Нельзя утвердить «Сценарии»: нет готовых сценариев у 1 серий (2)');
    memory.saveScript(Script.parse(script(2)));
    expect((await post(`/api/projects/${id}/steps/scripts/approve`)).json()).toMatchObject({ status: 'approved' });
  });

  it('exports mark unapproved episodes and can leave them out', async () => {
    const { id, memory } = await readyForCards(app);
    memory.saveCard(EpisodeCard.parse(card(1)));
    memory.saveScript(Script.parse(script(1)));
    const md = (await get(`/api/projects/${id}/export?format=md`)).body;
    expect(md).toContain('### Серия 1 — не утверждено');
    expect((await get(`/api/projects/${id}/export?format=md&exclude=1`)).body).not.toContain('### Серия 1');
  });
});

describe('a project without a genre', () => {
  it('still opens in the overview', async () => {
    db.insert(projects).values({ id: 'old', title: 'Старый' }).run();
    const ov = await get('/api/projects/old/overview');
    expect(ov.statusCode).toBe(200);
    expect(ov.json().genre).toBeNull();
    expect(ov.json().next.task).toMatch(/не выбран жанр/u);
  });
});
