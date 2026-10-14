import { z } from 'zod';
import { EpisodeNumber, NonEmpty } from './common.ts';

export const ScriptBlockKind = z.enum(['scene', 'sound', 'overlay', 'insert', 'silence', 'line']);

export const ScriptBlock = z
  .object({
    t0: z.number().min(0),
    t1: z.number().min(0),
    kind: ScriptBlockKind,
    speaker: NonEmpty.optional(),
    /** How the line is delivered, e.g. "шёпотом". Only for kind "line". */
    parenthetical: NonEmpty.optional(),
    text: NonEmpty,
    /** Beat of the episode grid this block belongs to. */
    beat: z.enum(['HOOK', 'STAKES', 'ESCALATION', 'REVERSAL', 'CLIFFHANGER']).optional(),
    /** scene: shot size; a wide shot is allowed once per episode and ≤3 s. */
    shot: z.enum(['detail', 'close', 'medium', 'wide']).optional(),
    /** scene: faces in frame; more than two need `in_frame`. */
    faces: z.int().min(0).optional(),
    in_frame: z.array(NonEmpty).optional(),
    /** scene: a flashback (forbidden in the first episode before the conflict). */
    flashback: z.boolean().optional(),
    /** scene: the first seconds repeat the previous cliffhanger (overlap link). */
    overlap: z.boolean().optional(),
    /** sound: a plot sound (needs a visual double) and a sound hit (at most one per episode). */
    plot: z.boolean().optional(),
    hit: z.boolean().optional(),
    visual_double: NonEmpty.optional(),
    /** line: voice-over (≤8 s per episode). */
    voiceover: z.boolean().optional(),
  })
  .superRefine((b, ctx) => {
    if (b.t1 < b.t0) {
      ctx.addIssue({ code: 'custom', path: ['t1'], message: 'Конец блока раньше начала' });
    }
    if (b.kind === 'line' && !b.speaker) {
      ctx.addIssue({ code: 'custom', path: ['speaker'], message: 'У реплики должен быть говорящий' });
    }
    if (b.kind !== 'line' && (b.speaker || b.parenthetical)) {
      ctx.addIssue({ code: 'custom', path: ['speaker'], message: 'Говорящий бывает только у реплики' });
    }
  });
export type ScriptBlock = z.infer<typeof ScriptBlock>;

export const Script = z
  .object({
    ep: EpisodeNumber,
    title: NonEmpty,
    duration_s: z.int().positive(),
    blocks: z.array(ScriptBlock).min(1),
  })
  .superRefine((s, ctx) => {
    s.blocks.forEach((b, i) => {
      const prev = s.blocks[i - 1];
      if (prev && b.t0 < prev.t0) {
        ctx.addIssue({ code: 'custom', path: ['blocks', i, 't0'], message: 'Блоки должны идти по порядку времени' });
      }
    });
  });
export type Script = z.infer<typeof Script>;
