import type { Bible } from '../../schemas/bible.ts';
import type { ActsOn } from '../../schemas/common.ts';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';

/** Anything that happens in an episode: a plan outline or an episode card. */
export interface EpisodeActs {
  ep: number;
  acts_on: ActsOn[];
}

/** When each character learns each fact: the earliest episode wins. 0 = before the season. */
export function knowledgeIndex(bible: Bible): Map<string, number> {
  const idx = new Map<string, number>();
  const put = (who: string, fact: string, since: number) => {
    const key = `${who}\u0000${fact}`;
    idx.set(key, Math.min(idx.get(key) ?? Infinity, since));
  };
  for (const c of bible.characters) for (const f of c.knows_at_start) put(c.name, f, 0);
  for (const v of bible.villains) for (const k of v.knows) put(v.name, k.fact, k.since_ep);
  for (const k of bible.knowledge) put(k.who, k.fact, k.since_ep);
  return idx;
}

/**
 * Character knowledge: nobody acts on a fact before the episode they learned it,
 * and nobody learns a fact before it becomes true.
 */
export function checkKnowledge(items: EpisodeActs[], bible: Bible): Finding[] {
  const out: Finding[] = [];
  const facts = new Map(bible.facts.map((f) => [f.id, f]));
  const known = knowledgeIndex(bible);
  const add = (code: string, episode: number | undefined, quote: string, question: string, fixes: [string] | [string, string]) =>
    out.push(
      makeFinding({ check: `knowledge.${code}`, controller: 'consistency', severity: 'blocker', holeType: 11, episode, quote, question, fixes }),
    );

  const learned: { who: string; fact: string; since: number }[] = [
    ...bible.characters.flatMap((c) => c.knows_at_start.map((fact) => ({ who: c.name, fact, since: 0 }))),
    ...bible.villains.flatMap((v) => v.knows.map((k) => ({ who: v.name, fact: k.fact, since: k.since_ep }))),
    ...bible.knowledge.map((k) => ({ who: k.who, fact: k.fact, since: k.since_ep })),
  ];
  for (const k of learned) {
    const fact = facts.get(k.fact);
    if (fact?.since_ep !== undefined && k.since < fact.since_ep) {
      add(
        'before_fact',
        k.since || undefined,
        `${k.who} знает «${fact.text}» с ${k.since ? `${k.since}-й серии` : 'начала сезона'}, а это случается в ${fact.since_ep}-й`,
        `откуда ${k.who} знает то, чего ещё не было?`,
        [`${k.who} узнаёт это не раньше ${fact.since_ep}-й серии`],
      );
    }
  }

  for (const item of items) {
    for (const act of item.acts_on) {
      const fact = facts.get(act.fact);
      const text = fact?.text ?? act.fact;
      if (facts.size > 0 && !fact) {
        add('unknown_fact', item.ep, `${item.ep}-я серия: ${act.who} действует по факту «${act.fact}», которого нет в базе фактов`, 'на чём держится эта сцена?', [
          'Добавить факт в базу',
          'Исправить ссылку на факт',
        ]);
        continue;
      }
      if (fact?.since_ep !== undefined && fact.since_ep > item.ep) {
        add('fact_not_yet', item.ep, `${item.ep}-я серия: ${act.who} действует по «${text}», а это случается только в ${fact.since_ep}-й`, 'как можно знать о том, чего ещё не было?', [
          `Перенести сцену не раньше ${fact.since_ep}-й серии`,
        ]);
      }
      const since = known.get(`${act.who}\u0000${act.fact}`);
      if (since === undefined) {
        add('unknown', item.ep, `${item.ep}-я серия: ${act.who} действует по «${text}», но нигде не узнаёт об этом`, `откуда ${act.who} это знает?`, [
          `Показать, как и когда ${act.who} узнаёт «${text}»`,
        ]);
      } else if (since > item.ep) {
        add('too_early', item.ep, `${item.ep}-я серия: ${act.who} действует по «${text}», а узнаёт это только в ${since}-й`, `откуда ${act.who} это знает уже сейчас?`, [
          `Перенести сцену не раньше ${since}-й серии`,
          `Показать, что ${act.who} узнаёт раньше`,
        ]);
      }
    }
  }
  return out;
}
