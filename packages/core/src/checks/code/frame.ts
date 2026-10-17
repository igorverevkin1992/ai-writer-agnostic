import { episodeRange, type SeasonFrame } from '@aiw/kb';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { formatEpisodes, makeFinding } from './finding.ts';

/**
 * Season frame: episode count and anchors on their numbers.
 * An anchor may move by the frame tolerance only with the author's written reason.
 */
/** This many untagged anchors mean the plan is not marked up, not that the story misses them. */
const UNTAGGED_IS_A_GAP = 3;

export function checkSeasonFrame(plan: SeasonPlan, frame: SeasonFrame): Finding[] {
  const out: Finding[] = [];
  const eps = plan.episodes.map((e) => e.ep).sort((a, b) => a - b);

  if (eps.length !== frame.episodes || eps.some((ep, i) => ep !== i + 1)) {
    const missing = Array.from({ length: frame.episodes }, (_, i) => i + 1).filter((n) => !eps.includes(n));
    const extra = eps.filter((n) => n > frame.episodes);
    out.push(
      makeFinding({
        check: 'season_frame.count',
        controller: 'structure',
        severity: 'blocker',
        holeType: 10,
        quote: `Серий в плане: ${eps.length}${missing.length ? `; нет серий ${formatEpisodes(missing)}` : ''}${extra.length ? `; лишние: ${formatEpisodes(extra)}` : ''}`,
        question: `где остальные серии? В сезоне их должно быть ${frame.episodes}.`,
        fixes: [`Довести план до ${frame.episodes} серий с номерами 1–${frame.episodes}`],
      }),
    );
  }

  // A plan that tags almost no anchors is not a plan with a dozen story holes: it is a plan
  // without anchor marks. Each missing anchor is still reported (the frame metric needs it),
  // but as major instead of blocker, under one finding that says what is really wrong.
  const untagged = Object.keys(frame.anchors).filter((a) => !plan.episodes.some((e) => e.anchors.includes(a)));
  const taggingGap = untagged.length >= UNTAGGED_IS_A_GAP;
  if (taggingGap) {
    out.push(
      makeFinding({
        check: 'season_frame.anchors_unmarked',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        quote: `Не отмечены опорные точки: ${untagged.map((a) => frame.anchor_labels[a] ?? a).join(', ')}`,
        question: `где в плане опорные точки каркаса? Не отмечено ${untagged.length} из ${Object.keys(frame.anchors).length}.`,
        fixes: ['Отметить опорные точки каркаса в сериях плана (`anchors`), чтобы код мог проверить их места'],
      }),
    );
  }

  for (const [anchor, spec] of Object.entries(frame.anchors)) {
    const { min, max } = episodeRange(spec);
    const tagged = plan.episodes.filter((e) => e.anchors.includes(anchor)).map((e) => e.ep);
    const code = `season_frame.anchor.${anchor}`;
    const place = min === max ? `${min}-й серии` : `сериях ${min}–${max}`;
    // People read the anchor's name from the frame, not its id.
    const name = frame.anchor_labels[anchor] ?? anchor;

    if (tagged.length === 0) {
      out.push(
        makeFinding({
          check: code,
          controller: 'structure',
          severity: taggingGap ? 'major' : 'blocker',
          holeType: 10,
          episode: min,
          quote: `Опорная точка «${name}» не отмечена ни в одной серии`,
          question: `где «${name}»? По каркасу она в ${place}.`,
          fixes: [`Поставить «${name}» в ${place} и отметить её в плане`],
        }),
      );
      continue;
    }

    const span = max - min + 1;
    if (min !== max && tagged.length > span) {
      out.push(
        makeFinding({
          check: code,
          controller: 'structure',
          severity: 'blocker',
          holeType: 10,
          episode: Math.min(...tagged),
          quote: `«${name}» длится ${tagged.length} серий (${formatEpisodes(tagged)})`,
          question: `почему «${name}» тянется так долго? Допустимо не больше ${span} серий.`,
          fixes: [`Сократить «${name}» до ${span} серий`],
        }),
      );
    }

    for (const ep of tagged) {
      if (ep >= min && ep <= max) continue;
      const withinTolerance = ep >= min - frame.tolerance && ep <= max + frame.tolerance;
      const reason = plan.deviations.find((d) => d.anchor === anchor && d.ep === ep);
      if (withinTolerance && reason) continue;
      out.push(
        makeFinding({
          check: code,
          controller: 'structure',
          severity: 'blocker',
          holeType: 10,
          episode: ep,
          quote: `«${name}» стоит в ${ep}-й серии`,
          question: withinTolerance
            ? `почему «${name}» сдвинута с ${place}? Объяснения автора нет.`
            : `почему «${name}» не на своём месте? По каркасу она в ${place}.`,
          fixes: withinTolerance
            ? [`Вернуть «${name}» в ${place}`, 'Записать причину сдвига в план (deviations)']
            : [`Перенести «${name}» в ${place}`],
        }),
      );
    }
  }
  return out;
}
