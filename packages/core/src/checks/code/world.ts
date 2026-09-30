import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';

/** How many world rules the genre allows (e.g. «one miracle»: 1–3 rules). */
export function checkWorldRules(bible: Bible, limits: { min: number; max: number }): Finding[] {
  const n = bible.world_rules.length;
  if (n >= limits.min && n <= limits.max) return [];
  const range = Number.isFinite(limits.max) ? `${limits.min}–${limits.max}` : `не меньше ${limits.min}`;
  return [
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
