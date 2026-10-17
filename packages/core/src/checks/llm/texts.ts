import { DEFAULT_TERMS, type GenreTerms } from '@aiw/kb';
import { heroOf, type Bible } from '../../schemas/bible.ts';
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
  if (b.stakes) {
    lines.push('', 'СТАВКИ');
    lines.push(`Внешняя: ${b.stakes.external.text} (решается в ${b.stakes.external.closed_ep}-й).`);
    lines.push(`Внутренняя: ${b.stakes.internal.text} (решается в ${b.stakes.internal.closed_ep}-й).`);
    lines.push(`Философская: ${b.stakes.philosophical.text} (решается в ${b.stakes.philosophical.closed_ep}-й).`);
  }
  if (b.trunk) {
    lines.push('', 'СТВОЛ');
    lines.push(`Вопрос сезона: ${b.trunk.question} Ответ: ${b.trunk.answer} Остаётся на следующий сезон: ${b.trunk.next_question}`);
  }
  if (b.nested.length) {
    lines.push('', 'МАТРЁШКА ВОПРОСОВ');
    for (const q of [...b.nested].sort((x, y) => x.level - y.level)) {
      lines.push(`Уровень ${q.level} (масштаб ${q.scale}): ${q.question} Открыт в ${q.opened_ep}-й, ${q.closed_ep ? `закрыт в ${q.closed_ep}-й` : 'не закрыт'}.`);
    }
  }
  if (b.threat) {
    lines.push('', 'УГРОЗА');
    lines.push(`${b.threat.archetype}: ${b.threat.identity}. Визуальный знак: ${b.threat.visual_sign}, впервые показан в ${b.threat.sign_shown_ep}-й.`);
  }
  if (b.clues.length) {
    lines.push('', 'УЛИКИ');
    for (const c of [...b.clues].sort((x, y) => x.planted_ep - y.planted_ep)) {
      const kind = [c.visual && 'визуальная', c.contradicts && 'против основной версии'].filter(Boolean).join(', ');
      lines.push(`${c.id} (вопрос ${c.question}${kind ? `, ${kind}` : ''}): ${c.text} Заложена в ${c.planted_ep}-й, ${c.paid_ep ? `окупается в ${c.paid_ep}-й` : 'не окупается'}.`);
    }
  }
  if (b.guns.length) {
    lines.push('', 'РУЖЬЯ');
    for (const g of [...b.guns].sort((x, y) => x.planted_ep - y.planted_ep)) {
      lines.push(`${g.id}: ${g.object}. Показано в ${g.planted_ep}-й, ${g.fired_ep ? `стреляет в ${g.fired_ep}-й` : 'не стреляет'}${g.metro_visible ? ', читается без звука' : ''}.`);
    }
  }
  if (b.locations.length) lines.push('', 'ЛОКАЦИИ', b.locations.join('; '));
  if (b.facts.length) lines.push('', 'ФАКТЫ И КТО ЧТО ЗНАЕТ', factsText(b));
  return lines.join('\n');
}

export function episodeLine(e: EpisodeOutline, terms: GenreTerms = DEFAULT_TERMS, hookTypes: Record<string, string> = {}): string {
  const hero = terms.hero;
  const flags = [
    e.strike_by_villain && 'удар злодеев',
    e.strike_by_heroine && `удар ${hero.gen}`,
    e.takedown_rank && `снят злодей ${e.takedown_rank}`,
    e.reveals_secret && 'раскрытие тайны',
    e.threat_to_heroine && `угроза ${hero.dat}`,
    e.kaif_lines.length > 0 && `кайф: ${e.kaif_lines.join(', ')}`,
    e.mood === 'suffering' ? 'страдание' : e.mood === 'kaif' ? 'кайф' : null,
    e.anchors.length > 0 && `якоря: ${e.anchors.join(', ')}`,
    e.arc && `${hero.nom} по ${ARC_LABEL[e.arc]}`,
    e.victory && 'победа',
    e.cost && `цена: ${e.cost}`,
    e.knowledge_mode && `режим: ${MODE_LABEL[e.knowledge_mode]}`,
    e.threat_seen_by_viewer && 'угрозу видит зритель',
    e.threat_known_to_hero && `угрозу узнаёт ${hero.nom}`,
    e.irreversible && 'необратимо',
    e.question_turn && 'поворот вопроса',
    e.personal_stake && `личная ставка: ${e.personal_stake}`,
    e.strongest_twist && 'главный твист',
    e.acts_on.length > 0 && `действует по знанию: ${e.acts_on.map((a) => `${a.who} — ${a.fact}`).join('; ')}`,
  ].filter(Boolean);
  const hook = hookTypes[e.hook_type] ? `${hookTypes[e.hook_type]} (${e.hook_type})` : e.hook_type;
  return (
    `Серия ${e.ep}. «${e.title}». ${dot(e.event)} Вопрос серии: ${dot(e.question)} ${hero.nom.charAt(0).toUpperCase() + hero.nom.slice(1)}: ${dot(e.heroine_action)} ` +
    `Крючок (${hook}): ${dot(e.cliffhanger)}` +
    (flags.length ? ` [${flags.join('; ')}]` : '')
  );
}

/** Ends a fragment with a period unless it already ends a sentence. */
const dot = (t: string) => (/[.!?…»)]$/u.test(t.trim()) ? t.trim() : `${t.trim()}.`);

const ARC_LABEL = { lie: 'лжи', truth: 'правде', relapse: 'откату ко лжи' } as const;
const MODE_LABEL = { suspense: 'саспенс', mystery: 'тайна', surprise: 'сюрприз' } as const;

/** Smallest audit block: frame blocks are merged until they hold this many episodes. */
export const MIN_AUDIT_BLOCK = 12;

/**
 * Audit blocks from the frame's blocks, merged so that each holds at least MIN_AUDIT_BLOCK
 * episodes: a pass over a block sees setups and payoffs of one movement together, and the
 * model's answer is not asked to cover the whole season at once.
 */
export function auditRanges(frameBlocks: readonly (readonly [number, number])[] | undefined, episodes: number, min = MIN_AUDIT_BLOCK): [number, number][] {
  const out: [number, number][] = [];
  if (frameBlocks?.length) {
    let start: number | undefined;
    for (const [a, b] of frameBlocks) {
      start ??= a;
      if (b - start + 1 >= min) {
        out.push([start, b]);
        start = undefined;
      }
    }
    if (start !== undefined) {
      if (out.length) out[out.length - 1]![1] = frameBlocks.at(-1)![1];
      else out.push([start, frameBlocks.at(-1)![1]]);
    }
    return out;
  }
  for (let a = 1; a <= episodes; a += min + 3) out.push([a, Math.min(episodes, a + min + 2)]);
  return out;
}

/** The plan split into the given episode ranges. */
export function planBlocksByRanges(plan: SeasonPlan, ranges: readonly (readonly [number, number])[], terms: GenreTerms = DEFAULT_TERMS, hookTypes: Record<string, string> = {}): AuditBlock[] {
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  return ranges
    .map(([a, b]) => {
      const chunk = eps.filter((e) => e.ep >= a && e.ep <= b);
      return { label: `план, серии ${a}–${b}`, text: chunk.map((e) => episodeLine(e, terms, hookTypes)).join('\n'), episodes: [a, b] as [number, number] };
    })
    .filter((b) => b.text.length > 0);
}

/** The plan split into blocks of `size` episodes. */
export function planBlocks(plan: SeasonPlan, size: number, terms: GenreTerms = DEFAULT_TERMS, hookTypes: Record<string, string> = {}): AuditBlock[] {
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  const out: AuditBlock[] = [];
  for (let i = 0; i < eps.length; i += size) {
    const chunk = eps.slice(i, i + size);
    const a = chunk[0]!.ep;
    const b = chunk.at(-1)!.ep;
    out.push({ label: `план, серии ${a}–${b}`, text: chunk.map((e) => episodeLine(e, terms, hookTypes)).join('\n'), episodes: [a, b] });
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
 * One compact line per episode, without the protagonist: the model has the episode text already.
 */
export function noticeQuestions(b: Bible, block: AuditBlock, plan?: SeasonPlan): string[] {
  const hero = heroOf(b).name;
  const names = [...new Set([...b.characters.map((c) => c.name), ...b.villains.map((v) => v.name)])].filter((n) => n !== hero);
  if (block.episodes && plan) {
    const [a, z] = block.episodes;
    return plan.episodes
      .filter((e) => e.ep >= a && e.ep <= z)
      .flatMap((e) => {
        const text = `${e.event} ${e.heroine_action}`;
        const near = names.filter((n) => text.includes(n) || text.includes(n.slice(0, -1)));
        return near.length ? [`Серия ${e.ep}: рядом ${near.join(', ')} — кто из них должен был заметить или вмешаться, и почему не вмешался?`] : [];
      });
  }
  return b.timeline.events.flatMap((e) =>
    e.participants.map((p) => `${e.year}: «${e.text}». Рядом ${p} — почему никто вокруг ничего не заметил?`),
  );
}
