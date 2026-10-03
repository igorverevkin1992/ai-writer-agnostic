import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';

/**
 * How many world rules the genre allows (e.g. «one miracle»: 1–3 rules) and, if the genre asks,
 * that each rule is said in some episode and fires with a consequence after that.
 */
export function checkWorldRules(bible: Bible, limits: { min: number; max: number; requireEpisodes?: boolean }): Finding[] {
  const n = bible.world_rules.length;
  // A genre may allow any number of rules if each one is said in some episode and fires in another.
  const unshown = limits.requireEpisodes
    ? bible.world_rules.flatMap((r) => {
        const missing = [!r.heard_eps.length && 'серия, где его произносят', !r.plays_eps.length && 'серия, где он срабатывает'].filter(Boolean);
        const early = r.heard_eps.length && r.plays_eps.length && Math.min(...r.plays_eps) < Math.min(...r.heard_eps);
        if (!missing.length && !early) return [];
        return [
          makeFinding({
            check: 'world_rules.unshown',
            controller: 'logic',
            severity: 'major',
            holeType: 1,
            episode: r.plays_eps[0] ?? r.heard_eps[0],
            quote: `Закон «${r.rule}»: ${missing.length ? `нет — ${missing.join(' и ')}` : `срабатывает в ${Math.min(...r.plays_eps)}-й, а произнесён только в ${Math.min(...r.heard_eps)}-й`}`,
            question: missing.length ? 'зачем мне этот закон, если он не срабатывает?' : 'откуда этот закон, если о нём ещё не говорили?',
            fixes: [`Назвать серию, где закон «${r.rule}» произносят, и серию, где он срабатывает с видимым последствием — позже`],
          }),
        ];
      })
    : [];
  if (n >= limits.min && n <= limits.max) return unshown;
  const range = Number.isFinite(limits.max) ? `${limits.min}–${limits.max}` : `не меньше ${limits.min}`;
  return [
    ...unshown,
    makeFinding({
      check: 'world_rules.count',
      controller: 'logic',
      severity: 'major',
      holeType: 1,
      quote: `Правил мира: ${n}${n ? ` (${bible.world_rules.map((r) => r.rule).join('; ')})` : ''}`,
      question: n > limits.max ? 'сколько тут ещё чудес? Я запуталась в правилах.' : 'по каким правилам живёт этот мир?',
      fixes: [`Оставить ${range} правил мира`],
    }),
  ];
}
