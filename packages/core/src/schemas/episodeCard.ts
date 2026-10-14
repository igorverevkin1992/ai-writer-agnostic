import { z } from 'zod';
import { ActsOn, EpisodeNumber, NonEmpty } from './common.ts';

/** Beats of the episode grid: hook 0–3 s, stakes to ~15 s, escalation, reversal, cliffhanger in the last 5–10 s. */
export const BEAT_TYPES = ['HOOK', 'STAKES', 'ESCALATION', 'REVERSAL', 'CLIFFHANGER'] as const;
export const BeatType = z.enum(BEAT_TYPES);
export type BeatType = z.infer<typeof BeatType>;

export const Beat = z
  .object({
    t0: z.number().min(0),
    t1: z.number().min(0),
    type: BeatType,
    /** What happens, as one line for the frame. */
    text: NonEmpty,
    /** HOOK: visual_shock | paradox_line | in_media_res | norm_violation | continuation. */
    hook_kind: NonEmpty.optional(),
    /** ESCALATION: the new information or worsening this beat brings. */
    new_info: NonEmpty.optional(),
    /** REVERSAL / CLIFFHANGER: its kind (trap, clue_flip, antagonist_move… / a hook type from the frame). */
    kind: NonEmpty.optional(),
    /** REVERSAL: the turn is readable with the sound off. */
    mute_readable: z.boolean().optional(),
  })
  .refine((b) => b.t1 >= b.t0, { message: 'Конец бита раньше начала', path: ['t1'] });
export type Beat = z.infer<typeof Beat>;

/** How the episode links to the previous cliffhanger. */
export const LinkPrev = z.enum(['overlap', 'instant', 'false_result', 'pov_shift']);
export type LinkPrev = z.infer<typeof LinkPrev>;

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
  /** Time skeleton of the episode (written before the dialogue). Empty in genres that do not ask for it. */
  beats: z.array(Beat).optional(),
  link_prev: LinkPrev.optional(),
  /** The one question this episode leaves the viewer with (≤12 words). */
  question: z.string().trim().optional(),
  /** «Герой хочет X, но Y»: the goal in this episode and what stands in the way. */
  goal: z.string().trim().optional(),
  obstacle: z.string().trim().optional(),
  /** Audio beats with their visual doubles (the viewer may watch muted). */
  audio: z.array(z.object({ cue: NonEmpty, visual_double: NonEmpty })).optional(),
});
export type EpisodeCard = z.infer<typeof EpisodeCard>;
