import { eq, sql } from 'drizzle-orm';
import type { Db } from '../db/client.ts';
import { llmCalls, projects } from '../db/schema.ts';
import type { ModelsConfig } from './config.ts';
import { BudgetExceededError } from './errors.ts';

export interface BudgetStatus {
  spentUsd: number;
  limitUsd: number;
  /** Spent at least warn_at of the limit. */
  warning: boolean;
  exceeded: boolean;
}

export function projectSpent(db: Db, projectId: string): number {
  const row = db
    .select({ total: sql<number>`coalesce(sum(${llmCalls.costUsd}), 0)` })
    .from(llmCalls)
    .where(eq(llmCalls.projectId, projectId))
    .get();
  return row?.total ?? 0;
}

export function projectLimit(db: Db, config: ModelsConfig, projectId: string): number {
  const row = db.select({ limit: projects.budgetLimitUsd }).from(projects).where(eq(projects.id, projectId)).get();
  return row?.limit ?? config.budget.project_limit_usd;
}

export function budgetStatus(db: Db, config: ModelsConfig, projectId: string): BudgetStatus {
  const spentUsd = projectSpent(db, projectId);
  const limitUsd = projectLimit(db, config, projectId);
  return {
    spentUsd,
    limitUsd,
    warning: spentUsd >= limitUsd * config.budget.warn_at,
    exceeded: spentUsd >= limitUsd,
  };
}

/** Stops work once the project budget is used up: the producer has to decide. */
export function assertBudget(db: Db, config: ModelsConfig, projectId: string): BudgetStatus {
  const status = budgetStatus(db, config, projectId);
  if (status.exceeded) throw new BudgetExceededError(status.spentUsd, status.limitUsd);
  return status;
}

export interface CostSummary {
  totalUsd: number;
  limitUsd: number;
  calls: number;
  byRole: { role: string; calls: number; costUsd: number }[];
  byStep: { step: string; calls: number; costUsd: number }[];
  unknownPriceCalls: number;
}

export function costSummary(db: Db, config: ModelsConfig, projectId: string): CostSummary {
  const group = (column: typeof llmCalls.role | typeof llmCalls.step) =>
    db
      .select({
        key: sql<string>`coalesce(${column}, '—')`,
        calls: sql<number>`count(*)`,
        costUsd: sql<number>`coalesce(sum(${llmCalls.costUsd}), 0)`,
      })
      .from(llmCalls)
      .where(eq(llmCalls.projectId, projectId))
      .groupBy(column)
      .all();
  const byRole = group(llmCalls.role).map(({ key, ...r }) => ({ role: key, ...r }));
  const byStep = group(llmCalls.step).map(({ key, ...r }) => ({ step: key, ...r }));
  const unknown = db
    .select({ n: sql<number>`count(*)` })
    .from(llmCalls)
    .where(sql`${llmCalls.projectId} = ${projectId} and ${llmCalls.priceKnown} = 0`)
    .get();
  return {
    totalUsd: projectSpent(db, projectId),
    limitUsd: projectLimit(db, config, projectId),
    calls: byRole.reduce((n, r) => n + r.calls, 0),
    byRole,
    byStep,
    unknownPriceCalls: unknown?.n ?? 0,
  };
}
