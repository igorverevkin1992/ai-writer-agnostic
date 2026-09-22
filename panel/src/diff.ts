/** Построчный дифф без библиотек (аудит 5.2): общий префикс/суффикс отбрасываются,
 *  середина — LCS по строкам; если середина слишком велика, показываем только
 *  «строки есть лишь у вас / лишь на диске» (без выравнивания). */

export type DiffLine = { kind: " " | "+" | "-"; text: string };
export type DiffRow = DiffLine | { kind: "…"; count: number };

const LCS_LIMIT = 2500; // строк с каждой стороны: таблица Uint16 ≤ ~12,5 МБ

export function lineDiff(a: string, b: string): { lines: DiffLine[]; approximate: boolean } {
  const A = a.split("\n");
  const B = b.split("\n");
  let head = 0;
  while (head < A.length && head < B.length && A[head] === B[head]) head++;
  let tail = 0;
  while (tail < A.length - head && tail < B.length - head && A[A.length - 1 - tail] === B[B.length - 1 - tail]) tail++;
  const midA = A.slice(head, A.length - tail);
  const midB = B.slice(head, B.length - tail);
  const out: DiffLine[] = [];
  for (let i = 0; i < head; i++) out.push({ kind: " ", text: A[i] });
  let approximate = false;
  if (midA.length <= LCS_LIMIT && midB.length <= LCS_LIMIT) {
    out.push(...lcsDiff(midA, midB));
  } else {
    approximate = true;
    const inB = new Set(midB);
    const inA = new Set(midA);
    for (const l of midA) if (!inB.has(l)) out.push({ kind: "-", text: l });
    for (const l of midB) if (!inA.has(l)) out.push({ kind: "+", text: l });
  }
  for (let i = A.length - tail; i < A.length; i++) out.push({ kind: " ", text: A[i] });
  return { lines: out, approximate };
}

function lcsDiff(a: string[], b: string[]): DiffLine[] {
  const n = a.length;
  const m = b.length;
  if (n === 0) return b.map((text) => ({ kind: "+" as const, text }));
  if (m === 0) return a.map((text) => ({ kind: "-" as const, text }));
  const w = m + 1;
  const dp = new Uint16Array((n + 1) * w); // dp[i][j] = LCS(a[i:], b[j:])
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: " ", text: a[i] });
      i++;
      j++;
    } else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) {
      out.push({ kind: "-", text: a[i] });
      i++;
    } else {
      out.push({ kind: "+", text: b[j] });
      j++;
    }
  }
  while (i < n) out.push({ kind: "-", text: a[i++] });
  while (j < m) out.push({ kind: "+", text: b[j++] });
  return out;
}

/** Компактный вид: контекст ±2 строки вокруг изменений, остальное свёрнуто в «… N строк …». */
export function compactDiff(lines: DiffLine[], context = 2): DiffRow[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((l, i) => {
    if (l.kind === " ") return;
    for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep[k] = true;
  });
  const out: DiffRow[] = [];
  let skipped = 0;
  lines.forEach((l, i) => {
    if (keep[i]) {
      if (skipped) out.push({ kind: "…", count: skipped });
      skipped = 0;
      out.push(l);
    } else skipped++;
  });
  if (skipped) out.push({ kind: "…", count: skipped });
  return out;
}
