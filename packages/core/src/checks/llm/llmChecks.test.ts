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
      req.task === 'devil_advocate:Б:план сезона'
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
    expect(res.calls).toBe(14);
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

  it('one hole seen from two categories is one finding with both, in the review format', async () => {
    const draft = {
      level: 'high', episodes: '1, 8', quote: golden.plan.episodes[0]!.event, viewerQuestion: 'Зритель спросит: почему?',
      whyNoticed: 'Это первая серия', fixes: ['Один', 'Два', 'Три'], agentRule: 'Проверять мотив на момент хода',
    };
    answer = (req) => (req.task === 'devil_advocate:А:план сезона' || req.task === 'devil_advocate:З:план сезона' ? [draft] : []);
    const res = await runDevilAdvocate({ llm: client(), kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, { categories: ['А', 'З'], personas: false });
    expect(res.calls).toBe(2);
    expect(res.findings).toHaveLength(1);
    expect(res.findings[0]).toMatchObject({
      category: ['А', 'З'], level: 'high', severity: 'major', episode: 1, episodes: '1, 8',
      whyNoticed: 'Это первая серия', agentRule: 'Проверять мотив на момент хода', fixes: ['Один', 'Два', 'Три'],
    });
  });
});

