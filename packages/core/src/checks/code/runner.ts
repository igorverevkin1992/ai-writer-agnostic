import type { CheckRun, GenreKit } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import type { Script } from '../../schemas/script.ts';
import { checkSeasonFrame } from './frame.ts';
import { checkKnowledge } from './knowledge.ts';
import { checkBetrayerRank, checkVillainLadder, type LadderParams } from './ladder.ts';
import { checkGuns, checkLegalMarkers, checkLimits, checkScriptMetrics, collectTexts } from './production.ts';
import { checkRhythm } from './rhythm.ts';
import { checkBetrayalTiming, checkPaywallHook, checkSecretTurns, type PaywallNeed, type SecretTurnParams } from './story.ts';
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
  rhythm: { always: true, codes: ['response', 'suffering', 'hook_repeat', 'emotions', 'line_gap'], run: ({ plan, kit }) => plan && checkRhythm(plan, kit.frame, kit.genre.terms) },
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
      const bad = needs.filter((n) => n !== 'reveal' && n !== 'threat');
      if (bad.length) throw new UnknownCheckError(`Проверка «paywall_hook»: needs — только reveal и threat, а не ${bad.join(', ')}`);
      return plan && checkPaywallHook(plan, p.anchor, needs.length ? (needs as PaywallNeed[]) : undefined, kit.genre.terms);
    },
  },
  world_rules: {
    always: false, codes: ['count', 'unshown'],
    run: ({ bible }, p) =>
      bible && checkWorldRules(bible, { min: num(p, 'min') ?? 1, max: num(p, 'max') ?? Infinity, requireEpisodes: p.require_episodes === true }),
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
