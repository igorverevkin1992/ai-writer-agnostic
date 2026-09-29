import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';

const AGE_TOLERANCE = 1;

export interface TimelineIssue {
  finding: Finding;
  /** Timeline events and characters the finding is about. */
  events: string[];
  people: string[];
}

/**
 * Chronology: nobody takes part in events before being born or after dying,
 * stated ages match birth years, ordered events keep their order.
 */
export function checkTimeline(bible: Bible): Finding[] {
  return checkTimelineDetailed(bible).map((i) => i.finding);
}

export function checkTimelineDetailed(bible: Bible): TimelineIssue[] {
  const out: TimelineIssue[] = [];
  const events = bible.timeline.events;
  const byId = new Map(events.map((e) => [e.id, e]));
  const born = new Map<string, number>(bible.characters.map((c) => [c.name, c.birth_year]));
  const died = new Map<string, number>();
  const deathEvents = (who: string) => events.filter((x) => x.kind === 'death' && x.participants[0] === who).map((x) => x.id);

  for (const e of events) {
    const who = e.participants[0];
    if (!who) continue;
    if (e.kind === 'death') died.set(who, Math.min(died.get(who) ?? Infinity, e.year));
    if (e.kind === 'birth' && born.has(who) && born.get(who) !== e.year) {
      out.push({
        finding: timelineFinding('birth', `${who} родился в ${e.year}, а в библии год рождения — ${born.get(who)}`, `в каком году родился ${who}?`),
        events: [e.id],
        people: [who],
      });
    }
  }

  for (const e of events) {
    for (const who of e.participants) {
      const b = born.get(who);
      if (b !== undefined && e.year < b && !(e.kind === 'birth' && e.participants[0] === who)) {
        out.push({
          finding: timelineFinding('born_after', `${who} (род. ${b}) участвует в «${e.text}» (${e.year})`, `как ${who} мог быть там, если ещё не родился?`),
          events: [e.id],
          people: [who],
        });
      }
      const d = died.get(who);
      if (d !== undefined && e.year > d) {
        out.push({
          finding: timelineFinding('dead_before', `${who} (умер в ${d}) участвует в «${e.text}» (${e.year})`, `разве ${who} не умер в ${d}-м?`),
          events: [e.id, ...deathEvents(who)],
          people: [who],
        });
      }
    }

    for (const [who, age] of Object.entries(e.ages)) {
      const b = born.get(who);
      if (b === undefined) continue;
      const actual = e.year - b;
      if (Math.abs(actual - age) > AGE_TOLERANCE) {
        out.push({
          finding: timelineFinding('age', `«${e.text}» (${e.year}): ${who} ${age} лет, а по году рождения (${b}) — ${actual}`, `сколько же лет было ${who}?`),
          events: [e.id],
          people: [who],
        });
      }
    }

    for (const id of e.after) {
      const prev = byId.get(id);
      if (!prev) {
        out.push({
          finding: timelineFinding('unknown_ref', `Событие «${e.text}» ссылается на неизвестное событие ${id}`, 'после чего это случилось?'),
          events: [e.id],
          people: [],
        });
      } else if (prev.year > e.year) {
        out.push({
          finding: timelineFinding('order', `«${e.text}» (${e.year}) должно быть после «${prev.text}» (${prev.year})`, 'как следствие случилось раньше причины?'),
          events: [e.id, prev.id],
          people: [],
        });
      }
    }
  }

  const present = bible.timeline.present_year;
  if (present !== undefined) {
    for (const c of bible.characters) {
      const d = died.get(c.name);
      if (c.birth_year > present) {
        out.push({
          events: [],
          people: [c.name],
          finding: makeFinding({
            check: 'timeline.born_after',
            controller: 'consistency',
            severity: 'blocker',
            holeType: 7,
            quote: `${c.name}: год рождения ${c.birth_year}, действие — ${present}`,
            question: `как ${c.name} может быть в сериале, если ещё не родился?`,
            fixes: ['Исправить год рождения'],
          }),
        });
      }
      if (d !== undefined && d < present && c.regular) {
        out.push({
          events: deathEvents(c.name),
          people: [c.name],
          finding: makeFinding({
            check: 'timeline.dead_before',
            controller: 'consistency',
            severity: 'blocker',
            holeType: 7,
            quote: `${c.name} умер в ${d}, но он постоянный герой сезона ${present} года`,
            question: `как ${c.name} может быть в кадре, если умер?`,
            fixes: ['Исправить год смерти', 'Убрать персонажа из постоянных'],
          }),
        });
      }
    }
  }
  return out;
}

function timelineFinding(code: string, quote: string, question: string): Finding {
  return makeFinding({
    check: `timeline.${code}`,
    controller: 'consistency',
    severity: 'blocker',
    holeType: 7,
    quote,
    question,
    fixes: ['Исправить год события или год рождения', 'Убрать персонажа из события'],
  });
}
