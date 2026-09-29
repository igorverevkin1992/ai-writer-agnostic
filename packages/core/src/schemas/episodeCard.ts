import { z } from 'zod';
import { ActsOn, EpisodeNumber, NonEmpty } from './common.ts';

/** Episode card ("episode" template in SPEC.md). */
export const EpisodeCard = z.object({
  ep: EpisodeNumber,
  duration_s: z.int().positive(),
  hook_0_5s: NonEmpty,
  event: NonEmpty,
  twist: NonEmpty,
  emotions: z.array(NonEmpty).min(1),
  /** What the heroine does in this episode. Required by the genre rules, checked in every card. */
  heroine_action: NonEmpty,
  punchline: NonEmpty,
  cliffhanger: NonEmpty,
  cast: z.array(NonEmpty).min(1),
  location: NonEmpty,
  sound: NonEmpty,
  metro_frame: z.object({ face: NonEmpty, action: NonEmpty, object: NonEmpty }),
  knowledge: z.object({ viewer: NonEmpty, heroine: NonEmpty, villain: NonEmpty }),
  acts_on: z.array(ActsOn).default([]),
});
export type EpisodeCard = z.infer<typeof EpisodeCard>;
