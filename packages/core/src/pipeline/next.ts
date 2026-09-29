import { genreKit, type Kb } from '@aiw/kb';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { STEP_IDS, type StepId } from '../steps.ts';
import { Pipeline, STEP_LABELS } from './machine.ts';
import { getProject } from './project.ts';
import { cardBlocks } from './runners.ts';

export type TaskAction = 'run' | 'wait' | 'choose_concept' | 'resolve' | 'approve' | 'approve_block' | 'polish' | 'export';

/** The one task the producer sees on the «Сейчас» screen, with its done-criterion in one line. */
export interface NextTask {
  step: StepId;
  stepLabel: string;
  action: TaskAction;
  task: string;
  criterion: string;
  /** approve_block: first episode of the block; run: episodes still missing. */
  block?: number;
  episodes?: number[];
  openBlockers?: number;
}

export function nextTask(db: Db, kb: Kb, projectId: string): NextTask {
  const project = getProject(db, projectId);
  const kit = genreKit(kb, project.genreId ?? '');
  const pipeline = new Pipeline(db, projectId);
  const memory = new ProjectMemory(db, projectId);
  const f = kit.frame;
  const criteria: Record<StepId, string> = {
    concept: 'Агент предложит три концепции — выберите одну',
    logline: 'Логлайн: все поля заполнены, текст до 35 слов',
    bible: 'Библия без открытых блокирующих замечаний',
    season_plan: `План на ${f.episodes} серий: опорные точки на местах, чек-лист не ниже ${kit.checklist.pass} из ${kit.checklist.total}`,
    episode_cards: `Карточки всех ${f.episodes} серий утверждены блоками по 10`,
    scripts: 'Сценарии всех серий без открытых блокирующих замечаний',
    polish: 'Доработайте реплики, где хочется, и утвердите',
    export: 'Файлы для команды скачаны',
  };

  for (const step of STEP_IDS) {
    const s = pipeline.get(step);
    if (s.status === 'approved' || s.status === 'skipped') continue;
    const base = { step, stepLabel: STEP_LABELS[step], criterion: criteria[step] };
    if (step === 'export') return { ...base, action: 'export', task: 'Скачайте библию, таблицу сезона, сценарии и промпты для видео' };
    if (s.status === 'checking') return { ...base, action: 'wait', task: `Агент работает: «${STEP_LABELS[step]}»` };
    if (pipeline.hasIncomplete(step)) {
      const eps = step === 'episode_cards' ? missingCards(memory) : step === 'scripts' ? missingScripts(memory) : [];
      return { ...base, action: 'run', task: `Шаг «${STEP_LABELS[step]}» прервался — запустите его ещё раз`, ...(eps.length ? { episodes: eps } : {}) };
    }
    const n = pipeline.openBlockers(step);
    if (s.status === 'needs_fix' && n > 0) {
      return { ...base, action: 'resolve', task: `Закройте блокирующие замечания — ${n}`, criterion: 'Ноль открытых блокирующих замечаний', openBlockers: n };
    }
    if (step === 'polish') return { ...base, action: 'polish', task: 'Доработайте фрагменты сценариев или утвердите шаг' };
    if (s.version === 0) return { ...base, action: 'run', task: `Запустите шаг «${STEP_LABELS[step]}»` };
    if (step === 'concept') return { ...base, action: 'choose_concept', task: 'Выберите одну из трёх концепций' };
    if (step === 'episode_cards') {
      const eps = memory.currentPlan()?.episodes.map((e) => e.ep) ?? [];
      const missing = missingCards(memory);
      if (missing.length) return { ...base, action: 'run', task: `Допишите карточки: не хватает ${missing.length}`, episodes: missing };
      const approved = new Set((memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []);
      const block = cardBlocks(eps).find((b) => !approved.has(b[0]!));
      if (block) return { ...base, action: 'approve_block', task: `Утвердите карточки серий ${block[0]}–${block.at(-1)}`, block: block[0] };
    }
    if (step === 'scripts') {
      const missing = missingScripts(memory);
      if (missing.length) return { ...base, action: 'run', task: `Напишите сценарии: осталось ${missing.length}`, episodes: missing };
    }
    return { ...base, action: 'approve', task: `Проверьте и утвердите: «${STEP_LABELS[step]}»` };
  }
  return { step: 'export', stepLabel: STEP_LABELS.export, action: 'export', task: 'Всё готово. Скачайте файлы', criterion: criteria.export };
}

function missingCards(memory: ProjectMemory): number[] {
  const have = new Set(memory.cards().map((c) => c.ep));
  return (memory.currentPlan()?.episodes.map((e) => e.ep) ?? []).filter((ep) => !have.has(ep));
}

function missingScripts(memory: ProjectMemory): number[] {
  const have = new Set(memory.scripts().map((x) => x.ep));
  return memory.cards().map((c) => c.ep).filter((ep) => !have.has(ep));
}
