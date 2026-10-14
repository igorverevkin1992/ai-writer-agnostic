import type { CheckRun, GenreKit } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import type { Script } from '../../schemas/script.ts';
import { checkSeasonFrame } from './frame.ts';
import { checkKnowledge } from './knowledge.ts';
import { checkHeroArc, checkPromise, checkStakes, checkVictoryCost, type HeroArcParams } from './method.ts';
import { checkBetrayerRank, checkVillainLadder, type LadderParams } from './ladder.ts';
import { checkGuns, checkLegalMarkers, checkLimits, checkScriptMetrics, collectTexts } from './production.ts';
import { checkRhythm } from './rhythm.ts';
import { checkBetrayalTiming, checkPaywallHook, checkSecretTurns, PAYWALL_NEEDS, type PaywallNeed, type SecretTurnParams } from './story.ts';
import { checkBeats, checkClues, checkGolden3, checkHookQuota, checkKnowledgeMode, checkMatryoshka, checkResets, checkScriptLinter, checkStrongestTwist, checkThreatSign } from './thriller.ts';
import { checkTimeline } from './timeline.ts';
import { checkWorldRules } from './world.ts';

/** What is available to check. Checks whose inputs are missing are skipped. */
export interface CheckInput {
  kit: GenreKit;
  bible?: Bible;
  plan?: SeasonPlan;
  cards?: EpisodeCard[];
  scripts?: Script[];
}

type Params = Record<string, unknown>;

interface CodeCheck {
  /** Returns undefined when its inputs are not available yet. */
  run(input: CheckInput, params: Params): Finding[] | undefined;
  /** Runs on every check pass even if no rule names it. Checks that need params do not. */
  always: boolean;
  /**
   * Finding codes this check emits (after "<fn>."). A rule's `only` must name one of them,
   * or a sub-code under it ("anchor.mask" under "anchor"). null — any code (e.g. legal categories).
   */
  codes: string[] | null;
}

const num = (p: Params, key: string): number | undefined => (typeof p[key] === 'number' ? (p[key] as number) : undefined);
const nums = (p: Params, key: string): number[] | undefined =>
  Array.isArray(p[key]) ? (p[key] as unknown[]).filter((x): x is number => typeof x === 'number') : undefined;
const strs = (p: Params, key: string): string[] =>
  Array.isArray(p[key]) ? (p[key] as unknown[]).filter((x): x is string => typeof x === 'string') : [];

/** Every code check a genre rule may reference in check.run[].fn. */
export const CODE_CHECKS: Record<string, CodeCheck> = {
  season_frame: { always: true, codes: ['count', 'anchor'], run: ({ plan, kit }) => plan && checkSeasonFrame(plan, kit.frame) },
  rhythm: { always: true, codes: ['response', 'suffering', 'hook_repeat', 'hook_unknown', 'emotions', 'line_gap'], run: ({ plan, kit }) => plan && checkRhythm(plan, kit.frame, kit.genre.terms) },
  villain_ladder: {
    always: true, codes: ['count', 'rank', 'role', 'on_screen', 'introduced', 'takedown', 'takedown_plan', 'key_to_next', 'counterstrike', 'public_legal', 'order', 'punishment', 'turned_ally', 'infighting'],
    run: ({ bible, plan, kit }, p) =>
      bible && plan && checkVillainLadder(bible, plan, kit.frame, { ranks: nums(p, 'ranks'), min_infighting: num(p, 'min_infighting') } satisfies LadderParams, kit.genre.terms),
  },
  timeline: { always: true, codes: ['birth', 'born_after', 'dead_before', 'age', 'unknown_ref', 'order'], run: ({ bible }) => bible && checkTimeline(bible) },
  knowledge: {
    always: true, codes: ['before_fact', 'unknown_fact', 'fact_not_yet', 'unknown', 'too_early'],
    run: ({ bible, cards, plan }) => {
      const items = cards ?? plan?.episodes;
      return bible && items && checkKnowledge(items, bible);
    },
  },
  limits: { always: true, codes: ['regular_characters', 'locations', 'location_not_listed'], run: ({ bible, cards, kit }) => bible && checkLimits(bible, cards ?? [], kit.production) },
  guns: { always: true, codes: ['not_fired', 'order', 'outside'], run: ({ bible, plan, kit }) => bible && checkGuns(bible, kit.frame, plan) },
  script_metrics: {
    always: true, codes: ['chars_per_minute', 'line_words', 'overlay_words', 'duration', 'speakers'],
    run: ({ scripts, kit }) => scripts && scripts.flatMap((s) => checkScriptMetrics(s, kit.production, kit.frame)),
  },
  legal_markers: {
    always: true, codes: null,
    run: (input) => {
      const texts = collectTexts(input);
      return texts.length ? checkLegalMarkers(texts, input.kit.legal) : undefined;
    },
  },
  betrayal_timing: {
    always: false, codes: ['late'],
    run: ({ bible }, p) => bible && checkBetrayalTiming(bible, num(p, 'max_second') ?? Infinity),
  },
  betrayer_rank: { always: false, codes: ['rank'], run: ({ bible }, p) => bible && checkBetrayerRank(bible, nums(p, 'ranks') ?? []) },
  paywall_hook: {
    always: false, codes: ['content'],
    run: ({ plan, kit }, p) => {
      // The anchor id comes from the genre rule: code does not know the frame's anchor names.
      if (typeof p.anchor !== 'string') throw new UnknownCheckError('Проверке «paywall_hook» нужен параметр anchor — id опорной точки из каркаса');
      const needs = strs(p, 'needs');
      const bad = needs.filter((n) => !(PAYWALL_NEEDS as string[]).includes(n));
      if (bad.length) throw new UnknownCheckError(`Проверка «paywall_hook»: needs — только ${PAYWALL_NEEDS.join(', ')}, а не ${bad.join(', ')}`);
      return plan && checkPaywallHook(plan, p.anchor, needs.length ? (needs as PaywallNeed[]) : undefined, kit.genre.terms);
    },
  },
  world_rules: {
    always: false, codes: ['count', 'unshown', 'per_episode'],
    run: ({ bible }, p) =>
      bible && checkWorldRules(bible, { min: num(p, 'min') ?? 1, max: num(p, 'max') ?? Infinity, requireEpisodes: p.require_episodes === true, maxPerEpisode: num(p, 'max_per_episode') }),
  },
  // Vertical thriller study: the season question, clues, threat, golden three, resets, irony, beats, linter.
  matryoshka: {
    always: false, codes: ['unmarked', 'trunk', 'levels', 'scale', 'chain', 'active', 'finale_questions'],
    run: ({ bible, kit }, p) => bible && checkMatryoshka(bible, kit.frame, { minLevels: num(p, 'min_levels') ?? 5, maxActive: num(p, 'max_active') ?? 3, maxQuestionWords: num(p, 'max_question_words') ?? 12 }),
  },
  clues: { always: false, codes: ['count', 'visual', 'contradiction', 'payoff_late'], run: ({ bible }, p) => bible && checkClues(bible, { min: num(p, 'min') ?? 3, payoffWithin: num(p, 'payoff_within') ?? 15 }) },
  threat_sign: { always: false, codes: ['missing', 'late'], run: ({ bible }, p) => bible && checkThreatSign(bible, num(p, 'by_ep') ?? 5) },
  golden_three: { always: false, codes: ['irreversible', 'characters'], run: ({ plan, cards }, p) => plan && checkGolden3(plan, cards, { irreversibleBy: num(p, 'irreversible_by') ?? 3, maxCastEp1: num(p, 'max_cast_ep1') ?? 2 }) },
  resets: {
    always: false, codes: ['count', 'gap'],
    run: ({ plan, kit }, p) => {
      const anchors = strs(p, 'anchors');
      if (!anchors.length) throw new UnknownCheckError('Проверке «resets» нужен параметр anchors — id опорных точек перезагрузок');
      return plan && checkResets(plan, kit.frame, { anchors, min: num(p, 'min') ?? 2, maxGap: num(p, 'max_gap') ?? 25 });
    },
  },
  knowledge_mode: { always: false, codes: ['unmarked', 'same_episode', 'suspense_run'], run: ({ plan, kit }, p) => plan && checkKnowledgeMode(plan, num(p, 'max_suspense_run') ?? 3, kit.genre.terms) },
  hook_quota: {
    always: false, codes: ['exceeded'],
    run: ({ plan, kit }, p) => {
      if (typeof p.type !== 'string') throw new UnknownCheckError('Проверке «hook_quota» нужен параметр type — тип обрыва');
      return plan && checkHookQuota(plan, kit.frame, p.type, num(p, 'per') ?? 6, num(p, 'max') ?? 1);
    },
  },
  strongest_twist: {
    always: false, codes: ['missing', 'on_paywall'],
    run: ({ plan, kit }, p) => {
      if (typeof p.anchor !== 'string') throw new UnknownCheckError('Проверке «strongest_twist» нужен параметр anchor — id опорной точки барьера');
      return plan && checkStrongestTwist(plan, kit.frame, p.anchor);
    },
  },
  beats: { always: false, codes: ['missing', 'order', 'hook_long', 'stakes_late', 'goal', 'new_info', 'reversal', 'reversal_double', 'mute', 'cliffhanger', 'link'], run: ({ cards, kit }) => cards && checkBeats(cards, kit.frame) },
  script_linter: {
    always: false, codes: ['flashback', 'talking_heads', 'faces', 'wide_shots', 'sound_double', 'sound_hits', 'speakers_episode', 'overlap', 'voiceover'],
    run: ({ scripts, kit }, p) =>
      scripts &&
      scripts.flatMap((s) =>
        checkScriptLinter(s, {
          talkingHeadsS: num(p, 'talking_heads_s') ?? 10,
          voiceoverS: num(p, 'voiceover_s') ?? 8,
          maxFaces: num(p, 'max_faces') ?? 2,
          maxSpeakers: num(p, 'max_speakers') ?? kit.production.limits.max_speakers_per_scene,
          maxWideShots: num(p, 'max_wide_shots') ?? 1,
          maxWideS: num(p, 'max_wide_s') ?? 3,
          maxSoundHits: num(p, 'max_sound_hits') ?? 1,
          overlapS: num(p, 'overlap_s') ?? 5,
          paywallEp: kit.frame.free,
          overlapAfterPaywallS: num(p, 'overlap_after_paywall_s') ?? 3,
        }),
      ),
  },
  stakes: { always: false, codes: ['missing', 'closed_outside'], run: ({ bible, kit }) => bible && checkStakes(bible, kit.frame) },
  hero_arc: {
    always: false, codes: ['unmarked', 'lie_acts', 'no_relapse'],
    run: ({ bible, plan, kit }, p) => {
      if (typeof p.anchor !== 'string') throw new UnknownCheckError('Проверке «hero_arc» нужен параметр anchor — id опорной точки мидпоинта из каркаса');
      const params: HeroArcParams = { anchor: p.anchor, minLieActs: num(p, 'min_lie_acts') ?? 3, relapse: p.relapse !== false };
      return bible && plan && checkHeroArc(bible, plan, params, kit.frame, kit.genre.terms);
    },
  },
  victory_cost: { always: false, codes: ['unmarked', 'free'], run: ({ plan, kit }, p) => plan && checkVictoryCost(plan, kit.frame, num(p, 'within') ?? 1, kit.genre.terms) },
  promise: {
    always: false, codes: ['absent'],
    run: ({ plan, kit }, p) => {
      const lines = strs(p, 'lines');
      const anchors = strs(p, 'anchors');
      if (!lines.length && !anchors.length) throw new UnknownCheckError('Проверке «promise» нужны lines или anchors — что жанр обещает зрителю');
      return plan && checkPromise(plan, kit.frame, { ep: num(p, 'ep') ?? 1, lines, anchors });
    },
  },
  secret_turns: {
    always: false, codes: ['count', 'near'],
    run: ({ bible, kit }, p) =>
      bible && checkSecretTurns(bible, kit.frame, { min: num(p, 'min') ?? 1, near: strs(p, 'near') } satisfies SecretTurnParams, kit.genre.terms),
  },
};

export class UnknownCheckError extends Error {
  override name = 'UnknownCheckError';
}

export interface CodeCheckResult {
  /** Unique findings, rule-attributed where a rule covers them. */
  findings: Finding[];
  /** All findings per rule id (a finding may count for several rules). */
  byRule: Record<string, Finding[]>;
  /** Rules whose code part could run on the given input. */
  evaluatedRules: Set<string>;
}

/** A finding code matches a filter when it is the filter itself or a sub-code under it. */
export function codeMatches(check: string | undefined, filter: string): boolean {
  return !!check && (check === filter || check.startsWith(`${filter}.`));
}

function runOne(input: CheckInput, run: CheckRun): Finding[] | undefined {
  const check = CODE_CHECKS[run.fn];
  if (!check) throw new UnknownCheckError(`Проверка «${run.fn}» из базы знаний не существует в коде`);
  for (const o of run.only ?? []) {
    if (check.codes && !check.codes.some((c) => o === c || o.startsWith(`${c}.`))) {
      throw new UnknownCheckError(`У проверки «${run.fn}» нет кода «${o}». Есть: ${check.codes.join(', ')}`);
    }
  }
  const found = check.run(input, run.params);
  if (!found) return undefined;
  const only = run.only;
  return only ? found.filter((f) => only.some((o) => codeMatches(f.check, `${run.fn}.${o}`))) : found;
}

const SEVERITY_RANK = { blocker: 0, major: 1, minor: 2 } as const;

/**
 * Runs code checks: first the ones genre rules name (with their params),
 * then every always-on check so nothing is missed. Findings are deduplicated by id;
 * a rule-attributed finding takes the rule's severity unless the check marked it minor.
 */
export function runCodeChecks(input: CheckInput): CodeCheckResult {
  const unique = new Map<string, Finding>();
  const byRule: Record<string, Finding[]> = {};
  const evaluatedRules = new Set<string>();

  for (const rule of input.kit.rules.rules) {
    const runs = rule.check.run;
    if (runs.length === 0) continue;
    let complete = true;
    const found: Finding[] = [];
    for (const run of runs) {
      const res = runOne(input, run);
      if (res === undefined) complete = false;
      else found.push(...res);
    }
    if (complete) evaluatedRules.add(rule.id);
    byRule[rule.id] = found;
    for (const f of found) {
      const severity = f.severity === 'minor' ? 'minor' : rule.severity;
      const prev = unique.get(f.id);
      // Several rules may cover one finding: the strictest one decides.
      if (prev && SEVERITY_RANK[prev.severity] <= SEVERITY_RANK[severity]) continue;
      unique.set(f.id, { ...f, rule: rule.id, severity });
    }
  }

  for (const [fn, check] of Object.entries(CODE_CHECKS)) {
    if (!check.always) continue;
    for (const f of runOne(input, { fn, params: {} }) ?? []) {
      if (!unique.has(f.id)) unique.set(f.id, f);
    }
  }

  return { findings: [...unique.values()], byRule, evaluatedRules };
}
