/** An error of the local server: a readable Russian message plus the status, so screens can offer the right action. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The project budget is used up: the producer can raise it. */
    readonly budgetExceeded = false,
  ) {
    super(message);
  }
}

/** Thin client for the local server. Errors come back as readable Russian messages. */
export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? (init?.body !== undefined ? 'POST' : 'GET'),
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = (await res.json().catch(() => ({}))) as T & { error?: string; budgetExceeded?: boolean };
  if (!res.ok) throw new ApiError(data.error ?? `Ошибка сервера (${res.status})`, res.status, !!data.budgetExceeded);
  return data;
}

export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { body });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body });
export const del = (path: string) => api<undefined>(path, { method: 'DELETE' });

/** Episode numbers from text like «1-10, 12, 15-17». Empty — nothing chosen. */
export function parseEpisodes(text: string, max: number): number[] {
  const out = new Set<number>();
  for (const part of text.split(/[,;\s]+/u).filter(Boolean)) {
    const m = /^(\d+)(?:[-–](\d+))?$/u.exec(part);
    if (!m) throw new Error(`Непонятно, что такое «${part}»: пишите номера серий через запятую, диапазон — через дефис`);
    const a = Number(m[1]);
    const b = Number(m[2] ?? m[1]);
    for (let n = Math.min(a, b); n <= Math.max(a, b); n++) if (n >= 1 && n <= max) out.add(n);
  }
  return [...out].sort((x, y) => x - y);
}

/** Text like «1–10, 12» from episode numbers. */
export function formatEpisodes(eps: number[]): string {
  const sorted = [...new Set(eps)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j]! + 1) j++;
    parts.push(j > i + 1 ? `${sorted[i]}–${sorted[j]}` : sorted.slice(i, j + 1).join(', '));
    i = j + 1;
  }
  return parts.join(', ');
}

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
  check: string | null;
  /** Hole review format: categories А–П, level, all episodes, why noticed, rule for the agent. */
  category: string[] | null;
  categoryNames?: string[];
  level: 'critical' | 'high' | 'medium' | 'low' | null;
  episodes: string | null;
  whyNoticed: string | null;
  agentRule: string | null;
  doubt: boolean | null;
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

export interface HeroTerms {
  nom: string;
  gen: string;
  dat: string;
}

export interface GenreTerms {
  hero: HeroTerms;
  wound: string;
}

export interface Progress {
  unit: 'episode' | 'block' | 'part' | 'call';
  done: number;
  total: number;
  current?: number;
  lastEpisode?: number;
  /** Cost of this run so far. */
  costUsd: number;
}

export interface Job {
  step: string;
  running: boolean;
  error?: string;
  startedAt: string;
  cancelRequested: boolean;
  /** Stopped by the producer: not an error. */
  stopped?: boolean;
  /** The run hit the project budget. */
  budgetExceeded?: boolean;
  progress: Progress;
}

export interface GenreInfo {
  id: string;
  title: string;
  episodes: number;
  free: number;
  anchors: Record<string, unknown>;
  anchorLabels: Record<string, string>;
  lines: Record<string, string>;
  terms: GenreTerms;
  /** Frame inputs as this project resolves them (e.g. {paywall: 12}). */
  inputs: Record<string, number>;
  pass: number;
  total: number;
}

export interface Overview {
  project: { id: string; title: string; genreId: string | null; budgetLimitUsd: number | null; frameInputs: Record<string, number> | null; terms: Partial<GenreTerms> | null };
  demo: boolean;
  /** null for a project without a genre (made outside the app). */
  genre: GenreInfo | null;
  steps: StepState[];
  next: NextTask;
  idea: string | null;
  concepts: Doc[] | null;
  concept: Doc | null;
  logline: Doc | null;
  bible: Doc | null;
  plan: { episodes: Doc[]; deviations: Doc[] } | null;
  cardBlocks: number[];
  cards: number[];
  scripts: number[];
  /** Cards and scripts a changed fact made outdated. */
  stale: { cards: number[]; scripts: number[] };
  polish: Doc | null;
  budget: { totalUsd: number; limitUsd: number; calls: number; byRole: Doc[]; byStep: Doc[]; unknownPriceCalls: number };
  /** Model of each role, from config/models.yaml. */
  models: Record<string, string>;
  /** The last run started for the project: runs go on in the background. */
  job: Job | null;
}

/** «серия 7 из 60, $3.20» — progress of a background run in the producer's words. */
export function progressLine(p: Progress, step: string): string {
  const cost = `$${p.costUsd.toFixed(2)}`;
  const n = Math.min(p.done + 1, p.total);
  if (p.unit === 'episode') return `серия ${n} из ${p.total}, ${cost}`;
  if (p.unit === 'block') return `блок карточек ${n} из ${p.total}${p.current ? ` (до ${p.current}-й серии)` : ''}, ${cost}`;
  if (p.unit === 'part') return `часть плана ${n} из ${p.total}, ${cost}`;
  return `${STEP_LABELS[step] ?? step}, ${cost}`;
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
