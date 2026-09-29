import { episodeRange, type SeasonFrame } from '@aiw/kb';
import type { Bible, Villain } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { makeFinding, type FindingInput } from './finding.ts';

export interface LadderParams {
  /** Check only villains of these ranks (per-villain findings). */
  ranks?: number[];
  /** Minimum takedowns through villain infighting. */
  min_infighting?: number;
}

const EMPTY = /^[\s—–-]*$/u;

/**
 * Villain ladder from the genre frame: count, ranks, roles, on-screen deadlines,
 * takedowns on their numbers and in order, counterstrikes, punishments.
 * Genres without a ladder (no frame.villains) get no findings.
 */
export function checkVillainLadder(
  bible: Bible,
  plan: SeasonPlan,
  frame: SeasonFrame,
  params: LadderParams = {},
): Finding[] {
  const v = frame.villains;
  if (!v) return [];
  const out: Finding[] = [];
  const add = (f: Omit<FindingInput, 'controller' | 'holeType'> & { holeType?: number }) =>
    out.push(makeFinding({ controller: 'structure', holeType: 10, ...f }));
  const inScope = (x: Villain) => !params.ranks || params.ranks.includes(x.rank);
  const villains = bible.villains;

  if (villains.length !== v.count) {
    add({
      check: 'villain_ladder.count',
      severity: 'blocker',
      quote: `Злодеев в библии: ${villains.length} (${villains.map((x) => x.name).join(', ')})`,
      question: `сколько у неё врагов? По жанру их ровно ${v.count}.`,
      fixes: [`Оставить ровно ${v.count} злодеев по рангам 1–${v.count}`],
    });
  }

  const byRank = new Map<number, Villain[]>();
  for (const x of villains) byRank.set(x.rank, [...(byRank.get(x.rank) ?? []), x]);
  for (let rank = 1; rank <= v.count; rank++) {
    const list = byRank.get(rank) ?? [];
    if (list.length !== 1 && (!params.ranks || params.ranks.includes(rank))) {
      add({
        check: 'villain_ladder.rank',
        severity: 'blocker',
        quote: list.length ? `Ранг ${rank}: ${list.map((x) => x.name).join(', ')}` : `Ранг ${rank}: никого`,
        question: `кто враг ранга ${rank}?`,
        fixes: [`Назначить ровно одного злодея на ранг ${rank}`],
      });
    }
  }
  for (const x of villains.filter((y) => y.rank > v.count && inScope(y))) {
    add({
      check: 'villain_ladder.rank',
      severity: 'blocker',
      quote: `${x.name}: ранг ${x.rank}`,
      question: `что за лишний враг — ${x.name}?`,
      fixes: [`Ранги злодеев — от 1 до ${v.count}`],
    });
  }

  for (const x of villains.filter(inScope)) {
    const key = String(x.rank);
    const role = v.roles[key];
    if (role && x.role !== role) {
      add({
        check: 'villain_ladder.role',
        severity: 'major',
        quote: `${x.name}: ранг ${x.rank}, роль «${x.role}»`,
        question: `кто ${x.name} в иерархии злодеев?`,
        fixes: [`Роль ранга ${x.rank} — «${role}»`],
      });
    }

    const deadline = Math.min(
      v.on_screen_by[key] ?? Infinity,
      v.all_on_screen_by,
      x.rank === 1 ? v.boss_on_screen_by : Infinity,
    );
    if (x.on_screen_ep > deadline) {
      add({
        check: 'villain_ladder.on_screen',
        severity: 'blocker',
        episode: x.on_screen_ep,
        quote: `${x.name} впервые в кадре в ${x.on_screen_ep}-й серии`,
        question: `откуда взялся ${x.name} так поздно?`,
        fixes: [`Показать ${x.name} не позже ${deadline}-й серии`],
      });
    }

    const spec = v.takedowns[key];
    if (spec !== undefined) {
      const { min, max } = episodeRange(spec);
      const ep = x.takedown_ep;
      const place = min === max ? `${min}-й серии` : `сериях ${min}–${max}`;
      const inside = ep >= min && ep <= max;
      const tolerated =
        ep >= min - frame.tolerance &&
        ep <= max + frame.tolerance &&
        plan.deviations.some((d) => d.anchor === `villain_${x.rank}` && d.ep === ep);
      if (!inside && !tolerated) {
        add({
          check: 'villain_ladder.takedown',
          severity: 'blocker',
          episode: ep,
          quote: `${x.name} (ранг ${x.rank}) снят в ${ep}-й серии`,
          question: `почему ${x.name} падает не вовремя? По лестнице — в ${place}.`,
          fixes: [`Перенести снятие ${x.name} в ${place}`],
        });
      }
    }

    const planned = plan.episodes.filter((e) => e.takedown_rank === x.rank).map((e) => e.ep);
    if (!planned.includes(x.takedown_ep) || planned.length > 1) {
      add({
        check: 'villain_ladder.takedown_plan',
        severity: 'major',
        episode: x.takedown_ep,
        quote: planned.length
          ? `В плане снятие ранга ${x.rank} в сериях ${planned.join(', ')}, в библии — в ${x.takedown_ep}-й`
          : `В плане нет снятия ${x.name} (ранг ${x.rank})`,
        question: `когда на самом деле падает ${x.name}?`,
        fixes: [`Отметить takedown_rank ${x.rank} ровно в ${x.takedown_ep}-й серии плана`],
      });
    }

    if (x.rank > 1) {
      if (EMPTY.test(x.key_to_next)) {
        add({
          check: 'villain_ladder.key_to_next',
          severity: 'major',
          quote: `${x.name}: ключ к следующему — «${x.key_to_next}»`,
          question: `что даёт героине победа над ${x.name}?`,
          fixes: ['Дать победе ключ к следующему злодею: улику, союзника или доступ'],
        });
      }
      const T = x.takedown_ep;
      const hit = plan.episodes.some((e) => e.strike_by_villain && e.ep > T && e.ep <= T + v.counterstrike_within);
      if (!hit) {
        add({
          check: 'villain_ladder.counterstrike',
          severity: 'major',
          episode: T,
          quote: `После снятия ${x.name} (${T}-я серия) нет удара злодеев в сериях ${T + 1}–${T + v.counterstrike_within}`,
          question: 'почему злодеи молчат после такого поражения?',
          fixes: [`Ответный удар злодеев в сериях ${T + 1}–${T + v.counterstrike_within}`],
        });
      }
    } else if (!x.link_to_ghost || EMPTY.test(x.link_to_ghost)) {
      add({
        check: 'villain_ladder.key_to_next',
        severity: 'major',
        quote: `${x.name}: связь с раной героини не указана`,
        question: `почему главный враг — именно ${x.name}?`,
        fixes: ['Связать финального босса с главной раной героини'],
      });
    }

    if (v.public_and_legal.includes(x.rank) && !(x.punishment.public && x.punishment.type === 'law')) {
      add({
        check: 'villain_ladder.public_legal',
        severity: 'blocker',
        episode: x.takedown_ep,
        quote: `${x.name}: наказание «${x.punishment.type}», публичное: ${x.punishment.public ? 'да' : 'нет'}`,
        question: `где публичное разоблачение ${x.name} по закону?`,
        fixes: [`Снять ${x.name} публично и законно`],
      });
    }
  }

  const ordered = [...villains].sort((a, b) => a.takedown_ep - b.takedown_ep);
  if (!params.ranks) {
    for (let i = 1; i < ordered.length; i++) {
      const [a, b] = [ordered[i - 1]!, ordered[i]!];
      if (b.rank > a.rank) {
        add({
          check: 'villain_ladder.order',
          severity: 'blocker',
          episode: b.takedown_ep,
          quote: `${a.name} (ранг ${a.rank}) падает раньше, чем ${b.name} (ранг ${b.rank})`,
          question: 'почему сильный враг падает раньше слабого?',
          fixes: ['Снимать злодеев снизу вверх: от слабого к финальному боссу'],
        });
      }
      const exempt = v.public_and_legal.includes(a.rank) && v.public_and_legal.includes(b.rank);
      if (!exempt && a.punishment.type === b.punishment.type) {
        add({
          check: 'villain_ladder.punishment',
          severity: 'major',
          episode: b.takedown_ep,
          quote: `${a.name} и ${b.name}: оба наказания «${a.punishment.type}»`,
          question: 'опять то же наказание?',
          fixes: ['Сменить тип наказания: позор, закон, статус, бегство, потеря союзников'],
        });
      }
    }

    const turned = villains.filter((x) => x.punishment.type === 'turned_ally');
    const badRank = turned.filter((x) => !v.turned_ally_ranks.includes(x.rank));
    if (turned.length > v.max_turned_allies || badRank.length) {
      add({
        check: 'villain_ladder.turned_ally',
        severity: 'major',
        quote: `Переходят на сторону героини: ${turned.map((x) => `${x.name} (ранг ${x.rank})`).join(', ')}`,
        question: 'почему враги так легко становятся друзьями?',
        fixes: [`Переход — не больше ${v.max_turned_allies}, только ранги ${v.turned_ally_ranks.join(', ')}`],
      });
    }

    const needed = params.min_infighting ?? 0;
    const infighting = villains.filter((x) => x.via_infighting).length;
    if (infighting < needed) {
      add({
        check: 'villain_ladder.infighting',
        severity: 'major',
        quote: `Снятий через стравливание злодеев: ${infighting}`,
        question: 'злодеи — это сеть или очередь?',
        fixes: ['Снять хотя бы одного злодея через то, что героиня стравила его с другими'],
      });
    }
  }
  return out;
}

/** The betrayer must be one of the villains of the given ranks (e.g. boss or right hand). */
export function checkBetrayerRank(bible: Bible, ranks: number[]): Finding[] {
  const who = bible.betrayal.who.trim();
  const villain = bible.villains.find((x) => x.name.trim() === who);
  if (villain && ranks.includes(villain.rank)) return [];
  return [
    makeFinding({
      check: 'betrayer_rank.rank',
      controller: 'genre',
      severity: 'blocker',
      holeType: 6,
      quote: villain ? `Предатель ${who} — злодей ранга ${villain.rank}` : `Предатель ${who} — не в списке злодеев`,
      question: `почему предатель ${who} не главный враг?`,
      fixes: [`Сделать предателя злодеем ранга ${ranks.join(' или ')}`],
    }),
  ];
}
