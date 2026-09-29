export const APP_VERSION = '0.0.0';

/** Pipeline steps in execution order (see SPEC.md, «Конвейер шагов»). */
export const STEP_IDS = [
  'concept',
  'logline',
  'bible',
  'season_plan',
  'episode_cards',
  'scripts',
  'polish',
  'export',
] as const;

export type StepId = (typeof STEP_IDS)[number];

export * from './schemas/index.ts';
export * from './providers/index.ts';
export * from './db/index.ts';
export * from './checks/code/index.ts';
export * from './fixtures.ts';
export * from './memory/index.ts';
export * from './eval/index.ts';
