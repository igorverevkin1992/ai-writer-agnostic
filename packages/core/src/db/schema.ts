import { sql } from 'drizzle-orm';
import { index, integer, real, sqliteTable, text } from 'drizzle-orm/sqlite-core';

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
