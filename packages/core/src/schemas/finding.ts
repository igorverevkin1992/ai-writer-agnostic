import { z } from 'zod';
import { EpisodeNumber, NonEmpty } from './common.ts';

export const Controller = z.enum(['logic', 'genre', 'structure', 'consistency', 'production', 'metro', 'legal']);
export const Severity = z.enum(['blocker', 'major', 'minor']);
export const FindingStatus = z.enum(['open', 'resolved', 'dismissed']);

export const Finding = z
  .object({
    id: NonEmpty,
    controller: Controller,
    holeType: z.int().min(1).max(11).optional(),
    severity: Severity,
    episode: EpisodeNumber.optional(),
    /** Mandatory: a finding without a quote is discarded. */
    quote: NonEmpty,
    viewerQuestion: NonEmpty,
    fixes: z.array(NonEmpty).min(1).max(2),
    status: FindingStatus,
    resolutionFactId: NonEmpty.optional(),
    /** Genre rule this finding breaks, e.g. "R08". */
    rule: NonEmpty.optional(),
    /** Code check that produced it, e.g. "season_frame.anchor". */
    check: NonEmpty.optional(),
  })
  .superRefine((f, ctx) => {
    if (f.status === 'dismissed' && !f.resolutionFactId) {
      ctx.addIssue({
        code: 'custom',
        path: ['resolutionFactId'],
        message: 'Отклонённое замечание должно ссылаться на факт в базе',
      });
    }
  });
export type Finding = z.infer<typeof Finding>;

/**
 * Validates raw findings from a model. Invalid ones (e.g. without a quote)
 * are dropped, not fixed: the auditor must back every claim with a quote.
 */
export function parseFindings(raw: unknown[]): { kept: Finding[]; dropped: number } {
  const kept: Finding[] = [];
  for (const item of raw) {
    const res = Finding.safeParse(item);
    if (res.success) kept.push(res.data);
  }
  return { kept, dropped: raw.length - kept.length };
}

const SEVERITY_ORDER: Record<z.infer<typeof Severity>, number> = { blocker: 0, major: 1, minor: 2 };

/** The UI shows at most three open findings, blockers first. */
export function topOpenFindings(findings: Finding[], limit = 3): Finding[] {
  return findings
    .filter((f) => f.status === 'open')
    .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
    .slice(0, limit);
}
