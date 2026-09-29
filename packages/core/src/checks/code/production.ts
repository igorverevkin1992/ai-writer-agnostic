import type { LegalConstraints, ProductionConstraints, SeasonFrame } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import { countWords } from '../../schemas/common.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import type { Script } from '../../schemas/script.ts';
import { makeFinding } from './finding.ts';

/** Production limits: regular characters, locations, speakers per scene. */
export function checkLimits(bible: Bible, cards: EpisodeCard[], production: ProductionConstraints): Finding[] {
  const out: Finding[] = [];
  const l = production.limits;
  const regular = bible.characters.filter((c) => c.regular);
  if (regular.length > l.max_regular_characters) {
    out.push(
      makeFinding({
        check: 'limits.regular_characters',
        controller: 'production',
        severity: 'major',
        holeType: 8,
        quote: `Постоянных героев: ${regular.length} (${regular.map((c) => c.name).join(', ')})`,
        question: 'кто все эти люди?',
        fixes: [`Не больше ${l.max_regular_characters} постоянных героев`],
      }),
    );
  }
  if (bible.locations.length > l.max_locations) {
    out.push(
      makeFinding({
        check: 'limits.locations',
        controller: 'production',
        severity: 'major',
        holeType: 8,
        quote: `Локаций в библии: ${bible.locations.length}`,
        question: 'где это всё снимать?',
        fixes: [`Не больше ${l.max_locations} локаций внутри одной основной`],
      }),
    );
  }
  const listed = new Set(bible.locations);
  const used = new Set<string>();
  for (const c of cards) {
    used.add(c.location);
    if (listed.size > 0 && !listed.has(c.location)) {
      out.push(
        makeFinding({
          check: 'limits.location_not_listed',
          controller: 'production',
          severity: 'major',
          holeType: 8,
          episode: c.ep,
          quote: `${c.ep}-я серия: локация «${c.location}»`,
          question: 'откуда эта новая локация?',
          fixes: ['Выбрать локацию из списка библии', 'Добавить локацию в библию'],
        }),
      );
    }
    if (c.cast.length > l.max_speakers_per_scene) {
      out.push(
        makeFinding({
          check: 'limits.speakers',
          controller: 'production',
          severity: 'major',
          holeType: 8,
          episode: c.ep,
          quote: `${c.ep}-я серия: в кадре ${c.cast.join(', ')}`,
          question: 'кто тут говорит?',
          fixes: [`Не больше ${l.max_speakers_per_scene} говорящих в сцене`],
        }),
      );
    }
  }
  if (listed.size === 0 && used.size > l.max_locations) {
    out.push(
      makeFinding({
        check: 'limits.locations',
        controller: 'production',
        severity: 'major',
        holeType: 8,
        quote: `Локаций в карточках: ${used.size}`,
        question: 'где это всё снимать?',
        fixes: [`Не больше ${l.max_locations} локаций`],
      }),
    );
  }
  return out;
}

/** Every Chekhov's gun that is planted must fire, later and inside the season. */
export function checkGuns(bible: Bible, frame: SeasonFrame, plan?: SeasonPlan): Finding[] {
  const last = plan ? Math.max(...plan.episodes.map((e) => e.ep)) : frame.episodes;
  const out: Finding[] = [];
  for (const g of bible.guns) {
    const add = (code: string, quote: string, question: string, fix: string) =>
      out.push(
        makeFinding({ check: `guns.${code}`, controller: 'structure', severity: 'major', holeType: 4, episode: g.planted_ep, quote, question, fixes: [fix] }),
      );
    if (g.fired_ep === undefined || g.fired_ep === null) {
      add('not_fired', `«${g.object}» появляется в ${g.planted_ep}-й серии и больше не нужен`, `зачем нам показали «${g.object}»?`, `Назначить серию, где «${g.object}» выстрелит`);
    } else if (g.fired_ep < g.planted_ep) {
      add('order', `«${g.object}»: появляется в ${g.planted_ep}-й, срабатывает в ${g.fired_ep}-й`, `откуда взялся «${g.object}»?`, `Показать «${g.object}» раньше ${g.fired_ep}-й серии`);
    } else if (g.fired_ep > last || g.planted_ep > last) {
      add('outside', `«${g.object}»: серии ${g.planted_ep} → ${g.fired_ep}, а в сезоне ${last}`, `что стало с «${g.object}»?`, `Уложить «${g.object}» в серии 1–${last}`);
    }
  }
  return out;
}

/** Script metrics: line density, line and overlay length, timing, speakers per scene. */
export function checkScriptMetrics(script: Script, production: ProductionConstraints, frame?: SeasonFrame): Finding[] {
  const out: Finding[] = [];
  const m = production.script_metrics;
  const ep = script.ep;
  const add = (code: string, quote: string, question: string, fix: string, holeType = 9) =>
    out.push(makeFinding({ check: `script_metrics.${code}`, controller: 'production', severity: 'major', holeType, episode: ep, quote, question, fixes: [fix] }));

  const lines = script.blocks.filter((b) => b.kind === 'line');
  const chars = lines.reduce((n, b) => n + b.text.length, 0);
  const perMinute = Math.round(chars / (script.duration_s / 60));
  const [lo, hi] = m.line_chars_per_minute;
  if (lines.length > 0 && (perMinute < lo || perMinute > hi)) {
    add(
      'chars_per_minute',
      `${ep}-я серия: ${perMinute} знаков реплик в минуту`,
      perMinute > hi ? 'почему все так тараторят?' : 'почему все молчат?',
      `Реплик — ${lo}–${hi} знаков в минуту`,
    );
  }
  for (const b of lines) {
    const words = countWords(b.text);
    if (words > m.max_line_words) add('line_words', `${b.speaker}: ${b.text}`, 'что он сказал? Слишком длинно.', `Сократить реплику до ${m.max_line_words} слов`);
  }
  for (const b of script.blocks.filter((x) => x.kind === 'overlay')) {
    if (countWords(b.text) > m.max_overlay_words) add('overlay_words', b.text, 'я не успела прочитать надпись', `Плашка — до ${m.max_overlay_words} слов`);
  }

  const end = Math.max(...script.blocks.map((b) => b.t1));
  if (Math.abs(end - script.duration_s) > script.duration_s * m.duration_tolerance) {
    add('duration', `${ep}-я серия: заявлено ${script.duration_s} с, по блокам ${end} с`, 'сколько идёт серия?', `Хронометраж в пределах ±${Math.round(m.duration_tolerance * 100)}%`);
  }
  if (frame && (script.duration_s < frame.duration_s.min || script.duration_s > frame.duration_s.max)) {
    add('duration', `${ep}-я серия: ${script.duration_s} с`, 'почему серия такой длины?', `Серия — ${frame.duration_s.min}–${frame.duration_s.max} с`);
  }

  let scene: string[] = [];
  let sceneText = '';
  const flush = () => {
    const speakers = [...new Set(scene)];
    if (speakers.length > production.limits.max_speakers_per_scene) {
      add('speakers', `${sceneText}: говорят ${speakers.join(', ')}`, 'кто тут говорит?', `Не больше ${production.limits.max_speakers_per_scene} говорящих в сцене`, 8);
    }
  };
  for (const b of script.blocks) {
    if (b.kind === 'scene') {
      flush();
      scene = [];
      sceneText = b.text;
    } else if (b.kind === 'line' && b.speaker) scene.push(b.speaker);
  }
  flush();
  return out;
}

export interface TextSource {
  /** Where the text comes from, e.g. "План, 12-я серия". */
  where: string;
  episode?: number;
  text: string;
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

/**
 * Legal marker words (smoking, vigilante justice, negative police, on-screen violence).
 * A hit is only a suspicion: it goes to the model-judge for confirmation.
 */
export function checkLegalMarkers(texts: TextSource[], legal: LegalConstraints): Finding[] {
  const out: Finding[] = [];
  const principles = new Map(legal.principles.map((p) => [p.id, p.text]));
  for (const marker of legal.markers) {
    // A marker is a word or stem: up to 3 more letters (endings), then a word boundary.
    // So «бьёт» and «сигарету» match, «ментальный» does not match «мент».
    const re = new RegExp(`(?<![\\p{L}])(?:${marker.words.map(escape).join('|')})\\p{L}{0,3}(?![\\p{L}])`, 'iu');
    for (const t of texts) {
      const m = re.exec(t.text);
      if (!m) continue;
      const sentence = t.text.split(/(?<=[.!?…])\s+/u).find((s) => re.test(s)) ?? t.text;
      out.push(
        makeFinding({
          check: `legal_markers.${marker.category}`,
          controller: 'legal',
          // Only a suspicion: the legal model controller confirms it. Minor keeps it from blocking a step.
          severity: 'minor',
          episode: t.episode,
          quote: sentence.trim(),
          question: `это можно показывать при ${legal.age_rating}?`,
          fixes: [`Проверить по правилу «${principles.get(marker.principle) ?? marker.principle}» и переписать сцену`],
        }),
      );
    }
  }
  return out;
}

/** Collects all human-readable text of a plan, cards and scripts for text checks. */
export function collectTexts(input: { plan?: SeasonPlan; cards?: EpisodeCard[]; scripts?: Script[] }): TextSource[] {
  const out: TextSource[] = [];
  for (const e of input.plan?.episodes ?? []) {
    out.push({ where: `План, ${e.ep}-я серия`, episode: e.ep, text: [e.title, e.event, e.heroine_action, e.cliffhanger].join('. ') });
  }
  for (const c of input.cards ?? []) {
    out.push({ where: `Карточка ${c.ep}`, episode: c.ep, text: [c.hook_0_5s, c.event, c.twist, c.heroine_action, c.punchline, c.cliffhanger, c.sound].join('. ') });
  }
  for (const s of input.scripts ?? []) {
    out.push({ where: `Сценарий ${s.ep}`, episode: s.ep, text: s.blocks.map((b) => b.text).join(' ') });
  }
  return out;
}

