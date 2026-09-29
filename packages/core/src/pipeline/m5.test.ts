import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { loadGolden } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { parseModelsConfig } from '../providers/config.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { compressBible } from './compress.ts';
import { Pipeline } from './machine.ts';
import { choosePolish, proposePolish } from './polish.ts';
import { createProject } from './project.ts';
import { approveStep, runStep, skipStep } from './runners.ts';

const kb = loadKb();
const golden = loadGolden();

/** A valid card for an outline of the golden plan. */
function card(ep: number) {
  const o = golden.plan.episodes.find((e) => e.ep === ep)!;
  return {
    ep,
    duration_s: 90,
    hook_0_5s: 'Лиза смотрит в зеркало',
    event: o.event,
    twist: o.cliffhanger,
    emotions: o.emotions,
    heroine_action: o.heroine_action,
    punchline: 'Я всё помню.',
    cliffhanger: o.cliffhanger,
    cast: ['Лиза', 'Вера'],
    location: 'архив',
    sound: 'Тишина театра',
    metro_frame: { face: 'Лиза', action: 'смотрит', object: 'фото' },
    knowledge: { viewer: 'Семья опасна', heroine: 'Муж лжёт', villain: 'Лиза слаба' },
    acts_on: o.acts_on,
  };
}

/** A script that passes the code metrics: 90 s, ~330 characters of lines. */
function script(ep: number, extra = '') {
  const lines = [
    'Ты снова в архиве так поздно?',
    'Я ищу старые фото для выставки.',
    'Здесь нет ничего интересного для тебя.',
    'Тогда почему ты закрыла этот шкаф на ключ?',
    'Не задавай вопросов, на которые не хочешь ответов.',
    'Я уже знаю ответ. Просто хочу услышать его от тебя.',
    extra || 'Иди спать, Лиза. Завтра тяжёлый день.',
    'Я не уйду, пока не узнаю правду о своей семье.',
  ];
  return {
    ep,
    title: `Серия ${ep}`,
    duration_s: 90,
    blocks: [
      { t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. АРХИВ — НОЧЬ. Лиза держит фото.' },
      ...lines.map((text, i) => ({ t0: 5 + i * 10, t1: 15 + i * 10, kind: 'line', speaker: i % 2 ? 'ЛИЗА' : 'ВЕРА', text })),
      { t0: 85, t1: 90, kind: 'scene', text: 'Вера гасит свет.' },
    ],
  };
}

let db: Db;
let tasks: string[];
let writerCalls: LlmRequest[];
let controllerReply: (c: string, ep: string) => unknown[];
let projectId: string;

function reply(req: LlmRequest): string {
  const task = req.task ?? '';
  tasks.push(task);
  const [kind, a = '', b = ''] = task.split(':');
  if (kind === 'episode_cards') {
    const [from, to] = a.split('-').map(Number);
    const eps = Array.from({ length: to! - from! + 1 }, (_, i) => from! + i);
    return JSON.stringify(eps.map((ep) => (ep === 14 ? { ...card(ep), acts_on: [{ who: 'Лиза', fact: 'f_herman_initiator' }] } : card(ep))));
  }
  if (kind === 'script') {
    writerCalls.push(req);
    return JSON.stringify(script(Number(a), req.system?.includes('Исправь замечания') ? 'Спи. Завтра выставка.' : ''));
  }
  if (kind === 'controller') return JSON.stringify(controllerReply(a, b));
  if (kind === 'polish') {
    const v = (n: number) => [{ t0: 5, t1: 15, kind: 'line', speaker: 'ВЕРА', text: `Вариант ${n}: ты опять здесь?` }];
    return JSON.stringify({ variants: [1, 2, 3, 4, 5].map(v) });
  }
  throw new Error(task);
}

function client(tokens?: (req: LlmRequest) => number) {
  const cfg = testConfig();
  const config = parseModelsConfig({ ...cfg, roles: { ...cfg.roles, writer: { provider: 'google', model: 'g-pro', max_input: 1000 } } });
  return new LlmClient({
    config,
    db,
    env: {},
    providers: {
      anthropic: new FakeProvider('anthropic', [reply]),
      google: new FakeProvider('google', [reply], tokens ?? (() => 10)),
    },
  });
}

const deps = (llm = client()) => ({ db, llm, kb, projectId });

beforeEach(() => {
  db = openDb(':memory:');
  tasks = [];
  writerCalls = [];
  controllerReply = () => [];
  projectId = createProject(db, kb, { title: 'Кровь', genreId: 'revenge_thriller', idea: 'идея' });
  // Steps 1–4 are done: the golden bible and plan are approved.
  const memory = new ProjectMemory(db, projectId);
  memory.importBible(golden.bible);
  memory.importPlan(golden.plan);
  for (const step of ['concept', 'logline', 'bible', 'season_plan'] as const) skipStep({ db, kb, projectId }, step);
});

describe('episode cards', () => {
  it('are written in blocks of 10 and checked by code', async () => {
    const run = await runStep(deps(), 'episode_cards');
    expect(tasks.filter((t) => t.startsWith('episode_cards'))).toEqual([
      'episode_cards:1-10', 'episode_cards:11-20', 'episode_cards:21-30',
      'episode_cards:31-40', 'episode_cards:41-50', 'episode_cards:51-60',
    ]);
    expect(new ProjectMemory(db, projectId).cards()).toHaveLength(60);
    expect(run.findings.map((f) => [f.check, f.episode])).toEqual([['knowledge.too_early', 14]]);
    expect(run.status).toBe('needs_fix');
  });

  it('are approved block by block; a block with a blocker waits', async () => {
    await runStep(deps(), 'episode_cards');
    approveStep({ db, kb, projectId }, 'episode_cards', { block: 1 });
    expect(() => approveStep({ db, kb, projectId }, 'episode_cards', { block: 11 })).toThrow('Нельзя утвердить серии 11–20');
    expect(() => approveStep({ db, kb, projectId }, 'episode_cards', { block: 5 })).toThrow('Нет блока');
    expect(new Pipeline(db, projectId).get('episode_cards').status).toBe('needs_fix');

    // Regenerating only the block with episode 14.
    tasks = [];
    await runStep(deps(), 'episode_cards', { episodes: [14] });
    expect(tasks).toEqual(['episode_cards:11-20']);
  });

  it('the whole step is approved when every block is', async () => {
    await runStep(deps(), 'episode_cards');
    const memory = new ProjectMemory(db, projectId);
    const blocker = memory.findingsOf('episode_cards', 'open')[0]!;
    memory.setFindingOutcome(blocker.id, { status: 'dismissed', resolutionFactId: 'f_herman_initiator' });
    for (const block of [1, 11, 21, 31, 41, 51]) approveStep({ db, kb, projectId }, 'episode_cards', { block });
    expect(new Pipeline(db, projectId).get('episode_cards').status).toBe('approved');
  });
});

describe('scripts', () => {
  beforeEach(async () => {
    await runStep(deps(), 'episode_cards');
    skipStep({ db, kb, projectId }, 'episode_cards');
  });

  it('the writer gets the card, a compressed bible and the two previous episodes', async () => {
    await runStep(deps(), 'scripts', { episodes: [1, 2, 3] });
    const third = writerCalls[2]!.system!;
    expect(third).toContain('СЕРИЯ 1. «Серия 1»');
    expect(third).toContain('СЕРИЯ 2. «Серия 2»');
    expect(third).not.toContain('"timeline"');
    expect(new ProjectMemory(db, projectId).scripts().map((s) => s.ep)).toEqual([1, 2, 3]);
  });

  it('compresses the bible further when the input is over the writer limit', async () => {
    const tokens = (req: LlmRequest) => (req.system?.includes('"practical_effect"') ? 5000 : 10);
    await runStep(deps(client(tokens)), 'scripts', { episodes: [5] });
    const sent = writerCalls[0]!.system!;
    expect(sent).not.toContain('"practical_effect"');
    expect(sent).toMatch(/"name": ?"Лиза"/u);
    expect(sent).not.toMatch(/"name": ?"Олег"/u);
  });

  it('runs six model controllers from another family and code metrics', async () => {
    controllerReply = (c) =>
      c === 'metro'
        ? [
            { holeType: 9, severity: 'major', quote: 'Вера гасит свет.', viewerQuestion: 'Зритель спросит: что там в темноте?', fixes: ['Крупный план'] },
            { holeType: 9, severity: 'major', quote: 'Этого нет в сценарии', viewerQuestion: 'Зритель спросит: ?', fixes: ['—'] },
          ]
        : [];
    const run = await runStep(deps(), 'scripts', { episodes: [1] });
    expect(tasks.filter((t) => t.startsWith('controller:')).map((t) => t.split(':')[1])).toEqual([
      'logic', 'genre', 'structure', 'metro', 'production', 'legal',
    ]);
    expect(run.findings).toMatchObject([{ controller: 'metro', episode: 1, quote: 'Вера гасит свет.' }]);
  });

  it('rewrites an episode taking open findings into account', async () => {
    controllerReply = (c) =>
      c === 'logic'
        ? [{ holeType: 3, severity: 'blocker', quote: 'Иди спать, Лиза.', viewerQuestion: 'Зритель спросит: почему она уходит?', fixes: ['Лиза остаётся'] }]
        : [];
    const first = await runStep(deps(), 'scripts', { episodes: [1] });
    expect(first.status).toBe('needs_fix');
    controllerReply = () => [];
    const second = await runStep(deps(), 'scripts', { episodes: [1], fix: true });
    expect(writerCalls[1]!.system).toContain('Исправь замечания к прошлой версии');
    expect(writerCalls[1]!.system).toContain('«Иди спать, Лиза.»');
    expect(second.status).toBe('draft');
    expect(new ProjectMemory(db, projectId).findingsOf('scripts', 'resolved')[0]?.verdict).toBe('Сценарий переписан');
  });

  it('a clean script gets no findings', async () => {
    const run = await runStep(deps(), 'scripts', { episodes: [2] });
    expect(run.findings).toEqual([]);
  });
});

describe('polish', () => {
  beforeEach(async () => {
    await runStep(deps(), 'episode_cards');
    skipStep({ db, kb, projectId }, 'episode_cards');
    await runStep(deps(), 'scripts', { episodes: [7] });
  });

  it('waits until scripts are approved or skipped', async () => {
    await expect(proposePolish(deps(), { ep: 7, from: 1, to: 1 })).rejects.toThrow('Сначала утвердите или пропустите шаг «Сценарии»');
  });

  it('offers 5 variants; the producer picks one and it is logged as creative contribution', async () => {
    skipStep({ db, kb, projectId }, 'scripts');
    const offer = await proposePolish(deps(), { ep: 7, from: 1, to: 1, note: 'Жёстче' });
    expect(offer.variants).toHaveLength(5);
    const { script: s } = choosePolish({ db, kb, projectId }, 2);
    expect(s.blocks[1]).toMatchObject({ speaker: 'ВЕРА', text: 'Вариант 3: ты опять здесь?' });
    const log = new ProjectMemory(db, projectId).revisionsLog('producer');
    expect(log.at(-1)).toMatchObject({ entity: 'script', entityId: '7', note: 'Доработка 7-й серии: выбран вариант 3 из 5 («Жёстче»)' });
    expect(() => choosePolish({ db, kb, projectId }, 9)).toThrow('Выберите вариант от 1 до 5');
  });
});

describe('compressBible', () => {
  it('level 2 keeps only the cast and the facts the card relies on', () => {
    const c = { ...card(30), cast: ['Зоя', 'Лиза'], acts_on: [{ who: 'Зоя', fact: 'f_herman_initiator' }] };
    const small = compressBible(golden.bible, c, 2) as { characters: { name: string }[]; facts: { id: string }[]; secrets: unknown[] };
    expect(small.characters.map((x) => x.name)).toEqual(['Лиза', 'Зоя']);
    expect(small.facts.map((f) => f.id)).toEqual(['f_herman_initiator']);
    expect(small.secrets).toHaveLength(3);
  });
});
