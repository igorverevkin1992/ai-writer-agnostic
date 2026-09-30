import type { GenreKit, Kb } from '@aiw/kb';
import type { Authors, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import { renderPrompt, schemaText } from '../../prompts/render.ts';
import { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { checkKnowledge } from '../code/knowledge.ts';
import { checkTimeline } from '../code/timeline.ts';
import { AuthorReply, JudgeVerdict } from './schemas.ts';

export interface ResolveDeps {
  llm: LlmClient;
  kb: Kb;
  kit: GenreKit;
  projectId?: string;
  step?: string;
  /** Author of the text: answers findings. */
  authorRole: RoleName;
  authorProvider: Authors;
  /** Judge from another family than the author. */
  judgeRole: RoleName;
}

export interface Resolution {
  finding: Finding;
  reply?: AuthorReply;
  /** Why the finding stays open or was closed. */
  verdict: string;
  /** The bible with the accepted new fact, if any. */
  bible: Bible;
}

/** Code checks a new fact must pass: chronology and character knowledge. */
function consistencyIds(bible: Bible, plan?: SeasonPlan): Set<string> {
  return new Set([...checkTimeline(bible), ...checkKnowledge(plan?.episodes ?? [], bible)].map((f) => f.id));
}

/**
 * The author answers a finding only with a fact from the base or a new fact.
 * A new fact must not break chronology or knowledge. A judge of another family decides.
 */
export async function resolveFinding(deps: ResolveDeps, finding: Finding, bible: Bible, plan?: SeasonPlan): Promise<Resolution> {
  const factsList = bible.facts.map((f) => `${f.id}: ${f.text}${f.since_ep ? ` (с ${f.since_ep}-й серии)` : ''}`).join('\n') || 'Фактов нет.';
  const findingText = `${finding.quote}\n${finding.viewerQuestion}${finding.episode ? `\nСерия: ${finding.episode}` : ''}`;

  const { data: reply, provider: replyProvider } = await deps.llm.completeJson(AuthorReply, {
    role: deps.authorRole,
    projectId: deps.projectId,
    step: deps.step,
    request: {
      task: `respond:${finding.id}`,
      system: renderPrompt(deps.kb, `architect/respond`, { finding: findingText, facts: factsList, schema: schemaText(AuthorReply) }),
      messages: [{ role: 'user', content: 'Ответь на замечание. Только JSON.' }],
    },
  });

  let next = bible;
  let fact = reply.fact_id ? bible.facts.find((f) => f.id === reply.fact_id) : undefined;
  if (reply.action === 'cite' && !fact) {
    return { finding, reply, bible, verdict: `Автор сослался на факт ${reply.fact_id}, которого нет в базе` };
  }
  if (reply.action === 'new_fact' && reply.fact && bible.facts.some((f) => f.id === reply.fact!.id)) {
    // A new fact must not quietly rewrite an existing one: that is the producer's decision.
    return { finding, reply, bible, verdict: `Автор выдал за новый факт уже существующий ${reply.fact.id}` };
  }
  if (reply.action === 'new_fact' && reply.fact) {
    const candidate = Bible.parse({
      ...bible,
      facts: [...bible.facts.filter((f) => f.id !== reply.fact!.id), reply.fact],
      knowledge: [...bible.knowledge, ...reply.knowledge],
    });
    const before = consistencyIds(bible, plan);
    const broken = [...checkTimeline(candidate), ...checkKnowledge(plan?.episodes ?? [], candidate)].filter((f) => !before.has(f.id));
    if (broken.length) {
      return { finding, reply, bible, verdict: `Новый факт ломает сюжет: ${broken.map((f) => f.quote).join('; ')}` };
    }
    next = candidate;
    fact = reply.fact;
  }

  const { data: verdict } = await deps.llm.completeJson(JudgeVerdict, {
    role: deps.judgeRole,
    projectId: deps.projectId,
    step: deps.step,
    // The judge checks the text and the author's answer: it must differ from both writers.
    authorProvider: [...(typeof deps.authorProvider === 'string' ? [deps.authorProvider] : deps.authorProvider), replyProvider],
    request: {
      task: `judge:${finding.id}`,
      system: renderPrompt(deps.kb, `critic_of_architect/judge`, {
        finding: findingText,
        answer: reply.explanation,
        fact: fact ? `${fact.id}: ${fact.text}` : 'нет',
        schema: schemaText(JudgeVerdict),
      }),
      messages: [{ role: 'user', content: 'Вынеси решение. Только JSON.' }],
    },
  });

  if (!verdict.closed) return { finding, reply, bible, verdict: verdict.reason };
  return {
    finding: { ...finding, status: 'resolved', resolutionFactId: fact?.id },
    reply,
    bible: next,
    verdict: verdict.reason,
  };
}
