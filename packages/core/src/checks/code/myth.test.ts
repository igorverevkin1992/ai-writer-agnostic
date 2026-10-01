import { genreKit, loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { loadGolden } from '../../fixtures.ts';
import { knowledgeBlock } from '../../prompts/render.ts';
import { bibleText, episodeLine } from '../llm/texts.ts';
import { scoreChecklist } from './checklist.ts';
import { checkVillainLadder } from './ladder.ts';
import { checkRhythm } from './rhythm.ts';
import { runCodeChecks } from './runner.ts';
import { checkPaywallHook } from './story.ts';

const kb = loadKb();
const kit = genreKit(kb, 'slavic_myth');
const golden = loadGolden();

describe('genre pack: dark Slavic fantasy myth', () => {
  it('runs every code check its rules name, on a project of another genre', () => {
    const res = runCodeChecks({ kit, bible: golden.bible, plan: golden.plan });
    const codes = new Set(res.findings.map((f) => f.check));
    // The thriller plan has none of the myth's anchors.
    expect(codes).toContain('season_frame.anchor.descent');
    expect(codes).toContain('season_frame.anchor.rules_recount');
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.total).toBe(27);
    expect(score.items.find((i) => i.id === 'C15')?.status).toBe('fail');
  });

  it('the protagonist is «герой» and the original wrong is «Рана» everywhere code writes', () => {
    expect(kit.genre.terms.hero.nom).toBe('герой');
    const plan = structuredClone(golden.plan);
    for (const e of plan.episodes) e.strike_by_heroine = false;
    const response = checkRhythm(plan, kit.frame, kit.genre.terms).find((f) => f.check === 'rhythm.response');
    expect(response?.viewerQuestion).toMatch(/почему герой не отвечает/u);
    expect(response?.fixes[0]).toMatch(/^Дать герою ответный ход/u);
    expect(bibleText(golden.bible, kit.genre.terms).split('\n')[0]).toBe('РАНА');
    expect(episodeLine(golden.plan.episodes[0]!, kit.genre.terms)).toContain('. Герой: ');
    // Genres that say nothing keep «героиня».
    expect(genreKit(kb, 'revenge_thriller').genre.terms.hero.nom).toBe('героиня');
  });

  it('the paywall episode needs only a reveal: a question, not a threat', () => {
    const plan = structuredClone(golden.plan);
    const ep = plan.episodes.find((e) => e.ep === 10)!;
    ep.anchors = ['paywall_hook'];
    ep.reveals_secret = true;
    ep.threat_to_heroine = false;
    expect(checkPaywallHook(plan, 'paywall_hook', ['reveal'], kit.genre.terms)).toEqual([]);
    expect(checkPaywallHook(plan, 'paywall_hook', undefined, kit.genre.terms)[0]?.fixes[0]).toBe('В 10-й серии: угроза герою');
  });

  it('monsters leave the story by the genre ways: no law, no arrest', () => {
    const bible = structuredClone(golden.bible);
    bible.villains[0]!.punishment.type = 'law';
    const found = checkVillainLadder(bible, golden.plan, kit.frame, {}, kit.genre.terms).filter((f) => f.check === 'villain_ladder.punishment');
    expect(found.some((f) => f.quote.startsWith(`${bible.villains[0]!.name}: уходит из истории так: «law»`))).toBe(true);
    bible.villains[0]!.punishment.type = 'laid_to_rest';
    const after = checkVillainLadder(bible, golden.plan, kit.frame, {}, kit.genre.terms).filter((f) => f.quote.includes('уходит из истории'));
    expect(after.some((f) => f.quote.startsWith(bible.villains[0]!.name))).toBe(false);
  });

  it('models get the myth guide, and examples of other genres stay out', () => {
    const text = knowledgeBlock(kb, kit);
    expect(text).toContain('## Банк славянского фольклора');
    expect(text).toContain('Точка оплаты');
    expect(text).not.toContain('fated_forbidden_alpha');
    expect(knowledgeBlock(kb, genreKit(kb, 'romantasy_revenge'))).toContain('fated_forbidden_alpha');
  });
});
