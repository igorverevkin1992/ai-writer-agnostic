import { DEFAULT_TERMS, episodeRange, type GenreTerms, type SeasonFrame } from '@aiw/kb';
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

/** What the paywall episode must hold: a partial reveal of the secret and/or a threat to the protagonist. */
export type PaywallNeed = 'reveal' | 'threat';

/** The episode at the given anchor (paywall hook) partly reveals the secret and threatens the protagonist. */
export function checkPaywallHook(
  plan: SeasonPlan,
  anchor: string,
  needs: PaywallNeed[] = ['reveal', 'threat'],
  terms: GenreTerms = DEFAULT_TERMS,
): Finding[] {
  const noReveal = (e: SeasonPlan['episodes'][number]) => needs.includes('reveal') && !e.reveals_secret;
  const noThreat = (e: SeasonPlan['episodes'][number]) => needs.includes('threat') && !e.threat_to_heroine;
  return plan.episodes
    .filter((e) => e.anchors.includes(anchor) && (noReveal(e) || noThreat(e)))
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
          `В ${e.ep}-й серии: ${[noReveal(e) && 'частичное раскрытие тайны', noThreat(e) && `угроза ${terms.hero.dat}`].filter(Boolean).join(' и ')}`,
        ],
      }),
    );
}

export interface SecretTurnParams {
  min: number;
  /** Frame anchors near which the goal of revenge must change. */
  near: string[];
}

/** The secret changes the protagonist's goal at least `min` times, near the given anchors. */
export function checkSecretTurns(bible: Bible, frame: SeasonFrame, p: SecretTurnParams, terms: GenreTerms = DEFAULT_TERMS): Finding[] {
  const goal = `цель ${terms.hero.gen}`;
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
        quote: `Смен цели ${terms.hero.gen}: ${turns.length}`,
        question: 'тайна что-то меняет или просто добавляет фактов?',
        fixes: [`Не меньше ${p.min} слоёв тайны, меняющих ${goal}`],
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
          quote: `Около «${anchor}» (серии ${lo}–${hi}) ${goal} не меняется`,
          question: `что меняется для ${terms.hero.gen} в районе ${min}-й серии?`,
          fixes: [`Раскрыть слой тайны, меняющий ${goal}, в сериях ${lo}–${hi}`],
        }),
      );
    }
  }
  return out;
}
