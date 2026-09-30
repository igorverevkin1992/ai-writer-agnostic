import { genreKit, loadKb, type GenreKit } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { loadGolden, type ProjectFixture } from '../../fixtures.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import { sampleCard, sampleScript } from '../../schemas/samples.ts';
import { EpisodeCard as CardSchema } from '../../schemas/episodeCard.ts';
import type { Script } from '../../schemas/script.ts';
import { scoreChecklist } from './checklist.ts';
import { checkSeasonFrame } from './frame.ts';
import { checkKnowledge } from './knowledge.ts';
import { checkBetrayerRank, checkVillainLadder } from './ladder.ts';
import { checkGuns, checkLegalMarkers, checkLimits, checkScriptMetrics } from './production.ts';
import { checkRhythm } from './rhythm.ts';
import { CODE_CHECKS, UnknownCheckError, runCodeChecks } from './runner.ts';
import { checkBetrayalTiming, checkPaywallHook, checkSecretTurns } from './story.ts';
import { checkTimeline } from './timeline.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const frame = kit.frame;
let p: ProjectFixture;

beforeEach(() => {
  p = structuredClone(loadGolden());
});

const ep = (n: number) => p.plan.episodes.find((e) => e.ep === n)!;
const codes = (fs: { check?: string }[]) => fs.map((f) => f.check);
const villain = (name: string) => p.bible.villains.find((v) => v.name === name)!;

describe('checkSeasonFrame', () => {
  it('reports a missing episode', () => {
    p.plan.episodes = p.plan.episodes.filter((e) => e.ep !== 60);
    const f = checkSeasonFrame(p.plan, frame);
    expect(f[0]).toMatchObject({ check: 'season_frame.count', severity: 'blocker' });
    expect(f[0]!.quote).toContain('нет серий 60');
  });

  it('allows a ±1 shift only with a written reason', () => {
    ep(30).anchors = [];
    ep(31).anchors.push('midpoint');
    expect(codes(checkSeasonFrame(p.plan, frame))).toEqual(['season_frame.anchor.midpoint']);
    p.plan.deviations.push({ anchor: 'midpoint', ep: 31, reason: 'Сцена с Зоей не помещается в 30-ю' });
    expect(checkSeasonFrame(p.plan, frame)).toEqual([]);
  });

  it('does not allow a shift beyond the tolerance even with a reason', () => {
    ep(30).anchors = [];
    ep(33).anchors.push('midpoint');
    p.plan.deviations.push({ anchor: 'midpoint', ep: 33, reason: 'хочется' });
    expect(checkSeasonFrame(p.plan, frame)[0]?.viewerQuestion).toContain('не на своём месте');
  });

  it('reports a missing anchor and a fall longer than 5 episodes', () => {
    ep(8).anchors = [];
    for (const n of [44, 48, 49]) ep(n).anchors.push('fall');
    const f = checkSeasonFrame(p.plan, frame);
    expect(f.map((x) => x.quote)).toEqual(
      expect.arrayContaining(['Опорная точка «paywall_hook» не отмечена ни в одной серии', '«fall» длится 6 серий (44–49)']),
    );
  });
});

describe('checkRhythm', () => {
  it('reports a villain strike without an answer within 3 episodes', () => {
    ep(11).strike_by_heroine = false;
    ep(12).strike_by_heroine = false;
    expect(checkRhythm(p.plan, frame).map((f) => [f.check, f.episode])).toEqual([['rhythm.response', 9]]);
  });

  it('reports three episodes of suffering in a row', () => {
    ep(3).mood = 'suffering';
    const f = checkRhythm(p.plan, frame);
    expect(f[0]).toMatchObject({ check: 'rhythm.suffering', episode: 1, quote: 'Страдание подряд в сериях 1–3' });
  });

  it('reports the same hook three times and wrong emotion count', () => {
    ep(7).hook_type = 'угроза';
    ep(10).emotions = ['горе'];
    expect(codes(checkRhythm(p.plan, frame)).sort()).toEqual(['rhythm.emotions', 'rhythm.hook_repeat']);
  });
});

describe('checkVillainLadder', () => {
  const ladder = (params = {}) => checkVillainLadder(p.bible, p.plan, frame, params);

  it('reports a missing villain', () => {
    p.bible.villains = p.bible.villains.filter((v) => v.rank !== 3);
    expect(codes(ladder())).toEqual(expect.arrayContaining(['villain_ladder.count', 'villain_ladder.rank']));
  });

  it('reports a late takedown, the plan mismatch and the broken order', () => {
    villain('Олег').takedown_ep = 42;
    expect(codes(ladder())).toEqual(
      expect.arrayContaining(['villain_ladder.takedown', 'villain_ladder.takedown_plan', 'villain_ladder.order']),
    );
  });

  it('reports a villain who appears too late', () => {
    villain('Анна').on_screen_ep = 12;
    expect(ladder().map((f) => f.quote)).toContain('Анна впервые в кадре в 12-й серии');
  });

  it('reports a wrong role, repeated punishment and a non-public boss takedown', () => {
    villain('Олег').role = 'boss';
    villain('Анна').punishment.type = 'law';
    villain('Герман').punishment.public = false;
    expect(codes(ladder())).toEqual(
      expect.arrayContaining(['villain_ladder.role', 'villain_ladder.punishment', 'villain_ladder.public_legal']),
    );
  });

  it('reports a takedown with no counterstrike', () => {
    ep(22).strike_by_villain = false;
    ep(21).strike_by_villain = false;
    expect(ladder().find((f) => f.check === 'villain_ladder.counterstrike')?.episode).toBe(20);
  });

  it('counts infighting takedowns only when a rule asks', () => {
    villain('Олег').via_infighting = false;
    expect(codes(ladder())).not.toContain('villain_ladder.infighting');
    expect(codes(ladder({ min_infighting: 1 }))).toContain('villain_ladder.infighting');
  });

  it('can be limited to some ranks', () => {
    villain('Олег').takedown_ep = 25;
    villain('Кира').takedown_ep = 5;
    expect(ladder({ ranks: [5] }).every((f) => f.quote.includes('Кира') || f.quote.includes('ранга 5'))).toBe(true);
  });

  it('gives nothing for a genre without a villain ladder', () => {
    expect(checkVillainLadder(p.bible, p.plan, { ...frame, villains: undefined })).toEqual([]);
  });
});

describe('story checks', () => {
  it('betrayer must be the boss or the right hand', () => {
    expect(checkBetrayerRank(p.bible, [1, 2])).toEqual([]);
    p.bible.betrayal.who = 'Олег';
    expect(checkBetrayerRank(p.bible, [1, 2])[0]?.quote).toBe('Предатель Олег — злодей ранга 4');
  });

  it('betrayal within the first 90 seconds', () => {
    p.bible.betrayal.ep1_second = 120;
    expect(checkBetrayalTiming(p.bible, 90)[0]?.episode).toBe(1);
  });

  it('paywall episode must reveal and threaten', () => {
    ep(8).threat_to_heroine = false;
    expect(checkPaywallHook(p.plan, 'paywall_hook')[0]?.fixes[0]).toBe('В 8-й серии: угроза героине');
  });

  it('the secret must change the goal near episode 10 and the midpoint', () => {
    p.bible.secrets[2]!.goal_to = p.bible.secrets[2]!.goal_from;
    expect(codes(checkSecretTurns(p.bible, frame, { min: 2, near: ['secret_turn', 'midpoint'] }))).toEqual([
      'secret_turns.count',
      'secret_turns.near',
    ]);
  });
});

describe('checkTimeline', () => {
  it('the fixed golden bible (Liza 32 in 2026) has no chronology holes', () => {
    expect(checkTimeline(p.bible)).toEqual([]);
  });

  it('finds the contradiction of the first concept: Liza was 2 when her aunt died in 1996, but is 28 in 2026', () => {
    p.bible.characters.find((c) => c.name === 'Лиза')!.birth_year = 1998;
    p.bible.timeline.events.find((e) => e.id === 'e_lisa_born')!.year = 1998;
    const f = checkTimeline(p.bible);
    expect(codes(f)).toEqual(expect.arrayContaining(['timeline.born_after', 'timeline.age']));
    expect(f.map((x) => x.quote)).toContain('«Смерть двоюродной тёти Лизы от «болезни крови»; подмена сестёр» (1996): Лиза 2 лет, а по году рождения (1998) — -2');
  });

  it('reports a dead character taking part later', () => {
    p.bible.timeline.events.push({ id: 'e_x', year: 2000, kind: 'death', text: 'Смерть Олега', participants: ['Олег'], ages: {}, after: [] });
    expect(codes(checkTimeline(p.bible))).toEqual(expect.arrayContaining(['timeline.dead_before']));
  });

  it('reports broken event order and unknown references', () => {
    p.bible.timeline.events.find((e) => e.id === 'e_grimer')!.year = 1960;
    p.bible.timeline.events.find((e) => e.id === 'e_sonya')!.after = ['e_nope'];
    expect(codes(checkTimeline(p.bible)).sort()).toEqual(['timeline.age', 'timeline.order', 'timeline.unknown_ref']);
  });

  it('reports a birth event that disagrees with the birth year', () => {
    p.bible.characters.find((c) => c.name === 'Зоя')!.birth_year = 1950;
    expect(codes(checkTimeline(p.bible))).toContain('timeline.birth');
  });
});

describe('checkKnowledge', () => {
  it('reports a character acting on a fact before learning it', () => {
    ep(14).acts_on = [{ who: 'Лиза', fact: 'f_herman_initiator' }];
    const f = checkKnowledge(p.plan.episodes, p.bible);
    expect(f[0]).toMatchObject({ check: 'knowledge.too_early', episode: 14, holeType: 11 });
    expect(f[0]!.quote).toBe('14-я серия: Лиза действует по «Цикл начал сам Герман», а узнаёт это только в 30-й');
  });

  it('reports a character who never learns the fact', () => {
    ep(20).acts_on = [{ who: 'Даша', fact: 'f_twins' }];
    expect(codes(checkKnowledge(p.plan.episodes, p.bible))).toEqual(['knowledge.unknown']);
  });

  it('reports knowing a fact before it happens', () => {
    p.bible.knowledge.find((k) => k.who === 'Герман')!.since_ep = 40;
    expect(codes(checkKnowledge(p.plan.episodes, p.bible))).toEqual(['knowledge.before_fact']);
  });

  it('reports acting on a fact that is not true yet or does not exist', () => {
    ep(45).acts_on = [{ who: 'Лиза', fact: 'f_vera_testimony' }, { who: 'Лиза', fact: 'f_ghost' }];
    expect(codes(checkKnowledge(p.plan.episodes, p.bible)).sort()).toEqual([
      'knowledge.fact_not_yet',
      'knowledge.unknown',
      'knowledge.unknown_fact',
    ]);
  });
});

describe('production checks', () => {
  const card = (over: Partial<EpisodeCard>): EpisodeCard => CardSchema.parse({ ...sampleCard, location: 'архив', ...over });

  it('limits regular characters and locations; everyone in the episode is not a speaker', () => {
    for (let i = 0; i < 3; i++) p.bible.characters.push({ ...p.bible.characters[0]!, name: `Новый ${i}` });
    const f = checkLimits(p.bible, [card({ ep: 3, location: 'вокзал', cast: ['Лиза', 'Вера', 'Анна', 'Герман'] })], kit.production);
    expect(codes(f).sort()).toEqual(['limits.location_not_listed', 'limits.regular_characters']);
  });

  it('every gun fires, and after it is planted', () => {
    p.bible.guns[0]!.fired_ep = null;
    p.bible.guns[1]!.fired_ep = 3;
    expect(codes(checkGuns(p.bible, frame, p.plan)).sort()).toEqual(['guns.not_fired', 'guns.order']);
  });

  it('script metrics: long line, long overlay, timing, speakers', () => {
    const s: Script = structuredClone(sampleScript) as Script;
    s.blocks.push(
      { t0: 20, t1: 30, kind: 'line', speaker: 'ВЕРА', text: 'Ты правда думаешь, что сможешь так просто уйти из этого дома живой и здоровой?' },
      { t0: 30, t1: 35, kind: 'overlay', text: 'Три дня до премьеры, которую никто не ждал' },
      { t0: 35, t1: 40, kind: 'line', speaker: 'АННА', text: 'Она видела.' },
      { t0: 40, t1: 45, kind: 'line', speaker: 'ГЕРМАН', text: 'Нет.' },
      { t0: 45, t1: 50, kind: 'line', speaker: 'ЛИЗА', text: 'Да.' },
    );
    s.duration_s = 150;
    const f = checkScriptMetrics(s, kit.production, frame);
    expect(codes(f)).toEqual(
      expect.arrayContaining(['script_metrics.line_words', 'script_metrics.overlay_words', 'script_metrics.duration', 'script_metrics.speakers']),
    );
  });

  it('legal markers are suspicions with a quote', () => {
    const f = checkLegalMarkers([{ where: 'План', episode: 7, text: 'Тихий вечер. Вера закурила у окна. Все молчат.' }], kit.legal);
    expect(f).toMatchObject([{ check: 'legal_markers.smoking', controller: 'legal', episode: 7, quote: 'Вера закурила у окна.' }]);
    expect(checkLegalMarkers([{ where: 'План', text: 'Скурила? Нет: просто ушла.' }], kit.legal)).toEqual([]);
  });
});

describe('runCodeChecks', () => {
  it('knows every check the genre rules name', () => {
    for (const r of kit.rules.rules) for (const run of r.check.run) expect(Object.keys(CODE_CHECKS)).toContain(run.fn);
  });

  it('tags findings with the rule and its severity', () => {
    ep(3).mood = 'suffering';
    const f = runCodeChecks({ kit, ...p }).findings.find((x) => x.check === 'rhythm.suffering');
    expect(f).toMatchObject({ rule: 'R04', severity: 'blocker' });
  });

  it('keeps findings no rule covers', () => {
    p.bible.guns[0]!.fired_ep = null;
    const { findings } = runCodeChecks({ kit, ...p });
    expect(findings.map((f) => f.check)).toEqual(['guns.not_fired']);
    expect(findings[0]!.rule).toBeUndefined();
  });

  it('skips checks whose input is missing', () => {
    const res = runCodeChecks({ kit, bible: p.bible });
    expect(res.evaluatedRules.has('R08')).toBe(false);
    expect(res.evaluatedRules.has('R17')).toBe(false);
    expect(res.evaluatedRules.has('R02')).toBe(true);
  });

  it('fails on a rule that names a check the code does not have', () => {
    const broken: GenreKit = structuredClone(kit);
    broken.rules.rules[0]!.check.run = [{ fn: 'no_such_check', params: {} }];
    expect(() => runCodeChecks({ kit: broken, ...p })).toThrow(UnknownCheckError);
  });
});

describe('scoreChecklist', () => {
  it('loses the points of a broken rule', () => {
    villain('Кира').takedown_ep = 6;
    const score = scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...p }));
    expect(score.items.filter((i) => i.status === 'fail').map((i) => i.id).sort()).toEqual(['C02', 'C07']);
    // Model-judged rules (R01, R09, R10, R15, R16) wait for the judge: 10 points unknown.
    expect(score.score).toBe(4);
    expect(score.passed).toBe(null);
  });

  it('fails for sure when the unknown points cannot save it', () => {
    ep(8).anchors = [];
    ep(30).anchors = [];
    villain('Кира').takedown_ep = 6;
    p.bible.betrayal.ep1_second = 200;
    const score = scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...p }));
    expect(score.passed).toBe(false);
    expect(score.finding?.check).toBe('checklist.score');
  });
});

describe('review fixes', () => {
  it('a plan/bible takedown mismatch is not promoted to a rank-5 blocker (only matches whole codes)', () => {
    ep(3).takedown_rank = undefined;
    ep(4).takedown_rank = 5;
    const res = runCodeChecks({ kit, ...p });
    const f = res.findings.find((x) => x.check === 'villain_ladder.takedown_plan');
    expect(f).toMatchObject({ rule: 'R11', severity: 'blocker' });
    expect(res.byRule.R05).toEqual([]);
  });

  it('when several rules cover a finding, the strictest severity wins', () => {
    ep(1).anchors = ['betrayal'];
    ep(5).anchors.push('mask');
    const f = runCodeChecks({ kit, ...p }).findings.find((x) => x.check === 'season_frame.anchor.mask');
    expect(f?.severity).toBe('blocker');
  });

  it('a checklist item counts only its own sub-check', () => {
    ep(37).anchors = [];
    ep(40).anchors.push('pinch2');
    const score = scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...p }));
    expect(score.items.filter((i) => i.status === 'fail')).toEqual([]);
    expect(score.score).toBe(8);
  });

  it('«законный финал» fails on a vigilante marker instead of scoring by code', () => {
    ep(60).event = 'Героиня устраивает самосуд и своими руками наказывает мужа';
    const score = scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...p }));
    expect(score.items.find((i) => i.id === 'C08')?.status).toBe('fail');
  });

  it('a legal marker matches «е» written for «ё»', () => {
    expect(checkLegalMarkers([{ where: 'План', text: 'Он бьет её.' }], kit.legal)).toHaveLength(1);
  });

  it('a moved takedown is excused by the villain role in the deviations', () => {
    const k = villain('Кира');
    const planned = ep(k.takedown_ep);
    const moved = k.takedown_ep + 1;
    planned.takedown_rank = undefined;
    ep(moved).takedown_rank = k.rank;
    k.takedown_ep = moved;
    const before = codes(checkVillainLadder(p.bible, p.plan, frame));
    p.plan.deviations.push({ anchor: k.role, ep: moved, reason: 'Нужна лишняя серия на подготовку' });
    const after = codes(checkVillainLadder(p.bible, p.plan, frame));
    expect(before).toContain('villain_ladder.takedown');
    expect(after).not.toContain('villain_ladder.takedown');
  });

  it('legal markers are minor suspicions and match words, not parts of other words', () => {
    const f = checkLegalMarkers([{ where: 'План', text: 'Разговор о ментальном здоровье. Он закурил сигарету.' }], kit.legal);
    expect(f.map((x) => [x.check, x.severity])).toEqual([['legal_markers.smoking', 'minor']]);
  });

  it('a law takedown next to a public-and-legal one is not a repeated punishment', () => {
    villain('Олег').punishment.type = 'status';
    villain('Анна').punishment.type = 'law';
    expect(codes(checkVillainLadder(p.bible, p.plan, frame))).not.toContain('villain_ladder.punishment');
  });

  it('a rule filter naming a code the check does not emit is an error, not a silent pass', () => {
    const broken: GenreKit = structuredClone(kit);
    broken.rules.rules[3]!.check.run = [{ fn: 'rhythm', only: ['suferring'], params: {} }];
    expect(() => runCodeChecks({ kit: broken, ...p })).toThrow('У проверки «rhythm» нет кода «suferring»');
  });
});
