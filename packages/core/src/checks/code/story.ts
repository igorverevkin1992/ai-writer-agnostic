import { episodeRange, type SeasonFrame } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { makeFinding } from './finding.ts';

/** The heroine sees the betrayal early in episode 1. */
export function checkBetrayalTiming(bible: Bible, maxSecond: number): Finding[] {
  const s = bible.betrayal.ep1_second;
  if (s <= maxSecond) return [];
  return [
    makeFinding({
      check: 'betrayal_timing.late',
      controller: 'genre',
      severity: 'blocker',
      holeType: 10,
      episode: 1,
      quote: `Предательство на ${s}-й секунде 1-й серии`,
      question: 'когда уже начнётся история?',
      fixes: [`Показать предательство не позже ${maxSecond}-й секунды`],
    }),
  ];
}

/** The episode at the given anchor (paywall hook) partly reveals the secret and threatens the heroine. */
export function checkPaywallHook(plan: SeasonPlan, anchor: string): Finding[] {
  return plan.episodes
    .filter((e) => e.anchors.includes(anchor) && !(e.reveals_secret && e.threat_to_heroine))
    .map((e) =>
      makeFinding({
        check: 'paywall_hook.content',
        controller: 'genre',
        severity: 'blocker',
        holeType: 10,
        episode: e.ep,
        quote: e.cliffhanger,
        question: 'зачем мне платить за следующую серию?',
        fixes: [
          `В ${e.ep}-й серии: ${[!e.reveals_secret && 'частичное раскрытие тайны', !e.threat_to_heroine && 'угроза героине'].filter(Boolean).join(' и ')}`,
        ],
      }),
    );
}

export interface SecretTurnParams {
  min: number;
  /** Frame anchors near which the goal of revenge must change. */
  near: string[];
}

/** The secret changes the goal of revenge at least `min` times, near the given anchors. */
export function checkSecretTurns(bible: Bible, frame: SeasonFrame, p: SecretTurnParams): Finding[] {
  const norm = (s: string) => s.trim().toLowerCase();
  const turns = bible.secrets.filter((s) => norm(s.goal_from) !== norm(s.goal_to));
  const out: Finding[] = [];
  if (turns.length < p.min) {
    out.push(
      makeFinding({
        check: 'secret_turns.count',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        quote: `Смен цели мести: ${turns.length}`,
        question: 'тайна что-то меняет или просто добавляет фактов?',
        fixes: [`Не меньше ${p.min} слоёв тайны, меняющих цель мести`],
      }),
    );
  }
  for (const anchor of p.near) {
    const spec = frame.anchors[anchor];
    if (spec === undefined) continue;
    const { min, max } = episodeRange(spec);
    const lo = min - frame.tolerance;
    const hi = max + frame.tolerance;
    if (!turns.some((s) => s.revealed_ep >= lo && s.revealed_ep <= hi)) {
      out.push(
        makeFinding({
          check: 'secret_turns.near',
          controller: 'structure',
          severity: 'major',
          holeType: 10,
          episode: min,
          quote: `Около «${anchor}» (серии ${lo}–${hi}) цель мести не меняется`,
          question: `что меняется для героини в районе ${min}-й серии?`,
          fixes: [`Раскрыть слой тайны, меняющий цель мести, в сериях ${lo}–${hi}`],
        }),
      );
    }
  }
  return out;
}
