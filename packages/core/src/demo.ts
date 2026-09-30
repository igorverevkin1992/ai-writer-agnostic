/**
 * Demo mode: a provider that answers from the golden project instead of real models,
 * so the producer can walk the whole path in the browser without API keys.
 * Never used in real runs: the server enables it only with DEMO=1.
 */
import type { ProviderName } from './providers/config.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './providers/types.ts';
import { loadGolden, type ProjectFixture } from './fixtures.ts';

let cached: ProjectFixture | undefined;
const G = () => (cached ??= loadGolden());

function card(ep: number) {
  const o = G().plan.episodes.find((e) => e.ep === ep)!;
  const cast = ['Лиза', ...G().bible.characters.map((c) => c.name).filter((n) => n !== 'Лиза' && o.event.includes(n.slice(0, -1)))].slice(0, 3);
  return {
    ep,
    duration_s: 90,
    hook_0_5s: ep === 1 ? 'Свадебный бокал в руке Лизы дрожит' : `Продолжение: ${G().plan.episodes[ep - 2]?.cliffhanger ?? ''}`,
    event: o.event,
    twist: o.question,
    emotions: o.emotions,
    heroine_action: o.heroine_action,
    punchline: 'Я всё помню. И я никуда не уйду.',
    cliffhanger: o.cliffhanger,
    cast: cast.length > 1 ? cast : ['Лиза', 'Вера'],
    location: G().bible.locations[ep % G().bible.locations.length]!,
    sound: 'Скрип старой сцены',
    metro_frame: { face: 'Лиза', action: o.heroine_action.toLowerCase(), object: 'фото' },
    knowledge: { viewer: o.question, heroine: o.event, villain: 'Лиза ничего не знает' },
    acts_on: o.acts_on,
  };
}

function script(ep: number, fixed: boolean) {
  const c = card(ep);
  const other = (c.cast[1] ?? 'Вера').toUpperCase();
  const lines = [
    [other, 'Ты снова здесь так поздно? Что ты ищешь?'],
    ['ЛИЗА', 'Старые фотографии для выставки. Ничего особенного.'],
    [other, 'В этом театре лучше не открывать чужие шкафы.'],
    ['ЛИЗА', 'Тогда почему этот шкаф заперт на новый замок?'],
    [other, 'Не задавай вопросов, на которые не хочешь ответов.'],
    ['ЛИЗА', fixed ? 'Я уже знаю ответ. Мне нужны доказательства.' : 'Я уже знаю ответ. Просто хочу услышать его.'],
    [other, 'Иди спать. Завтра у всех тяжёлый день.'],
    ['ЛИЗА', 'Я не уйду, пока не узнаю правду о семье.'],
  ];
  return {
    ep,
    title: G().plan.episodes[ep - 1]!.title,
    duration_s: 90,
    blocks: [
      { t0: 0, t1: 5, kind: 'scene', text: `ИНТ. ${c.location.toUpperCase()} — НОЧЬ. ${c.hook_0_5s}.` },
      ...lines.map(([speaker, text], i) => ({ t0: 5 + i * 10, t1: 15 + i * 10, kind: 'line', speaker, text })),
      { t0: 85, t1: 90, kind: 'scene', text: `${c.cliffhanger}.` },
    ],
  };
}

/**
 * A judge that confirms every checklist item it is asked about, quoting the first line
 * of the judged text. For the demo and for tests: never used with real models.
 */
export function confirmChecklist(system: string): { items: { id: string; ok: boolean; quote: string; reason: string }[] } {
  const items = /Пункты:\n([\s\S]*?)\n\n/u.exec(system)?.[1] ?? '';
  const text = /Библия и план сезона:\n([\s\S]*)/u.exec(system)?.[1] ?? '';
  const quote = text.split('\n').find((l) => l.trim().length > 0)?.trim() ?? '';
  return {
    items: items
      .split('\n')
      .map((l) => /^([^:\s]+):/u.exec(l)?.[1])
      .filter((id): id is string => !!id)
      .map((id) => ({ id, ok: true, quote, reason: 'Выполнено в эталоне' })),
  };
}

function answer(task: string, req: LlmRequest): unknown {
  const [kind, a = '', b = ''] = task.split(':');
  switch (kind) {
    case 'concept':
      return [
        { id: 'c1', title: 'Муж женился на мне ради крови', premise: 'Реставратор узнаёт, что муж женился на ней ради крови её рода, которая возвращает молодость его семье.', hook: 'На свадьбе Лиза слышит: «Она ничего не подозревает».', genre_formula: 'предательство мужа → маска → тайный поиск правды → семейная тайна → законный финал', reference_cases: [] },
        { id: 'c2', title: 'Свекровь пьёт мою кровь', premise: 'Невестка замечает, что свекровь-прима не стареет, и находит в архиве театра её фото 1966 года.', hook: 'Фото 1966 года с лицом свекрови.', genre_formula: 'тайна → маска → стравливание → разоблачение', reference_cases: [] },
        { id: 'c3', title: 'Юбилей театра', premise: 'К столетию театра семья готовит «угасание» невестки, а она готовит своё выступление.', hook: 'Афиша юбилея с её именем в траурной рамке.', genre_formula: 'угроза → маска → союз женщин → публичное разоблачение', reference_cases: [] },
      ];
    case 'logline':
      return {
        heroine: 'Лиза, 32 года, реставратор фотографий',
        wound: 'Выросла в семье, где о прошлом молчали',
        betrayer: 'Муж Герман, ведущий актёр театра',
        motive: 'Кровь женщин её рода возвращает молодость семье мужа',
        revenge_goal: 'Разоблачить семью и спасти младшую сестру',
        twist_secret: 'Цикл начал сам Герман, а не его мать',
        stakes: 'Жизнь Лизы и её сестры Даши',
        text_35w: 'Реставратор узнаёт, что муж женился на ней ради крови её рода. Она остаётся рядом, играет любящую жену и готовит месть, после которой он потеряет всё.',
        ad_15w: 'Он женился ради её крови. Она осталась ради мести.',
      };
    case 'bible':
      return G().bible;
    case 'season_plan': {
      const [from = 1, to = Infinity] = a.split('-').map(Number);
      const inPart = (ep: number) => ep >= from && ep <= to;
      return { episodes: G().plan.episodes.filter((e) => inPart(e.ep)), deviations: G().plan.deviations.filter((d) => inPart(d.ep)) };
    }
    case 'extract_facts':
      return { facts: G().bible.facts, knowledge: [] };
    case 'devil_advocate':
      if (a === '3' && b.includes('41–50')) {
        return [{ holeType: 3, severity: 'blocker', quote: 'Лиза сама возвращается в семью, чтобы спасти сестру', viewerQuestion: 'Зритель спросит: почему она не идёт в полицию, а возвращается к убийцам?', fixes: ['Показать, что следователь без доказательств не может защитить Дашу', 'Дать Лизе план, ради которого она возвращается'] }];
      }
      return [];
    case 'persona':
      return [];
    case 'respond':
      return { action: 'cite', fact_id: 'f_dasha_target', explanation: 'Даша — следующая в роду, поэтому Лиза не может ждать полицию' };
    case 'judge':
      return { closed: false, reason: 'Ответ объясняет мотив, но не видно, почему полиция не поможет' };
    case 'episode_cards': {
      const [from, to] = a.split('-').map(Number);
      return Array.from({ length: to! - from! + 1 }, (_, i) => card(from! + i));
    }
    case 'script':
      return script(Number(a), req.system?.includes('Исправь замечания') ?? false);
    case 'controller':
      if (a === 'metro' && Number(b) % 7 === 1) {
        return [{ holeType: 9, severity: 'minor', quote: 'Иди спать. Завтра у всех тяжёлый день.', viewerQuestion: 'Зритель спросит: что тут важного без звука?', fixes: ['Крупный план ключа в руке'] }];
      }
      return [];
    case 'polish': {
      const tone = ['холоднее', 'с угрозой', 'с подтекстом', 'с усмешкой', 'шёпотом'];
      const t0 = Number(/с (\d+(?:\.\d+)?)-й по/u.exec(req.system ?? '')?.[1] ?? 5);
      const t1 = Number(/по (\d+(?:\.\d+)?)-ю секунду/u.exec(req.system ?? '')?.[1] ?? 15);
      return { variants: tone.map((p) => [{ t0, t1, kind: 'line', speaker: 'ЛИЗА', parenthetical: p, text: 'Я знаю, что ты сделала. И скоро узнают все.' }]) };
    }
    case 'checklist_judge':
      return confirmChecklist(req.system ?? '');
    case 'shootable':
      return { verdict: 'light_edit', reason: 'Реплики стоит сделать живее' };
    case 'eval_match':
      return { matches: [] };
    default:
      return [];
  }
}

export class DemoProvider implements Provider {
  constructor(readonly name: ProviderName) {}

  async complete(req: LlmRequest, target: CallTarget): Promise<ProviderResult> {
    return {
      text: JSON.stringify(answer(req.task ?? '', req)),
      model: `${target.model} (демо)`,
      usage: { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 },
    };
  }

  async countTokens(): Promise<number> {
    return 1;
  }
}

export const demoProviders = (): Record<ProviderName, Provider> => ({
  anthropic: new DemoProvider('anthropic'),
  google: new DemoProvider('google'),
  openai_compatible: new DemoProvider('openai_compatible'),
});
