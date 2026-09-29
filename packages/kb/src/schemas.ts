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

/** One code check a rule runs: function id, optional filter by finding code, optional params. */
export const CheckRun = z.strictObject({
  fn: z.string().regex(/^[a-z_]+$/, 'Имя проверки — латиница и _'),
  /** Keep only findings whose code starts with one of these, e.g. "anchor.mask". */
  only: z.array(Text).optional(),
  params: z.record(z.string(), z.unknown()).default({}),
});
export type CheckRun = z.infer<typeof CheckRun>;

export const Rule = z.strictObject({
  id: z.string().regex(/^R\d{2}$/, 'Номер правила — вида R05'),
  module: Text,
  text: Text,
  /** code: what code checks (checks/code); llm: what a cross-family judge decides, with a quote. */
  check: z
    .strictObject({
      code: Text.optional(),
      llm: Text.optional(),
      /** Code checks (packages/core/checks/code) that enforce the code part of this rule. */
      run: z.array(CheckRun).default([]),
    })
    .refine((c) => c.code || c.llm, 'Нужна хотя бы одна проверка: code или llm'),
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

/** A map keyed by villain rank ("1".."count"). Completeness is checked against count in the loader. */
const byRank = <T extends z.ZodType>(value: T) =>
  z.record(z.string().regex(/^[1-9]\d*$/, 'Ранг злодея — целое число от 1'), value);

export const SeasonFrame = z.strictObject({
  season_frame: z.strictObject({
    episodes: z.int().min(1),
    free: z.int().min(0),
    duration_s: z.strictObject({ min: z.int().min(1), max: z.int().min(1), target: z.int().min(1) }),
    anchors: z.record(Text, EpisodeSpec),
    /** Human names of anchors for screens and exports. */
    anchor_labels: z.record(Text, Text).default({}),
    /** Optional: genres without a villain ladder omit it. */
    villains: z
      .strictObject({
      count: z.int().min(1),
      roles: byRank(Text),
      boss_on_screen_by: z.int().min(1),
      all_on_screen_by: z.int().min(1),
      on_screen_by: byRank(z.int().min(1)),
      takedowns: byRank(EpisodeSpec),
      counterstrike_within: z.int().min(1),
      max_turned_allies: z.int().min(0),
      turned_ally_ranks: z.array(z.int().min(1)),
      public_and_legal: z.array(z.int().min(1)),
      })
      .optional(),
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
        /** Only these code-check findings of the rule count for this item, e.g. "season_frame.anchor.fall". */
        only: z.array(Text).optional(),
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
    .min(1),
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

/** Genre pack: which knowledge base files apply to projects of this genre and format. */
export const Genre = z.strictObject({
  id: z.string().regex(/^[a-z0-9_]+$/, 'id — латиница, цифры и _'),
  title: Text,
  platform: Text.optional(),
  rules: Text,
  frame: Text,
  checklist: Text,
  personas: Text,
  constraints: z.strictObject({ legal: Text, production: Text }),
});
export type Genre = z.infer<typeof Genre>;
