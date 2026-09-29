import type { Finding } from './api.ts';

const ORDER = { blocker: 0, major: 1, minor: 2 } as const;

/** At most three open findings, blockers first (the screen never shows more). */
export function topThree(list: Finding[]): Finding[] {
  return list
    .filter((f) => f.status === 'open')
    .sort((a, b) => ORDER[a.severity] - ORDER[b.severity])
    .slice(0, 3);
}
