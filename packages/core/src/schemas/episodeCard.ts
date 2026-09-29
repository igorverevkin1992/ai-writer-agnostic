import { z } from 'zod';
import { EpisodeNumber, NonEmpty } from './common.ts';

/** Episode card ("episode" template in SPEC.md). */
export const EpisodeCard = z.object({
  ep: EpisodeNumber,
  duration_s: z.int().positive(),
  hook_0_5s: NonEmpty,
  event: NonEmpty,
  twist: NonEmpty,
  emotions: z.array(NonEmpty).min(1),
  punchline: NonEmpty,
  cliffhanger: NonEmpty,
  cast: z.array(NonEmpty).min(1),
  location: NonEmpty,
  sound: NonEmpty,
  metro_frame: z.object({ face: NonEmpty, action: NonEmpty, object: NonEmpty }),
  knowledge: z.object({ viewer: NonEmpty, heroine: NonEmpty, villain: NonEmpty }),
});
export type EpisodeCard = z.infer<typeof EpisodeCard>;
