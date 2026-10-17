import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { loadGolden } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { confirmChecklist } from '../demo.ts';
import { createProject } from './project.ts';
import { PARTIAL_PLAN, planParts, runStep, skipStep } from './runners.ts';

/**
 * Wave 2 of the audit (quality of the series itself) on a thriller project: the golden «Прима»
 * passes every code check, so each test breaks exactly one thing and looks for one finding.
 */
const kb = loadKb();
const prima = loadGolden('prima');

/** A card that passes the thriller beat checks. */
function card(ep: number) {
  const o = prima.plan.episodes.find((e) => e.ep === ep)!;
  return {
    ep,
    duration_s: 90,
    hook_0_5s: `Продолжение обрыва ${ep - 1}-й`,
    event: o.event,
    twist: o.cliffhanger,
    emotions: o.emotions,
    heroine_action: o.heroine_action,
    punchline: 'Теперь ты.',
    cliffhanger: o.cliffhanger,
    cast: ['Ася', 'Регина'],
    location: 'Театр: сцена и зал',
    sound: 'Скрип пуант по мелу',
    metro_frame: { face: 'Ася', action: 'смотрит на крест', object: 'пуанты' },
    knowledge: { viewer: 'Крест — знак', heroine: 'Лика исчезла', villain: 'Ася послушна' },
    acts_on: o.acts_on,
    beats: [
      { t0: 0, t1: 3, type: 'HOOK', text: 'Крест в луче', hook_kind: 'continuation' },
      { t0: 3, t1: 15, type: 'STAKES', text: 'Ася хочет выйти на поклон, но луч чужой' },
      { t0: 15, t1: 60, type: 'ESCALATION', text: 'Регина выталкивает Асю', new_info: 'Лика не выходила из гримёрки' },
      { t0: 60, t1: 80, type: 'REVERSAL', text: 'На зеркале помада', kind: 'clue_flip', mute_readable: true },
      { t0: 80, t1: 90, type: 'CLIFFHANGER', text: o.cliffhanger, kind: o.hook_type },
    ],
    link_prev: 'overlap',
    goal: 'выйти на поклон',
    obstacle: 'луч чужой',
    question: o.question,
    audio: [{ cue: 'скрип пуант', visual_double: 'пуанты на кресте' }],
  };
}

/** A script that passes the metrics; `talkingHeads` makes one run of lines longer than the linter allows. */
function script(ep: number, opts: { talkingHeads?: boolean } = {}) {
  const lines = [
    'Ты снова в гримёрке так поздно?',
    'Я ищу пуанты Лики, они пропали.',
    'Здесь нет ничего интересного для тебя.',
    'Тогда почему ты закрыла этот шкаф на ключ?',
    'Не задавай вопросов, на которые не хочешь ответов.',
    'Я уже знаю ответ. Просто хочу услышать его.',
    'Иди спать. Завтра премьера.',
    'Я не уйду, пока не узнаю правду о Лике.',
  ];
  const blocks: unknown[] = [{ t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. ГРИМЁРКА — НОЧЬ. Ася держит пуанты.', shot: 'close', faces: 1 }];
  lines.forEach((text, i) => {
    const t0 = 5 + i * 10;
    // A sound between every two lines breaks the run; without it the dialogue is talking heads.
    if (!opts.talkingHeads && i > 0) blocks.push({ t0, t1: t0, kind: 'sound', text: 'Скрип двери' });
    blocks.push({ t0, t1: t0 + 10, kind: 'line', speaker: i % 2 ? 'АСЯ' : 'РЕГИНА', text });
  });
  blocks.push({ t0: 85, t1: 90, kind: 'scene', text: 'Регина гасит свет.', shot: 'close', faces: 1 });
  return { ep, title: `Серия ${ep}`, duration_s: 90, blocks };
}

let db: Db;
let tasks: string[];
let requests: LlmRequest[];
let projectId: string;
/** What the fakes answer per task kind; tests override single kinds. */
let answers: Record<string, (a: string, req: LlmRequest) => unknown>;

function reply(req: LlmRequest): string {
  const task = req.task ?? '';
  tasks.push(task);
  requests.push(req);
  const [kind = '', a = ''] = task.split(':');
  const answer = answers[kind];
  if (!answer) throw new Error(`Неожиданная задача ${task}`);
  return JSON.stringify(answer(a, req));
}

function epsOf(range: string): number[] {
  const [from, to = from] = range.split('-').map(Number);
  return Array.from({ length: to! - from! + 1 }, (_, i) => from! + i);
}

/** The golden plan's episodes of one part, e.g. "1-12". */
function planPart(range: string) {
  const eps = epsOf(range);
  return { episodes: prima.plan.episodes.filter((e) => eps.includes(e.ep)), deviations: prima.plan.deviations.filter((d) => eps.includes(d.ep)) };
}

function defaultAnswers(): typeof answers {
  return {
    season_plan: (a) => planPart(a),
    season_plan_fix: (a) => a.split(',').map(Number).map((ep) => prima.plan.episodes.find((e) => e.ep === ep)),
    checklist_judge: (_a, req) => confirmChecklist(req.system ?? ''),
    devil_advocate: () => [],
    persona: () => [],
    review_summary: () => ({ verdict: 'Сезон почти готов.', dangers: [{ where: 'Серия 1', why: 'Первая серия' }], legal: [] }),
    episode_cards: (a) => epsOf(a).map(card),
    script: (a) => script(Number(a)),
    controller: () => [],
    continuity: () => ({ facts: [], knowledge: [], states: [] }),
  };
}

function client() {
  return new LlmClient({
    config: testConfig(),
    db,
    env: {},
    providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply], () => 10) },
  });
}

const deps = () => ({ db, llm: client(), kb, projectId });
const memory = () => new ProjectMemory(db, projectId);

beforeEach(() => {
  db = openDb(':memory:');
  tasks = [];
  requests = [];
  answers = defaultAnswers();
  projectId = createProject(db, kb, { title: 'Прима', genreId: 'vertical_thriller', idea: 'Прима исчезла на поклоне' });
  const m = memory();
  m.importBible(prima.bible);
  m.importPlan(prima.plan);
  for (const step of ['concept', 'logline', 'bible', 'season_plan'] as const) skipStep({ db, kb, projectId }, step);
});

describe('code checks in the app (audit item 13)', () => {
  it('cards without beats get the genre beat findings, attributed to the rule', async () => {
    answers.episode_cards = (a) => epsOf(a).map((ep) => (ep === 2 ? { ...card(ep), beats: undefined } : ep === 3 ? { ...card(ep), goal: undefined } : card(ep)));
    const run = await runStep(deps(), 'episode_cards', { episodes: [1] });
    expect(run.findings.map((f) => [f.check, f.episode, f.rule, f.severity])).toEqual([
      ['beats.missing', 2, 'R01', 'blocker'],
      ['beats.goal', 3, 'R03', 'blocker'],
    ]);
    expect(run.status).toBe('needs_fix');
  });

  it('a clean card block gets no findings', async () => {
    const run = await runStep(deps(), 'episode_cards', { episodes: [1] });
    expect(run.findings).toEqual([]);
  });

  it('the script linter runs on every script and its findings enter the fix loop', async () => {
    await runStep(deps(), 'episode_cards', { episodes: [1] });
    skipStep({ db, kb, projectId }, 'episode_cards');
    answers.script = (a) => script(Number(a), { talkingHeads: true });
    const first = await runStep(deps(), 'scripts', { episodes: [3] });
    expect(first.findings.map((f) => [f.check, f.rule, f.severity])).toEqual([['script_linter.talking_heads', 'R05', 'major']]);
    expect(first.status).toBe('draft');
    // The rewrite sees the linter finding like any controller finding.
    answers.script = (a) => script(Number(a));
    requests = [];
    const second = await runStep(deps(), 'scripts', { episodes: [3], fix: true });
    const writer = requests.find((r) => r.task === 'script:3')!;
    expect(writer.system).toContain('Исправь замечания к прошлой версии');
    expect(writer.system).toContain('с разговора без смены положения');
    expect(second.findings).toEqual([]);
  });
});

describe('season plan in parts (audit item 14)', () => {
  it('merges short frame blocks into parts of at least 10 episodes; a short tail joins the last part', () => {
    expect(planParts(60, [[1, 8], [9, 20], [21, 30], [31, 40], [41, 50], [51, 60]])).toEqual([[1, 20], [21, 30], [31, 40], [41, 50], [51, 60]]);
    expect(planParts(60, kb.frames.thriller60!.blocks)).toEqual([[1, 12], [13, 24], [25, 45], [46, 60]]);
    // Blocks that do not cover the season: blocks of 10, the tail of 5 joins the last one.
    expect(planParts(25, [[1, 5]])).toEqual([[1, 10], [11, 25]]);
  });

  it('writes the plan in four parts, each seeing the full table of the episodes written so far', async () => {
    await runStep(deps(), 'season_plan');
    expect(tasks.filter((t) => t.startsWith('season_plan'))).toEqual(['season_plan:1-12', 'season_plan:13-24', 'season_plan:25-45', 'season_plan:46-60']);
    const third = requests.find((r) => r.task === 'season_plan:25-45')!.system!;
    expect(third).toContain('Серия 1. «Крест»');
    expect(third).toContain('Серия 24.');
    expect(third).not.toContain('Серия 25.');
    // The table carries hook type, knowledge mode and anchors, not only the events.
    expect(third).toContain('Крючок (Откровение (revelation))');
    expect(third).toContain('режим: тайна');
    expect(third).toContain('якоря: hook_violation');
    expect(memory().currentPlan()?.episodes).toHaveLength(60);
  });

  it('resumes from the parts saved before a failure instead of writing them again', async () => {
    answers.season_plan = (a) => {
      if (a === '25-45') throw new Error('сеть упала');
      return planPart(a);
    };
    await expect(runStep(deps(), 'season_plan')).rejects.toThrow();
    const partial = memory().latestArtifact(PARTIAL_PLAN) as { parts: Record<string, unknown> };
    expect(Object.keys(partial.parts)).toEqual(['1-12', '13-24']);

    answers = defaultAnswers();
    tasks = [];
    const run = await runStep(deps(), 'season_plan');
    expect(tasks.filter((t) => t.startsWith('season_plan:'))).toEqual(['season_plan:25-45', 'season_plan:46-60']);
    expect(run.status).toBe('draft');
    expect((memory().latestArtifact(PARTIAL_PLAN) as { parts: Record<string, unknown> }).parts).toEqual({});
  });

  it('one reconciliation call rewrites only the episodes with blocker or major code findings', async () => {
    answers.season_plan = (a) => {
      const part = planPart(a);
      // Episode 1 loses its anchor: a blocker of the frame check, pointing at episode 1.
      return { ...part, episodes: part.episodes.map((e) => (e.ep === 1 ? { ...e, anchors: [] } : e)) };
    };
    const run = await runStep(deps(), 'season_plan');
    expect(tasks.filter((t) => t.startsWith('season_plan_fix:'))).toEqual(['season_plan_fix:1']);
    const fix = requests.find((r) => r.task === 'season_plan_fix:1')!.system!;
    expect(fix).toContain('Опорная точка «Хук-нарушение и необратимый инцидент» не отмечена');
    expect(fix).toContain('Серия 60.');
    expect(memory().currentPlan()?.episodes[0]?.anchors).toEqual(['hook_violation']);
    expect(run.findings.filter((f) => f.check?.startsWith('season_frame'))).toEqual([]);
  });

  it('a hook type outside the frame vocabulary is rejected when the answer is parsed', async () => {
    answers.season_plan = (a) => {
      const part = planPart(a);
      return { ...part, episodes: part.episodes.map((e) => (e.ep === 1 ? { ...e, hook_type: 'boom' } : e)) };
    };
    await expect(runStep(deps(), 'season_plan')).rejects.toThrow(/дважды/);
    expect(tasks.filter((t) => t.startsWith('season_plan_fix:'))).toEqual([]);
  });
});

describe('writer context and continuity ledger (audit item 16)', () => {
  beforeEach(async () => {
    answers.episode_cards = (a) => epsOf(a).map((ep) => (ep === 2 ? { ...card(ep), plants: ['c1_shoes', 'g_lika_shoes'], closes_question: 1, opens_question: 2 } : card(ep)));
    await runStep(deps(), 'episode_cards', { episodes: [1] });
    skipStep({ db, kb, projectId }, 'episode_cards');
  });

  it('the architect sees the clues, guns and nested questions of the block', () => {
    const cards = requests.find((r) => r.task === 'episode_cards:1-10')!.system!;
    expect(cards).toContain('c1_shoes (улика, заложена в 2-й, окупается в 12-й)');
    expect(cards).toContain('g_flask (ружьё, показано в 7-й, стреляет в 20-й)');
    expect(cards).toContain('уровень 1:');
    expect(memory().cards().find((c) => c.ep === 2)).toMatchObject({ plants: ['c1_shoes', 'g_lika_shoes'], closes_question: 1, opens_question: 2 });
  });

  it('the writer gets the plan outline, the next hook, the plants with their text and the knowledge mode', async () => {
    await runStep(deps(), 'scripts', { episodes: [2] });
    const writer = requests.find((r) => r.task === 'script:2')!.system!;
    expect(writer).toContain('Серия 1. «Крест»');
    expect(writer).toContain('Серия 4.');
    expect(writer).not.toContain('Серия 5.');
    expect(writer).toContain('Следующая серия начинается с: Продолжение обрыва 2-й');
    expect(writer).toContain('c1_shoes — улика: Пуанты Лики с именем Аси в её шкафчике (визуальная, без комментария)');
    expect(writer).toContain('g_lika_shoes — ружьё: Пуанты Лики (читается без звука)');
    expect(writer).toContain('Режим знания в серии: ');
    // The whole bible: nothing is compressed while the input fits.
    expect(writer).toContain('"world_rules"');
    expect(writer).toContain('"timeline"');
  });

  it('the continuity ledger merges new facts and knowledge and reaches the next writer and the critics', async () => {
    answers.continuity = (a) =>
      a === '1'
        ? {
            facts: [
              { id: 'f_key_asya', text: 'Ключ от гримёрки Лики теперь у Аси', since_ep: 1 },
              { id: 'f_lika_alive_dup', text: 'Лика жива и спрятана за зеркалом гримёрки, в бункере под сценой' },
            ],
            knowledge: [{ who: 'Ася', fact: 'f_key_asya', since_ep: 1, how: 'нашла в пуантах' }, { who: 'Ася', fact: 'f_asya_entered', since_ep: 5, how: 'Запись камеры' }],
            states: [{ object: 'Пуанты Лики', holder: 'Ася', since_ep: 1, note: 'спрятаны в шкафчике' }],
          }
        : { facts: [], knowledge: [], states: [] };
    await runStep(deps(), 'scripts', { episodes: [1, 2] });
    const m = memory();
    const bible = m.currentBible()!;
    // The new fact is added once; the duplicate of a bible fact (same text) is not.
    expect(bible.facts.map((f) => f.id)).toContain('f_key_asya');
    expect(bible.facts.map((f) => f.id)).not.toContain('f_lika_alive_dup');
    expect(bible.facts).toHaveLength(prima.bible.facts.length + 1);
    // Knowledge already in the base is not duplicated.
    expect(bible.knowledge.filter((k) => k.who === 'Ася' && k.fact === 'f_asya_entered')).toHaveLength(1);
    expect(bible.knowledge).toContainEqual({ who: 'Ася', fact: 'f_key_asya', since_ep: 1, how: 'нашла в пуантах' });
    expect(m.continuityDigest(2)).toEqual([
      'Серия 1: факт f_key_asya — Ключ от гримёрки Лики теперь у Аси',
      'Серия 1: факт f_lika_alive_dup — Лика жива и спрятана за зеркалом гримёрки, в бункере под сценой',
      'Серия 1: Ася знает f_key_asya (нашла в пуантах)',
      'Серия 1: Ася знает f_asya_entered (Запись камеры)',
      'Серия 1: Пуанты Лики — у Ася (спрятаны в шкафчике)',
    ]);
    expect(m.continuityDigest(1)).toEqual([]);
    const writer2 = requests.find((r) => r.task === 'script:2')!.system!;
    expect(writer2).toContain('Серия 1: Пуанты Лики — у Ася');
    const logic2 = requests.find((r) => r.task === 'controller:logic:2')!.system!;
    expect(logic2).toContain('Пуанты Лики — у Ася');
    // The helper is called once per written script; a rewrite replaces the episode's lines.
    expect(tasks.filter((t) => t.startsWith('continuity:'))).toEqual(['continuity:1', 'continuity:2']);
    answers.continuity = () => ({ facts: [], knowledge: [], states: [{ object: 'Ключ', holder: 'Регина', since_ep: 1 }] });
    await runStep(deps(), 'scripts', { episodes: [1] });
    expect(m.continuityDigest(2)).toEqual(['Серия 1: Ключ — у Регина']);
  });
});
