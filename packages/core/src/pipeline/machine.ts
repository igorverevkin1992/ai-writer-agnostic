import { and, eq } from 'drizzle-orm';
import type { Db } from '../db/client.ts';
import { findings, steps } from '../db/schema.ts';
import { STEP_IDS, type StepId } from '../steps.ts';

export type StepStatus = 'draft' | 'checking' | 'needs_fix' | 'approved' | 'skipped';

export interface StepState {
  step: StepId;
  status: StepStatus;
  /** 0 until the step has produced its first result. */
  version: number;
}

export class PipelineError extends Error {
  override name = 'PipelineError';
}

const LABELS: Record<StepId, string> = {
  concept: 'Концепции',
  logline: 'Логлайн',
  bible: 'Библия',
  season_plan: 'План сезона',
  episode_cards: 'Карточки серий',
  scripts: 'Сценарии',
  polish: 'Доработка',
  export: 'Экспорт',
};

/**
 * Step state machine: draft → checking → needs_fix → approved, or skipped.
 * A step can run only when the previous one is approved or explicitly skipped by the producer.
 */
export class Pipeline {
  constructor(
    private readonly db: Db,
    readonly projectId: string,
  ) {}

  states(): StepState[] {
    const rows = this.db.select().from(steps).where(eq(steps.projectId, this.projectId)).all();
    return STEP_IDS.map((step) => {
      const row = rows.find((r) => r.step === step);
      return { step, status: (row?.status ?? 'draft') as StepStatus, version: row?.version ?? 0 };
    });
  }

  get(step: StepId): StepState {
    return this.states().find((s) => s.step === step)!;
  }

  assertCanRun(step: StepId): void {
    const i = STEP_IDS.indexOf(step);
    const prev = STEP_IDS[i - 1];
    if (prev) {
      const p = this.get(prev);
      if (p.status !== 'approved' && p.status !== 'skipped') {
        throw new PipelineError(`Сначала утвердите или пропустите шаг «${LABELS[prev]}»`);
      }
    }
    if (this.get(step).status === 'checking') throw new PipelineError(`Шаг «${LABELS[step]}» уже выполняется`);
  }

  begin(step: StepId): void {
    this.assertCanRun(step);
    this.setStatus(step, 'checking');
  }

  /** After a run: needs_fix while blocking findings are open, otherwise draft (ready for approval). */
  finish(step: StepId): StepStatus {
    const status: StepStatus = this.openBlockers(step) > 0 ? 'needs_fix' : 'draft';
    this.setStatus(step, status);
    this.resetLater(step);
    return status;
  }

  /** A run failed: back to the previous state so it can be retried. */
  abort(step: StepId): void {
    this.setStatus(step, this.get(step).version > 0 ? 'needs_fix' : 'draft');
  }

  approve(step: StepId): void {
    const s = this.get(step);
    if (s.version === 0) throw new PipelineError(`Шаг «${LABELS[step]}» ещё не выполнялся`);
    if (s.status === 'checking') throw new PipelineError(`Шаг «${LABELS[step]}» ещё выполняется`);
    const blockers = this.openBlockers(step);
    if (blockers > 0) throw new PipelineError(`Нельзя утвердить «${LABELS[step]}»: открытых блокирующих замечаний — ${blockers}`);
    this.setStatus(step, 'approved');
  }

  /** After findings are closed: needs_fix becomes draft once no blocker is open. */
  refresh(step: StepId): StepStatus {
    const s = this.get(step);
    if (s.status === 'needs_fix' && this.openBlockers(step) === 0) this.setStatus(step, 'draft');
    return this.get(step).status;
  }

  skip(step: StepId): void {
    if (this.get(step).status === 'approved') throw new PipelineError(`Шаг «${LABELS[step]}» уже утверждён`);
    this.setStatus(step, 'skipped');
  }

  openBlockers(step: StepId): number {
    return this.db
      .select()
      .from(findings)
      .where(
        and(eq(findings.projectId, this.projectId), eq(findings.step, step), eq(findings.status, 'open'), eq(findings.severity, 'blocker')),
      )
      .all().length;
  }

  /** Re-running a step makes later approved results stale: they need approval again. */
  private resetLater(step: StepId): void {
    for (const later of STEP_IDS.slice(STEP_IDS.indexOf(step) + 1)) {
      const s = this.get(later);
      if (s.status === 'approved' || s.status === 'skipped') this.setStatus(later, 'draft');
    }
  }

  private setStatus(step: StepId, status: StepStatus): void {
    this.db
      .insert(steps)
      .values({ projectId: this.projectId, step, status })
      .onConflictDoUpdate({ target: [steps.projectId, steps.step], set: { status, updatedAt: new Date() } })
      .run();
  }
}

export { LABELS as STEP_LABELS };
