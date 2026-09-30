import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { loadGolden } from '../../fixtures.ts';
import { knowledgeBlock } from '../../prompts/render.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { scoreChecklist } from './checklist.ts';
import { checkRhythm } from './rhythm.ts';
import { runCodeChecks } from './runner.ts';
import { checkWorldRules } from './world.ts';

const kb = loadKb();
const kit = genreKit(kb, 'romantasy_revenge');
const golden = loadGolden();

/** The golden plan with both kaif lines in every episode except the given ones. */
function withLines(skipLove: number[] = []): SeasonPlan {
  const plan = structuredClone(golden.plan);
  for (const e of plan.episodes) e.kaif_lines = skipLove.includes(e.ep) ? ['revenge'] : ['revenge', 'love'];
  return plan;
}

describe('genre pack: romantasy with revenge', () => {
  it('runs every code check its rules name, on a project of another genre', () => {
    const res = runCodeChecks({ kit, bible: golden.bible, plan: golden.plan });
    const codes = new Set(res.findings.map((f) => f.check));
    // The thriller plan has no love or power line: the frame says so.
    expect(codes).toContain('season_frame.anchor.love_near_kiss');
    expect(codes).toContain('season_frame.anchor.power_spark');
    expect(codes).toContain('rhythm.line_gap');
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.total).toBe(20);
    expect(score.items.find((i) => i.id === 'C09')?.status).toBe('fail');
  });

  it('no line may fall silent for more than 3 episodes, except in the breakup', () => {
    expect(checkRhythm(withLines(), kit.frame).filter((f) => f.check === 'rhythm.line_gap')).toEqual([]);
    const gap = checkRhythm(withLines([12, 13, 14, 15]), kit.frame).filter((f) => f.check === 'rhythm.line_gap');
    expect(gap).toMatchObject([{ episode: 15, quote: 'Кайф любви: нет в сериях 12–15' }]);
    const breakup = checkRhythm(withLines([44, 45, 46, 47, 48]), kit.frame).filter((f) => f.check === 'rhythm.line_gap');
    expect(breakup).toEqual([]);
  });

  it('one miracle: 1–3 world rules', () => {
    const rules = golden.bible.world_rules;
    expect(checkWorldRules({ ...golden.bible, world_rules: rules.slice(0, 3) }, { min: 1, max: 3 })).toEqual([]);
    expect(checkWorldRules({ ...golden.bible, world_rules: [...rules, ...rules].slice(0, 4) }, { min: 1, max: 3 })).toMatchObject([
      { check: 'world_rules.count', holeType: 1 },
    ]);
  });

  it('models get the anchor names, both lines and the genre guide', () => {
    const text = knowledgeBlock(kb, kit);
    expect(text).toContain('love_near_kiss (Почти-поцелуй): 8');
    expect(text).toContain('Линии кайфа (id: название): revenge: Кайф мести; love: Кайф любви.');
    expect(text).toContain('## Одно чудо');
    expect(text).toContain('Сила даёт знание, закон даёт наказание');
    // The thriller does not get the romantasy guide.
    expect(knowledgeBlock(kb, genreKit(kb, 'revenge_thriller'))).not.toContain('## Одно чудо');
  });
});
