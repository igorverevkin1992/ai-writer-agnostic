import { DEFAULT_TERMS, type GenreTerms } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import type { EpisodeOutline, SeasonPlan } from '../../schemas/season.ts';
import { knowledgeIndex } from '../code/knowledge.ts';

/** A piece of text the auditor reads, with the episodes it covers. */
export interface AuditBlock {
  label: string;
  text: string;
  /** Inclusive range for plan blocks; absent for the bible. */
  episodes?: [number, number];
}

/** Human-readable bible for the auditor. Quotes must be copied from this text. */
export function bibleText(b: Bible, terms: GenreTerms = DEFAULT_TERMS): string {
  const lines: string[] = [];
  lines.push(terms.wound.toUpperCase(), `${b.betrayal.who}: ${b.betrayal.what}. Почему: ${b.betrayal.why}. Год: ${b.betrayal.year}.`);
  lines.push('', 'ПРАВИЛА МИРА');
  for (const r of b.world_rules) {
    const eps = [r.heard_eps.length && `Звучит в сериях ${r.heard_eps.join(', ')}`, r.plays_eps.length && `срабатывает в ${r.plays_eps.join(', ')}`].filter(Boolean);
    lines.push(`${r.rule}. Причина: ${r.why}. Цена: ${r.cost}. Нельзя: ${r.cannot}. Эффект: ${r.practical_effect}.${eps.length ? ` ${eps.join('; ')}.` : ''}`);
  }
  lines.push('', 'ПЕРСОНАЖИ');
  for (const c of b.characters) {
    lines.push(`${c.name} (${c.birth_year} г. р.${c.regular ? '' : ', эпизодический'}): ${c.look}. Призрак: ${c.ghost}. Хочет: ${c.want}. Маска: ${c.mask}. Сила: ${c.hidden_power}.`);
  }
  lines.push('', 'ЗЛОДЕИ');
  for (const v of [...b.villains].sort((x, y) => y.rank - x.rank)) {
    lines.push(
      `${v.name}, ранг ${v.rank} (${v.role}). Угрожает: ${v.threat}. Мотив: ${v.motive}. План: ${v.own_plan}. Слабость: ${v.weakness}. ` +
        `В кадре с ${v.on_screen_ep}-й серии, снят в ${v.takedown_ep}-й (${v.punishment.type}). Ключ к следующему: ${v.key_to_next}.`,
    );
  }
  lines.push('', 'ТАЙНА');
  for (const s of b.secrets) lines.push(`Слой ${s.layer} (серия ${s.revealed_ep}): ${s.truth}. Цель ${terms.hero.gen}: ${s.goal_from} → ${s.goal_to}.`);
  lines.push('', 'ХРОНОЛОГИЯ');
  for (const e of [...b.timeline.events].sort((x, y) => x.year - y.year)) {
    const ages = Object.entries(e.ages).map(([n, a]) => `${n} — ${a}`).join(', ');
    lines.push(`${e.year}: ${e.text}${e.participants.length ? ` (${e.participants.join(', ')})` : ''}${ages ? `; возраст: ${ages}` : ''}.`);
  }
  return lines.join('\n');
}

export function episodeLine(e: EpisodeOutline, terms: GenreTerms = DEFAULT_TERMS): string {
  const hero = terms.hero;
  const flags = [
    e.strike_by_villain && 'удар злодеев',
    e.strike_by_heroine && `удар ${hero.gen}`,
    e.takedown_rank && `снят злодей ${e.takedown_rank}`,
    e.reveals_secret && 'раскрытие тайны',
    e.threat_to_heroine && `угроза ${hero.dat}`,
    e.kaif_lines.length > 0 && `кайф: ${e.kaif_lines.join(', ')}`,
  ].filter(Boolean);
  return (
    `Серия ${e.ep}. «${e.title}». ${e.event}. ${hero.nom.charAt(0).toUpperCase() + hero.nom.slice(1)}: ${e.heroine_action}. Крючок: ${e.cliffhanger}.` +
    (flags.length ? ` [${flags.join(', ')}]` : '')
  );
}

/** The plan split into blocks of `size` episodes. */
export function planBlocks(plan: SeasonPlan, size: number, terms: GenreTerms = DEFAULT_TERMS): AuditBlock[] {
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  const out: AuditBlock[] = [];
  for (let i = 0; i < eps.length; i += size) {
    const chunk = eps.slice(i, i + size);
    const a = chunk[0]!.ep;
    const b = chunk.at(-1)!.ep;
    out.push({ label: `план, серии ${a}–${b}`, text: chunk.map((e) => episodeLine(e, terms)).join('\n'), episodes: [a, b] });
  }
  // The whole season in one block is simply «the season plan».
  if (out.length === 1) out[0]!.label = 'план сезона';
  return out;
}

/** Fact base summary: links between blocks go through it. */
export function factsText(b: Bible): string {
  const known = knowledgeIndex(b);
  return b.facts
    .map((f) => {
      const who = [...known.entries()]
        .filter(([k]) => k.endsWith(`\u0000${f.id}`))
        .map(([k, since]) => `${k.split('\u0000')[0]} ${since === 0 ? 'до сезона' : `с ${since}-й`}`);
      return `${f.id}: ${f.text}${f.since_ep ? ` (с ${f.since_ep}-й серии)` : ''}. Знают: ${who.join(', ') || 'никто'}.`;
    })
    .join('\n');
}

/**
 * Type-2 questions («почему никто не заметил?»): code pairs each event with the characters
 * next to it — people named in the episode text, or participants of a timeline event.
 */
export function noticeQuestions(b: Bible, block: AuditBlock, plan?: SeasonPlan): string[] {
  const names = [...new Set([...b.characters.map((c) => c.name), ...b.villains.map((v) => v.name)])];
  if (block.episodes && plan) {
    const [a, z] = block.episodes;
    return plan.episodes
      .filter((e) => e.ep >= a && e.ep <= z)
      .flatMap((e) => {
        const text = `${e.event} ${e.heroine_action}`;
        const near = names.filter((n) => text.includes(n) || text.includes(n.slice(0, -1)));
        return near.map((n) => `Серия ${e.ep}: «${e.event}». Рядом ${n} — почему ${n} ничего не заметил(а) или не вмешал(ась)?`);
      });
  }
  return b.timeline.events.flatMap((e) =>
    e.participants.map((p) => `${e.year}: «${e.text}». Рядом ${p} — почему никто вокруг ничего не заметил?`),
  );
}
