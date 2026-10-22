import { genreKit, loadKb, resolveFrameInputs, SeasonFrame as FrameSchema } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { loadGolden } from '../../fixtures.ts';
import { loadProducerHoles } from '../../eval/auditEval.ts';
import { Bible, EpisodeCard, EpisodeOutline, SeasonPlan, Script } from '../../schemas/index.ts';
import { sampleBible, sampleCard, sampleScript } from '../../schemas/samples.ts';
import { scoreChecklist } from './checklist.ts';
import { checkScriptMetrics } from './production.ts';
import { runCodeChecks } from './runner.ts';

const kb = loadKb();
const kit = genreKit(kb, 'vertical_thriller');
const frame = kit.frame;
const codes = (fs: { check?: string }[]) => fs.map((f) => f.check).sort();
const of = (fs: { check?: string }[], prefix: string) => codes(fs).filter((c) => c!.startsWith(prefix));

/** A season plan that follows the thriller frame to the letter. */
function thrillerPlan(): SeasonPlan {
  const hooks = ['ticking', 'attack', 'revelation', 'betrayal', 'mystical_shift', 'decision'];
  const anchorsOf = (ep: number) =>
    Object.entries(frame.anchors)
      .filter(([, spec]) => (typeof spec === 'number' ? spec === ep : Array.isArray(spec) && spec[0] === ep))
      .map(([id]) => id);
  const episodes = Array.from({ length: frame.episodes }, (_, i) => {
    const ep = i + 1;
    return EpisodeOutline.parse({
      ep,
      title: `Серия ${ep}`,
      event: `Событие ${ep}`,
      question: `Вопрос ${ep}?`,
      emotions: ['страх', 'надежда'],
      heroine_action: `Действие ${ep}`,
      mood: ep % 4 === 0 ? 'kaif' : 'neutral',
      anchors: anchorsOf(ep),
      hook_type: ep === 20 ? 'false_rescue' : hooks[ep % hooks.length],
      cliffhanger: `Обрыв ${ep}`,
      knowledge_mode: ep % 3 === 0 ? 'surprise' : 'suspense',
      threat_seen_by_viewer: ep % 3 === 1,
      threat_known_to_hero: ep % 3 === 0,
      irreversible: ep === 1,
      reveals_secret: ep === 10,
      threat_to_heroine: ep === 10,
      question_turn: ep === 10,
      personal_stake: ep === 10 ? 'Сестра в шкафу' : '',
      strongest_twist: ep === 27,
      arc: ep === 2 || ep === 5 || ep === 8 ? 'lie' : ep === 35 ? 'relapse' : undefined,
      victory: ep === 7,
      cost: ep === 7 ? 'Сосед теперь знает её имя' : '',
    });
  });
  return SeasonPlan.parse({ episodes, deviations: [] });
}

function thrillerBible(): Bible {
  const q = (level: number, question: string, opened: number, closed?: number) => ({ level, question, scale: level, opened_ep: opened, closed_ep: closed });
  const clues = [0, 1, 2, 3, 4, 5].flatMap((level) =>
    [1, 2, 3].map((n) => ({ id: `c${level}_${n}`, text: `Улика ${level}.${n}`, question: level, visual: n === 1, contradicts: n === 3, planted_ep: 1 + level * 10, paid_ep: 6 + level * 10 })),
  );
  return Bible.parse({
    ...sampleBible,
    villains: [],
    guns: [],
    characters: [{ ...sampleBible.characters[0], hero: true }, { ...sampleBible.characters[0], name: 'Сосед' }],
    locations: ['Квартира 9Б', 'Лестница'],
    world_rules: [
      { ...sampleBible.world_rules[0], rule: 'После 3:07 не открывать', heard_eps: [2], plays_eps: [3] },
      { ...sampleBible.world_rules[0], rule: 'Названный по имени не отвечает', heard_eps: [14], plays_eps: [20] },
    ],
    stakes: {
      external: { text: 'Выживет ли героиня в доме', closed_ep: 60 },
      internal: { text: 'Сестра: спасёт или потеряет', closed_ep: 59 },
      philosophical: { text: 'Правда или покой', closed_ep: 60 },
    },
    trunk: { question: 'Кто убивает жильцов дома?', answer: 'Дом', next_question: 'Почему героиня жила здесь в 2019-м?' },
    nested: [q(1, 'Кто убил соседа?', 1, 12), q(2, 'Почему убийца умер пять лет назад?', 12, 24), q(3, 'Сколько жильцов — не те?', 24, 36), q(4, 'Кто управляет домом?', 36, 50), q(5, 'Что это значит для героини?', 50)],
    threat: { archetype: 'двойник', identity: 'Она сама из 2019-го', visual_sign: 'Красная нитка на запястье', sign_shown_ep: 2 },
    clues,
  });
}

function thrillerCard(ep: number): EpisodeCard {
  return EpisodeCard.parse({
    ...sampleCard,
    ep,
    cast: ['Лиза', 'Сосед'],
    location: 'Квартира 9Б',
    goal: 'Найти сестру',
    obstacle: 'Дверь заперта изнутри',
    question: 'Кто стучит?',
    link_prev: 'instant',
    audio: [{ cue: 'три стука', visual_double: 'дрожь двери' }],
    beats: [
      { t0: 0, t1: 3, type: 'HOOK', text: 'В глазке — она сама', hook_kind: 'norm_violation' },
      { t0: 3, t1: 15, type: 'STAKES', text: 'Хочет открыть, но записка запрещает' },
      { t0: 15, t1: 45, type: 'ESCALATION', text: 'Стук повторяется', new_info: 'Стучат изнутри' },
      { t0: 45, t1: 80, type: 'REVERSAL', text: 'Видео: она сама вошла вчера', kind: 'clue_flip', mute_readable: true },
      { t0: 80, t1: 90, type: 'CLIFFHANGER', text: 'Дверь открывается сама', kind: 'mystical_shift' },
    ],
  });
}

describe('vertical thriller genre pack', () => {
  const plan = thrillerPlan();
  const bible = thrillerBible();

  it('resolves the frame from its inputs: paywall 10, answer 11–12, resets at 45% and 80%, finale L-4..L', () => {
    expect(frame.inputs).toEqual({ paywall: 10 });
    expect(frame.free).toBe(10);
    expect(frame.anchors).toMatchObject({ false_solution: 8, solution_collapse: 9, paywall_hook: 10, paywall_answer: [11, 12], reset1: [25, 30], false_finale: [46, 50], clues_converge: 56, finale: 60 });
    expect(frame.blocks[0]).toEqual([1, 3]);
    expect(frame.blocks.at(-1)).toEqual([51, 60]);
    expect(frame.villains).toBeUndefined();
  });

  it('rebuilds the pre-paywall arc for another paywall episode or season length', () => {
    const raw = { season_frame: { inputs: { paywall: 12 }, episodes: 'L', free: 'paywall', anchors: { hook: 1, false_solution: 'paywall-2', paywall_answer: 'paywall+1..paywall+2', reset1: '0.45*L', finale: 'L' }, blocks: ['1..L'] } };
    const a = resolveFrameInputs(raw, { L: 80 }) as { season_frame: Record<string, unknown> };
    expect(a.season_frame).toMatchObject({ episodes: 80, free: 12, anchors: { false_solution: 10, paywall_answer: [13, 14], reset1: 36, finale: 80 }, blocks: [[1, 80]] });
    const b = resolveFrameInputs(raw, { paywall: 8, L: 60 }) as { season_frame: Record<string, unknown> };
    expect(b.season_frame).toMatchObject({ free: 8, anchors: { false_solution: 6, paywall_answer: [9, 10], reset1: 27 } });
    expect(() => FrameSchema.parse({ season_frame: { inputs: {}, episodes: 60, free: 'pw', anchors: {}, blocks: [[1, 60]], tolerance: 1, duration_s: { min: 60, max: 90, target: 90 }, rhythm: { response_within: 3, max_suffering_run: 2, max_same_hook_run: 2, emotions_per_episode: [2, 3] } } })).toThrow(/pw/u);
  });

  it('a plan and bible built by the study pass every code rule of the genre', () => {
    const res = runCodeChecks({ kit, bible, plan, cards: [thrillerCard(1), thrillerCard(12)] });
    expect(res.findings.map((f) => `${f.check} [${f.episode ?? '-'}] ${f.quote}`)).toEqual([]);
    const withCode = kit.rules.rules.filter((r) => r.check.run.length > 0).map((r) => r.id);
    // R05–R07, R14 and R19 need scripts, which appear at step 6.
    expect([...res.evaluatedRules].sort()).toEqual(withCode.filter((id) => !['R05', 'R06', 'R07', 'R14', 'R19'].includes(id)).sort());
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.items.filter((i) => i.status === 'fail')).toEqual([]);
  });

  it('matryoshka: trunk words, levels, scale, chain, active subplots, finale questions', () => {
    const b = structuredClone(bible);
    b.trunk!.question = 'Кто на самом деле стоит за всеми этими странными исчезновениями жильцов нашего старого дома';
    b.nested[1]!.scale = 1;
    b.nested[2]!.opened_ep = 30;
    b.nested[3]!.closed_ep = undefined;
    const found = runCodeChecks({ kit, bible: b, plan }).findings;
    expect(of(found, 'matryoshka')).toEqual(['matryoshka.chain', 'matryoshka.finale_questions', 'matryoshka.scale', 'matryoshka.trunk']);
    const few = { ...bible, nested: bible.nested.slice(0, 2), trunk: undefined };
    expect(of(runCodeChecks({ kit, bible: Bible.parse(few), plan }).findings, 'matryoshka')).toEqual(['matryoshka.levels', 'matryoshka.unmarked']);
    const crowded = { ...bible, nested: bible.nested.map((q) => ({ ...q, opened_ep: 1, closed_ep: undefined })) };
    expect(of(runCodeChecks({ kit, bible: Bible.parse(crowded), plan }).findings, 'matryoshka')).toContain('matryoshka.active');
  });

  it('clues: count per question, a visual one, a contradicting one, payoff within 15 episodes', () => {
    const b = structuredClone(bible);
    b.clues = b.clues.filter((c) => c.question !== 1).map((c) => (c.question === 2 ? { ...c, visual: false, contradicts: false } : c));
    b.clues[0]!.paid_ep = 40;
    expect(of(runCodeChecks({ kit, bible: b, plan }).findings, 'clues')).toEqual(['clues.contradiction', 'clues.count', 'clues.payoff_late', 'clues.visual']);
  });

  it('threat sign, golden three, resets, knowledge mode, hook quota, strongest twist', () => {
    const b = structuredClone(bible);
    b.threat!.sign_shown_ep = 9;
    const p = structuredClone(plan);
    p.episodes[0]!.irreversible = false;
    p.episodes[9]!.strongest_twist = true;
    p.episodes[21]!.hook_type = 'false_rescue';
    p.episodes[0]!.threat_known_to_hero = true;
    for (const e of p.episodes) if (e.ep >= 40 && e.ep <= 45) e.knowledge_mode = 'suspense';
    for (const e of p.episodes) e.anchors = e.anchors.filter((a) => a !== 'false_finale');
    const found = runCodeChecks({ kit, bible: b, plan: p, cards: [{ ...thrillerCard(1), cast: ['Лиза', 'Сосед', 'Мать'] }] }).findings;
    expect(codes(found)).toEqual(
      expect.arrayContaining(['threat_sign.late', 'golden_three.irreversible', 'golden_three.characters', 'resets.count', 'resets.gap', 'knowledge_mode.same_episode', 'knowledge_mode.suspense_run', 'hook_quota.exceeded', 'strongest_twist.on_paywall', 'season_frame.anchor.false_finale']),
    );
    const noThreat = Bible.parse({ ...bible, threat: undefined });
    expect(of(runCodeChecks({ kit, bible: noThreat, plan }).findings, 'threat')).toEqual(['threat_sign.missing']);
  });

  it('paywall «В+У+Л» and the hook vocabulary', () => {
    const p = structuredClone(plan);
    p.episodes[9]!.question_turn = false;
    p.episodes[9]!.personal_stake = '';
    p.episodes[4]!.hook_type = 'угроза';
    const found = runCodeChecks({ kit, bible, plan: p }).findings;
    expect(found.find((f) => f.check === 'paywall_hook.content')!.fixes[0]).toMatch(/question_turn.*personal_stake/u);
    expect(found.filter((f) => f.check === 'rhythm.hook_unknown').map((f) => f.episode)).toEqual([5]);
  });

  it('world rules: one new rule per episode', () => {
    const b = structuredClone(bible);
    b.world_rules[1]!.heard_eps = [2];
    expect(of(runCodeChecks({ kit, bible: b, plan }).findings, 'world_rules')).toEqual(['world_rules.per_episode']);
  });

  it('beats of a card: order, hook ≤3 s, stakes ≤15 s, new info, one reversal readable muted, cliffhanger last', () => {
    const ok = thrillerCard(12);
    expect(runCodeChecks({ kit, bible, plan, cards: [ok] }).findings).toEqual([]);
    const bad = EpisodeCard.parse({
      ...ok,
      duration_s: 60,
      goal: '',
      link_prev: undefined,
      beats: [
        { t0: 0, t1: 5, type: 'HOOK', text: 'x' },
        { t0: 5, t1: 20, type: 'STAKES', text: 'x' },
        { t0: 20, t1: 30, type: 'ESCALATION', text: 'x' },
        { t0: 30, t1: 40, type: 'REVERSAL', text: 'x', mute_readable: false },
        { t0: 40, t1: 50, type: 'REVERSAL', text: 'y' },
        { t0: 50, t1: 60, type: 'STAKES', text: 'ответ' },
      ],
    });
    const found = of(runCodeChecks({ kit, bible, plan, cards: [bad] }).findings, 'beats');
    expect(found).toEqual(['beats.cliffhanger', 'beats.goal', 'beats.hook_long', 'beats.link', 'beats.mute', 'beats.new_info', 'beats.order', 'beats.reversal_double', 'beats.stakes_late']);
    expect(of(runCodeChecks({ kit, bible, plan, cards: [EpisodeCard.parse({ ...sampleCard, ep: 2 })] }).findings, 'beats')).toEqual(['beats.missing']);
    const finale = EpisodeCard.parse({ ...ok, ep: 60, beats: ok.beats!.map((b) => (b.type === 'CLIFFHANGER' ? { ...b, type: 'REVERSAL' } : b)) });
    expect(of(runCodeChecks({ kit, bible, plan, cards: [finale] }).findings, 'beats')).toEqual([]);
  });

  it('script linter: flashback in episode 1, talking heads, faces, wide shots, sound doubles, hits, speakers, overlap, voice-over', () => {
    const script = Script.parse({
      ep: 1,
      title: 'x',
      duration_s: 90,
      blocks: [
        { t0: 0, t1: 6, kind: 'scene', text: 'ИНТ. КВАРТИРА — НОЧЬ', flashback: true, overlap: true, shot: 'wide' },
        { t0: 6, t1: 8, kind: 'scene', text: 'Коридор', shot: 'wide', faces: 3 },
        { t0: 8, t1: 10, kind: 'sound', text: 'три стука', plot: true },
        { t0: 10, t1: 11, kind: 'sound', text: 'удар', hit: true },
        { t0: 11, t1: 12, kind: 'sound', text: 'удар два', hit: true },
        { t0: 12, t1: 18, kind: 'line', speaker: 'А', text: 'раз' },
        { t0: 18, t1: 24, kind: 'line', speaker: 'Б', text: 'два' },
        { t0: 24, t1: 30, kind: 'line', speaker: 'В', text: 'три' },
        { t0: 30, t1: 36, kind: 'line', speaker: 'Г', text: 'четыре' },
        { t0: 36, t1: 50, kind: 'line', speaker: 'Д', text: 'пять', voiceover: true },
        { t0: 50, t1: 90, kind: 'scene', text: 'Тишина' },
      ],
    });
    const found = of(runCodeChecks({ kit, bible, plan, scripts: [script] }).findings, 'script_linter');
    expect(found).toEqual(['script_linter.faces', 'script_linter.flashback', 'script_linter.overlap', 'script_linter.sound_double', 'script_linter.sound_hits', 'script_linter.speakers_episode', 'script_linter.talking_heads', 'script_linter.voiceover', 'script_linter.wide_shots']);
    expect(of(runCodeChecks({ kit, bible, plan, scripts: [sampleScript] }).findings, 'script_linter')).toEqual([]);
  });

  it('a long line warns past 15 words and blocks past 20', () => {
    const words = (n: number) => Array.from({ length: n }, (_, i) => `слово${i}`).join(' ');
    const script = (n: number) => Script.parse({ ...sampleScript, blocks: [{ t0: 0, t1: 90, kind: 'line', speaker: 'А', text: words(n) }] });
    const sev = (n: number) => checkScriptMetrics(script(n), kit.production).filter((f) => f.check === 'script_metrics.line_words').map((f) => f.severity);
    expect(sev(12)).toEqual([]);
    expect(sev(17)).toEqual(['minor']);
    expect(sev(21)).toEqual(['major']);
  });

  it('the stress test rejects a concept on a failed critical item whatever the score', () => {
    const p = structuredClone(plan);
    p.episodes[9]!.question_turn = false;
    const res = runCodeChecks({ kit, bible, plan: p });
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.items.find((i) => i.id === 'S09')?.status).toBe('fail');
    expect(score.passed).toBe(false);
    expect(kit.checklist.items.filter((i) => i.critical).map((i) => i.id)).toEqual(['S01', 'S02', 'S03', 'S09']);
  });
});

describe('golden project «Прима» (vertical thriller)', () => {
  const golden = loadGolden('prima');

  it('passes every code check of its genre and the stress test has no failed item', () => {
    const res = runCodeChecks({ kit, ...golden });
    expect(res.findings.map((f) => `${f.check} [${f.episode ?? '-'}] ${f.quote}`)).toEqual([]);
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.items.filter((i) => i.status === 'fail')).toEqual([]);
    expect(score.passed).not.toBe(false);
  });

  it('follows the study\'s season grid: paywall «В+У+Л» in 10, the strongest twist on reset 1, five nested questions', () => {
    const ep = (n: number) => golden.plan.episodes.find((e) => e.ep === n)!;
    expect(ep(10)).toMatchObject({ anchors: ['paywall_hook'], question_turn: true, threat_to_heroine: true });
    expect(ep(10).personal_stake).not.toBe('');
    expect(ep(27)).toMatchObject({ anchors: ['reset1'], strongest_twist: true });
    expect(golden.bible.nested).toHaveLength(5);
    expect(golden.bible.nested.map((q) => q.closed_ep)).toEqual([12, 24, 36, 50, 60]);
    expect(golden.plan.episodes.filter((e) => e.ep <= 3 && e.irreversible).length).toBeGreaterThan(0);
  });
});

describe('golden project «Прима. Сумерки» (the mystical Prima)', () => {
  const dusk = loadGolden('prima_dusk');

  it('passes every code check of its genre with real supernatural rules of the world', () => {
    const res = runCodeChecks({ kit, ...dusk });
    expect(res.findings.map((f) => `${f.check} [${f.episode ?? '-'}] ${f.quote}`)).toEqual([]);
    expect(scoreChecklist(kit.checklist, kit.rules, res).items.filter((i) => i.status === 'fail')).toEqual([]);
    expect(dusk.bible.world_rules).toHaveLength(4);
    expect(dusk.bible.nested.map((q) => q.closed_ep)).toEqual([12, 24, 36, 50, 60]);
    expect(dusk.bible.characters.filter((c) => c.regular)).toHaveLength(6);
  });
});

describe('golden project «Прима» v1: the planted holes for the auditor', () => {
  it('keeps every code check silent, so the holes are the model\'s to find', () => {
    const kb = loadKb();
    const kit = genreKit(kb, 'vertical_thriller');
    const holed = loadGolden('prima_v1');
    expect(runCodeChecks({ kit, bible: holed.bible, plan: holed.plan }).findings).toEqual([]);
    // The benchmark is frozen at the Prima of 17.10.2026: the clean Prima keeps evolving.
    const holes = loadProducerHoles('prima_v1')!;
    expect(holes.holes).toHaveLength(28);
    for (const h of holes.holes) {
      expect(h.holeType, h.id).toBeDefined();
      const eps = [h.episode, ...(h.episodes ?? '').split(/[,\s–-]+/u).map(Number)].filter((n): n is number => !!n);
      for (const ep of eps) expect(holed.plan.episodes.some((e) => e.ep === ep), `${h.id}: серия ${ep}`).toBe(true);
    }
  });
});
