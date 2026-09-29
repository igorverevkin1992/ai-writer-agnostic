import { describe, expect, it } from 'vitest';
import type { Finding } from './api.ts';
import { topThree } from './findings.ts';

const f = (id: string, severity: Finding['severity'], status: Finding['status'] = 'open') =>
  ({ id, severity, status }) as Finding;

describe('topThree', () => {
  it('shows at most three open findings, blockers first', () => {
    const list = [f('a', 'minor'), f('b', 'blocker', 'resolved'), f('c', 'major'), f('d', 'blocker'), f('e', 'minor')];
    expect(topThree(list).map((x) => x.id)).toEqual(['d', 'c', 'a']);
  });
});
