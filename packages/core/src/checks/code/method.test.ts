import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { loadGolden } from '../../fixtures.ts';
import { heroOf, type Bible } from '../../schemas/bible.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { checkHeroArc, checkPromise, checkStakes, checkVictoryCost } from './method.ts';
import { runCodeChecks, UnknownCheckError } from './runner.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const golden = loadGolden();
const codes = (fs: { check?: string }[]) => fs.map((f) => f.check);

describe('stakes', () => {
  it('passes the golden bible and asks for stakes when they are missing', () => {
    expect(checkStakes(golden.bible, kit.frame)).toEqual([]);
    const without: Bible = { ...golden.bible };
    delete without.stakes;
    expect(codes(checkStakes(without, kit.frame))).toEqual(['stakes.missing']);
  });

  it('wants every stake closed in the final block', () => {
    const b = structuredClone(golden.bible);
    b.stakes!.internal.closed_ep = 30;
    const found = checkStakes(b, kit.frame);
    expect(codes(found)).toEqual(['stakes.closed_outside']);
    expect(found[0]!.viewerQuestion).toContain('внутренняя');
    expect(found[0]!.episode).toBe(30);
  });
});

describe('hero arc', () => {
  const params = { anchor: 'midpoint', minLieActs: 3, relapse: true };

  it('passes the golden plan and knows the hero', () => {
    expect(heroOf(golden.bible).name).toBe(golden.bible.characters[0]!.name);
    expect(checkHeroArc(golden.bible, golden.plan, params, kit.frame)).toEqual([]);
  });

  it('reports an unmarked arc once, as a minor finding', () => {
    const plan: SeasonPlan = { ...golden.plan, episodes: golden.plan.episodes.map((e) => ({ ...e, arc: undefined })) };
    const found = checkHeroArc(golden.bible, plan, params, kit.frame);
    expect(codes(found)).toEqual(['hero_arc.unmarked']);
    expect(found[0]!.severity).toBe('minor');
  });

  it('counts lie acts before the midpoint and the relapse after it', () => {
    const plan = structuredClone(golden.plan);
    plan.episodes.find((e) => e.ep === 6)!.arc = undefined;
    plan.episodes.find((e) => e.ep === 34)!.arc = 'truth';
    const found = checkHeroArc(golden.bible, plan, params, kit.frame);
    expect(codes(found)).toEqual(['hero_arc.lie_acts', 'hero_arc.no_relapse']);
    expect(found[0]!.quote).toBe(heroOf(golden.bible).lie);
    expect(found[0]!.viewerQuestion).toContain('2 сериях');
  });
});

describe('victory cost', () => {
  it('passes the golden plan; a victory without a cost nearby is free', () => {
    expect(checkVictoryCost(golden.plan, kit.frame, 1)).toEqual([]);
    const plan = structuredClone(golden.plan);
    plan.episodes.find((e) => e.ep === 17)!.cost = '';
    const found = checkVictoryCost(plan, kit.frame, 1);
    expect(codes(found)).toEqual(['victory_cost.free']);
    expect(found[0]!.episode).toBe(16);
  });

  it('does not ask a price for the final block and notices a season without victories', () => {
    const plan = structuredClone(golden.plan);
    plan.episodes.find((e) => e.ep === 59)!.victory = true;
    expect(checkVictoryCost(plan, kit.frame, 1)).toEqual([]);
    const none: SeasonPlan = { ...golden.plan, episodes: golden.plan.episodes.map((e) => ({ ...e, victory: false })) };
    expect(codes(checkVictoryCost(none, kit.frame, 1))).toEqual(['victory_cost.unmarked']);
  });
});

describe('promise of the genre', () => {
  it('is kept by an anchor or a kaif line in the first episode', () => {
    expect(checkPromise(golden.plan, kit.frame, { ep: 1, lines: [], anchors: ['betrayal'] })).toEqual([]);
    const found = checkPromise(golden.plan, kit.frame, { ep: 1, lines: ['love'], anchors: ['midpoint'] });
    expect(codes(found)).toEqual(['promise.absent']);
    expect(found[0]!.viewerQuestion).toContain('Мидпоинт');
    const romantasy = genreKit(kb, 'romantasy_revenge');
    const r = loadGolden('naslednitsa');
    expect(checkPromise(r.plan, romantasy.frame, { ep: 1, lines: ['revenge', 'love'], anchors: [] })).toEqual([]);
  });

  it('is wired into the genre rules with their params', () => {
    const ids = kit.rules.rules.filter((r) => r.check.run.some((x) => ['stakes', 'hero_arc', 'victory_cost', 'promise'].includes(x.fn))).map((r) => r.id);
    expect(ids).toEqual(['R19', 'R20', 'R21', 'R22']);
    const res = runCodeChecks({ kit, ...golden });
    expect(['R19', 'R20', 'R21', 'R22'].every((id) => res.evaluatedRules.has(id))).toBe(true);
    const bad = { ...kit, rules: { ...kit.rules, rules: [{ ...kit.rules.rules[0]!, check: { code: 'x', run: [{ fn: 'promise', params: {} }] } }] } };
    expect(() => runCodeChecks({ kit: bad, ...golden })).toThrow(UnknownCheckError);
  });
});

describe('two cliffhangers in a row differ', () => {
  it('every genre frame allows one hook type per run', () => {
    for (const id of Object.keys(kb.genres)) expect(genreKit(kb, id).frame.rhythm.max_same_hook_run, id).toBe(1);
  });
});
