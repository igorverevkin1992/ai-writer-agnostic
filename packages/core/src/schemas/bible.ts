import { z } from 'zod';
import { EpisodeNumber, NonEmpty } from './common.ts';

const Year = z.int().min(1900).max(2100);

/** Character template ("hero" in SPEC.md), used for every regular character. */
export const Character = z.object({
  name: NonEmpty,
  birth_year: Year,
  look: NonEmpty,
  ghost: NonEmpty,
  lie: NonEmpty,
  truth: NonEmpty,
  want: NonEmpty,
  need: NonEmpty,
  arc_type: NonEmpty,
  mask: NonEmpty,
  hidden_power: NonEmpty,
  knows_at_start: z.array(NonEmpty).default([]),
  speech: NonEmpty,
  limits: z.array(NonEmpty).default([]),
  /** Regular (постоянный) or episodic character. Production limits count regular ones. */
  regular: z.boolean().default(true),
});
export type Character = z.infer<typeof Character>;

export const PunishmentType = z.enum(['shame', 'law', 'status', 'flight', 'allies', 'turned_ally']);

export const Villain = z
  .object({
    name: NonEmpty,
    /** 1 is the final boss; the highest rank is the weakest. Ladder size and roles come from the genre frame. */
    rank: z.int().min(1),
    /** Role id from the genre frame, e.g. "pawn" or "boss". */
    role: NonEmpty,
    threat: NonEmpty,
    /** Link to the heroine's main wound; required for rank 1. */
    link_to_ghost: z.string().trim().optional(),
    motive: NonEmpty,
    own_plan: NonEmpty,
    resources: NonEmpty,
    weakness: NonEmpty,
    on_screen_ep: EpisodeNumber,
    mask: NonEmpty,
    first_strike_ep: EpisodeNumber,
    takedown_ep: EpisodeNumber,
    punishment: z.object({ type: PunishmentType, public: z.boolean() }),
    /** Taken down by setting villains against each other. */
    via_infighting: z.boolean().default(false),
    key_to_next: NonEmpty,
    ties: z.array(z.object({ villain: NonEmpty, relation: NonEmpty })).default([]),
    knows: z.array(z.object({ fact: NonEmpty, since_ep: EpisodeNumber })).default([]),
  })
  .superRefine((v, ctx) => {
    if (v.rank === 1 && !v.link_to_ghost) {
      ctx.addIssue({
        code: 'custom',
        path: ['link_to_ghost'],
        message: 'У финального босса (ранг 1) обязательна связь с раной героини',
      });
    }
  });
export type Villain = z.infer<typeof Villain>;

export const Betrayal = z.object({
  who: NonEmpty,
  what: NonEmpty,
  why: NonEmpty,
  year: Year,
  accomplices: z.array(NonEmpty).default([]),
  who_else_knew: z.array(z.object({ name: NonEmpty, why_silent: NonEmpty })).default([]),
  /** Second of episode 1 when the heroine sees the betrayal. */
  ep1_second: z.number().min(0),
});
export type Betrayal = z.infer<typeof Betrayal>;

export const Secret = z.object({
  layer: z.int().min(1),
  truth: NonEmpty,
  revealed_ep: EpisodeNumber,
  goal_from: NonEmpty,
  goal_to: NonEmpty,
  clues: z.array(NonEmpty).default([]),
});
export type Secret = z.infer<typeof Secret>;

export const WorldRule = z.object({
  rule: NonEmpty,
  why: NonEmpty,
  cost: NonEmpty,
  cannot: NonEmpty,
  who_knows: z.array(NonEmpty).default([]),
  practical_effect: NonEmpty,
});
export type WorldRule = z.infer<typeof WorldRule>;

export const Gun = z.object({
  id: NonEmpty,
  object: NonEmpty,
  planted_ep: EpisodeNumber,
  /** May be empty in a draft; checkGuns reports guns that never fire. */
  fired_ep: EpisodeNumber.nullish(),
  metro_visible: z.boolean(),
});
export type Gun = z.infer<typeof Gun>;

export const TimelineEvent = z.object({
  id: NonEmpty,
  year: Year,
  text: NonEmpty,
  /** birth/death: the first participant is the one born or who died. */
  kind: z.enum(['birth', 'death', 'other']).default('other'),
  participants: z.array(NonEmpty).default([]),
  /** Ages stated by the story at this event, e.g. {"Лиза": 2}. Checked against birth years. */
  ages: z.record(z.string(), z.int().min(0)).default({}),
  /** Ids of events that must happen no later than this one. */
  after: z.array(NonEmpty).default([]),
});
export type TimelineEvent = z.infer<typeof TimelineEvent>;

export const Timeline = z.object({
  /** Year in which the season takes place. */
  present_year: Year.optional(),
  events: z.array(TimelineEvent),
});
export type Timeline = z.infer<typeof Timeline>;

/** A fact of the series that scenes rely on. */
export const Fact = z.object({
  id: NonEmpty,
  text: NonEmpty,
  /** First episode where the fact is true; empty means it is true before the season starts. */
  since_ep: EpisodeNumber.optional(),
});
export type Fact = z.infer<typeof Fact>;

/** Who knows which fact and from which episode (0 = before the season). */
export const KnowledgeEntry = z.object({
  who: NonEmpty,
  fact: NonEmpty,
  since_ep: z.int().min(0),
  /** How they learned it. */
  how: z.string().optional(),
});
export type KnowledgeEntry = z.infer<typeof KnowledgeEntry>;

export const Bible = z.object({
  world_rules: z.array(WorldRule),
  characters: z.array(Character).min(1),
  /** How many villains a season needs is a genre rule (frame), checked by code, not here. */
  villains: z.array(Villain),
  betrayal: Betrayal,
  secrets: z.array(Secret).min(1),
  guns: z.array(Gun),
  timeline: Timeline,
  /** Locations the season may use. Cards must pick from this list. */
  locations: z.array(NonEmpty).default([]),
  facts: z.array(Fact).default([]),
  knowledge: z.array(KnowledgeEntry).default([]),
});
export type Bible = z.infer<typeof Bible>;
