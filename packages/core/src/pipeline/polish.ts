import { genreKit, type Kb } from '@aiw/kb';
import { z } from 'zod';
import { checkLegalMarkers, checkScriptMetrics, collectTexts } from '../checks/code/production.ts';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { renderPrompt, schemaText } from '../prompts/render.ts';
import type { LlmClient } from '../providers/llm.ts';
import type { Finding } from '../schemas/finding.ts';
import { Script, ScriptBlock } from '../schemas/script.ts';
import { renderBlock, renderScript } from '../text/script.ts';
import { Pipeline, PipelineError } from './machine.ts';
import { getProject } from './project.ts';

export const POLISH_VARIANTS = 5;

export const PolishVariants = z.object({ variants: z.array(z.array(ScriptBlock).min(1)).length(POLISH_VARIANTS) });
export type PolishVariants = z.infer<typeof PolishVariants>;

export interface PolishRequest {
  ep: number;
  /** Indices of the first and last block of the fragment (inclusive). */
  from: number;
  to: number;
  note?: string;
}

interface PolishState extends PolishRequest {
  variants: z.infer<typeof ScriptBlock>[][];
}

/** The writer offers 5 variants of a fragment the producer selected. */
export async function proposePolish(deps: { db: Db; llm: LlmClient; kb: Kb; projectId: string }, req: PolishRequest): Promise<PolishState> {
  const project = getProject(deps.db, deps.projectId);
  const kit = genreKit(deps.kb, project.genreId ?? '');
  new Pipeline(deps.db, deps.projectId).assertCanRun('polish');
  const memory = new ProjectMemory(deps.db, deps.projectId);
  const script = memory.script(req.ep);
  if (!script) throw new PipelineError(`Нет сценария ${req.ep}-й серии`);
  if (req.from < 0 || req.to < req.from || req.to >= script.blocks.length) throw new PipelineError('Неверные границы фрагмента');
  const fragment = script.blocks.slice(req.from, req.to + 1);

  const { data } = await deps.llm.completeJson(PolishVariants, {
    role: 'writer',
    projectId: deps.projectId,
    step: 'polish',
    request: {
      task: `polish:${req.ep}:${req.from}-${req.to}`,
      system: renderPrompt(deps.kb, 'writer/polish', {
        ep: req.ep,
        script: renderScript(script),
        from: req.from + 1,
        to: req.to + 1,
        fragment: fragment.map(renderBlock).join('\n'),
        note: req.note?.trim() || 'нет',
        t0: fragment[0]!.t0,
        t1: fragment.at(-1)!.t1,
        max_line_words: kit.production.script_metrics.max_line_words,
        schema: schemaText(PolishVariants),
      }),
      messages: [{ role: 'user', content: 'Дай пять вариантов. Только JSON.' }],
    },
  });
  const state: PolishState = { ...req, variants: data.variants };
  memory.saveArtifact('polish', state);
  return state;
}

/**
 * The producer picks a variant: it replaces the fragment, the script is re-checked by code,
 * and the choice goes to the producer's creative contribution log.
 */
export function choosePolish(deps: { db: Db; kb: Kb; projectId: string }, variant: number): { script: Script; findings: Finding[] } {
  const project = getProject(deps.db, deps.projectId);
  const kit = genreKit(deps.kb, project.genreId ?? '');
  const memory = new ProjectMemory(deps.db, deps.projectId);
  const state = memory.latestArtifact('polish') as PolishState | undefined;
  if (!state) throw new PipelineError('Нет вариантов доработки');
  const chosen = state.variants[variant];
  if (!chosen) throw new PipelineError(`Выберите вариант от 1 до ${state.variants.length}`);
  const before = memory.script(state.ep);
  if (!before) throw new PipelineError(`Нет сценария ${state.ep}-й серии`);

  const blocks = [...before.blocks.slice(0, state.from), ...chosen, ...before.blocks.slice(state.to + 1)].sort((a, b) => a.t0 - b.t0);
  const script = Script.parse({ ...before, blocks });
  memory.closeObsolete('scripts', state.ep, 'Сценарий доработан');
  memory.saveScript(script);
  memory.logEdit('script', String(state.ep), before.blocks.slice(state.from, state.to + 1), chosen, {
    author: 'producer',
    note: `Доработка ${state.ep}-й серии: выбран вариант ${variant + 1} из ${state.variants.length}${state.note ? ` («${state.note}»)` : ''}`,
  });
  memory.saveArtifact('polish', { ...state, chosen: variant });

  const findings = [...checkScriptMetrics(script, kit.production, kit.frame), ...checkLegalMarkers(collectTexts({ scripts: [script] }), kit.legal)];
  memory.saveFindings(findings, 'scripts');
  return { script, findings };
}
