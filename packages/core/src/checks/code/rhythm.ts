import { DEFAULT_TERMS, episodeRange, type GenreTerms, type SeasonFrame } from '@aiw/kb';
import type { Finding } from '../../schemas/finding.ts';
import type { EpisodeOutline, SeasonPlan } from '../../schemas/season.ts';
import { formatEpisodes, makeFinding } from './finding.ts';

/** Consecutive runs of episodes sharing a key. */
function runs(eps: EpisodeOutline[], key: (e: EpisodeOutline) => string | null): { value: string; eps: number[] }[] {
  const out: { value: string; eps: number[] }[] = [];
  for (const e of eps) {
    const v = key(e);
    const last = out.at(-1);
    if (v !== null && last && last.value === v && last.eps.at(-1) === e.ep - 1) last.eps.push(e.ep);
    else if (v !== null) out.push({ value: v, eps: [e.ep] });
  }
  return out;
}

/** Rhythm: strike → answer, suffering runs, hook variety, emotional beats per episode. */
export function checkRhythm(plan: SeasonPlan, frame: SeasonFrame, terms: GenreTerms = DEFAULT_TERMS): Finding[] {
  const hero = terms.hero;
  const out: Finding[] = [];
  const r = frame.rhythm;
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  const last = eps.at(-1)?.ep ?? 0;

  for (const e of eps.filter((x) => x.strike_by_villain)) {
    if (e.ep >= last) continue;
    const answered = eps.some((x) => x.strike_by_heroine && x.ep > e.ep && x.ep <= e.ep + r.response_within);
    if (!answered) {
      out.push(
        makeFinding({
          check: 'rhythm.response',
          controller: 'structure',
          severity: 'major',
          holeType: 3,
          episode: e.ep,
          quote: e.event,
          question: `почему ${hero.nom} не отвечает на удар ${e.ep}-й серии?`,
          fixes: [`Дать ${hero.dat} ответный ход в сериях ${e.ep + 1}–${e.ep + r.response_within}`],
        }),
      );
    }
  }

  for (const run of runs(eps, (e) => (e.mood === 'suffering' ? 'suffering' : null))) {
    if (run.eps.length <= r.max_suffering_run) continue;
    out.push(
      makeFinding({
        check: 'rhythm.suffering',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: run.eps[0],
        quote: `Страдание подряд в сериях ${formatEpisodes(run.eps)}`,
        question: `сколько можно страдать? ${run.eps.length} серии подряд без кайфа.`,
        fixes: [`Вставить кайф не позже ${run.eps[0]! + r.max_suffering_run}-й серии`],
      }),
    );
  }

  for (const run of runs(eps, (e) => e.hook_type)) {
    if (run.eps.length <= r.max_same_hook_run) continue;
    out.push(
      makeFinding({
        check: 'rhythm.hook_repeat',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: run.eps[r.max_same_hook_run],
        quote: `Крючок «${run.value}» в сериях ${formatEpisodes(run.eps)}`,
        question: 'опять тот же крючок?',
        fixes: [`Сменить тип крючка в ${run.eps[r.max_same_hook_run]}-й серии`],
      }),
    );
  }

  // Several story lines (e.g. revenge and love): none may fall silent for too long.
  if (r.max_without_line) {
    const max = r.max_without_line;
    const exempt = new Set(
      r.line_gap_exempt.flatMap((id) => {
        const spec = frame.anchors[id];
        if (spec === undefined) return [];
        const { min, max: to } = episodeRange(spec);
        return Array.from({ length: to - min + 1 }, (_, i) => min + i);
      }),
    );
    for (const [line, name] of Object.entries(frame.lines)) {
      let gap: number[] = [];
      const flush = () => {
        if (gap.length > max) {
          out.push(
            makeFinding({
              check: 'rhythm.line_gap',
              controller: 'structure',
              severity: 'major',
              holeType: 10,
              episode: gap[max],
              quote: `${name}: нет в сериях ${formatEpisodes(gap)}`,
              question: `куда пропала линия «${name}»? ${gap.length} серии подряд без неё.`,
              fixes: [`Дать «${name}» не позже ${gap[max]}-й серии`],
            }),
          );
        }
        gap = [];
      };
      for (const e of eps) {
        if (e.kaif_lines.includes(line) || exempt.has(e.ep)) flush();
        else gap.push(e.ep);
      }
      flush();
    }
  }

  const [lo, hi] = r.emotions_per_episode;
  for (const e of eps) {
    if (e.emotions.length >= lo && e.emotions.length <= hi) continue;
    out.push(
      makeFinding({
        check: 'rhythm.emotions',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: e.ep,
        quote: `Эмоции ${e.ep}-й серии: ${e.emotions.join(', ')}`,
        question: e.emotions.length < lo ? 'что я должна тут почувствовать?' : 'слишком много всего в одной серии?',
        fixes: [`Оставить ${lo}–${hi} эмоциональные точки`],
      }),
    );
  }
  return out;
}
