import { sql } from 'drizzle-orm';
import { index, integer, primaryKey, real, sqliteTable, text } from 'drizzle-orm/sqlite-core';

const createdAt = () =>
  integer('created_at', { mode: 'timestamp_ms' })
    .notNull()
    .default(sql`(unixepoch('subsec') * 1000)`);

export const projects = sqliteTable('projects', {
  id: text('id').primaryKey(),
  title: text('title').notNull(),
  /** Genre pack id from packages/kb/genres. Required when a project is created through the API. */
  genreId: text('genre_id'),
  /** Overrides budget.project_limit_usd from config/models.yaml when the producer raises it. */
  budgetLimitUsd: real('budget_limit_usd'),
  createdAt: createdAt(),
});

export const llmCalls = sqliteTable(
  'llm_calls',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    projectId: text('project_id'),
    step: text('step'),
    role: text('role').notNull(),
    provider: text('provider').notNull(),
    model: text('model').notNull(),
    inputTokens: integer('input_tokens').notNull().default(0),
    outputTokens: integer('output_tokens').notNull().default(0),
    cacheReadTokens: integer('cache_read_tokens').notNull().default(0),
    cacheWriteTokens: integer('cache_write_tokens').notNull().default(0),
    costUsd: real('cost_usd').notNull().default(0),
    /** False when the model has no price in config: cost is unknown, not zero. */
    priceKnown: integer('price_known', { mode: 'boolean' }).notNull().default(true),
    durationMs: integer('duration_ms').notNull(),
    /** ok | error | invalid_output */
    status: text('status').notNull(),
    error: text('error'),
    fallbackUsed: integer('fallback_used', { mode: 'boolean' }).notNull().default(false),
    createdAt: createdAt(),
  },
  (t) => [index('llm_calls_project_idx').on(t.projectId)],
);

export type LlmCallRow = typeof llmCalls.$inferSelect;
export type NewLlmCall = typeof llmCalls.$inferInsert;

// ---- Project memory (M3) ----

const json = <T>(name: string) => text(name, { mode: 'json' }).$type<T>();

/** Status and version of each pipeline step. */
export const steps = sqliteTable(
  'steps',
  {
    projectId: text('project_id').notNull(),
    step: text('step').notNull(),
    /** draft | checking | needs_fix | approved | skipped */
    status: text('status').notNull().default('draft'),
    version: integer('version').notNull().default(0),
    updatedAt: createdAt(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.step] })],
);

/** Result of a step as JSON, every version kept. */
export const artifacts = sqliteTable(
  'artifacts',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    projectId: text('project_id').notNull(),
    step: text('step').notNull(),
    version: integer('version').notNull(),
    data: json<unknown>('data').notNull(),
    createdAt: createdAt(),
  },
  (t) => [index('artifacts_step_idx').on(t.projectId, t.step, t.version)],
);

export const characters = sqliteTable(
  'characters',
  {
    projectId: text('project_id').notNull(),
    name: text('name').notNull(),
    data: json<unknown>('data').notNull(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.name] })],
);

/** Facts of the series: text, first episode where it holds, source. */
export const facts = sqliteTable(
  'facts',
  {
    projectId: text('project_id').notNull(),
    id: text('id').notNull(),
    text: text('text').notNull(),
    sinceEp: integer('since_ep'),
    /** bible | producer | agent */
    source: text('source').notNull().default('bible'),
    updatedAt: createdAt(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.id] })],
);

/** Dated events for chronology and ages. */
export const events = sqliteTable(
  'events',
  {
    projectId: text('project_id').notNull(),
    id: text('id').notNull(),
    year: integer('year').notNull(),
    text: text('text').notNull(),
    kind: text('kind').notNull().default('other'),
    participants: json<string[]>('participants').notNull(),
    ages: json<Record<string, number>>('ages').notNull(),
    after: json<string[]>('after').notNull(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.id] })],
);

/** Who knows which fact, from which episode. */
export const knowledge = sqliteTable(
  'knowledge',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    projectId: text('project_id').notNull(),
    who: text('who').notNull(),
    factId: text('fact_id').notNull(),
    sinceEp: integer('since_ep').notNull(),
    how: text('how'),
  },
  (t) => [index('knowledge_project_idx').on(t.projectId)],
);

export const worldRules = sqliteTable('world_rules', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  projectId: text('project_id').notNull(),
  data: json<unknown>('data').notNull(),
});

export const guns = sqliteTable(
  'guns',
  {
    projectId: text('project_id').notNull(),
    id: text('id').notNull(),
    data: json<unknown>('data').notNull(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.id] })],
);

const episodeDoc = (name: string) =>
  sqliteTable(
    name,
    {
      projectId: text('project_id').notNull(),
      ep: integer('ep').notNull(),
      data: json<unknown>('data').notNull(),
      version: integer('version').notNull().default(1),
      /** A fact it depends on has changed: needs a re-check. */
      stale: integer('stale', { mode: 'boolean' }).notNull().default(false),
      updatedAt: createdAt(),
    },
    (t) => [primaryKey({ columns: [t.projectId, t.ep] })],
  );

export const episodeCards = episodeDoc('episode_cards');
export const scripts = episodeDoc('scripts');

/** Which episode documents rely on which facts or events. */
export const sceneFactLinks = sqliteTable(
  'scene_fact_links',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    projectId: text('project_id').notNull(),
    ep: integer('ep').notNull(),
    /** outline | card | script */
    target: text('target').notNull(),
    /** Fact id or timeline event id. */
    factId: text('fact_id').notNull(),
  },
  (t) => [index('links_fact_idx').on(t.projectId, t.factId)],
);

export const findings = sqliteTable(
  'findings',
  {
    projectId: text('project_id').notNull(),
    id: text('id').notNull(),
    step: text('step'),
    controller: text('controller').notNull(),
    holeType: integer('hole_type'),
    severity: text('severity').notNull(),
    episode: integer('episode'),
    quote: text('quote').notNull(),
    viewerQuestion: text('viewer_question').notNull(),
    fixes: json<string[]>('fixes').notNull(),
    status: text('status').notNull().default('open'),
    resolutionFactId: text('resolution_fact_id'),
    rule: text('rule'),
    check: text('check'),
    /** Verdict of the cross-family judge, used in quality evaluation. */
    verdict: text('verdict'),
    /** Hole review fields: categories А–П, level, episodes, why noticed, rule for the agent, doubt. */
    category: json<string[]>('category'),
    level: text('level'),
    episodes: text('episodes'),
    whyNoticed: text('why_noticed'),
    agentRule: text('agent_rule'),
    doubt: integer('doubt', { mode: 'boolean' }),
    createdAt: createdAt(),
  },
  (t) => [primaryKey({ columns: [t.projectId, t.id] })],
);

/** Edit log; producer entries double as the log of the producer's creative contribution. */
export const revisions = sqliteTable(
  'revisions',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    projectId: text('project_id').notNull(),
    entity: text('entity').notNull(),
    entityId: text('entity_id').notNull(),
    before: json<unknown>('before'),
    after: json<unknown>('after'),
    /** producer | agent */
    author: text('author').notNull(),
    note: text('note'),
    createdAt: createdAt(),
  },
  (t) => [index('revisions_project_idx').on(t.projectId)],
);

/** Model checks waiting to run after an edit. */
export const checkQueue = sqliteTable('check_queue', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  projectId: text('project_id').notNull(),
  /** card | script */
  target: text('target').notNull(),
  ep: integer('ep').notNull(),
  reason: text('reason').notNull(),
  done: integer('done', { mode: 'boolean' }).notNull().default(false),
  createdAt: createdAt(),
});
