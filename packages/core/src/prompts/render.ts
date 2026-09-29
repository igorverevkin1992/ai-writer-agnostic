import { episodeRange, type GenreKit, type Kb } from '@aiw/kb';
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

/**
 * The knowledge base block for a genre: rules, season frame, checklist, glossary, methods.
 * Stable across calls, so it goes into the cacheable prefix.
 */
export function knowledgeBlock(kb: Kb, kit: GenreKit): string {
  const f = kit.frame;
  const anchors = Object.entries(f.anchors).map(([id, spec]) => {
    const { min, max } = episodeRange(spec);
    return `${id}: ${min === max ? min : `${min}–${max}`}`;
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
  return [
    `# База знаний: ${kit.genre.title}${kit.genre.platform ? ` (${kit.genre.platform})` : ''}`,
    '## Правила жанра',
    list(kit.rules.rules.map((r) => `${r.id} [${r.severity}] ${r.text}`)),
    '## Каркас сезона',
    `Серий: ${f.episodes}, бесплатных: ${f.free}, длительность ${f.duration_s.min}–${f.duration_s.max} с (цель ${f.duration_s.target} с). Допуск опорных точек: ±${f.tolerance} с объяснением.`,
    `Опорные точки (id: серия): ${anchors.join('; ')}.`,
    `Блоки: ${f.blocks.map(([a, b]) => `${a}–${b}`).join(', ')}.`,
    `Ритм: ответ на удар за ${f.rhythm.response_within} серии; страдание не дольше ${f.rhythm.max_suffering_run} серий подряд; один тип крючка не больше ${f.rhythm.max_same_hook_run} серий подряд; ${f.rhythm.emotions_per_episode.join('–')} эмоциональные точки в серии.`,
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
    '## Словарь',
    list(kb.glossary.terms.map((t) => `${t.term} — ${t.definition}`)),
    '## Методики',
    list(Object.values(kb.methods).map((m) => `${m.name} (${m.author}): ${m.summary}`)),
    '## Кейсы',
    list(kb.cases.filter((c) => !c.stub).map((c) => `${c.id}: ${c.title} — ${c.summary}`)),
  ].join('\n');
}
