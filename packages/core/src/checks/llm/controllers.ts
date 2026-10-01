import type { GenreKit, Kb } from '@aiw/kb';
import { z } from 'zod';
import { renderPrompt, schemaText } from '../../prompts/render.ts';
import type { Authors, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { EpisodeCard } from '../../schemas/episodeCard.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { EpisodeOutline } from '../../schemas/season.ts';
import type { Script } from '../../schemas/script.ts';
import { renderScript } from '../../text/script.ts';
import { makeFinding } from '../code/finding.ts';
import { FindingDrafts } from './schemas.ts';
import { episodeLine } from './texts.ts';

/** Model controllers for scripts. Consistency is checked by code against the fact base. */
export const SCRIPT_CONTROLLERS = ['logic', 'genre', 'structure', 'metro', 'production', 'legal'] as const;
export type ScriptController = (typeof SCRIPT_CONTROLLERS)[number];

const NAMES: Record<ScriptController, string> = {
  logic: 'Логический аудитор',
  genre: 'Жанровый',
  structure: 'Структурный',
  metro: 'Проверка метро',
  production: 'Производственный',
  legal: 'Юридический',
};

const list = (xs: string[]) => xs.map((x) => `- ${x}`).join('\n');

/** What each controller looks for — all of it from the genre pack. */
export function controllerCriteria(kb: Kb, kit: GenreKit, c: ScriptController, outline?: EpisodeOutline): string {
  const p = kit.production;
  switch (c) {
    case 'logic':
      return list(kb.holes.holes.map((h) => `${h.id}. ${h.name}: ${h.questions.join(' ')}`));
    case 'genre':
      return list(kit.rules.rules.filter((r) => r.check.llm).map((r) => `${r.id}: ${r.text}. Вопрос: ${r.check.llm}`));
    case 'structure': {
      const r = kit.frame.rhythm;
      return list([
        outline ? `По плану сезона эта серия: ${episodeLine(outline, kit.genre.terms)}` : 'Плана серии нет.',
        `Одно событие, один новый вопрос, ${r.emotions_per_episode.join('–')} эмоциональные точки.`,
        'Первые 5 секунд продолжают крючок прошлой серии, последние 5–10 — клиффхэнгер.',
        'Экспозиция — через конфликт, а не через пересказ.',
      ]);
    }
    case 'metro':
      return list([
        `Ключевое раскрытие понятно без звука за ${p.metro.reveal_readable_s} с на экране телефона: ${p.metro.frame.join(', ')}.`,
        `Плашка — до ${p.script_metrics.max_overlay_words} слов.`,
        'Важная деталь видна крупно, а не на общем плане.',
      ]);
    case 'production':
      return list([
        `Одна основная локация, до ${p.limits.max_locations} зон внутри неё.`,
        p.limits.practical_effects_only ? 'Только практические эффекты: грим, реквизит. Никакой графики.' : 'Эффекты — по бюджету.',
        `До ${p.limits.max_speakers_per_scene} говорящих в сцене.`,
        p.limits.crowds_allowed ? 'Массовка допустима.' : 'Без толп и массовки.',
      ]);
    case 'legal':
      return list([...kit.legal.principles.map((x) => x.text), `Возрастной рейтинг ${kit.legal.age_rating}.`]);
  }
}

export interface ControllerDeps {
  llm: LlmClient;
  kb: Kb;
  kit: GenreKit;
  projectId?: string;
  step?: string;
  criticRole?: RoleName;
}

const norm = (s: string) => s.replace(/[«»"“”„]/gu, '"').replace(/[ёЁ]/gu, 'е').replace(/\s+/gu, ' ').trim().toLowerCase();

/** Runs model controllers over one script. The critic is from another family than the writer. */
export async function runScriptControllers(
  deps: ControllerDeps,
  target: { script: Script; card: EpisodeCard; outline?: EpisodeOutline; authorProvider: Authors },
  controllers: readonly ScriptController[] = SCRIPT_CONTROLLERS,
): Promise<{ findings: Finding[]; dropped: number }> {
  const role = deps.criticRole ?? 'critic_of_writer';
  const text = renderScript(target.script);
  const findings: Finding[] = [];
  let dropped = 0;
  for (const c of controllers) {
    const { data } = await deps.llm.completeJson(FindingDrafts, {
      role,
      projectId: deps.projectId,
      step: deps.step,
      authorProvider: target.authorProvider,
      request: {
        task: `controller:${c}:${target.script.ep}`,
        system: renderPrompt(deps.kb, `${role}/controller`, {
          controller: NAMES[c],
          criteria: controllerCriteria(deps.kb, deps.kit, c, target.outline),
          card: JSON.stringify(target.card),
          ep: target.script.ep,
          text,
          holes: deps.kb.holes.holes.map((h) => `${h.id} — ${h.name}`).join('; '),
          schema: schemaText(FindingDrafts),
        }),
        messages: [{ role: 'user', content: 'Проверь сценарий. Только JSON.' }],
      },
    });
    for (const d of data) {
      if (!norm(text).includes(norm(d.quote))) {
        dropped++;
        continue;
      }
      const f = makeFinding({
        check: `controller.${c}`,
        controller: c === 'logic' ? 'logic' : c,
        severity: d.severity,
        holeType: d.holeType,
        episode: target.script.ep,
        quote: d.quote,
        question: d.viewerQuestion.replace(/^Зритель спросит:\s*/u, ''),
        fixes: d.fixes as [string] | [string, string],
      });
      if (!findings.some((x) => x.id === f.id)) findings.push(f);
    }
  }
  return { findings, dropped };
}

export const Shootable = z.object({ verdict: z.enum(['yes', 'light_edit', 'no']), reason: z.string().min(1) });
export type Shootable = z.infer<typeof Shootable>;

/** Can this episode be shot as is, after a light edit, or not? Judged by another family than the writer. */
export async function rateShootable(deps: ControllerDeps, script: Script, authorProvider: Authors): Promise<Shootable> {
  const role = deps.criticRole ?? 'critic_of_writer';
  const { data } = await deps.llm.completeJson(Shootable, {
    role,
    projectId: deps.projectId,
    step: deps.step,
    authorProvider,
    request: {
      task: `shootable:${script.ep}`,
      system: renderPrompt(deps.kb, `${role}/shootable`, { text: renderScript(script), schema: schemaText(Shootable) }),
      messages: [{ role: 'user', content: 'Оцени. Только JSON.' }],
    },
  });
  return data;
}
