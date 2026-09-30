import { checkKnowledge } from '../checks/code/knowledge.ts';
import { checkTimeline } from '../checks/code/timeline.ts';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { Bible, Fact, KnowledgeEntry } from '../schemas/bible.ts';
import { STEP_IDS, type StepId } from '../steps.ts';
import { INCOMPLETE_CHECK, Pipeline, PipelineError } from './machine.ts';

export interface ProducerResolution {
  /** An existing fact that closes the finding. */
  factId?: string;
  /** Or a new fact, with who knows it. */
  fact?: Fact;
  knowledge?: KnowledgeEntry[];
}

/**
 * The producer closes a finding with an existing fact or a new one.
 * A new fact must not break chronology or character knowledge.
 */
export function resolveByProducer(db: Db, projectId: string, findingId: string, input: ProducerResolution): void {
  const memory = new ProjectMemory(db, projectId);
  const row = memory.finding(findingId);
  if (!row) throw new PipelineError(`Замечание ${findingId} не найдено`);
  if (row.status !== 'open') throw new PipelineError('Замечание уже закрыто');
  if (row.check === INCOMPLETE_CHECK) throw new PipelineError('Это замечание закрывается повторным запуском шага');
  const bible = memory.currentBible();
  if (!bible) throw new PipelineError('Нет библии проекта');

  let factId = input.factId;
  if (input.fact) {
    const fact = Fact.parse(input.fact);
    if (bible.facts.some((f) => f.id === fact.id)) throw new PipelineError(`Факт ${fact.id} уже есть: выберите его из списка или дайте новому факту другой id`);
    const known = (input.knowledge ?? []).map((k) => KnowledgeEntry.parse(k));
    const candidate = Bible.parse({ ...bible, facts: [...bible.facts.filter((f) => f.id !== fact.id), fact], knowledge: [...bible.knowledge, ...known] });
    const plan = memory.currentPlan();
    const before = new Set([...checkTimeline(bible), ...checkKnowledge(plan?.episodes ?? [], bible)].map((f) => f.id));
    const broken = [...checkTimeline(candidate), ...checkKnowledge(plan?.episodes ?? [], candidate)].filter((f) => !before.has(f.id));
    if (broken.length) throw new PipelineError(`Новый факт ломает сюжет: ${broken.map((f) => f.quote).join('; ')}`);
    memory.addFact(fact, known, { author: 'producer', note: `Закрывает замечание ${findingId}` });
    factId = fact.id;
  } else if (!factId || !bible.facts.some((f) => f.id === factId)) {
    throw new PipelineError(`Нужен факт из базы или новый факт${factId ? `: факта ${factId} нет` : ''}`);
  }
  memory.setFindingOutcome(findingId, { status: 'resolved', verdict: 'Закрыто продюсером', resolutionFactId: factId });
  refreshStep(db, projectId, row.step);
}

/** The producer dismisses a finding; a dismissal must point to the fact that answers it. */
export function dismissByProducer(db: Db, projectId: string, findingId: string, factId: string): void {
  const memory = new ProjectMemory(db, projectId);
  const row = memory.finding(findingId);
  if (!row) throw new PipelineError(`Замечание ${findingId} не найдено`);
  if (row.status !== 'open') throw new PipelineError('Замечание уже закрыто');
  if (row.check === INCOMPLETE_CHECK) throw new PipelineError('Это замечание закрывается повторным запуском шага');
  if (!memory.currentBible()?.facts.some((f) => f.id === factId)) {
    throw new PipelineError(`Чтобы отклонить замечание, укажите факт из базы${factId ? `: факта ${factId} нет` : ''}`);
  }
  memory.setFindingOutcome(findingId, { status: 'dismissed', verdict: 'Отклонено продюсером', resolutionFactId: factId });
  refreshStep(db, projectId, row.step);
}

function refreshStep(db: Db, projectId: string, step: string | null): void {
  if (step && (STEP_IDS as readonly string[]).includes(step)) new Pipeline(db, projectId).refresh(step as StepId);
}
