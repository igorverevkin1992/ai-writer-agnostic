import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { FIXTURES_DIR, loadGolden } from '../fixtures.ts';
import { applyPatch } from './patch.ts';
import { detectSeeded, seedHoles, withHole } from './seed.ts';

const kit = genreKit(loadKb(), 'revenge_thriller');
const golden = loadGolden();

describe('seeded holes', () => {
  it('plants 30–50 holes of several types, reproducibly', () => {
    const holes = seedHoles(golden, kit, 1);
    expect(holes.length).toBeGreaterThanOrEqual(30);
    expect(holes.length).toBeLessThanOrEqual(50);
    expect(new Set(holes.map((h) => h.holeType))).toEqual(new Set([1, 3, 7, 8, 10, 11]));
    expect(seedHoles(golden, kit, 1)).toEqual(holes);
    expect(seedHoles(golden, kit, 2)).not.toEqual(holes);
  });

  it('the committed set matches the generator', () => {
    const file = parse(readFileSync(join(FIXTURES_DIR, 'seeded', 'muzh_krov.yaml'), 'utf8')) as { seed: number; holes: unknown };
    expect(file.holes).toEqual(seedHoles(golden, kit, file.seed));
  });

  it('never changes the golden project itself', () => {
    const before = JSON.stringify(golden);
    for (const h of seedHoles(golden, kit, 1)) withHole(golden, h);
    expect(JSON.stringify(golden)).toBe(before);
  });

  // M3 acceptance criterion: code finds 100% of seeded chronology (7) and character knowledge (11) holes.
  it.each([1, 2, 3, 4, 5, 6, 7, 8])('code finds every chronology and knowledge hole (seed %i)', (seed) => {
    const report = detectSeeded(kit, golden, seedHoles(golden, kit, seed));
    for (const type of [7, 11]) {
      const s = report.byType[type]!;
      expect(s.planted).toBeGreaterThan(0);
      expect(report.results.filter((r) => r.hole.holeType === type && !r.found).map((r) => r.hole.description)).toEqual([]);
    }
  });
});

describe('applyPatch', () => {
  it('replaces, adds and removes', () => {
    const doc = { a: [1, 2, 3], b: { c: 'x' } };
    expect(
      applyPatch(doc, [
        { op: 'replace', path: '/b/c', value: 'y' },
        { op: 'add', path: '/a/-', value: 4 },
        { op: 'remove', path: '/a/0' },
      ]),
    ).toEqual({ a: [2, 3, 4], b: { c: 'y' } });
    expect(doc.a).toEqual([1, 2, 3]);
  });

  it('fails on a wrong path', () => {
    expect(() => applyPatch({ a: 1 }, [{ op: 'replace', path: '/x/y', value: 1 }])).toThrow(/Нет пути/);
  });
});
