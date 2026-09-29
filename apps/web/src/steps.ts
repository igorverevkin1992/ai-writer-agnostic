import { STEP_IDS, type StepId } from '@aiw/core';

/** UI labels for pipeline steps. */
export const STEP_LABELS: Record<StepId, string> = {
  concept: 'Идея',
  logline: 'Логлайн',
  bible: 'Библия',
  season_plan: 'План сезона',
  episode_cards: 'Карточки серий',
  scripts: 'Сценарии',
  polish: 'Доработка',
  export: 'Экспорт',
};

export const STEPS = STEP_IDS.map((id) => ({ id, label: STEP_LABELS[id] }));
