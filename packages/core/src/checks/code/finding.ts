import type { Finding } from '../../schemas/finding.ts';

type Controller = Finding['controller'];
type Severity = Finding['severity'];

export interface FindingInput {
  /** Check id and code, e.g. "season_frame.anchor.midpoint". */
  check: string;
  controller: Controller;
  severity: Severity;
  holeType?: number;
  episode?: number;
  /** The exact piece of the checked text (or data) the finding is about. */
  quote: string;
  /** What the viewer will ask, without the "Зритель спросит:" prefix. */
  question: string;
  fixes: [string] | [string, string] | [string, string, string];
}

/** Stable short hash so that re-running checks yields the same finding ids. */
function hash(text: string): string {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

export function makeFinding(f: FindingInput): Finding {
  return {
    id: `${f.check}@${f.episode ?? 0}#${hash(`${f.quote}|${f.question}`)}`,
    controller: f.controller,
    holeType: f.holeType,
    severity: f.severity,
    episode: f.episode,
    quote: f.quote,
    viewerQuestion: `Зритель спросит: ${f.question}`,
    fixes: f.fixes,
    status: 'open',
    check: f.check,
  };
}

/** Lists episode numbers compactly: 3, 4, 5, 9 → "3–5, 9". */
export function formatEpisodes(eps: number[]): string {
  const sorted = [...new Set(eps)].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j]! + 1) j++;
    parts.push(i === j ? `${sorted[i]}` : `${sorted[i]}–${sorted[j]}`);
    i = j + 1;
  }
  return parts.join(', ');
}
