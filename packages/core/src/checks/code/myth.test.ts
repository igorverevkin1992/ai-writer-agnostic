import { genreKit, loadKb } from '@aiw/kb';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { FIXTURES_DIR, loadGolden } from '../../fixtures.ts';
import { knowledgeBlock } from '../../prompts/render.ts';
import { bibleText, episodeLine } from '../llm/texts.ts';
import { scoreChecklist } from './checklist.ts';
import { checkVillainLadder } from './ladder.ts';
import { checkRhythm } from './rhythm.ts';
import { runCodeChecks } from './runner.ts';
import { loadProducerHoles } from '../../eval/auditEval.ts';
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

  it('the genre follows «Три луны»: its frame and seven laws pass, only real holes stay', () => {
    const tri = loadGolden('tri_luny');
    const codes = runCodeChecks({ kit, bible: tri.bible, plan: tri.plan }).findings.map((f) => `${f.check}${f.episode ? `@${f.episode}` : ''}`);
    expect(codes).toEqual(expect.arrayContaining(['villain_ladder.counterstrike@8', 'villain_ladder.counterstrike@18', 'rhythm.hook_repeat@27']));
    expect(codes.filter((c) => /^(season_frame|world_rules|villain_ladder\.(takedown|punishment)|limits)/u.test(c))).toEqual([]);
  });

  it('any number of world rules, if each is said in an episode and fires later', () => {
    const tri = loadGolden('tri_luny');
    const bible = structuredClone(tri.bible);
    bible.world_rules[0]!.plays_eps = [];
    bible.world_rules[1]!.plays_eps = [2];
    const found = runCodeChecks({ kit, bible, plan: tri.plan }).findings.filter((f) => f.check === 'world_rules.unshown');
    expect(found.map((f) => f.quote)).toEqual([
      'Закон «Долг платит должник»: нет — серия, где он срабатывает',
      'Закон «Ложь кормит лес, правда его останавливает»: срабатывает в 2-й, а произнесён только в 20-й',
    ]);
  });

  it('the script doctor\'s 58 fixes are data, and their lessons are review rules', () => {
    const file = parse(readFileSync(join(FIXTURES_DIR, 'golden', 'tri_luny', 'fixes.yaml'), 'utf8')) as {
      pairs: { id: number; before: string; after: string; why: string; holes: string[] }[];
    };
    expect(file.pairs.map((p) => p.id)).toEqual(Array.from({ length: 58 }, (_, i) => i + 1));
    for (const p of file.pairs) expect(p.before && p.after && p.why).toBeTruthy();
    const rules = kb.review.categories.flatMap((c) => c.rules);
    expect(rules).toContain('Одна константа — одно число во всём документе: барьер, срок, возраст, число голосов называть одинаково везде');
  });

  it('version 1 of «Три луны» and the script doctor\'s review make an eval set', () => {
    const v1 = loadGolden('tri_luny_v1');
    expect(v1.plan.episodes).toHaveLength(60);
    // A map row over several episodes: one event, one cliffhanger at its end — as the concept has it.
    expect(v1.plan.episodes.filter((e) => e.ep >= 13 && e.ep <= 21).map((e) => e.cliffhanger).filter((c) => c !== '—')).toHaveLength(1);
    const holes = loadProducerHoles('tri_luny_v1')!;
    expect(holes.holes).toHaveLength(85);
    expect(new Set(holes.holes.map((h) => h.id)).size).toBe(85);
    expect(holes.holes.find((h) => h.id === 'v1_П1')?.category).toEqual(['Н']);
    // Code alone already finds some of the doctor's holes in version 1.
    const codes = new Set(runCodeChecks({ kit, bible: v1.bible, plan: v1.plan }).findings.map((f) => f.check));
    for (const c of ['villain_ladder.on_screen', 'villain_ladder.punishment', 'guns.not_fired', 'rhythm.hook_repeat', 'rhythm.suffering']) expect(codes).toContain(c);
  });
});
