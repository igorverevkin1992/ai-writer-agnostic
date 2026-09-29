import type { Bible } from '../schemas/bible.ts';
import type { EpisodeCard } from '../schemas/episodeCard.ts';

/** How much of the bible the writer gets: 0 — all, 1 — compressed, 2 — only what this episode needs. */
export type CompressionLevel = 0 | 1 | 2;
export const MAX_COMPRESSION: CompressionLevel = 2;

/**
 * Bible for the writer. Level 1 keeps what scenes need (look, speech, masks, facts, knowledge);
 * level 2 keeps only the cast of the card and the facts it relies on.
 */
export function compressBible(b: Bible, card: EpisodeCard, level: CompressionLevel): unknown {
  if (level === 0) return b;
  const inCast = (name: string) => level === 1 || card.cast.some((c) => c.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(c.toLowerCase()));
  const facts = level === 1 ? b.facts : b.facts.filter((f) => card.acts_on.some((a) => a.fact === f.id));
  const factIds = new Set(facts.map((f) => f.id));
  return {
    characters: b.characters.filter((c) => inCast(c.name)).map((c) => ({ name: c.name, look: c.look, speech: c.speech, mask: c.mask, want: c.want })),
    villains: b.villains.filter((v) => inCast(v.name)).map((v) => ({ name: v.name, rank: v.rank, mask: v.mask, own_plan: v.own_plan, weakness: v.weakness })),
    world_rules: level === 1 ? b.world_rules.map((r) => ({ rule: r.rule, practical_effect: r.practical_effect })) : [],
    secrets: b.secrets.filter((s) => s.revealed_ep <= card.ep).map((s) => ({ truth: s.truth, revealed_ep: s.revealed_ep })),
    facts,
    knowledge: b.knowledge.filter((k) => level === 1 || (factIds.has(k.fact) && inCast(k.who))),
    locations: b.locations,
  };
}
