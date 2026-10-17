import { z } from 'zod';
import { Fact, KnowledgeEntry } from './bible.ts';
import { EpisodeNumber, NonEmpty } from './common.ts';

/** Where an object of the story is after an episode: who holds it, since when. */
export const ObjectState = z.object({
  object: NonEmpty,
  holder: NonEmpty,
  since_ep: EpisodeNumber,
  note: z.string().trim().optional(),
});
export type ObjectState = z.infer<typeof ObjectState>;

/**
 * What the helper extracts from one written script for the continuity ledger: new facts the scenes
 * rely on, who learned what in this episode, and where the objects are now.
 */
export const ContinuityExtract = z.object({
  facts: z.array(Fact).default([]),
  knowledge: z.array(KnowledgeEntry).default([]),
  states: z.array(ObjectState).default([]),
});
export type ContinuityExtract = z.infer<typeof ContinuityExtract>;

/** One line of the ledger, tied to the episode it comes from. */
export interface LedgerEntry {
  ep: number;
  line: string;
}

/** The ledger lines of one episode, in the order the writer reads them: facts, knowledge, objects. */
export function ledgerLines(ep: number, x: ContinuityExtract): LedgerEntry[] {
  return [
    ...x.facts.map((f) => `Серия ${ep}: факт ${f.id} — ${f.text}`),
    ...x.knowledge.map((k) => `Серия ${ep}: ${k.who} знает ${k.fact}${k.how ? ` (${k.how})` : ''}`),
    ...x.states.map((s) => `Серия ${ep}: ${s.object} — у ${s.holder}${s.note ? ` (${s.note})` : ''}`),
  ].map((line) => ({ ep, line }));
}
