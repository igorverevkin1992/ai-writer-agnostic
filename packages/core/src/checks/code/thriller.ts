import { DEFAULT_TERMS, episodeRange, type GenreTerms, type SeasonFrame } from '@aiw/kb';
import type { Bible } from '../../schemas/bible.ts';
import { countWords } from '../../schemas/common.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { Script } from '../../schemas/script.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { formatEpisodes, makeFinding } from './finding.ts';

/*
 * Checks of the vertical thriller study («Драматургия вертикального хоррора», блок 6): the season
 * question and its nested questions, the clue map, the threat's sign, the golden three, resets,
 * dramatic irony, the beat grid of a card and the script linter. Everything genre-specific
 * (numbers, anchor ids, hook types) comes in as parameters from the genre rules.
 */

type Sev = Finding['severity'];
const f = (check: string, severity: Sev, quote: string, question: string, fix: string, episode?: number, holeType = 10): Finding =>
  makeFinding({ check, controller: 'structure', severity, holeType, episode, quote, question, fixes: [fix] });

export interface MatryoshkaParams {
  minLevels: number;
  maxActive: number;
  maxQuestionWords: number;
}

/** Trunk question and nested questions: count, scale, chain «closed n → opened n+1», active subplots, finale. */
export function checkMatryoshka(bible: Bible, frame: SeasonFrame, p: MatryoshkaParams): Finding[] {
  const out: Finding[] = [];
  if (!bible.trunk) {
    out.push(f('matryoshka.unmarked', 'major', 'trunk', 'какой один вопрос держит весь сезон?', 'Записать в библии ствол: вопрос сезона, ответ и один вопрос на следующий сезон'));
  } else if (countWords(bible.trunk.question) > p.maxQuestionWords) {
    out.push(f('matryoshka.trunk', 'major', bible.trunk.question, 'я не могу повторить вопрос сезона одной фразой', `Сократить вопрос сезона до ${p.maxQuestionWords} слов`));
  }
  const nested = [...bible.nested].sort((a, b) => a.level - b.level);
  if (nested.length < p.minLevels) {
    out.push(
      f('matryoshka.levels', 'major', `Уровней матрёшки: ${nested.length}`, 'на сколько серий хватит этой тайны?', `Довести вложенные вопросы до ${p.minLevels} уровней с растущим масштабом`),
    );
  }
  for (let i = 1; i < nested.length; i++) {
    const prev = nested[i - 1]!;
    const cur = nested[i]!;
    if (cur.scale <= prev.scale) {
      out.push(f('matryoshka.scale', 'major', cur.question, `уровень ${cur.level} не крупнее уровня ${prev.level} — зачем мне идти дальше?`, 'Каждый следующий вопрос крупнее прежнего: кто → почему → сколько → кто стоит → что это значит', cur.opened_ep));
    }
    if (prev.closed_ep !== undefined && cur.opened_ep !== prev.closed_ep) {
      out.push(
        f('matryoshka.chain', 'major', `${prev.question} → ${cur.question}`, `вопрос ${prev.level} закрыт в ${prev.closed_ep}-й, а вопрос ${cur.level} открыт в ${cur.opened_ep}-й — что держит между ними?`, `Открыть вопрос ${cur.level} в той же серии, где закрыт вопрос ${prev.level}`, prev.closed_ep),
      );
    }
  }
  for (let ep = 1; ep <= frame.episodes; ep++) {
    const active = nested.filter((q) => q.opened_ep <= ep && (q.closed_ep === undefined || q.closed_ep > ep));
    if (active.length > p.maxActive) {
      out.push(f('matryoshka.active', 'blocker', active.map((q) => q.question).join('; '), `сколько тайн я должен держать в голове в ${ep}-й серии?`, `Не больше ${p.maxActive} открытых подтайн сразу: закрыть одну раньше`, ep));
      break;
    }
  }
  const open = nested.filter((q) => q.closed_ep === undefined);
  if (bible.trunk && open.length > 1) {
    out.push(f('matryoshka.finale_questions', 'major', open.map((q) => q.question).join('; '), 'сколько вопросов мне оставили в финале? Больше одного — обман', 'Закрыть все подтайны, кроме одной: в финале ровно один новый вопрос', frame.episodes));
  }
  return out;
}

export interface CluesParams {
  min: number;
  payoffWithin: number;
}

/** Clue map: per question ≥min clues, one visual, one contradicting, payoff ≤N episodes after planting. */
export function checkClues(bible: Bible, p: CluesParams): Finding[] {
  const out: Finding[] = [];
  const questions = new Map<number, string>([[0, bible.trunk?.question ?? 'ствол'], ...bible.nested.map((q) => [q.level, q.question] as [number, string])]);
  for (const [level, question] of questions) {
    const clues = bible.clues.filter((c) => c.question === level);
    if (clues.length < p.min) out.push(f('clues.count', 'major', question, `как я должен был догадаться? Улик: ${clues.length}`, `Посадить не меньше ${p.min} улик на вопрос «${question}»`));
    if (clues.length && !clues.some((c) => c.visual)) out.push(f('clues.visual', 'major', question, 'все улики — словами; что я мог заметить сам?', 'Хотя бы одну улику показать кадром без комментария'));
    if (clues.length >= p.min && !clues.some((c) => c.contradicts)) out.push(f('clues.contradiction', 'minor', question, 'версия одна — о чём спорить в комментариях?', 'Добавить улику-противоречие: на две поддерживающие — одна против'));
  }
  for (const c of bible.clues) {
    if (c.paid_ep !== undefined && c.paid_ep - c.planted_ep > p.payoffWithin) {
      out.push(f('clues.payoff_late', 'major', c.text, `улика из ${c.planted_ep}-й окупается только в ${c.paid_ep}-й — я её забыл`, `Окупить улику не позже чем через ${p.payoffWithin} серий после посадки`, c.paid_ep));
    }
  }
  return out;
}

/** The threat has a recurring visual sign shown early. */
export function checkThreatSign(bible: Bible, byEp: number): Finding[] {
  if (!bible.threat) return [f('threat_sign.missing', 'major', 'threat', 'у угрозы нет лица и знака — чего мне бояться?', 'Записать в библии угрозу: архетип, кто это, повторяющийся визуальный знак и серию, где он показан')];
  if (bible.threat.sign_shown_ep > byEp) {
    return [f('threat_sign.late', 'major', bible.threat.visual_sign, `знак угрозы впервые в ${bible.threat.sign_shown_ep}-й серии — до этого я не знаю, кого искать в кадре`, `Показать знак угрозы не позже ${byEp}-й серии`, bible.threat.sign_shown_ep)];
  }
  return [];
}

export interface Golden3Params {
  irreversibleBy: number;
  maxCastEp1: number;
}

/** Golden three: an irreversible event in the first episodes, few characters in the first one. */
export function checkGolden3(plan: SeasonPlan, cards: EpisodeCard[] | undefined, p: Golden3Params): Finding[] {
  const out: Finding[] = [];
  const first = plan.episodes.filter((e) => e.ep <= p.irreversibleBy);
  if (first.length && !first.some((e) => e.irreversible)) {
    out.push(f('golden_three.irreversible', 'blocker', first.map((e) => e.event).join(' / '), 'ничего нельзя вернуть? Тогда угроза не всерьёз', `В сериях 1–${p.irreversibleBy} дать необратимое событие и отметить его irreversible`, 1));
  }
  const card1 = cards?.find((c) => c.ep === 1);
  if (card1 && card1.cast.length > p.maxCastEp1) {
    out.push(f('golden_three.characters', 'major', `В кадре 1-й серии: ${card1.cast.join(', ')}`, 'кто все эти люди? Я ещё никого не знаю', `В 1-й серии не больше ${p.maxCastEp1} персонажей`, 1, 9));
  }
  return out;
}

export interface ResetsParams {
  anchors: string[];
  min: number;
  maxGap: number;
}

/** Resets: at least `min` of them in the season and no more than `maxGap` episodes between them. */
export function checkResets(plan: SeasonPlan, frame: SeasonFrame, p: ResetsParams): Finding[] {
  const out: Finding[] = [];
  const eps = plan.episodes.filter((e) => e.anchors.some((a) => p.anchors.includes(a))).map((e) => e.ep).sort((a, b) => a - b);
  if (eps.length < p.min) {
    out.push(f('resets.count', 'major', `Перезагрузок в плане: ${eps.length}`, 'правила игры ни разу не поменялись — зачем смотреть дальше середины?', `Поставить не меньше ${p.min} перезагрузок (${p.anchors.map((a) => frame.anchor_labels[a] ?? a).join(', ')})`));
  }
  const points = [0, ...eps, frame.episodes];
  for (let i = 1; i < points.length; i++) {
    const gap = points[i]! - points[i - 1]!;
    if (gap > p.maxGap) {
      out.push(f('resets.gap', 'major', `Серии ${points[i - 1]! + 1}–${points[i]}`, `${gap} серий без перезагрузки — всё предсказуемо`, `Между перезагрузками не больше ${p.maxGap} серий`, points[i - 1]! + 1));
    }
  }
  return out;
}

/** Dramatic irony: the threat shown to the viewer is not revealed to the hero in the same episode; suspense runs end in a surprise. */
export function checkKnowledgeMode(plan: SeasonPlan, maxSuspenseRun: number, terms: GenreTerms = DEFAULT_TERMS): Finding[] {
  const out: Finding[] = [];
  const eps = [...plan.episodes].sort((a, b) => a.ep - b.ep);
  if (!eps.some((e) => e.knowledge_mode)) {
    return [f('knowledge_mode.unmarked', 'minor', 'knowledge_mode', 'кто знает больше — я или героиня?', 'Отметить в каждой серии режим знания: suspense, mystery или surprise')];
  }
  for (const e of eps) {
    if (e.threat_seen_by_viewer && e.threat_known_to_hero) {
      out.push(f('knowledge_mode.same_episode', 'major', e.event, `${terms.hero.nom} узнаёт угрозу в той же серии, где я её увидел — где моё «оглянись!»?`, 'Раскрыть угрозу герою не раньше следующей серии', e.ep));
    }
  }
  let run: number[] = [];
  const flush = () => {
    if (run.length > maxSuspenseRun) out.push(f('knowledge_mode.suspense_run', 'major', `Саспенс в сериях ${formatEpisodes(run)}`, `${run.length} серий подряд я знаю больше ${terms.hero.gen} — она выглядит глупой`, `После ${maxSuspenseRun} серий саспенса дать серию сюрприза`, run[maxSuspenseRun]));
    run = [];
  };
  for (const e of eps) {
    if (e.knowledge_mode === 'suspense') run.push(e.ep);
    else flush();
  }
  flush();
  return out;
}

/** A hook type (e.g. false rescue) at most `max` times per `per` consecutive episodes. */
export function checkHookQuota(plan: SeasonPlan, frame: SeasonFrame, type: string, per: number, max: number): Finding[] {
  const eps = plan.episodes.filter((e) => e.hook_type === type).map((e) => e.ep).sort((a, b) => a - b);
  const name = frame.hook_types[type] ?? type;
  for (let i = 0; i + max < eps.length; i++) {
    if (eps[i + max]! - eps[i]! < per) {
      return [f('hook_quota.exceeded', 'major', `«${name}» в сериях ${formatEpisodes(eps.slice(i, i + max + 1))}`, 'опять обманули? Второй раз я не поверю', `«${name}» — не чаще ${max} на ${per} серий`, eps[i + max])];
    }
  }
  return [];
}

/** The strongest twist is marked and does not sit on the paywall episode. */
export function checkStrongestTwist(plan: SeasonPlan, frame: SeasonFrame, anchor: string): Finding[] {
  const marked = plan.episodes.filter((e) => e.strongest_twist);
  if (!marked.length) return [f('strongest_twist.missing', 'minor', 'strongest_twist', 'какой поворот сезона самый сильный?', 'Отметить серию самого сильного твиста (strongest_twist) — на перезагрузке или в финале')];
  const pw = plan.episodes.find((e) => e.anchors.includes(anchor))?.ep ?? (frame.anchors[anchor] ? episodeRange(frame.anchors[anchor]!).max : undefined);
  return marked
    .filter((e) => e.ep === pw)
    .map((e) => f('strongest_twist.on_paywall', 'major', e.event, 'самое сильное потрачено на барьере — что мне покажут за деньги потом?', 'Перенести сильнейший твист на перезагрузку или в финал; на барьере — второй по силе', e.ep));
}

/** Beat grid of a card: order, hook ≤3 s, stakes ≤15 s, new info in escalation, one reversal readable muted, cliffhanger last. */
export function checkBeats(cards: EpisodeCard[], frame: SeasonFrame, opts: { finalEp?: number } = {}): Finding[] {
  const out: Finding[] = [];
  const order = ['HOOK', 'STAKES', 'ESCALATION', 'REVERSAL', 'CLIFFHANGER'];
  for (const c of cards) {
    const ep = c.ep;
    const beats = c.beats ?? [];
    if (!beats.length) {
      out.push(f('beats.missing', 'major', `${ep}-я серия`, 'где скелет серии по секундам?', 'Расписать биты: HOOK 0–3 с, STAKES до 15 с, ESCALATION, REVERSAL, CLIFFHANGER последние 5–10 с', ep, 9));
      continue;
    }
    const idx = beats.map((b) => order.indexOf(b.type));
    if (idx.some((v, i) => i > 0 && v < idx[i - 1]!)) out.push(f('beats.order', 'major', beats.map((b) => b.type).join(' → '), 'биты перепутаны: разворот раньше ставок?', 'Порядок битов: HOOK → STAKES → ESCALATION → REVERSAL → CLIFFHANGER', ep, 9));
    const hook = beats.find((b) => b.type === 'HOOK');
    if (!hook) out.push(f('beats.missing', 'blocker', `${ep}-я серия: нет бита HOOK`, 'что меня держит на первой секунде?', 'Начать серию с хука 0–3 с', ep, 9));
    else if (hook.t0 > 0 || hook.t1 > 3) out.push(f('beats.hook_long', 'blocker', hook.text, `хук тянется до ${hook.t1}-й секунды — я уже пролистнул`, 'Хук укладывается в первые 3 секунды', ep, 9));
    const stakes = beats.find((b) => b.type === 'STAKES');
    if (stakes && stakes.t1 > 15) out.push(f('beats.stakes_late', 'major', stakes.text, `к ${stakes.t1}-й секунде я всё ещё не знаю, чего она хочет`, 'Ставки — к 15-й секунде: «герой хочет X, но Y»', ep, 9));
    if (!c.goal?.trim() || !c.obstacle?.trim()) out.push(f('beats.goal', 'blocker', `${ep}-я серия`, 'чего она хочет в этой серии и что мешает?', 'Заполнить goal и obstacle карточки', ep));
    const esc = beats.filter((b) => b.type === 'ESCALATION');
    if (esc.length && !esc.some((b) => b.new_info?.trim())) out.push(f('beats.new_info', 'major', esc[0]!.text, 'середина серии ничего нового не даёт — тут я ухожу', 'В эскалации — хотя бы одна новая информация или ухудшение (new_info)', ep, 9));
    const rev = beats.filter((b) => b.type === 'REVERSAL');
    if (!rev.length) out.push(f('beats.reversal', 'blocker', `${ep}-я серия`, 'где разворот на 180°?', 'Дать разворот: ловушка, улика меняет смысл или ход угрозы', ep));
    else {
      if (c.duration_s <= 60 && rev.length > 1) out.push(f('beats.reversal_double', 'major', rev.map((b) => b.text).join(' / '), 'два разворота за минуту — это две серии', 'При ≤60 с оставить один разворот', ep, 9));
      if (rev.some((b) => b.mute_readable === false)) out.push(f('beats.mute', 'major', rev.find((b) => b.mute_readable === false)!.text, 'без звука поворот пропал — значит, это была громкость', 'Дать развороту визуальный эквивалент', ep, 9));
    }
    const last = beats[beats.length - 1]!;
    const isFinal = c.ep === (opts.finalEp ?? frame.episodes);
    if (last.type !== 'CLIFFHANGER') {
      if (!isFinal) out.push(f('beats.cliffhanger', 'blocker', last.text, 'серия закончилась ответом — есть повод остановиться', 'Последний бит — CLIFFHANGER: кадр задаёт вопрос, а не отвечает', ep));
    } else {
      const len = last.t1 - last.t0;
      if (len < 3 || len > 12) out.push(f('beats.cliffhanger', 'major', last.text, `обрыв длится ${len} с`, 'Клиффхэнгер — последние 5–10 секунд', ep, 9));
      const types = Object.keys(frame.hook_types);
      if (types.length && last.kind && !types.includes(last.kind)) out.push(f('beats.cliffhanger', 'major', last.kind, 'что это за обрыв?', `Тип обрыва — из словаря: ${types.join(', ')}`, ep, 9));
    }
    if (c.ep > 1 && !c.link_prev) out.push(f('beats.link', 'minor', `${ep}-я серия`, 'как это связано с тем, на чём оборвалась прошлая?', 'Указать link_prev: overlap, instant, false_result или pov_shift', ep, 9));
  }
  return out;
}

export interface LinterParams {
  talkingHeadsS: number;
  voiceoverS: number;
  maxFaces: number;
  maxSpeakers: number;
  maxWideShots: number;
  maxWideS: number;
  maxSoundHits: number;
  overlapS: number;
  /** Episodes after the paywall: overlap limit after it. */
  paywallEp?: number;
  overlapAfterPaywallS: number;
}

/** Script linter: flashbacks in episode 1, talking heads, faces, wide shots, plot sounds without a double, sound hits, speakers, overlap, voice-over. */
export function checkScriptLinter(script: Script, p: LinterParams): Finding[] {
  const out: Finding[] = [];
  const ep = script.ep;
  const blocks = script.blocks;
  if (ep === 1 && blocks.some((b) => b.kind === 'scene' && b.flashback)) {
    out.push(f('script_linter.flashback', 'blocker', blocks.find((b) => b.flashback)!.text, 'мне показывают прошлое, когда я ещё не знаю настоящего', 'Убрать флэшбэк из 1-й серии: предыстория — через маркеры в действии', ep, 9));
  }
  // Talking heads: consecutive lines with nothing but lines between them.
  let runStart: number | undefined;
  let runEnd = 0;
  let runText = '';
  const flushRun = () => {
    if (runStart !== undefined && runEnd - runStart > p.talkingHeadsS) {
      out.push(f('script_linter.talking_heads', 'blocker', runText.trim(), `${Math.round(runEnd - runStart)} с разговора без смены положения, угрозы или информации`, `Разбить диалог действием, звуком или вставкой каждые ${p.talkingHeadsS} с`, ep, 9));
    }
    runStart = undefined;
    runText = '';
  };
  for (const b of blocks) {
    if (b.kind === 'line') {
      runStart ??= b.t0;
      runEnd = b.t1;
      runText += `${b.speaker}: ${b.text} `;
    } else flushRun();
  }
  flushRun();
  for (const b of blocks.filter((x) => x.kind === 'scene')) {
    if (b.faces !== undefined && b.faces > p.maxFaces && !(b.in_frame?.length)) {
      out.push(f('script_linter.faces', 'major', b.text, `${b.faces} лица в кадре 9:16 — кого я должен смотреть?`, 'Монтаж по одному/парами или пометка in_frame: кто именно в кадре', ep, 8));
    }
  }
  const wide = blocks.filter((b) => b.kind === 'scene' && b.shot === 'wide');
  if (wide.length > p.maxWideShots || wide.some((b) => b.t1 - b.t0 > p.maxWideS)) {
    out.push(f('script_linter.wide_shots', 'major', wide.map((b) => b.text).join(' / '), 'общий план в 9:16 — я не вижу деталей', `Общий план — не больше ${p.maxWideShots} на серию и не дольше ${p.maxWideS} с`, ep, 8));
  }
  for (const b of blocks.filter((x) => x.kind === 'sound' && x.plot && !x.visual_double)) {
    out.push(f('script_linter.sound_double', 'blocker', b.text, 'я смотрю без звука — что я пропустил?', 'Дать сюжетному звуку визуальный дубль (visual_double)', ep, 9));
  }
  const hits = blocks.filter((b) => b.kind === 'sound' && b.hit);
  if (hits.length > p.maxSoundHits) out.push(f('script_linter.sound_hits', 'major', hits.map((b) => b.text).join(' / '), 'второй удар уже не пугает', `Звуковой удар — не больше ${p.maxSoundHits} на серию`, ep, 9));
  const speakers = new Set(blocks.filter((b) => b.kind === 'line' && b.speaker).map((b) => b.speaker));
  if (speakers.size > p.maxSpeakers) out.push(f('script_linter.speakers_episode', 'major', [...speakers].join(', '), 'слишком много голосов за полторы минуты', `Говорящих в серии — не больше ${p.maxSpeakers}`, ep, 8));
  const overlap = blocks.filter((b) => b.kind === 'scene' && b.overlap).reduce((n, b) => n + (b.t1 - b.t0), 0);
  const limit = p.paywallEp !== undefined && ep > p.paywallEp ? p.overlapAfterPaywallS : p.overlapS;
  if (overlap > limit) out.push(f('script_linter.overlap', 'major', `${ep}-я серия: перехлёст ${overlap} с`, 'это я уже видел — хук съеден повтором', `Перехлёст с прошлой серией не длиннее ${limit} с`, ep, 9));
  const vo = blocks.filter((b) => b.kind === 'line' && b.voiceover).reduce((n, b) => n + (b.t1 - b.t0), 0);
  if (vo > p.voiceoverS) out.push(f('script_linter.voiceover', 'major', `${ep}-я серия: голос за кадром ${vo} с`, 'мне читают, а не показывают', `Голос за кадром — не дольше ${p.voiceoverS} с`, ep, 9));
  return out;
}
