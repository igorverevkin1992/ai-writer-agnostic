import { beforeEach, describe, expect, it } from 'vitest';
import { openDb } from '../db/client.ts';
import { checkQueue } from '../db/schema.ts';
import { loadGolden } from '../fixtures.ts';
import { EpisodeCard } from '../schemas/episodeCard.ts';
import { sampleCard } from '../schemas/samples.ts';
import { ProjectMemory } from './store.ts';

let mem: ProjectMemory;
let db: ReturnType<typeof openDb>;
const producer = { author: 'producer' as const, note: 'Правка продюсера' };

beforeEach(() => {
  db = openDb(':memory:');
  mem = new ProjectMemory(db, 'p1');
  const { bible, plan } = structuredClone(loadGolden());
  // The spec example: father died "10 years ago"; in episode 8 Liza remembers seeing him 15 years ago.
  bible.characters.push({ ...bible.characters[0]!, name: 'Отец Лизы', birth_year: 1965, regular: false });
  bible.timeline.events.push(
    { id: 'e_father_died', year: 2016, kind: 'death', text: 'Смерть отца Лизы', participants: ['Отец Лизы'], ages: {}, after: [] },
    { id: 'e_saw_father', year: 2011, kind: 'other', text: 'Лиза видит отца у театра', participants: ['Лиза', 'Отец Лизы'], ages: {}, after: [] },
  );
  mem.importBible(bible);
  mem.importPlan(plan);
  mem.link('outline', 8, ['e_saw_father']);
  for (const ep of [8, 31]) mem.saveCard(EpisodeCard.parse({ ...sampleCard, ep, acts_on: ep === 31 ? [{ who: 'Лиза', fact: 'f_herman_initiator' }] : [] }), ep === 8 ? ['e_saw_father'] : []);
});

describe('fact base', () => {
  it('rebuilds the bible from the fact base', () => {
    const bible = mem.currentBible()!;
    expect(bible.facts.map((f) => f.id)).toContain('f_herman_initiator');
    expect(bible.timeline.events.find((e) => e.id === 'e_saw_father')?.year).toBe(2011);
    expect(bible.knowledge).toContainEqual({ who: 'Лиза', fact: 'f_herman_initiator', since_ep: 30, how: 'Признание Зои' });
  });

  it('keeps every version of an artifact', () => {
    expect(mem.saveArtifact('bible', { draft: 2 })).toBe(2);
    expect(mem.latestArtifact('bible')).toEqual({ draft: 2 });
  });
});

describe('edit propagation', () => {
  it('spec example: the father now "died 20 years ago", so episode 8 turns red', () => {
    const res = mem.updateEvent('e_father_died', { year: 2006 }, producer);

    expect(res.affectedEpisodes).toEqual([]);
    expect(res.findings).toHaveLength(1);
    const f = res.findings[0]!;
    expect(f).toMatchObject({ check: 'timeline.dead_before', episode: 8, holeType: 7 });
    expect(f.quote).toBe('Отец Лизы (умер в 2006) участвует в «Лиза видит отца у театра» (2011)');
    expect(f.fixes).toEqual(['Поправить 8-ю серию', `Откатить правку №${res.revisionId}`]);
    expect(mem.openFindings().map((x) => x.episode)).toEqual([8]);
  });

  it('marks cards and scripts that rely on a changed fact as stale and queues model checks', () => {
    const res = mem.updateEvent('e_saw_father', { year: 2012 }, producer);
    expect(res.affectedEpisodes).toEqual([8]);
    expect(res.staleCards).toEqual([8]);
    expect(res.queued).toBe(1);
    expect(mem.staleEpisodes()).toEqual({ cards: [8], scripts: [] });
    expect(db.select().from(checkQueue).all()).toMatchObject([{ target: 'card', ep: 8, done: false }]);
  });

  it('re-checks knowledge in the episodes that rely on a changed fact', () => {
    const res = mem.updateFact('f_herman_initiator', { since_ep: 35 }, producer);
    expect(res.affectedEpisodes).toEqual([30, 31, 57]);
    expect(res.staleCards).toEqual([31]);
    expect(res.findings.map((f) => [f.check, f.episode])).toEqual(
      expect.arrayContaining([
        ['knowledge.fact_not_yet', 31],
        ['knowledge.before_fact', 30],
      ]),
    );
  });

  it('rolls an edit back and the finding disappears on re-check', () => {
    const edit = mem.updateEvent('e_father_died', { year: 2006 }, producer);
    const back = mem.rollback(edit.revisionId, producer);
    expect(back.findings).toEqual([]);
    expect(mem.currentBible()!.timeline.events.find((e) => e.id === 'e_father_died')?.year).toBe(2016);
  });

  it('logs the producer creative contribution', () => {
    mem.updateFact('f_twins', { text: 'Анна — сестра-близнец Веры' }, producer);
    mem.updateFact('f_twins', { text: 'Анна — старшая сестра-близнец Веры' }, { author: 'agent' });
    const log = mem.revisionsLog('producer');
    expect(log).toHaveLength(1);
    expect(log[0]).toMatchObject({ entity: 'fact', entityId: 'f_twins', author: 'producer', note: 'Правка продюсера' });
    expect(log[0]!.before).toEqual({ text: 'Анна — сестра-близнец Веры в старческом гриме', since_ep: null });
  });

  it('a saved card is fresh again', () => {
    mem.updateEvent('e_saw_father', { year: 2012 }, producer);
    mem.saveCard(EpisodeCard.parse({ ...sampleCard, ep: 8 }), ['e_saw_father']);
    expect(mem.staleEpisodes().cards).toEqual([]);
  });
});
