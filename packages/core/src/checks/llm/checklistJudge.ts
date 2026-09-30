import type { GenreKit, Kb } from '@aiw/kb';
import { z } from 'zod';
import { renderPrompt, schemaText } from '../../prompts/render.ts';
import type { Authors, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { Bible } from '../../schemas/bible.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import type { ChecklistScore } from '../code/checklist.ts';
import { bibleText, planBlocks } from './texts.ts';

export const ChecklistVerdicts = z.object({
  items: z.array(z.object({ id: z.string().min(1), ok: z.boolean(), quote: z.string(), reason: z.string().min(1) })),
});

const norm = (s: string) => s.replace(/[«»"“”„]/gu, '"').replace(/[ёЁ]/gu, 'е').replace(/\s+/gu, ' ').trim().toLowerCase();

/**
 * Judges the checklist items code cannot count. An item counts only when the judge
 * backs it with a quote that really is in the text.
 */
export async function judgeChecklist(
  deps: { llm: LlmClient; kb: Kb; kit: GenreKit; projectId?: string; step?: string; role?: RoleName },
  score: ChecklistScore,
  input: { bible: Bible; plan: SeasonPlan; authorProvider: Authors },
): Promise<ChecklistScore> {
  const unknown = score.items.filter((i) => i.status === 'unknown');
  if (unknown.length === 0) return score;
  const text = `${bibleText(input.bible)}\n\n${planBlocks(input.plan, input.plan.episodes.length).map((b) => b.text).join('\n')}`;
  const { data } = await deps.llm.completeJson(ChecklistVerdicts, {
    role: deps.role ?? 'critic_of_architect',
    projectId: deps.projectId,
    step: deps.step,
    authorProvider: input.authorProvider,
    request: {
      task: 'checklist_judge',
      system: renderPrompt(deps.kb, 'critic_of_architect/checklist', {
        items: unknown.map((i) => `${i.id}: ${i.text} (${i.points} балл.)`).join('\n'),
        text,
        schema: schemaText(ChecklistVerdicts),
      }),
      messages: [{ role: 'user', content: 'Оцени пункты. Только JSON.' }],
    },
  });
  const items = score.items.map((i) => {
    if (i.status !== 'unknown') return i;
    const v = data.items.find((x) => x.id === i.id);
    if (!v) return i;
    const backed = v.ok && v.quote.trim() !== '' && norm(text).includes(norm(v.quote));
    return { ...i, status: backed ? ('ok' as const) : v.ok ? ('unknown' as const) : ('fail' as const) };
  });
  const s = items.filter((i) => i.status === 'ok').reduce((n, i) => n + i.points, 0);
  const unknownPoints = items.filter((i) => i.status === 'unknown').reduce((n, i) => n + i.points, 0);
  return {
    ...score,
    items,
    score: s,
    unknownPoints,
    passed: s >= score.pass ? true : s + unknownPoints < score.pass ? false : null,
  };
}
