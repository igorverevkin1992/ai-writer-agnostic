import { genreKit, loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../../db/client.ts';
import { loadGolden } from '../../fixtures.ts';
import { LlmClient } from '../../providers/llm.ts';
import { FakeProvider, testConfig } from '../../providers/testing.ts';
import type { LlmRequest } from '../../providers/types.ts';
import type { Finding } from '../../schemas/finding.ts';
import { runDevilAdvocate } from './devilAdvocate.ts';
import { resolveFinding } from './resolve.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const golden = loadGolden();
let db: Db;
let answer: (req: LlmRequest) => unknown;

const client = () => {
  const reply = (req: LlmRequest) => JSON.stringify(answer(req));
  return new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) } });
};

beforeEach(() => {
  db = openDb(':memory:');
});

describe('review fixes: model checks', () => {
  it('the author cannot pass off an existing fact as a new one', async () => {
    const fact = golden.bible.facts[0]!;
    answer = () => ({ action: 'new_fact', fact: { id: fact.id, text: 'Совсем другой смысл' }, knowledge: [], explanation: 'Переписал факт' });
    const finding: Finding = {
      id: 'x', controller: 'logic', severity: 'blocker', quote: 'q', viewerQuestion: 'Зритель спросит: ?', fixes: ['a'], status: 'open',
    };
    const res = await resolveFinding(
      { llm: client(), kb, kit, authorRole: 'architect', authorProvider: 'anthropic', judgeRole: 'critic_of_architect' },
      finding,
      golden.bible,
      golden.plan,
    );
    expect(res.finding.status).toBe('open');
    expect(res.verdict).toMatch(/уже существующий/);
    expect(res.bible.facts.find((f) => f.id === fact.id)?.text).toBe(fact.text);
  });

  it('the same words in two episodes: the episode named by the auditor wins, both findings stay', async () => {
    const plan = structuredClone(golden.plan);
    plan.episodes[10]!.heroine_action = 'следит за мужем';
    plan.episodes[13]!.heroine_action = 'следит за мужем';
    answer = (req) =>
      req.task?.startsWith('devil_advocate:Б:')
        ? [11, 14].map((episode) => ({ level: 'medium', episode, quote: 'следит за мужем', viewerQuestion: 'Зритель спросит: как?', whyNoticed: 'видно', fixes: ['a'], agentRule: 'правило' }))
        : [];
    const res = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan, authorProvider: 'anthropic' }, { categories: ['Б'], personas: false });
    expect(res.findings.map((f) => f.episode).sort((a, b) => a! - b!)).toEqual([11, 14]);
  });

  it('a genre without a love line skips that review category and names its own protagonist', async () => {
    const systems: string[] = [];
    answer = (req) => {
      systems.push(req.system ?? '');
      return [];
    };
    const myth = genreKit(kb, 'slavic_myth');
    const res = await runDevilAdvocate({ llm: client(), kb, kit: myth }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { personas: false });
    // 14 categories × 3 blocks + 4 cross-season passes + the realism pass.
    expect(res.calls).toBe(14 * 3 + 4 + 1);
    expect(systems.some((s) => s.includes('Любовная линия'))).toBe(false);
    expect(systems[0]).toContain('Главный персонаж этого сериала — герой');
  });

  it('the genre guide adds its own questions for the devil advocate', async () => {
    const systems: string[] = [];
    answer = (req) => {
      systems.push(req.system ?? '');
      return [];
    };
    const romantasy = genreKit(kb, 'romantasy_revenge');
    await runDevilAdvocate({ llm: client(), kb, kit: romantasy }, { bible: golden.bible, authorProvider: 'anthropic' }, { holeTypes: [2], personas: false });
    expect(systems[0]).toContain('Почему никто не видит магию и знаки рода?');
    systems.length = 0;
    await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, authorProvider: 'anthropic' }, { holeTypes: [2], personas: false });
    expect(systems[0]).not.toContain('магию');
  });

  it('every audit pass gets the six questions and the review rules of its categories', async () => {
    const systems: string[] = [];
    answer = (req) => {
      systems.push(req.system ?? '');
      return [];
    };
    await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, authorProvider: 'anthropic' }, { holeTypes: [11], personas: false });
    expect(systems[0]).toContain('Откуда он это знает?');
    expect(systems[0]).toContain('Д. Информация.');
    expect(systems[0]).toContain('Каждую улику вести по цепочке «у кого она сейчас»');
    expect(systems[0]).not.toContain('Б. Закон.');
  });

  it('skips categories and catalog questions of other genres for the thriller', async () => {
    const thriller = genreKit(kb, 'vertical_thriller');
    const prima = loadGolden('prima');
    const systems: string[] = [];
    answer = (req) => {
      systems.push(`${req.task}\n${req.system ?? ''}`);
      return [];
    };
    const res = await runDevilAdvocate({ llm: client(), kb, kit: thriller }, { bible: prima.bible, plan: prima.plan, authorProvider: 'anthropic' }, { personas: false });
    const tasks = systems.map((s) => s.split('\n')[0] ?? '');
    expect(tasks.some((t) => t.startsWith('devil_advocate:Ж:'))).toBe(false);
    expect(tasks.some((t) => t.startsWith('devil_advocate:В:'))).toBe(false);
    expect(tasks.some((t) => t.startsWith('devil_advocate:И:'))).toBe(false);
    expect(tasks).toContain('devil_advocate:Р:план сезона');
    expect(tasks).toContain('devil_advocate:Р:план, серии 1–12');
    expect(tasks).toContain('realism:план сезона');
    const structure = systems.find((s) => s.startsWith('devil_advocate:М:')) ?? systems.find((s) => s.startsWith('devil_advocate:Р:'))!;
    expect(structure).not.toContain('Мидпоинт — контрраскрытие');
    expect(structure).not.toContain('Что стоит на 8-й серии?');
    // The plan pass reads the bible: world rules, clues, guns and the knowledge table.
    expect(structure).toContain('ПРАВИЛА МИРА');
    expect(structure).toContain('УЛИКИ');
    expect(res.calls).toBe(tasks.length);
  });

  it('one hole seen from two categories is one finding with both, in the review format', async () => {
    const draft = {
      level: 'high', episodes: '1, 8', quote: golden.plan.episodes[0]!.event, viewerQuestion: 'Зритель спросит: почему?',
      whyNoticed: 'Это первая серия', fixes: ['Один', 'Два', 'Три'], agentRule: 'Проверять мотив на момент хода',
    };
    answer = (req) => (req.task?.startsWith('devil_advocate:А:') || req.task?.startsWith('devil_advocate:З:') ? [draft] : []);
    const res = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { categories: ['А', 'З'], personas: false });
    expect(res.calls).toBe(2 * 3);
    expect(res.findings).toHaveLength(1);
    expect(res.findings[0]).toMatchObject({
      category: ['А', 'З'], level: 'high', severity: 'major', episode: 1, episodes: '1, 8',
      whyNoticed: 'Это первая серия', agentRule: 'Проверять мотив на момент хода', fixes: ['Один', 'Два', 'Три'],
    });
  });
});


describe('a malformed pass does not lose the audit', () => {
  it('keeps the findings of the other passes and names the lost one', async () => {
    const draft = { level: 'high', quote: golden.plan.episodes[0]!.event, viewerQuestion: 'Зритель спросит: почему?', whyNoticed: 'видно', fixes: ['a'], agentRule: 'правило' };
    answer = (req) => (req.task?.startsWith('devil_advocate:Б:') ? { not: 'an array' } : req.task?.startsWith('devil_advocate:А:') ? [draft] : []);
    const res = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { categories: ['А', 'Б'], personas: false });
    expect(res.findings).toHaveLength(1);
    expect(res.failedPasses.length).toBeGreaterThan(0);
    expect(res.failedPasses.every((t) => t.startsWith('devil_advocate:Б:'))).toBe(true);
  });
});

describe('the senior-editor pass', () => {
  it('drops a non-hole, merges a duplicate into the finding it repeats, keeps the rest', async () => {
    const ep = (n: number) => golden.plan.episodes[n - 1]!;
    const draft = (e: number, q: string, quote = ep(e).event) => ({ level: 'high', episode: e, quote, viewerQuestion: q, whyNoticed: 'видно', fixes: ['a'], agentRule: 'правило' });
    let seen: string[] = [];
    answer = (req) => {
      const t = req.task ?? '';
      if (t.startsWith('devil_advocate:А:')) return [draft(3, 'Зритель спросит: зачем он пришёл?'), draft(5, 'Зритель спросит: откуда ключ?'), draft(7, 'Зритель спросит: почему молчит?')];
      // The same hole quoted from another sentence of the episode: not caught by the exact key, caught by the editor.
      if (t.startsWith('devil_advocate:Б:')) return [draft(3, 'Зритель спросит: с какой целью он явился?', ep(3).heroine_action)];
      if (t.startsWith('confirm:')) {
        const ids = [...(req.system ?? '').matchAll(/^(\S+) \| серия (\d+)/gmu)].map((m) => ({ id: m[1]!, ep: Number(m[2]) }));
        seen.push(...ids.map((x) => x.id));
        const a3 = ids.find((x) => x.ep === 3)!;
        const dup = ids.filter((x) => x.ep === 3)[1];
        const five = ids.find((x) => x.ep === 5);
        return [
          ...(five ? [{ id: five.id, verdict: 'drop', reason: 'ключ объяснён в 4-й' }] : []),
          ...(dup ? [{ id: dup.id, verdict: 'duplicate', duplicate_of: a3.id, reason: 'то же' }] : []),
        ];
      }
      return [];
    };
    const res = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { personas: false, realism: false });
    expect(seen.length).toBeGreaterThanOrEqual(3);
    expect(res.confirmDropped).toBe(1);
    expect(res.confirmMerged).toBe(1);
    expect(res.findings.map((f) => f.episode).sort()).toEqual([3, 7]);
    expect(res.findings.find((f) => f.episode === 3)?.category).toEqual(expect.arrayContaining(['А', 'Б']));
    seen = [];
    const off = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { personas: false, realism: false, confirm: false });
    expect(seen).toEqual([]);
    expect(off.findings).toHaveLength(4);
  });
});
