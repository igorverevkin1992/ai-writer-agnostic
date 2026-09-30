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
  /** The fragment as it was when the variants were written. */
  fragment?: z.infer<typeof ScriptBlock>[];
  /** Index of the chosen variant, once the producer chose. */
  chosen?: number;
}

/** The writer offers 5 variants of a fragment the producer selected. */
export async function proposePolish(deps: { db: Db; llm: LlmClient; kb: Kb; projectId: string }, req: PolishRequest): Promise<PolishState> {
  const project = getProject(deps.db, deps.projectId);
  const kit = genreKit(deps.kb, project.genreId ?? '');
  assertPolishOpen(new Pipeline(deps.db, deps.projectId));
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
  const state: PolishState = { ...req, variants: data.variants, fragment };
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
  assertPolishOpen(new Pipeline(deps.db, deps.projectId));
  const memory = new ProjectMemory(deps.db, deps.projectId);
  const state = memory.latestArtifact('polish') as PolishState | undefined;
  if (!state) throw new PipelineError('Нет вариантов доработки');
  if (state.chosen !== undefined) throw new PipelineError('Вариант уже выбран. Выделите фрагмент заново');
  const chosen = state.variants[variant];
  if (!chosen) throw new PipelineError(`Выберите вариант от 1 до ${state.variants.length}`);
  const before = memory.script(state.ep);
  if (!before) throw new PipelineError(`Нет сценария ${state.ep}-й серии`);
  const replaced = before.blocks.slice(state.from, state.to + 1);
  if (state.fragment && JSON.stringify(replaced) !== JSON.stringify(state.fragment)) {
    throw new PipelineError('Сценарий изменился после того, как были написаны варианты. Выделите фрагмент заново');
  }
  const end = Math.max(before.duration_s, ...before.blocks.map((b) => b.t1));
  assertFits(chosen, before.blocks[state.from - 1]?.t0 ?? 0, before.blocks[state.to + 1]?.t0 ?? end, end);

  const blocks = [...before.blocks.slice(0, state.from), ...chosen, ...before.blocks.slice(state.to + 1)];
  const script = Script.parse({ ...before, blocks });
  // Findings about the replaced lines are gone with them; the rest of the episode keeps its findings.
  const oldText = replaced.map((b) => norm(renderBlock(b))).join('\n');
  for (const f of memory.findingsOf('scripts', 'open')) {
    if (f.episode === state.ep && oldText.includes(norm(f.quote))) {
      memory.setFindingOutcome(f.id, { status: 'resolved', verdict: 'Фрагмент заменён при доработке' });
    }
  }
  memory.saveScript(script);
  memory.logEdit('script', String(state.ep), replaced, chosen, {
    author: 'producer',
    note: `Доработка ${state.ep}-й серии: выбран вариант ${variant + 1} из ${state.variants.length}${state.note ? ` («${state.note}»)` : ''}`,
  });
  memory.saveArtifact('polish', { ...state, chosen: variant });
  // Files downloaded before this change are out of date: export needs doing again.
  new Pipeline(deps.db, deps.projectId).resetLater('polish');

  const findings = [...checkScriptMetrics(script, kit.production, kit.frame), ...checkLegalMarkers(collectTexts({ scripts: [script] }), kit.legal)];
  memory.saveFindings(findings, 'scripts');
  // Code checks of this episode were run again: what they no longer raise is fixed.
  const ids = new Set(findings.map((f) => f.id));
  for (const f of memory.findingsOf('scripts', 'open')) {
    const byCode = f.check?.startsWith('script_metrics.') || f.check?.startsWith('legal_markers.');
    if (f.episode === state.ep && byCode && !ids.has(f.id)) {
      memory.setFindingOutcome(f.id, { status: 'resolved', verdict: 'Исправлено доработкой' });
    }
  }
  return { script, findings };
}

function assertPolishOpen(pipeline: Pipeline): void {
  pipeline.assertCanRun('polish');
  if (pipeline.get('polish').status === 'approved') throw new PipelineError('Доработка уже утверждена');
}

const norm = (s: string) => s.replace(/[ёЁ]/gu, 'е').replace(/\s+/gu, ' ').trim().toLowerCase();

/** A variant must start between its neighbours, keep time order and end within the episode. */
function assertFits(blocks: ScriptBlock[], start: number, nextStart: number, end: number): void {
  let t = start;
  for (const b of blocks) {
    if (b.t0 < t || b.t0 > nextStart || b.t1 > end) {
      throw new PipelineError(`Вариант не помещается во время фрагмента (${start}–${nextStart} с). Выберите другой`);
    }
    t = b.t0;
  }
}
