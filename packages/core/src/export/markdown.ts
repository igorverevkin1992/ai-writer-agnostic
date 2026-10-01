import { bibleText, episodeLine } from '../checks/llm/texts.ts';
import { renderScript } from '../text/script.ts';
import { anchorLabel, type ProjectBundle } from './bundle.ts';

export function renderMarkdown(b: ProjectBundle): string {
  const out: string[] = [`# ${b.project.title}`, '', `Жанр: ${b.kit.genre.title}.`];
  if (b.logline) out.push('', '## Логлайн', '', b.logline.text_35w, '', `Реклама: ${b.logline.ad_15w}`);
  if (b.concept) out.push('', '## Концепция', '', `**${b.concept.title}.** ${b.concept.premise}`, '', `Формула: ${b.concept.genre_formula}`);
  if (b.bible) out.push('', '## Библия', '', '```', bibleText(b.bible, b.kit.genre.terms), '```');
  if (b.plan) {
    out.push('', '## План сезона', '', '| Серия | Название | Событие | Опорные точки | Настроение | Крючок |', '|---|---|---|---|---|---|');
    for (const e of b.plan.episodes) {
      out.push(`| ${e.ep} | ${e.title} | ${e.event.replace(/\|/gu, '/')} | ${e.anchors.map((a) => anchorLabel(b.kit, a)).join(', ')} | ${e.mood} | ${e.hook_type} |`);
    }
  }
  if (b.cards.length) {
    out.push('', '## Карточки серий', '');
    for (const c of b.cards) out.push(`**Серия ${c.ep}.** ${c.event} Клиффхэнгер: ${c.cliffhanger} Локация: ${c.location}. В кадре: ${c.cast.join(', ')}.`, '');
  }
  if (b.scripts.length) {
    out.push('', '## Сценарии', '');
    for (const s of b.scripts) out.push('```', renderScript(s), '```', '');
  }
  if (b.plan && !b.scripts.length && !b.cards.length) out.push('', ...b.plan.episodes.map((e) => episodeLine(e, b.kit.genre.terms)));
  return `${out.join('\n')}\n`;
}
