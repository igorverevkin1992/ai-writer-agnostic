import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { openDb } from '../../db/client.ts';
import { loadGolden } from '../../fixtures.ts';
import { LlmClient } from '../../providers/llm.ts';
import { FakeProvider, testConfig } from '../../providers/testing.ts';
import type { LlmRequest } from '../../providers/types.ts';
import type { Finding } from '../../schemas/finding.ts';
import { reviewTables, runReviewSummary } from './reviewSummary.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const golden = loadGolden();

const client = (answer: (req: LlmRequest) => string) =>
  new LlmClient({
    config: testConfig(),
    db: openDb(':memory:'),
    env: {},
    providers: { anthropic: new FakeProvider('anthropic', [answer]), google: new FakeProvider('google', [answer]) },
  });

const hole: Finding = {
  id: 'h1', controller: 'logic', severity: 'blocker', level: 'critical', category: ['А'], episode: 3, episodes: '3, 10',
  quote: 'q', viewerQuestion: 'Зритель спросит: почему не полиция?', fixes: ['a'], status: 'open',
};

describe('review summary', () => {
  it('code builds the knowledge, setups and ages tables from the bible', () => {
    const t = reviewTables(golden.bible);
    expect(t.knowledge.length).toBeGreaterThan(0);
    expect(t.knowledge.map((r) => r.since)).toEqual([...t.knowledge.map((r) => r.since)].sort((a, b) => a - b));
    // Fact ids become their text.
    const ids = new Set(golden.bible.facts.map((f) => f.id));
    expect(t.knowledge.some((r) => ids.has(r.fact))).toBe(false);
    expect(t.guns).toHaveLength(golden.bible.guns.length);
    const withAge = t.ages.flatMap((r) => r.ages).filter((a) => a.byBirthYear !== undefined);
    expect(withAge.length).toBeGreaterThan(0);
  });

  it('the critic writes the verdict from the findings; the reviewer is from another family', async () => {
    const seen: LlmRequest[] = [];
    const llm = client((req) => {
      seen.push(req);
      return JSON.stringify({ verdict: 'Почти готово.', dangers: [{ where: 'Серия 3', why: 'Полиция' }], legal: ['Рейтинг'] });
    });
    const res = await runReviewSummary({ llm, kb, kit }, { bible: golden.bible, plan: golden.plan, authorProvider: 'anthropic' }, [hole]);
    expect(res.summary?.dangers[0]?.where).toBe('Серия 3');
    expect(res.providers).toEqual(['google']);
    expect(seen[0]?.system).toContain('critical [А], серии 3, 10: Зритель спросит: почему не полиция?');
  });

  it('a failed call keeps the tables', async () => {
    const res = await runReviewSummary({ llm: client(() => 'не JSON'), kb, kit }, { bible: golden.bible, authorProvider: 'anthropic' }, [hole]);
    expect(res.summary).toBeUndefined();
    expect(res.tables.guns.length).toBe(golden.bible.guns.length);
  });
});
