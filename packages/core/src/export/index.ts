import type { Kb } from '@aiw/kb';
import type { Db } from '../db/client.ts';
import { loadBundle } from './bundle.ts';
import { buildDocx } from './docx.ts';
import { renderMarkdown } from './markdown.ts';
import { videoPrompts, videoPromptsMarkdown } from './video.ts';
import { buildXlsx } from './xlsx.ts';

export * from './bundle.ts';
export * from './video.ts';
export { renderMarkdown } from './markdown.ts';
export { buildDocx } from './docx.ts';
export { buildXlsx } from './xlsx.ts';

export const EXPORT_FORMATS = ['docx', 'xlsx', 'json', 'md', 'video'] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export interface ExportFile {
  filename: string;
  contentType: string;
  body: Buffer;
}

/** Transliterated, file-system-safe name. */
function slug(title: string): string {
  const map: Record<string, string> = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ы: 'y', э: 'e', ю: 'yu', я: 'ya' };
  const s = title.toLowerCase().replace(/[а-яё]/gu, (c) => map[c] ?? '').replace(/[^a-z0-9]+/gu, '-').replace(/^-|-$/gu, '');
  return s || 'project';
}

export async function exportProject(db: Db, kb: Kb, projectId: string, format: ExportFormat): Promise<ExportFile> {
  const b = loadBundle(db, kb, projectId);
  const name = slug(b.project.title);
  switch (format) {
    case 'docx':
      return { filename: `${name}.docx`, contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', body: await buildDocx(b) };
    case 'xlsx':
      return { filename: `${name}.xlsx`, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: await buildXlsx(b) };
    case 'md':
      return { filename: `${name}.md`, contentType: 'text/markdown; charset=utf-8', body: Buffer.from(renderMarkdown(b)) };
    case 'video':
      return { filename: `${name}-video.md`, contentType: 'text/markdown; charset=utf-8', body: Buffer.from(videoPromptsMarkdown(videoPrompts(b), b.project.title)) };
    case 'json': {
      const { kit, ...rest } = b;
      const data = { ...rest, genre: kit.genre, videoPrompts: videoPrompts(b) };
      return { filename: `${name}.json`, contentType: 'application/json; charset=utf-8', body: Buffer.from(JSON.stringify(data, null, 2)) };
    }
  }
}
