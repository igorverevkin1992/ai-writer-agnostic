/** Valid sample artifacts for tests. Fictional content, not genre rules. */
import type { Bible, Concept, EpisodeCard, EpisodeOutline, Finding, Logline, Script, Villain } from './index.ts';

export const sampleConcept: Concept = {
  id: 'c1',
  title: 'Кровь невесты',
  premise: 'Муж женился на героине ради её редкой крови.',
  hook: 'В первую брачную ночь она находит договор о донорстве.',
  genre_formula: 'предательство → маска → месть → семейная тайна',
  reference_cases: [],
};

export const sampleLogline: Logline = {
  heroine: 'Лиза, 27 лет, актриса',
  wound: 'Мать бросила её в детстве',
  betrayer: 'Муж Андрей',
  motive: 'Спасти больную мать Андрея',
  revenge_goal: 'Разоблачить семью мужа',
  twist_secret: 'Свекровь — её родная мать',
  stakes: 'Жизнь и имя героини',
  text_35w: 'Актриса узнаёт, что муж женился на ней ради крови для своей матери, и под маской покорной жены разрушает его семью, пока не находит в ней свою.',
  ad_15w: 'Он женился ради её крови. Она осталась ради мести.',
};

const villain = (rank: 1 | 2 | 3 | 4 | 5, over: Partial<Villain> = {}): Villain => ({
  name: `Злодей ${rank}`,
  rank,
  role: (['boss', 'right_hand', 'guardian', 'executor', 'pawn'] as const)[rank - 1]!,
  threat: 'Угроза',
  link_to_ghost: rank === 1 ? 'Связан с матерью героини' : undefined,
  motive: 'Мотив',
  own_plan: 'План',
  resources: 'Ресурсы',
  weakness: 'Слабость',
  on_screen_ep: 1,
  mask: 'Маска',
  first_strike_ep: 2,
  takedown_ep: 3,
  punishment: { type: 'shame', public: true },
  key_to_next: 'Улика',
  ties: [],
  knows: [{ fact: 'f1', since_ep: 1 }],
  ...over,
});

export const sampleVillains: Villain[] = [5, 4, 3, 2, 1].map((r) => villain(r as 1 | 2 | 3 | 4 | 5));

export const sampleBible: Bible = {
  world_rules: [
    {
      rule: 'Кровь редкой группы нельзя купить',
      why: 'Донорство анонимно',
      cost: 'Нарушение — уголовное дело',
      cannot: 'Взять кровь без согласия',
      who_knows: ['Андрей'],
      practical_effect: 'Семье нужна жена, а не донор',
    },
  ],
  characters: [
    {
      name: 'Лиза',
      birth_year: 1999,
      look: 'Тёмные волосы до плеч, шрам на запястье',
      ghost: 'Мать ушла, когда ей было 5',
      lie: 'Меня нельзя любить',
      truth: 'Я достойна любви',
      want: 'Отомстить',
      need: 'Найти семью',
      arc_type: 'позитивная',
      mask: 'Покорная жена',
      hidden_power: 'Актёрский талант',
      knows_at_start: [],
      speech: 'Коротко, с иронией',
      limits: ['Не причиняет вреда детям'],
    },
  ],
  villains: sampleVillains,
  betrayal: {
    who: 'Андрей',
    what: 'Женился ради крови',
    why: 'Спасти мать',
    year: 2025,
    accomplices: ['Свекровь'],
    who_else_knew: [{ name: 'Врач', why_silent: 'Получил деньги' }],
    ep1_second: 70,
  },
  secrets: [{ layer: 1, truth: 'Свекровь — мать Лизы', revealed_ep: 50, goal_from: 'месть мужу', goal_to: 'правда о семье', clues: ['фото'] }],
  guns: [{ id: 'g1', object: 'Ключ от гримёрки', planted_ep: 2, fired_ep: 12, metro_visible: true }],
  timeline: { events: [{ id: 'e1', year: 2004, text: 'Мать уходит из семьи', participants: ['Лиза'] }] },
};

export const sampleOutline: EpisodeOutline = {
  ep: 8,
  title: 'Анализ',
  event: 'Лиза находит результаты анализа крови',
  question: 'Кто заказал анализ?',
  emotions: ['страх', 'гнев'],
  mood: 'suffering',
  anchors: ['paywall_hook'],
  strike_by_villain: true,
  strike_by_heroine: false,
  villains_introduced: [],
  hook_type: 'угроза',
  cliffhanger: 'За дверью — свекровь со шприцем',
};

export const sampleCard: EpisodeCard = {
  ep: 12,
  duration_s: 90,
  hook_0_5s: 'Рука Лизы сжимает ключ',
  event: 'Лиза открывает гримёрку свекрови',
  twist: 'Внутри её детские фото',
  emotions: ['страх', 'надежда'],
  punchline: 'Он знал. С самого начала.',
  cliffhanger: 'Свекровь в дверях с тем же ключом',
  cast: ['Лиза', 'Свекровь'],
  location: 'Театр: гримёрка',
  sound: 'Шаги за дверью',
  metro_frame: { face: 'Лиза', action: 'сжимает', object: 'ключ' },
  knowledge: { viewer: 'Свекровь следит', heroine: 'Ключ подходит', villain: 'Лиза ищет правду' },
};

export const sampleScript: Script = {
  ep: 12,
  title: 'Ключ',
  duration_s: 90,
  blocks: [
    { t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. ГРИМЁРКА — НОЧЬ. Крупно: рука ЛИЗЫ сжимает ключ.' },
    { t0: 5, t1: 8, kind: 'sound', text: 'Шаги за дверью.' },
    { t0: 8, t1: 11, kind: 'line', speaker: 'ЛИЗА', parenthetical: 'шёпотом', text: 'Он знал. С самого начала.' },
    { t0: 11, t1: 14, kind: 'overlay', text: '«3 дня до премьеры»' },
    { t0: 14, t1: 16, kind: 'silence', text: '2 с.' },
    { t0: 85, t1: 90, kind: 'scene', text: 'Дверь открывается. В проёме — свекровь с тем же ключом.' },
  ],
};

export const sampleFinding: Finding = {
  id: 'f1',
  controller: 'logic',
  holeType: 3,
  severity: 'blocker',
  episode: 4,
  quote: 'Лиза остаётся в доме свекрови',
  viewerQuestion: 'Зритель спросит: почему она не ушла в 4-й серии?',
  fixes: ['Дать ей причину остаться: паспорт у свекрови'],
  status: 'open',
};
