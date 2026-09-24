// Типы ответов локального API (konveyer/server.py)

export interface QueueChapter {
  chapter: number;
  state: string;
  draft: number;
  e1: string;
  e2: string;
  author_min: number;
  machine_min: number;
  next: string;
}

export interface Brief {
  chapter: number;
  volume: number;
  focal: string;
  date: string;
}

export interface Job {
  name: string;
  chapter: number | null;
  status: "выполняется" | "готово" | "ошибка" | "ручной-режим" | "остановлено";
  started: string;
  finished?: string;
  /** «N из M» — из последней строки вида [N/M] в логе задачи (5.5); null — счётчика нет */
  progress?: [number, number] | null;
  /** автор нажал «Остановить» — флаг отмены поставлен, задача завершится между вызовами */
  cancel_requested?: boolean;
  output_tail: string; // хвост лога (в /api/state — без полного вывода, 5.5)
  output_len: number;
  output?: string; // полный лог — только /api/job
}

export interface LintFix { file: string; line: number; old: string; new: string; note: string }

export interface LintFinding {
  code: string;
  severity: "ошибка" | "предупреждение" | "заметка";
  file: string;
  line: number | null;
  message: string;
  quote: string;
  fix: LintFix | null;
  source: "машина" | "модель";
}

export interface LintReport {
  ts: string;
  files_checked: number;
  findings: LintFinding[];
  errors: number;
  warnings: number;
  notes: number;
}

export interface LintSummary { errors: number; warnings: number; notes: number; ts: string }

export interface AppState {
  workspace: string;
  /** текущий том рабочей области (конфиг.yaml: volume); очередь и главы — этого тома */
  volume?: number;
  chapters: QueueChapter[];
  briefs: Brief[];
  regression_green: boolean | null;
  models: { writer: string; verifier2: string; [role: string]: string };
  /** провайдер каждой роли из конфига (писатель, верификатор2, линтер, …) */
  providers?: Record<string, string>;
  job: Job | null;
  lint: LintSummary | null;
  /** минуты авторских пауз всех глав за сегодня (5.7, только отображение) */
  author_today_min?: number;
  /** в библиотеке есть незакоммиченные изменения (правки из вида «Канон», исправления линтера, правки на диске) */
  canon_uncommitted?: boolean;
  canon_uncommitted_files?: string[];
}

export interface Check {
  check_id: string;
  status: "PASS" | "FLAG" | "BRAK";
  threshold: string;
  actual: string;
  quotes: string[];
  rule_source: string;
  note: string;
}

export interface Flag {
  flag_id: string;
  type: string;
  severity: string;
  quote: string;
  rule: string;
  recommendation: string;
  kind: "violation" | "samovolka";
}

export interface Resolution {
  flag_id: string;
  decision: "вычеркнуть" | "канонизировать" | "отклонить" | null;
  target_registry: string | null;
  /** причина отклонения флага (FR-RV-2) — уходит в журнал отклонённых флагов */
  reason?: string;
}

/** Реестр, принимающий строки Канониста (тип с табличным извлечением) — список отдаёт сервер (П-1). */
export interface Registry { name: string; purpose: string }

export interface DiffReport {
  applied_share: number;
  not_applied: number[];
  unauthorized: string[];
  unverifiable: number[];
}

export interface EditParsed {
  seq: number;
  before: string;
  after: string;
  note: string;
  found: boolean;
}

export interface HistoryEntry {
  из: string;
  в: string;
  время: string;
  команда: string;
}

export interface ChapterDetail {
  chapter: number;
  state: string;
  draft: number;
  retries: number;
  iterations: number;
  history: HistoryEntry[];
  verdict: { draft: number; checks: Check[] } | null;
  flags: Flag[];
  resolutions: Resolution[];
  diff_report: DiffReport | null;
  text: string | null;
  drafts: number[];
  edits_md: string | null;
  /** версия правки.md (хэш текста) для проверки конфликта при сохранении; null — файла нет */
  edits_version: string | null;
  edits_parsed: EditParsed[];
  canon_batch: string | null;
  batch_version: string | null;
  author_min: number;
  machine_min: number;
  next: string;
  registries: Registry[];
}

export interface ApiLogRow {
  ts: string;
  role: string;
  model: string;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_est: number | null;
  chapter: number | null;
  duration: number | null;
  error?: string;
}
