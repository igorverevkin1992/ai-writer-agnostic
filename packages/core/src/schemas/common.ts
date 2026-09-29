import { z } from 'zod';

z.config(z.locales.ru());

/** Episode number inside a season. The season length comes from the genre frame and is checked by code. */
export const EpisodeNumber = z.int().min(1).max(999);

export const NonEmpty = z.string().trim().min(1);

export function countWords(text: string): number {
  return text.trim().split(/\s+/u).filter(Boolean).length;
}

/** Text limited by word count (e.g. a 35-word logline). */
export function maxWords(limit: number) {
  return NonEmpty.refine((s) => countWords(s) <= limit, {
    message: `Не больше ${limit} слов`,
  });
}
