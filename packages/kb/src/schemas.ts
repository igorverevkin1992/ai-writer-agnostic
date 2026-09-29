import { z } from 'zod';

z.config(z.locales.ru());

const Text = z.string().trim().min(1);
const Stub = z.boolean().default(false);

/** Episode position in the frame: exact number, "<=N" or an inclusive range [a, b]. */
export const EpisodeSpec = z.union([
  z.int().min(1),
  z.string().regex(/^<=\d+$/, 'Ожидалось число, строка вида "<=3" или диапазон [a, b]'),
  z
    .tuple([z.int().min(1), z.int().min(1)])
    .refine(([a, b]) => a <= b, 'Начало диапазона больше конца'),
]);
export type EpisodeSpec = z.infer<typeof EpisodeSpec>;

/** Resolves an EpisodeSpec to an inclusive range of episodes. */
export function episodeRange(spec: EpisodeSpec): { min: number; max: number } {
  if (typeof spec === 'number') return { min: spec, max: spec };
  if (typeof spec === 'string') return { min: 1, max: Number(spec.slice(2)) };
  return { min: spec[0], max: spec[1] };
}

export const Severity = z.enum(['blocker', 'major', 'minor']);

export const Rule = z.strictObject({
  id: z.string().regex(/^R\d{2}$/, 'Номер правила — вида R05'),
  module: Text,
  text: Text,
  check: z.discriminatedUnion('type', [
    z.strictObject({ type: z.literal('code'), condition: Text }),
    z.strictObject({ type: z.literal('llm'), question: Text }),
  ]),
  severity: Severity,
  checklist: z.strictObject({ item: Text, points: z.int().min(1) }).optional(),
  fix_hints: z.array(Text).default([]),
});
export type Rule = z.infer<typeof Rule>;

export const RulesFile = z.strictObject({
  module: Text,
  title: Text,
  stub: Stub,
  rules: z.array(Rule).min(1),
});
export type RulesFile = z.infer<typeof RulesFile>;

const RANKS = ['1', '2', '3', '4', '5'];
const byRank = <T extends z.ZodType>(value: T) =>
  z
    .record(z.string().regex(/^[1-5]$/, 'Ранг злодея — число от 1 до 5'), value)
    .refine((r) => RANKS.every((k) => k in r), {
      message: `Нужны все пять рангов: ${RANKS.join(', ')}`,
    });

export const SeasonFrame = z.strictObject({
  season_frame: z.strictObject({
    episodes: z.int().min(1),
    free: z.int().min(0),
    duration_s: z.strictObject({ min: z.int().min(1), max: z.int().min(1), target: z.int().min(1) }),
    anchors: z.record(Text, EpisodeSpec),
    villains: z.strictObject({
      count: z.int().min(1),
      boss_on_screen_by: z.int().min(1),
      all_on_screen_by: z.int().min(1),
      on_screen_by: byRank(z.int().min(1)),
      takedowns: byRank(EpisodeSpec),
      counterstrike_within: z.int().min(1),
      max_turned_allies: z.int().min(0),
      turned_ally_ranks: z.array(z.int().min(1).max(5)),
      public_and_legal: z.array(z.int().min(1).max(5)),
    }),
    rhythm: z.strictObject({
      response_within: z.int().min(1),
      max_suffering_run: z.int().min(1),
      max_same_hook_run: z.int().min(1),
      emotions_per_episode: z.tuple([z.int().min(1), z.int().min(1)]),
      max_fall_length: z.int().min(1),
    }),
    blocks: z.array(z.tuple([z.int().min(1), z.int().min(1)])).min(1),
    tolerance: z.int().min(0),
  }),
});
export type SeasonFrame = z.infer<typeof SeasonFrame>['season_frame'];

export const Checklist = z.strictObject({
  stub: Stub,
  total: z.int().min(1),
  pass: z.int().min(1),
  items: z
    .array(
      z.strictObject({
        id: Text,
        text: Text,
        points: z.int().min(1),
        rule: z.string().optional(),
      }),
    )
    .min(1),
});
export type Checklist = z.infer<typeof Checklist>;

export const Method = z.strictObject({
  id: Text,
  name: Text,
  author: Text,
  stub: Stub,
  summary: Text,
  principles: z.array(z.strictObject({ id: Text, text: Text })),
});
export type Method = z.infer<typeof Method>;

export const Glossary = z.strictObject({
  stub: Stub,
  terms: z.array(z.strictObject({ term: Text, definition: Text })).min(1),
});
export type Glossary = z.infer<typeof Glossary>;

export const LegalConstraints = z.strictObject({
  age_rating: Text,
  principles: z.array(z.strictObject({ id: Text, text: Text })).min(1),
  markers: z.array(
    z.strictObject({ category: Text, principle: Text, words: z.array(Text).min(1) }),
  ),
});
export type LegalConstraints = z.infer<typeof LegalConstraints>;

export const ProductionConstraints = z.strictObject({
  limits: z.strictObject({
    main_locations: z.int().min(1),
    max_locations: z.int().min(1),
    max_regular_characters: z.int().min(1),
    max_speakers_per_scene: z.int().min(1),
    practical_effects_only: z.boolean(),
    crowds_allowed: z.boolean(),
  }),
  script_metrics: z.strictObject({
    line_chars_per_minute: z.tuple([z.int().min(1), z.int().min(1)]),
    max_line_words: z.int().min(1),
    max_overlay_words: z.int().min(1),
    duration_tolerance: z.number().min(0).max(1),
  }),
  metro: z.strictObject({
    reveal_readable_s: z.number().min(0),
    frame: z.array(Text).min(1),
  }),
});
export type ProductionConstraints = z.infer<typeof ProductionConstraints>;

export const HoleCatalog = z.strictObject({
  holes: z
    .array(z.strictObject({ id: z.int().min(1).max(11), name: Text, questions: z.array(Text).min(1) }))
    .length(11, 'В каталоге должно быть ровно 11 типов дыр'),
});
export type HoleCatalog = z.infer<typeof HoleCatalog>;

export const Personas = z.strictObject({
  stub: Stub,
  personas: z
    .array(
      z.strictObject({
        id: Text,
        name: Text,
        age: z.int().min(1).optional(),
        focus: Text,
      }),
    )
    .length(5, 'Персон зрителей должно быть ровно пять'),
});
export type Personas = z.infer<typeof Personas>;

export const Case = z.strictObject({
  id: Text,
  title: Text,
  stub: Stub,
  source: Text,
  summary: Text,
  beats: z.array(z.strictObject({ ep: z.int().min(1), text: Text })).default([]),
  lessons: z.array(Text).default([]),
});
export type Case = z.infer<typeof Case>;
