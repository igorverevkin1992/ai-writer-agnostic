import { randomUUID } from 'node:crypto';
import { eq } from 'drizzle-orm';
import type { Kb } from '@aiw/kb';
import type { Db } from '../db/client.ts';
import { projects } from '../db/schema.ts';
import { ProjectMemory } from '../memory/store.ts';

export class ProjectError extends Error {
  override name = 'ProjectError';
}

export interface NewProject {
  title: string;
  genreId: string;
  /** The producer's idea: input of the concept step. */
  idea: string;
  budgetLimitUsd?: number;
}

export function createProject(db: Db, kb: Kb, input: NewProject): string {
  if (!kb.genres[input.genreId]) {
    throw new ProjectError(`Жанр «${input.genreId}» не найден. Есть: ${Object.keys(kb.genres).join(', ')}`);
  }
  if (!input.title.trim() || !input.idea.trim()) throw new ProjectError('Нужны название проекта и идея');
  const id = randomUUID();
  db.insert(projects).values({ id, title: input.title.trim(), genreId: input.genreId, budgetLimitUsd: input.budgetLimitUsd ?? null }).run();
  new ProjectMemory(db, id).saveArtifact('idea', { text: input.idea.trim() });
  return id;
}

export function getProject(db: Db, id: string) {
  const row = db.select().from(projects).where(eq(projects.id, id)).get();
  if (!row) throw new ProjectError(`Проект ${id} не найден`);
  return row;
}
