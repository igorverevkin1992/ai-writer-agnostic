import type { GenreKit } from '@aiw/kb';

/** The script linter's limits as the writer must hear them (the same numbers the `script_linter` check uses). */
export interface LinterLimits {
  talking_heads_s: number;
  voiceover_s: number;
  max_faces: number;
  max_wide_shots: number;
  max_wide_s: number;
  max_sound_hits: number;
  overlap_s: number;
  overlap_after_paywall_s: number;
  /** Speakers per episode (the linter's `speakers_episode`), by default the production limit per scene. */
  max_speakers_episode: number;
}

/**
 * Defaults of the `script_linter` code check (checks/code/runner.ts). A genre rule may override any
 * of them in `check.run[].params`; the writer's prompt then states the overridden numbers.
 */
const DEFAULTS = {
  talking_heads_s: 10,
  voiceover_s: 8,
  max_faces: 2,
  max_wide_shots: 1,
  max_wide_s: 3,
  max_sound_hits: 1,
  overlap_s: 5,
  overlap_after_paywall_s: 3,
} as const;

/** Linter limits of a genre: the rules' `script_linter` params over the check's defaults. */
export function linterLimits(kit: GenreKit): LinterLimits {
  const params: Record<string, unknown> = {};
  for (const rule of kit.rules.rules) {
    for (const run of rule.check.run) if (run.fn === 'script_linter') Object.assign(params, run.params);
  }
  const num = (key: keyof typeof DEFAULTS) => (typeof params[key] === 'number' ? (params[key] as number) : DEFAULTS[key]);
  return {
    talking_heads_s: num('talking_heads_s'),
    voiceover_s: num('voiceover_s'),
    max_faces: num('max_faces'),
    max_wide_shots: num('max_wide_shots'),
    max_wide_s: num('max_wide_s'),
    max_sound_hits: num('max_sound_hits'),
    overlap_s: num('overlap_s'),
    overlap_after_paywall_s: num('overlap_after_paywall_s'),
    max_speakers_episode: typeof params.max_speakers === 'number' ? params.max_speakers : kit.production.limits.max_speakers_per_scene,
  };
}
