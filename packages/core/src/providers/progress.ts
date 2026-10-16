import type { CallLog } from './llm.ts';

/** Prints one line per model call with a running total; for scripts that make many calls. */
export function progressLogger(write: (line: string) => void = (l) => process.stderr.write(`${l}\n`)): (log: CallLog) => void {
  let calls = 0;
  let totalUsd = 0;
  return (log) => {
    calls++;
    totalUsd += log.costUsd;
    const secs = (log.durationMs / 1000).toFixed(0);
    const mark = log.status === 'ok' ? '✓' : '✗';
    const tail = log.status === 'ok' ? '' : ` — ${log.error ?? 'ошибка'}`;
    write(`  ${mark} ${String(calls).padStart(3)}. ${log.task ?? log.role} · ${log.model} · ${secs} с · $${log.costUsd.toFixed(3)} (всего $${totalUsd.toFixed(2)})${tail}`);
  };
}
