import { DEFAULT_TERMS, episodeRange, type GenreTerms, type SeasonFrame } from '@aiw/kb';
import { heroOf, STAKE_KINDS, type Bible, type StakeKind } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { makeFinding } from './finding.ts';

const STAKE_LABELS: Record<StakeKind, string> = {
  external: 'внешняя (что получит или потеряет)',
  internal: 'внутренняя (отношения)',
  philosophical: 'философская (чья правда победит)',
};

const finalBlock = (frame: SeasonFrame) => frame.blocks.at(-1) ?? [1, frame.episodes];

/** Three stakes are named in the bible and each is closed in the final block (Mowery). */
export function checkStakes(bible: Bible, frame: SeasonFrame): Finding[] {
  const [from, to] = finalBlock(frame);
  if (!bible.stakes) {
    return [
      makeFinding({
        check: 'stakes.missing',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        quote: 'stakes',
        question: 'за что я переживаю: что герой получит, кого потеряет, чья правда победит?',
        fixes: ['Записать в библии три ставки сезона — внешнюю, внутреннюю, философскую — и серию, где каждая закрывается'],
      }),
    ];
  }
  return STAKE_KINDS.flatMap((kind) => {
    const s = bible.stakes![kind];
    if (s.closed_ep >= from && s.closed_ep <= to) return [];
    return [
      makeFinding({
        check: 'stakes.closed_outside',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: s.closed_ep,
        quote: s.text,
        question: `ставка ${STAKE_LABELS[kind]} решена в ${s.closed_ep}-й серии — а финал тогда про что?`,
        fixes: [`Закрыть ставку в финальном блоке (серии ${from}–${to}) или оставить её открытой до него`],
      }),
    ];
  });
}

export interface HeroArcParams {
  /** Frame anchor of the midpoint; the plan's episode with it, else the frame's number. */
  anchor: string;
  /** Episodes before the midpoint where the protagonist acts on the Lie. */
  minLieActs: number;
  /** A relapse into the Lie after the midpoint is required. */
  relapse: boolean;
}

/** The arc is laid out by episodes: the Lie acted on before the midpoint, a relapse after it (Weiland, Mowery). */
export function checkHeroArc(bible: Bible, plan: SeasonPlan, p: HeroArcParams, frame: SeasonFrame, terms: GenreTerms = DEFAULT_TERMS): Finding[] {
  const hero = heroOf(bible);
  const spec = frame.anchors[p.anchor];
  const planned = plan.episodes.find((e) => e.anchors.includes(p.anchor))?.ep;
  const mid = planned ?? (spec ? episodeRange(spec).max : undefined);
  if (mid === undefined) return [];
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  if (!eps.some((e) => e.arc)) {
    return [
      makeFinding({
        check: 'hero_arc.unmarked',
        controller: 'structure',
        severity: 'minor',
        holeType: 10,
        quote: hero.lie,
        question: `в каких сериях ${terms.hero.nom} живёт по своей лжи, а в каких — по правде?`,
        fixes: ['Отметить в плане серии арки: `arc: lie` до мидпоинта, `arc: relapse` и `arc: truth` после'],
      }),
    ];
  }
  const out: Finding[] = [];
  const lieActs = eps.filter((e) => e.ep < mid && e.arc === 'lie');
  if (lieActs.length < p.minLieActs) {
    out.push(
      makeFinding({
        check: 'hero_arc.lie_acts',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: mid,
        quote: hero.lie,
        question: `от чего ${terms.hero.nom} отказывается на мидпоинте, если по лжи «${hero.lie}» она жила всего в ${lieActs.length} сериях?`,
        fixes: [`До ${mid}-й серии показать не меньше ${p.minLieActs} поступков по лжи с ценой и отметить их \`arc: lie\``],
      }),
    );
  }
  if (p.relapse && !eps.some((e) => e.ep > mid && e.arc === 'relapse')) {
    out.push(
      makeFinding({
        check: 'hero_arc.no_relapse',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: mid,
        quote: hero.truth,
        question: `${terms.hero.nom} после ${mid}-й серии идёт к правде по прямой — почему я должен верить такой перемене?`,
        fixes: [`После ${mid}-й серии дать хотя бы один откат ко лжи с ценой и отметить его \`arc: relapse\``],
      }),
    );
  }
  return out;
}

/** Every victory before the final block is paid for in the same or the next `within` episodes (Mowery). */
export function checkVictoryCost(plan: SeasonPlan, frame: SeasonFrame, within: number, terms: GenreTerms = DEFAULT_TERMS): Finding[] {
  const [finalFrom] = finalBlock(frame);
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  if (!eps.some((e) => e.victory)) {
    return [
      makeFinding({
        check: 'victory_cost.unmarked',
        controller: 'structure',
        severity: 'minor',
        holeType: 10,
        quote: 'victory',
        question: `где ${terms.hero.nom} хоть раз получает то, чего добивалась?`,
        fixes: ['Отметить в плане серии побед (`victory: true`) и записать их цену в `cost`'],
      }),
    ];
  }
  return eps
    .filter((e) => e.victory && e.ep < finalFrom)
    .filter((e) => !eps.some((x) => x.ep >= e.ep && x.ep <= e.ep + within && x.cost))
    .map((e) =>
      makeFinding({
        check: 'victory_cost.free',
        controller: 'structure',
        severity: 'major',
        holeType: 10,
        episode: e.ep,
        quote: e.event,
        question: `почему победа ${e.ep}-й серии ничего не стоит ${terms.hero.dat}?`,
        fixes: [`Назначить цену победы в сериях ${e.ep}–${e.ep + within} и записать её в \`cost\``],
      }),
    );
}

export interface PromiseParams {
  /** Episode that must carry the promise (usually 1). */
  ep: number;
  /** Frame line ids any of which counts as the promise shown. */
  lines: string[];
  /** Frame anchor ids any of which counts as the promise shown. */
  anchors: string[];
}

/** What the genre promises the viewer is on screen in the given episode (Rabkin: the fun is in the pilot). */
export function checkPromise(plan: SeasonPlan, frame: SeasonFrame, p: PromiseParams): Finding[] {
  const e = plan.episodes.find((x) => x.ep === p.ep);
  if (!e) return [];
  const shown = p.lines.some((l) => e.kaif_lines.includes(l)) || p.anchors.some((a) => e.anchors.includes(a));
  if (shown) return [];
  const names = [...p.lines.map((l) => frame.lines[l] ?? l), ...p.anchors.map((a) => frame.anchor_labels[a] ?? a)];
  return [
    makeFinding({
      check: 'promise.absent',
      controller: 'genre',
      severity: 'major',
      holeType: 10,
      episode: e.ep,
      quote: `«${e.title}». ${e.event.split(/(?<=[.!?…])\s+/u)[0] ?? e.event}`,
      question: `это то, что мне обещали? в ${e.ep}-й серии нет: ${names.join(', ')}`,
      fixes: [`Показать в ${e.ep}-й серии то, за чем зритель пришёл в жанр: ${names.join(' или ')}`],
    }),
  ];
}
