import { z } from 'zod';
import { Fact, KnowledgeEntry } from '../../schemas/bible.ts';
import { NonEmpty } from '../../schemas/common.ts';
import { Severity } from '../../schemas/finding.ts';

/** A finding as the auditor model writes it; code adds id, controller and status. */
export const FindingDraft = z.object({
  holeType: z.int().min(1).max(11),
  severity: Severity,
  episode: z.int().min(1).optional(),
  quote: NonEmpty,
  viewerQuestion: NonEmpty,
  fixes: z.array(NonEmpty).min(1).max(2),
});
export type FindingDraft = z.infer<typeof FindingDraft>;
export const FindingDrafts = z.array(FindingDraft);

/** The author answers a finding only with a fact from the base or a new fact. */
export const AuthorReply = z
  .object({
    action: z.enum(['cite', 'new_fact']),
    fact_id: z.string().optional(),
    fact: Fact.optional(),
    knowledge: z.array(KnowledgeEntry).default([]),
    explanation: NonEmpty,
  })
  .superRefine((r, ctx) => {
    if (r.action === 'cite' && !r.fact_id) ctx.addIssue({ code: 'custom', path: ['fact_id'], message: 'Для cite нужен fact_id' });
    if (r.action === 'new_fact' && !r.fact) ctx.addIssue({ code: 'custom', path: ['fact'], message: 'Для new_fact нужен fact' });
  });
export type AuthorReply = z.infer<typeof AuthorReply>;

export const JudgeVerdict = z.object({ closed: z.boolean(), reason: NonEmpty });
export type JudgeVerdict = z.infer<typeof JudgeVerdict>;

export const ExtractedFacts = z.object({ facts: z.array(Fact), knowledge: z.array(KnowledgeEntry) });
export type ExtractedFacts = z.infer<typeof ExtractedFacts>;

export const EvalMatches = z.object({
  matches: z.array(
    z.object({
      finding_id: NonEmpty,
      hole_id: z.string().nullable(),
      real: z.boolean(),
      disputed: z.boolean(),
      reason: NonEmpty,
    }),
  ),
});
export type EvalMatches = z.infer<typeof EvalMatches>;
