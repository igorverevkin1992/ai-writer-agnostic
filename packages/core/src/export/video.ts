import type { Bible } from '../schemas/bible.ts';
import type { EpisodeCard } from '../schemas/episodeCard.ts';
import type { Script, ScriptBlock } from '../schemas/script.ts';
import type { ProjectBundle } from './bundle.ts';

export interface VideoPrompt {
  ep: number;
  scene: number;
  t0: number;
  t1: number;
  /** Characters in the scene; their look comes only from the bible. */
  characters: { name: string; look: string }[];
  prompt: string;
}

/** Name without its ending, to find it in any case form (Лиза / ЛИЗЫ, Герман / ГЕРМАНОМ). */
function stem(name: string): string {
  const first = name.split(/\s+/u)[0]!.toUpperCase();
  return first.length > 2 ? first.replace(/[АЯОЕЁЫИЬЙ]$/u, '') : first;
}

/** Russian case endings of names. */
const ENDING = '(?:ОЙ|ЕЙ|ОЮ|ЕЮ|ОМ|ЕМ|ЁМ|[АЯОЕЁЫИУЮЬЙ])?';

/** The name as a whole word in some case form: «ВЕРА», «ВЕРЫ», but not «ВЕРАНДА», «ВЕРХ» or «ПРОВЕРКА». */
function mentions(text: string, name: string): boolean {
  const s = stem(name).replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  return new RegExp(`(?<!\\p{L})${s}${ENDING}(?!\\p{L})`, 'u').test(text);
}

function charactersIn(bible: Bible, blocks: ScriptBlock[]): { name: string; look: string }[] {
  const text = blocks.map((b) => `${b.speaker ?? ''} ${b.text}`).join(' ').toUpperCase();
  return bible.characters.filter((c) => mentions(text, c.name)).map((c) => ({ name: c.name, look: c.look }));
}

/**
 * Prompts for AI video, one per scene. Appearance is taken only from the bible's characters,
 * so the same person looks the same in every episode.
 */
export function videoPrompts(bundle: Pick<ProjectBundle, 'bible' | 'cards' | 'scripts' | 'kit'>): VideoPrompt[] {
  const { bible } = bundle;
  if (!bible) return [];
  const practical = bundle.kit.production.limits.practical_effects_only;
  const out: VideoPrompt[] = [];
  for (const script of bundle.scripts) {
    const card: EpisodeCard | undefined = bundle.cards.find((c) => c.ep === script.ep);
    const scenes = splitScenes(script);
    scenes.forEach((blocks, i) => {
      const head = blocks[0]!;
      const people = charactersIn(bible, blocks);
      const action = blocks.filter((b) => b.kind !== 'line').map((b) => b.text).join(' ');
      const lines = blocks.filter((b) => b.kind === 'line').map((b) => `${b.speaker}: «${b.text}»`);
      const parts = [
        `Вертикальное видео 9:16, ${Math.round(head.t0)}–${Math.round(blocks.at(-1)!.t1)} с серии ${script.ep}.`,
        `Сцена: ${action}`,
        people.length ? `В кадре: ${people.map((p) => `${p.name} — ${p.look}`).join('; ')}.` : '',
        lines.length ? `Реплики: ${lines.join(' ')}` : '',
        card && i === scenes.length - 1 ? `Ключевой кадр: лицо — ${card.metro_frame.face}, действие — ${card.metro_frame.action}, предмет — ${card.metro_frame.object}; понятно без звука.` : '',
        practical ? 'Только практические эффекты: грим, реквизит, свет. Без компьютерной графики.' : '',
      ];
      out.push({ ep: script.ep, scene: i + 1, t0: head.t0, t1: blocks.at(-1)!.t1, characters: people, prompt: parts.filter(Boolean).join('\n') });
    });
  }
  return out;
}

function splitScenes(script: Script): ScriptBlock[][] {
  const out: ScriptBlock[][] = [];
  for (const b of script.blocks) {
    if (b.kind === 'scene' || out.length === 0) out.push([b]);
    else out.at(-1)!.push(b);
  }
  return out;
}

export function videoPromptsMarkdown(prompts: VideoPrompt[], title: string): string {
  const lines = [`# Промпты для ИИ-видео — ${title}`, ''];
  for (const p of prompts) lines.push(`## Серия ${p.ep}, сцена ${p.scene}`, '', p.prompt, '');
  return lines.join('\n');
}
