import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { loadGolden } from '../../fixtures.ts';
import { scoreChecklist } from './checklist.ts';
import { runCodeChecks } from './runner.ts';

const kit = genreKit(loadKb(), 'revenge_thriller');

describe('golden project «Муж женился на мне ради крови»', () => {
  const golden = loadGolden();

  it('passes every code check', () => {
    const { findings } = runCodeChecks({ kit, ...golden });
    expect(findings.map((f) => `${f.check} [${f.episode ?? '-'}] ${f.quote}`)).toEqual([]);
  });

  it('evaluates every rule that has a code part', () => {
    const { evaluatedRules } = runCodeChecks({ kit, ...golden });
    const withCode = kit.rules.rules.filter((r) => r.check.run.length > 0).map((r) => r.id);
    // Script metrics need scripts, which appear only at step 6.
    expect([...evaluatedRules].sort()).toEqual(withCode.filter((id) => id !== 'R18').sort());
  });

  it('scores the checklist like the producer did (20 of 20 minus what code cannot judge)', () => {
    const result = runCodeChecks({ kit, ...golden });
    const score = scoreChecklist(kit.checklist, kit.rules, result);
    expect(score.items.filter((i) => i.status === 'fail')).toEqual([]);
    expect(score.score + score.unknownPoints).toBe(20);
    expect(score.passed).toBe(true);
  });
});
