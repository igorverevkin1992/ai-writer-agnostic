/** Thin client for the local server. Errors come back as readable Russian messages. */
export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? (init?.body !== undefined ? 'POST' : 'GET'),
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) throw new Error(data.error ?? `Ошибка сервера (${res.status})`);
  return data;
}

export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { body });

export interface Finding {
  id: string;
  step: string | null;
  controller: string;
  holeType: number | null;
  severity: 'blocker' | 'major' | 'minor';
  episode: number | null;
  quote: string;
  viewerQuestion: string;
  fixes: string[];
  status: 'open' | 'resolved' | 'dismissed';
  verdict: string | null;
  resolutionFactId: string | null;
}

export interface StepState {
  step: string;
  status: 'draft' | 'checking' | 'needs_fix' | 'approved' | 'skipped';
  version: number;
}

export interface NextTask {
  step: string;
  stepLabel: string;
  action: 'run' | 'wait' | 'choose_concept' | 'resolve' | 'approve' | 'approve_block' | 'polish' | 'export';
  task: string;
  criterion: string;
  block?: number;
  episodes?: number[];
  openBlockers?: number;
}

export interface Fact {
  id: string;
  text: string;
  since_ep?: number;
}

/** Loosely typed documents: the screens only read them. */
export type Doc = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface Overview {
  project: { id: string; title: string; genreId: string };
  demo: boolean;
  genre: { id: string; title: string; episodes: number; free: number; anchors: Record<string, unknown>; anchorLabels: Record<string, string>; pass: number; total: number };
  steps: StepState[];
  next: NextTask;
  idea: string | null;
  concepts: Doc[] | null;
  concept: Doc | null;
  logline: Doc | null;
  bible: Doc | null;
  plan: { episodes: Doc[]; deviations: Doc[] } | null;
  cardBlocks: number[];
  scripts: number[];
  polish: Doc | null;
  budget: { totalUsd: number; limitUsd: number; calls: number; byRole: Doc[]; byStep: Doc[]; unknownPriceCalls: number };
  /** Model of each role, from config/models.yaml. */
  models: Record<string, string>;
  /** The last run started for the project: runs go on in the background. */
  job: { step: string; running: boolean; error?: string; startedAt: string } | null;
}

export const STEP_LABELS: Record<string, string> = {
  concept: 'Концепции',
  logline: 'Логлайн',
  bible: 'Библия',
  season_plan: 'План сезона',
  episode_cards: 'Карточки серий',
  scripts: 'Сценарии',
  polish: 'Доработка',
  export: 'Экспорт',
  memory: 'База фактов',
};

export const STATUS_LABELS: Record<StepState['status'], string> = {
  draft: 'черновик',
  checking: 'идёт работа',
  needs_fix: 'нужны правки',
  approved: 'утверждено',
  skipped: 'пропущено',
};
