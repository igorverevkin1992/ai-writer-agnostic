import { z } from 'zod';
import { EpisodeNumber, NonEmpty } from './common.ts';

export const Mood = z.enum(['suffering', 'kaif', 'neutral']);

/** One line of the season plan. Frame, rhythm and ladder are checked by code (M3). */
export const EpisodeOutline = z.object({
  ep: EpisodeNumber,
  title: NonEmpty,
  /** The single event of the episode. */
  event: NonEmpty,
  /** The new question the episode leaves the viewer with. */
  question: NonEmpty,
  emotions: z.array(NonEmpty).min(1),
  mood: Mood,
  /** Anchor ids from packages/kb/frames/season60.yaml, e.g. "paywall_hook". */
  anchors: z.array(NonEmpty).default([]),
  strike_by_villain: z.boolean().default(false),
  strike_by_heroine: z.boolean().default(false),
  /** Rank of the villain taken down in this episode, if any. */
  takedown_rank: z.int().min(1).max(5).optional(),
  /** Ranks of villains who appear on screen for the first time. */
  villains_introduced: z.array(z.int().min(1).max(5)).default([]),
  hook_type: NonEmpty,
  cliffhanger: NonEmpty,
});
export type EpisodeOutline = z.infer<typeof EpisodeOutline>;

/** An anchor moved by ±1 episode must carry the author's explanation. */
export const FrameDeviation = z.object({
  anchor: NonEmpty,
  ep: EpisodeNumber,
  reason: NonEmpty,
});
export type FrameDeviation = z.infer<typeof FrameDeviation>;

export const SeasonPlan = z
  .object({
    episodes: z.array(EpisodeOutline).min(1),
    deviations: z.array(FrameDeviation).default([]),
  })
  .superRefine((plan, ctx) => {
    const seen = new Set<number>();
    plan.episodes.forEach((e, i) => {
      if (seen.has(e.ep)) {
        ctx.addIssue({ code: 'custom', path: ['episodes', i, 'ep'], message: `Серия ${e.ep} встречается дважды` });
      }
      seen.add(e.ep);
    });
  });
export type SeasonPlan = z.infer<typeof SeasonPlan>;
