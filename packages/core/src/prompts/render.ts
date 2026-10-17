import { episodeRange, type GenreKit, type Kb, type MethodStep } from '@aiw/kb';
import { z } from 'zod';

export class PromptError extends Error {
  override name = 'PromptError';
}

/** Loads a prompt template "<role>/<name>" from the knowledge base and fills {{placeholders}}. */
export function renderPrompt(kb: Kb, key: string, vars: Record<string, string | number>): string {
  const template = kb.prompts[key];
  if (template === undefined) throw new PromptError(`Нет промпта ${key} в packages/kb/prompts`);
  const body = template.replace(/<!--[\s\S]*?-->/gu, '').trim();
  const missing = new Set<string>();
  const out = body.replace(/\{\{(\w+)\}\}/gu, (_, name: string) => {
    const v = vars[name];
    if (v === undefined) {
      missing.add(name);
      return '';
    }
    return String(v);
  });
  if (missing.size) throw new PromptError(`Промпт ${key}: не заданы ${[...missing].join(', ')}`);
  return out;
}

/** JSON schema of a Zod type, pretty-printed for a prompt. */
export function schemaText(schema: z.ZodType): string {
  return JSON.stringify(z.toJSONSchema(schema, { io: 'input', unrepresentable: 'any' }));
}

const list = (items: string[]) => items.map((s) => `- ${s}`).join('\n');

export interface KnowledgeOptions {
  /** Pipeline step the block is built for: method principles are narrowed to it. */
  step?: MethodStep;
}

/**
 * How much of the methods section stays when the block is over the size cap: everything; without
 * the general principles (no `for`); only the principles written for this step alone; summaries only.
 */
export type MethodsTrim = 'none' | 'general' | 'specific' | 'all';
const TRIM_ORDER: readonly MethodsTrim[] = ['none', 'general', 'specific', 'all'];

/** Method principles for a step and a genre: those addressed to them and those addressed to all. */
export function methodsBlock(kb: Kb, step?: MethodStep, genreId?: string, trim: MethodsTrim = 'none'): string[] {
  const kept = (p: { for: string[] }) =>
    trim === 'none' || (trim === 'general' && p.for.length > 0) || (trim === 'specific' && p.for.length === 1 && p.for[0] === step);
  return Object.values(kb.methods).flatMap((m) => {
    const principles = m.principles.filter(
      (p) => (!step || !p.for.length || p.for.includes(step)) && (!genreId || !p.genres.length || p.genres.includes(genreId)) && kept(p),
    );
    return [`### ${m.name} (${m.author})`, m.summary, ...principles.map((p) => `- ${p.id}. ${p.text}`)];
  });
}

/** Steps whose author gets the critic's full rule set; the others get the category list only. */
const FULL_CRITIC_STEPS: ReadonlySet<MethodStep> = new Set<MethodStep>(['bible', 'season_plan', 'episode_cards', 'scripts']);

/** The knowledge block is kept under this many characters per step; the methods section gives way first. */
export const KNOWLEDGE_CAP = 60_000;

/** Hole catalog questions as the auditor of this genre asks them: the genre guide replaces or adds to the catalog's. */
export function holeQuestions(kb: Kb, kit: GenreKit): { id: number; name: string; questions: string[] }[] {
  const guide = kit.guide;
  return kb.holes.holes.map((h) => {
    const own = guide?.hole_questions[String(h.id)] ?? [];
    const questions = guide?.replace_hole_questions.includes(String(h.id)) && own.length ? own : [...h.questions, ...own];
    return { id: h.id, name: h.name, questions };
  });
}

/**
 * «Что проверит критик»: what the auditor of another family will ask about this step's text.
 * The author sees the same questions as the critic, so fewer holes reach the review. Concept and
 * logline get only the category names and their checks; the later steps get the category rules,
 * the judge's questions and fix hints of the genre rules, and the hole catalog questions.
 */
export function criticBlock(kb: Kb, kit: GenreKit, step?: MethodStep): string[] {
  const full = step !== undefined && FULL_CRITIC_STEPS.has(step);
  const categories = kb.review.categories.filter((c) => !c.genres.length || c.genres.includes(kit.genre.id));
  const out = [
    '## Что проверит критик',
    'Текст прочитает проверяющий другой модели с этими вопросами. Ответ «так работает» не принимается; каждое замечание — с цитатой.',
    '### Категории разбора',
    ...categories.flatMap((c) => [`- ${c.id}. ${c.name}: ${c.check}`, ...(full ? c.rules.map((r) => `  - ${r}`) : [])]),
  ];
  if (!full) return out;
  const judged = kit.rules.rules.filter((r) => r.check.llm);
  if (judged.length) {
    out.push(
      '### Вопросы судьи к правилам жанра',
      ...judged.map((r) => `- ${r.id}: ${r.check.llm}${r.fix_hints.length ? ` Как исправить: ${r.fix_hints.join(' / ')}` : ''}`),
    );
  }
  out.push('### Каталог дыр', ...holeQuestions(kb, kit).map((h) => `- ${h.id}. ${h.name}: ${h.questions.join(' ')}`));
  return out;
}

/**
 * The knowledge base block for a genre: rules, season frame, checklist, glossary, methods.
 * Stable across calls of one step, so it goes into the cacheable prefix.
 */
export function knowledgeBlock(kb: Kb, kit: GenreKit, opts: KnowledgeOptions = {}): string {
  const f = kit.frame;
  const anchors = Object.entries(f.anchors).map(([id, spec]) => {
    const { min, max } = episodeRange(spec);
    const label = f.anchor_labels[id];
    return `${id}${label ? ` (${label})` : ''}: ${min === max ? min : `${min}–${max}`}`;
  });
  const v = f.villains;
  const ladder = v
    ? [
        `Злодеев ровно ${v.count}. Босс в кадре к ${v.boss_on_screen_by}-й серии, все — к ${v.all_on_screen_by}-й.`,
        ...Object.keys(v.roles)
          .sort((a, b) => Number(b) - Number(a))
          .map((r) => {
            const { min, max } = episodeRange(v.takedowns[r]!);
            return `ранг ${r} — ${v.roles[r]}: в кадре к ${v.on_screen_by[r]}-й, снятие ${min === max ? min : `${min}–${max}`}`;
          }),
        `Ответный удар злодеев после снятия — за ${v.counterstrike_within} серии. Публично и законно снимают ранги ${v.public_and_legal.join(', ')}.`,
      ]
    : ['Лестницы злодеев в этом жанре нет.'];
  const p = kit.production;
  const build = (trim: MethodsTrim) => [
    `# База знаний: ${kit.genre.title}${kit.genre.platform ? ` (${kit.genre.platform})` : ''}`,
    '## Правила жанра',
    list(kit.rules.rules.map((r) => `${r.id} [${r.severity}] ${r.text}`)),
    '## Каркас сезона',
    `Серий: ${f.episodes}, бесплатных: ${f.free}, длительность ${f.duration_s.min}–${f.duration_s.max} с (цель ${f.duration_s.target} с). Допуск опорных точек: ±${f.tolerance} с объяснением.`,
    `Опорные точки (id: серия): ${anchors.join('; ')}.`,
    `Блоки: ${f.blocks.map(([a, b]) => `${a}–${b}`).join(', ')}.`,
    ...(Object.keys(f.hook_types).length ? [`Типы обрывов (id: название): ${Object.entries(f.hook_types).map(([id, name]) => `${id}: ${name}`).join('; ')}.`] : []),
    `Ритм: ответ на удар за ${f.rhythm.response_within} серии; страдание не дольше ${f.rhythm.max_suffering_run} серий подряд; один тип крючка не больше ${f.rhythm.max_same_hook_run} серий подряд; ${f.rhythm.emotions_per_episode.join('–')} эмоциональные точки в серии.`,
    ...(Object.keys(f.lines).length
      ? [
          `Линии кайфа (id: название): ${Object.entries(f.lines).map(([id, name]) => `${id}: ${name}`).join('; ')}.` +
            (f.rhythm.max_without_line ? ` Каждая линия — не реже чем раз в ${f.rhythm.max_without_line + 1} серии.` : '') +
            (f.rhythm.line_gap_exempt.length ? ` Исключение: ${f.rhythm.line_gap_exempt.map((a) => f.anchor_labels[a] ?? a).join(', ')}.` : ''),
        ]
      : []),
    '## Лестница злодеев',
    list(ladder),
    '## Чек-лист сезона',
    list(kit.checklist.items.map((i) => `${i.text} — ${i.points}`)),
    `Порог: ${kit.checklist.pass} из ${kit.checklist.total}.`,
    '## Производство и право',
    list([
      `До ${p.limits.max_regular_characters} постоянных героев, до ${p.limits.max_locations} локаций, до ${p.limits.max_speakers_per_scene} говорящих в сцене.`,
      `Реплика-удар до ${p.script_metrics.max_line_words} слов, плашка до ${p.script_metrics.max_overlay_words} слов.`,
      ...kit.legal.principles.map((x) => x.text),
      `Возрастной рейтинг ${kit.legal.age_rating}.`,
    ]),
    ...(kit.guide
      ? kit.guide.sections.flatMap((s) => [`## ${s.title}`, list(s.items)])
      : []),
    '## Словарь',
    list(kb.glossary.terms.filter((t) => !t.genres.length || t.genres.includes(kit.genre.id)).map((t) => `${t.term} — ${t.definition}`)),
    '## Методики',
    ...methodsBlock(kb, opts.step, kit.genre.id, trim),
    '## Кейсы',
    list(kb.cases.filter((c) => !c.stub && (!c.genres.length || c.genres.includes(kit.genre.id))).map((c) => `${c.id}: ${c.title} — ${c.summary}`)),
    ...criticBlock(kb, kit, opts.step),
  ].join('\n');
  // Over the cap, the methods section gives way step by step; the last stage is taken as it is.
  let text = '';
  for (const trim of TRIM_ORDER) {
    text = build(trim);
    if (text.length <= KNOWLEDGE_CAP) break;
  }
  return text;
}
