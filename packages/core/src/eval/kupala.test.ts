import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { runCodeChecks } from '../checks/code/runner.ts';
import { loadGolden } from '../fixtures.ts';
import { loadProducerHoles } from './auditEval.ts';

const kit = genreKit(loadKb(), 'romantasy_revenge');

describe('golden project «Муж утопил меня в Купальскую ночь» (romantasy, with its holes)', () => {
  it('loads the 60-episode plan and the bible as written in the concept', () => {
    const g = loadGolden('kupala');
    expect(g.plan.episodes).toHaveLength(60);
    expect(g.bible.villains.map((v) => v.name)).toEqual(['Жанна', 'Кристина', 'Лужин', 'Глеб', 'Тамара']);
    expect(g.bible.world_rules).toHaveLength(3);
  });

  it('has the reference review: 68 holes, 7 critical, each with a rule for the agent', () => {
    const file = loadProducerHoles('kupala')!;
    expect(file.holes).toHaveLength(68);
    expect(file.holes.filter((h) => h.severity === 'critical')).toHaveLength(7);
    expect(file.holes.every((h) => h.category.length > 0 && h.agent_rule)).toBe(true);
  });

  it('code alone already finds three holes of the review', () => {
    const codes = runCodeChecks({ kit, ...loadGolden('kupala') }).findings.map((f) => `${f.check}@${f.episode}`);
    // k41: suffering in 50–52; k10: the keys from 16 never come back; k14: Lužin's phone from 28 vanishes.
    expect(codes).toEqual(expect.arrayContaining(['rhythm.suffering@50', 'guns.not_fired@16', 'guns.not_fired@28']));
  });
});
