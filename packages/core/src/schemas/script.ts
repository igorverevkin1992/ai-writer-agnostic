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
