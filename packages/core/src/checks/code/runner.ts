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
import { checkBetrayalTiming, checkPaywallHook, checkSecretTurns, type SecretTurnParams } from './story.ts';
import { checkTimeline } from './timeline.ts';

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
}

const num = (p: Params, key: string): number | undefined => (typeof p[key] === 'number' ? (p[key] as number) : undefined);
const nums = (p: Params, key: string): number[] | undefined =>
  Array.isArray(p[key]) ? (p[key] as unknown[]).filter((x): x is number => typeof x === 'number') : undefined;
const strs = (p: Params, key: string): string[] =>
  Array.isArray(p[key]) ? (p[key] as unknown[]).filter((x): x is string => typeof x === 'string') : [];

/** Every code check a genre rule may reference in check.run[].fn. */
export const CODE_CHECKS: Record<string, CodeCheck> = {
  season_frame: { always: true, run: ({ plan, kit }) => plan && checkSeasonFrame(plan, kit.frame) },
  rhythm: { always: true, run: ({ plan, kit }) => plan && checkRhythm(plan, kit.frame) },
  villain_ladder: {
    always: true,
    run: ({ bible, plan, kit }, p) =>
      bible && plan && checkVillainLadder(bible, plan, kit.frame, { ranks: nums(p, 'ranks'), min_infighting: num(p, 'min_infighting') } satisfies LadderParams),
  },
  timeline: { always: true, run: ({ bible }) => bible && checkTimeline(bible) },
  knowledge: {
    always: true,
    run: ({ bible, cards, plan }) => {
      const items = cards ?? plan?.episodes;
      return bible && items && checkKnowledge(items, bible);
    },
  },
  limits: { always: true, run: ({ bible, cards, kit }) => bible && checkLimits(bible, cards ?? [], kit.production) },
  guns: { always: true, run: ({ bible, plan, kit }) => bible && checkGuns(bible, kit.frame, plan) },
  script_metrics: {
    always: true,
    run: ({ scripts, kit }) => scripts && scripts.flatMap((s) => checkScriptMetrics(s, kit.production, kit.frame)),
  },
  legal_markers: {
    always: true,
    run: (input) => {
      const texts = collectTexts(input);
      return texts.length ? checkLegalMarkers(texts, input.kit.legal) : undefined;
    },
  },
  betrayal_timing: {
    always: false,
    run: ({ bible }, p) => bible && checkBetrayalTiming(bible, num(p, 'max_second') ?? Infinity),
  },
  betrayer_rank: { always: false, run: ({ bible }, p) => bible && checkBetrayerRank(bible, nums(p, 'ranks') ?? []) },
  paywall_hook: {
    always: false,
    run: ({ plan }, p) => plan && checkPaywallHook(plan, typeof p.anchor === 'string' ? p.anchor : 'paywall_hook'),
  },
  secret_turns: {
    always: false,
    run: ({ bible, kit }, p) =>
      bible && checkSecretTurns(bible, kit.frame, { min: num(p, 'min') ?? 1, near: strs(p, 'near') } satisfies SecretTurnParams),
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

function runOne(input: CheckInput, run: CheckRun): Finding[] | undefined {
  const check = CODE_CHECKS[run.fn];
  if (!check) throw new UnknownCheckError(`Проверка «${run.fn}» из базы знаний не существует в коде`);
  const found = check.run(input, run.params);
  if (!found) return undefined;
  const only = run.only;
  return only ? found.filter((f) => only.some((o) => f.check?.startsWith(`${run.fn}.${o}`))) : found;
}

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
      if (unique.has(f.id)) continue;
      unique.set(f.id, { ...f, rule: rule.id, severity: f.severity === 'minor' ? 'minor' : rule.severity });
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
