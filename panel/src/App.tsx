import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { apiGet, apiPost, errText, isOffline, OFFLINE_MESSAGE } from "./api";
import { ChapterView } from "./ChapterView";
import { Canon } from "./Canon";
import { Circles } from "./Circles";
import { JournalsView, OnboardingView, ProjectView, QualityView, RegressionView, VolumeView } from "./Views";
import { useConfirm } from "./Confirm";
import { createDirtyRegistry, DirtyContext } from "./drafts";
import { useOnline, usePending } from "./hooks";
import { JobCard } from "./JobCard";
import { QUEUE_NEXT, type Tab } from "./nextstep";
import type { AppState, Job } from "./types";

type View =
  | { kind: "глава"; n: number }
  | { kind: "дашборд" }
  | { kind: "журнал" }
  | { kind: "круги" }
  | { kind: "канон" }
  | { kind: "проект" }
  | { kind: "онбординг" }
  | { kind: "регрессия" }
  | { kind: "качество" }
  | { kind: "том" }
  | { kind: "поиск"; q: string };

export type Notify = (text: string, kind?: "ok" | "err") => void;
export type RunCommand = (cmd: string, chapter?: number, params?: Record<string, unknown>) => Promise<void>;
/** просьба открыть вкладку главы (из карточки задачи или подсказки «Дальше») */
export interface TabRequest { n: number; tab: Tab; tick: number }

/** Задача из ответа сервера не должна затирать более новую (гонка runCommand ↔ опрос, 5.8):
 *  ответ /api/state, стартовавший до POST /api/command, несёт старую задачу (или null). */
export function newerJob(cur: Job | null, incoming: Job | null): Job | null {
  if (!incoming) return cur;
  if (!cur) return incoming;
  return incoming.started >= cur.started ? incoming : cur;
}

// опрос /api/state: раз в секунду пока идёт задача, иначе — раз в 2,5 с
const POLL_RUNNING_MS = 1000;
const POLL_IDLE_MS = 2500;

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [view, setView] = useState<View | null>(null);
  const viewRef = useRef<View | null>(null);
  viewRef.current = view;
  const [toast, setToast] = useState<{ text: string; kind: "ok" | "err" } | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [query, setQuery] = useState("");
  // 5.1: предыдущая задача — в ref, а не в state: иначе refresh пересоздавался бы
  // на каждый ответ, а useEffect с интервалом перезапускался бы без задержки
  const prevJob = useRef<Job | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const lastPollError = useRef<string | null>(null);
  const [confirm, confirmDialog] = useConfirm();
  const [pending, run] = usePending();
  const online = useOnline();
  const [tabRequest, setTabRequest] = useState<TabRequest | null>(null);
  // единый реестр «не сохранено» (аудит 5.1–5.3): поля ввода регистрируются по ключу,
  // смена вида проходит через go(), закрытие страницы — через beforeunload
  const dirty = useRef(createDirtyRegistry()).current;

  const notify: Notify = useCallback((text, kind = "err") => {
    window.clearTimeout(toastTimer.current);
    setToast({ text: text.replace(/^(Api)?Error:\s*/, ""), kind });
    if (kind === "ok") toastTimer.current = window.setTimeout(() => setToast(null), 3500);
  }, []);
  useEffect(() => () => window.clearTimeout(toastTimer.current), []);

  const refresh = useCallback(async () => {
    try {
      const s = await apiGet<AppState>("/api/state");
      // гонка runCommand ↔ опрос (5.8): задача из POST новее — ответ опроса её не затирает
      let now: Job | null = s.job;
      setState((cur) => {
        now = newerJob(cur?.job ?? null, s.job);
        return { ...s, job: now };
      });
      // 5.2: задача завершилась — сменился started (новая задача уже закончилась,
      // например быстрый compile) или статус ушёл из «выполняется» → перечитать карточку
      const was = prevJob.current;
      if (now && now.status !== "выполняется" && (!was || was.started !== now.started || was.status === "выполняется")) {
        setRefreshTick((t) => t + 1);
      }
      prevJob.current = now;
      lastPollError.current = null;
    } catch (e) {
      // обрыв связи — баннер (useOnline), не тост каждые 2,5 с; прочие ошибки опроса — один раз
      if (isOffline(e)) return;
      const msg = errText(e);
      if (lastPollError.current !== msg) {
        lastPollError.current = msg;
        notify(msg);
      }
    }
  }, [notify]);

  const running = state?.job?.status === "выполняется";
  useEffect(() => {
    // зависимости — стабильный колбэк и булево: эффект перезапускается только
    // при смене «идёт/не идёт», а не на каждый ответ сервера
    refresh();
    const id = window.setInterval(refresh, running ? POLL_RUNNING_MS : POLL_IDLE_MS);
    return () => window.clearInterval(id);
  }, [refresh, running]);

  useEffect(() => {
    if (!view && state) {
      const first = state.chapters[0]?.chapter ?? state.briefs[0]?.chapter;
      if (first !== undefined) setView({ kind: "глава", n: first });
    }
  }, [state, view]);

  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (dirty.entries().length === 0) return;
      e.preventDefault();
      e.returnValue = ""; // браузер показывает свой диалог; черновик уже в localStorage
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);

  /** Смена вида с защитой несохранённого текста: тот же вид — без вопросов. */
  const go = useCallback(
    async (next: View) => {
      const cur = viewRef.current;
      if (cur !== null && sameView(cur, next)) return;
      const q = dirty.question();
      if (q && !(await confirm(q))) return;
      if (q) dirty.leave();
      setView(next);
    },
    [dirty, confirm],
  );

  const runCommand: RunCommand = useCallback(
    async (cmd, chapter, params) => {
      try {
        const r = await apiPost<{ job: Job }>("/api/command", { cmd, chapter, params });
        // ответ POST уже несёт задачу — кнопки блокируются сразу, не дожидаясь опроса;
        // более старый ответ опроса её не перезапишет (newerJob по started)
        setState((s) => (s ? { ...s, job: newerJob(s.job, r.job) } : s));
      } catch (e) {
        if (!isOffline(e)) notify(errText(e));
      }
    },
    [notify],
  );

  /** Открыть главу на нужной вкладке (карточка задачи → «Окно / ручной режим», подсказка «Дальше»). */
  const openTab = useCallback(
    (n: number, tab: Tab) => {
      go({ kind: "глава", n });
      setTabRequest((r) => ({ n, tab, tick: (r?.tick ?? 0) + 1 }));
    },
    [go],
  );

  if (!state) {
    return (
      <div style={{ padding: 30 }}>
        {online ? "Подключение к конвейеру…" : <div className="banner offline" role="alert">{OFFLINE_MESSAGE}</div>}
      </div>
    );
  }

  const known = new Set(state.chapters.map((c) => c.chapter));
  const notStarted = state.briefs.filter((b) => !known.has(b.chapter));
  const offline = !online;
  const busy = running || pending || offline;
  const isActive = (n: number) => view?.kind === "глава" && view.n === n;

  return (
    <DirtyContext.Provider value={dirty}>
    <div className="layout">
      {offline && (
        <div className="banner offline" role="alert">
          {OFFLINE_MESSAGE} — панель повторяет попытку подключения; кнопки заблокированы.
        </div>
      )}
      <aside className="sidebar">
        <div className="brand">КОНВЕЙЕР</div>
        <div className="muted">
          <span title="текущий том рабочей области; сменить — вид «Том»">
            Том {state.volume ?? 1}
          </span>
          <br />
          Писатель: {state.models.writer}
          <br />
          Регрессия:{" "}
          {state.regression_green === null ? "не запускалась" : state.regression_green ? "зелёная ✓" : "КРАСНАЯ ✗"}
          <br />
          Канон:{" "}
          {state.lint === null ? "не проверялся" : state.lint.errors ? `ошибок ${state.lint.errors} ✗` : state.lint.warnings ? `предупреждений ${state.lint.warnings}` : "противоречий нет ✓"}
          {state.canon_uncommitted && (
            <>
              <br />
              <span className="bad" title={(state.canon_uncommitted_files ?? []).join("\n")}>
                Канон: {(state.canon_uncommitted_files ?? []).length} файл(ов) не закоммичено
              </span>
            </>
          )}
          <br />
          <span title="авторские паузы всех глав за сегодня (5.7)">сегодня: {state.author_today_min ?? 0} мин автора</span>
        </div>
        <div className="sidebtns">
          <button disabled={busy} onClick={() => run(() => runCommand("export"))}>Экспорт канона</button>
          <button className={view?.kind === "проект" ? "primary" : ""} onClick={() => go({ kind: "проект" })}>
            Проект
          </button>
          <button className={view?.kind === "онбординг" ? "primary" : ""} onClick={() => go({ kind: "онбординг" })}>
            Онбординг
          </button>
          <button className={view?.kind === "дашборд" ? "primary" : ""} onClick={() => go({ kind: "дашборд" })}>
            Дашборд
          </button>
          <button className={view?.kind === "журнал" ? "primary" : ""} onClick={() => go({ kind: "журнал" })}>
            Журналы
          </button>
          <button className={view?.kind === "регрессия" ? "primary" : ""} onClick={() => go({ kind: "регрессия" })}>
            Регрессия
          </button>
          <button className={view?.kind === "качество" ? "primary" : ""} onClick={() => go({ kind: "качество" })}>
            Качество
          </button>
          <button className={view?.kind === "том" ? "primary" : ""} onClick={() => go({ kind: "том" })}>
            Том
          </button>
          <button className={view?.kind === "круги" ? "primary" : ""} onClick={() => go({ kind: "круги" })}>
            Драматургия
          </button>
          <button className={view?.kind === "канон" ? "primary" : ""} onClick={() => go({ kind: "канон" })}>
            Канон{state.lint?.errors ? ` (${state.lint.errors})` : ""}
          </button>
        </div>

        <form
          className="sidebtns"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim()) go({ kind: "поиск", q: query.trim() });
          }}
        >
          <input
            className="search"
            placeholder="Поиск по канону…"
            aria-label="Поиск по канону"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </form>

        <div className="muted" style={{ margin: "6px 0" }} id="queue-title">Очередь глав · том {state.volume ?? 1}</div>
        <div role="list" aria-labelledby="queue-title">
          {state.chapters.map((c) => (
            <QueueItem key={c.chapter} active={isActive(c.chapter)} onOpen={() => go({ kind: "глава", n: c.chapter })}>
              <div className="row">
                <strong>Глава {c.chapter}</strong>
                <span className={`badge b-${c.state}`}>{c.state}</span>
              </div>
              <div className="muted">
                черновик {c.draft} · Э1: {c.e1} · Э2: {c.e2}
                {c.author_min > 0 && <> · автор {c.author_min} мин</>}
              </div>
              <div className="muted" title={c.next}>→ {QUEUE_NEXT[c.state] ?? c.next}</div>
            </QueueItem>
          ))}
          {notStarted.map((b) => (
            <QueueItem key={b.chapter} active={isActive(b.chapter)} onOpen={() => go({ kind: "глава", n: b.chapter })}>
              <div className="row">
                <strong>Глава {b.chapter}</strong>
                <span className="badge">не начата</span>
              </div>
              <div className="muted">том {b.volume} · фокал {b.focal}</div>
              <div className="muted">→ {QUEUE_NEXT["не-начато"]}</div>
            </QueueItem>
          ))}
        </div>
      </aside>

      <main className="main">
        {state.job && <JobCard job={state.job} offline={offline} notify={notify} onOpenManual={(n) => openTab(n, "ручной")} />}
        {view?.kind === "дашборд" && (
          <>
            <h1>Дашборд</h1>
            <iframe className="dash" src="/dashboard" title="Дашборд" />
          </>
        )}
        {view?.kind === "журнал" && <JournalsView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} />}
        {view?.kind === "проект" && <ProjectView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} />}
        {view?.kind === "онбординг" && (
          <OnboardingView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} confirm={confirm} />
        )}
        {view?.kind === "регрессия" && <RegressionView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} />}
        {view?.kind === "качество" && (
          <QualityView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} confirm={confirm} />
        )}
        {view?.kind === "том" && (
          <VolumeView refreshTick={refreshTick} notify={notify} busy={busy} runCommand={runCommand} confirm={confirm} />
        )}
        {view?.kind === "круги" && (
          <Circles
            busy={running || offline}
            runCommand={runCommand}
            notify={notify}
            confirm={confirm}
            refreshTick={refreshTick}
            chapterCount={state.briefs.length}
          />
        )}
        {view?.kind === "канон" && (
          <Canon busy={running || offline} runCommand={runCommand} notify={notify} confirm={confirm} refreshTick={refreshTick}
            uncommitted={state.canon_uncommitted_files ?? []} />
        )}
        {view?.kind === "поиск" && <SearchView q={view.q} notify={notify} />}
        {view?.kind === "глава" && (
          <ChapterView
            key={view.n}
            chapter={view.n}
            job={state.job}
            offline={offline}
            refreshTick={refreshTick}
            tabRequest={tabRequest}
            runCommand={runCommand}
            notify={notify}
            confirm={confirm}
          />
        )}
      </main>

      {/* область объявлений существует всегда — так скринридер замечает появление текста */}
      <div role="status" aria-live={toast?.kind === "err" ? "assertive" : "polite"} className="toast-region">
        {toast && (
          <div className={`toast ${toast.kind}`} onClick={() => setToast(null)}>
            {toast.text}
          </div>
        )}
      </div>
      {confirmDialog}
    </div>
    </DirtyContext.Provider>
  );
}

function sameView(a: View, b: View): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "глава" && b.kind === "глава") return a.n === b.n;
  if (a.kind === "поиск" && b.kind === "поиск") return a.q === b.q;
  return true;
}

/** Элемент очереди глав: доступен с клавиатуры (Tab, Enter/Space) — аудит 5.7. */
function QueueItem({ active, onOpen, children }: { active: boolean; onOpen: () => void; children: ReactNode }) {
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen();
    }
  };
  return (
    <div
      role="listitem"
      className={"qitem" + (active ? " active" : "")}
      tabIndex={0}
      aria-current={active ? "true" : undefined}
      onClick={onOpen}
      onKeyDown={onKey}
    >
      {children}
    </div>
  );
}

function SearchView({ q, notify }: { q: string; notify: Notify }) {
  const [groups, setGroups] = useState<Record<string, { ref: string; text: string }[]> | null>(null);
  useEffect(() => {
    apiGet<Record<string, { ref: string; text: string }[]>>(`/api/find?q=${encodeURIComponent(q)}`)
      .then(setGroups)
      .catch((e) => notify(errText(e)));
  }, [q, notify]);
  if (!groups) return <p>Поиск «{q}»…</p>;
  const kinds = Object.keys(groups);
  return (
    <>
      <h1>Поиск: «{q}»</h1>
      {kinds.length === 0 && <p className="muted">Ничего не найдено.</p>}
      {kinds.map((kind) => (
        <div key={kind}>
          <h2>{kind} ({groups[kind].length})</h2>
          {groups[kind].map((h, i) => (
            <div className="editrow" key={i}>
              <strong>[{h.ref}]</strong> <span>{h.text}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
