import { useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";
import type { Notify } from "./App";
import { fmtElapsed, useElapsed, usePending } from "./hooks";
import { JOB_LABEL, MANUAL_JOBS } from "./nextstep";
import type { Job } from "./types";

/** Карточка текущей задачи вверху панели (аудит 2, 5.5): имя, глава, прошедшее время
 *  (таймер на клиенте от `started`), «N из M» по строкам [N/M] в логе, «Остановить»
 *  (флаг отмены между вызовами моделей). Результат ручного режима и ошибки — развёрнут
 *  по умолчанию; для Писателя/Э2 — переход во вкладку «Окно / ручной режим». */
export function JobCard(props: {
  job: Job;
  offline: boolean;
  notify: Notify;
  onOpenManual: (chapter: number) => void;
}) {
  const { job, offline, notify, onOpenManual } = props;
  const running = job.status === "выполняется";
  const attention = job.status === "ручной-режим" || job.status === "ошибка" || job.status === "остановлено";
  const [open, setOpen] = useState(attention);
  const [full, setFull] = useState<string | null>(null);
  const [pending, run] = usePending();
  const sec = useElapsed(job.started, job.finished, running);

  // новая задача или смена статуса: результат ручного режима/ошибки раскрывается сам
  useEffect(() => {
    setOpen(attention);
  }, [job.started, attention]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    apiGet<Job>("/api/job")
      .then((j) => alive && setFull(j.output ?? ""))
      .catch(() => alive && setFull(null));
    return () => {
      alive = false;
    };
  }, [open, job.output_len, job.status, job.started]);

  const stop = () =>
    run(async () => {
      try {
        await apiPost("/api/job/cancel");
        notify("Остановка запрошена — задача завершится после текущего вызова модели.", "ok");
      } catch (e) {
        notify(String(e));
      }
    });

  const label = JOB_LABEL[job.name] ?? job.name;
  const progress = job.progress && job.progress[1] > 0 ? job.progress : null;
  const manualTab = job.status === "ручной-режим" && job.chapter != null && MANUAL_JOBS.has(job.name);

  return (
    <div className={`jobcard s-${job.status}`} role="status" aria-live="polite" data-testid="jobcard">
      <div className="jobcard-row">
        {running && <span className="spin" aria-hidden="true" />}
        <strong>{label}</strong>
        {job.chapter != null ? <span className="muted"> · глава {job.chapter}</span> : <span className="muted"> · вся область</span>}
        <span className={`badge b-${job.status}`}>{job.status}</span>
        <span className="muted" title={job.started}>
          {running ? "идёт " : ""}{fmtElapsed(sec)}
        </span>
        {running && (
          <span className="muted">
            {progress ? `${progress[0]} из ${progress[1]}` : "идёт"}
            {job.cancel_requested ? " · остановка после текущего вызова…" : ""}
          </span>
        )}
        {progress && running && (
          <progress className="jobbar" max={progress[1]} value={progress[0]} aria-label="прогресс задачи" />
        )}
        <span className="jobcard-btns">
          {running && (
            <button className="danger" disabled={pending || offline || !!job.cancel_requested} onClick={stop}
              title="Флаг отмены: задача остановится между вызовами моделей, глава останется на последнем завершённом шаге">
              Остановить
            </button>
          )}
          {manualTab && (
            <button className="primary" disabled={offline} onClick={() => onOpenManual(job.chapter as number)}>
              Открыть вкладку «Окно / ручной режим»
            </button>
          )}
          {job.output_len > 0 && (
            <button aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "скрыть вывод" : "показать вывод"}
            </button>
          )}
        </span>
      </div>
      {open && job.output_len > 0 && <pre>{full ?? job.output_tail}</pre>}
    </div>
  );
}
