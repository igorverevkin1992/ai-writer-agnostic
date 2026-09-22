import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { connection } from "./api";

export type RunPending = <T>(fn: () => Promise<T>) => Promise<T | undefined>;

/** Pending-состояние кнопок (аудит 5.4): пока запрос идёт, кнопка disabled,
 *  а повторный клик (двойной клик, Enter) игнорируется — ref срабатывает
 *  раньше, чем React успеет перерисовать disabled. */
export function usePending(): [boolean, RunPending] {
  const [pending, setPending] = useState(false);
  const busyRef = useRef(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (busyRef.current) return undefined;
    busyRef.current = true;
    setPending(true);
    try {
      return await fn();
    } finally {
      busyRef.current = false;
      setPending(false);
    }
  }, []);
  return [pending, run];
}

/** true, пока сервер панели отвечает; false после обрыва связи (аудит 2, 5.4). */
export function useOnline(): boolean {
  return useSyncExternalStore(connection.subscribe, connection.isOnline, connection.isOnline);
}

/** Прошедшее время задачи в секундах: таймер на клиенте от `started` (5.5);
 *  после завершения — фиксированная разница finished − started. */
export function useElapsed(started: string | undefined, finished: string | undefined, running: boolean): number {
  const compute = () => {
    if (!started) return 0;
    const from = Date.parse(started);
    if (Number.isNaN(from)) return 0;
    const to = finished ? Date.parse(finished) : Date.now();
    return Math.max(0, Math.floor(((Number.isNaN(to) ? Date.now() : to) - from) / 1000));
  };
  const [sec, setSec] = useState(compute);
  useEffect(() => {
    setSec(compute());
    if (!running) return;
    const id = window.setInterval(() => setSec(compute()), 1000);
    return () => window.clearInterval(id);
  }, [started, finished, running]); // compute читает те же три значения
  return sec;
}

export function fmtElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m} мин ${String(s).padStart(2, "0")} с` : `${s} с`;
}
