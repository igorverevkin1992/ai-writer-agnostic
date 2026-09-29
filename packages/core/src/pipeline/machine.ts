import { and, eq } from 'drizzle-orm';
import { makeFinding } from '../checks/code/finding.ts';
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

/** State of a step before a run. */
export type RunMark = StepState;

/** Check id of the «run stopped halfway» finding. */
export const INCOMPLETE_CHECK = 'pipeline.incomplete';

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
    this.assertPreviousDone(step);
    const busy = this.states().find((s) => s.status === 'checking');
    if (busy) {
      throw new PipelineError(
        busy.step === step ? `Шаг «${LABELS[step]}» уже выполняется` : `Агент ещё работает над шагом «${LABELS[busy.step]}». Дождитесь окончания`,
      );
    }
  }

  assertPreviousDone(step: StepId): void {
    const prev = STEP_IDS[STEP_IDS.indexOf(step) - 1];
    if (!prev) return;
    const p = this.get(prev);
    if (p.status !== 'approved' && p.status !== 'skipped') {
      throw new PipelineError(`Сначала утвердите или пропустите шаг «${LABELS[prev]}»`);
    }
  }

  /** Starts a run. Returns what abort() needs to restore the step if the run fails. */
  begin(step: StepId): RunMark {
    this.assertCanRun(step);
    const mark = { ...this.get(step) };
    this.setStatus(step, 'checking');
    return mark;
  }

  /** After a run: needs_fix while blocking findings are open, otherwise draft (ready for approval). */
  finish(step: StepId): StepStatus {
    const status: StepStatus = this.openBlockers(step) > 0 ? 'needs_fix' : 'draft';
    this.setStatus(step, status);
    this.resetLater(step);
    return status;
  }

  /**
   * A run failed. If it had not changed the result yet, the step goes back to where it was.
   * If it had (e.g. a new plan saved, but the audit broke), the result is not checked:
   * a blocking finding asks to run the step again, and later steps need approval again.
   */
  abort(step: StepId, mark?: RunMark, reason?: string): void {
    const now = this.get(step);
    if (mark && now.version === mark.version) {
      this.setStatus(step, mark.status === 'checking' ? 'draft' : mark.status);
      return;
    }
    if (now.version === 0) {
      this.setStatus(step, 'draft');
      return;
    }
    this.markIncomplete(step, reason);
    this.setStatus(step, 'needs_fix');
    this.resetLater(step);
  }

  /** Blocking finding «the run stopped halfway». Closed by the next complete run. */
  markIncomplete(step: StepId, reason?: string): void {
    const f = makeFinding({
      check: INCOMPLETE_CHECK,
      controller: 'structure',
      severity: 'blocker',
      quote: `Шаг «${LABELS[step]}» прервался и проверен не до конца${reason ? `: ${reason}` : ''}`,
      question: 'можно ли этому верить, если проверка не прошла?',
      fixes: ['Запустить шаг ещё раз'],
    });
    const { id, severity, episode, quote, viewerQuestion, fixes, check, controller } = f;
    this.db
      .insert(findings)
      .values({ projectId: this.projectId, id, step, controller, severity, episode: episode ?? null, quote, viewerQuestion, fixes, status: 'open', check })
      .onConflictDoUpdate({ target: [findings.projectId, findings.id], set: { status: 'open', quote, verdict: null } })
      .run();
  }

  /** A complete run closes the «stopped halfway» finding. */
  clearIncomplete(step: StepId): void {
    this.db
      .update(findings)
      .set({ status: 'resolved', verdict: 'Шаг выполнен заново' })
      .where(and(eq(findings.projectId, this.projectId), eq(findings.step, step), eq(findings.check, INCOMPLETE_CHECK), eq(findings.status, 'open')))
      .run();
  }

  hasIncomplete(step: StepId): boolean {
    return this.db
      .select()
      .from(findings)
      .where(and(eq(findings.projectId, this.projectId), eq(findings.step, step), eq(findings.check, INCOMPLETE_CHECK), eq(findings.status, 'open')))
      .all().length > 0;
  }

  approve(step: StepId): void {
    const s = this.get(step);
    if (s.version === 0) throw new PipelineError(`Шаг «${LABELS[step]}» ещё не выполнялся`);
    if (s.status === 'checking') throw new PipelineError(`Шаг «${LABELS[step]}» ещё выполняется`);
    this.assertPreviousDone(step);
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
    const s = this.get(step);
    if (s.status === 'approved') throw new PipelineError(`Шаг «${LABELS[step]}» уже утверждён`);
    if (s.status === 'checking') throw new PipelineError(`Шаг «${LABELS[step]}» ещё выполняется`);
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
  resetLater(step: StepId): void {
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

/**
 * After a restart no run is alive: steps left in «checking» failed halfway.
 * Called once when the server starts.
 */
export function recoverStaleSteps(db: Db): number {
  const stale = db.select().from(steps).where(eq(steps.status, 'checking')).all();
  for (const row of stale) {
    const pipeline = new Pipeline(db, row.projectId);
    pipeline.abort(row.step as StepId, undefined, 'приложение перезапустилось во время работы');
  }
  return stale.length;
}

export { LABELS as STEP_LABELS };
