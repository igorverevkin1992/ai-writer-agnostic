import { randomUUID } from 'node:crypto';
import { eq } from 'drizzle-orm';
import { genreKit, type GenreKit, type GenreOverrides, type Kb } from '@aiw/kb';
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
  /** The project's frame inputs (e.g. paywall episode) and words for the hero, over the genre's. */
  frameInputs?: Record<string, number>;
  terms?: GenreOverrides['terms'];
}

export function createProject(db: Db, kb: Kb, input: NewProject): string {
  if (!kb.genres[input.genreId]) {
    throw new ProjectError(`Жанр «${input.genreId}» не найден. Есть: ${Object.keys(kb.genres).join(', ')}`);
  }
  if (!input.title.trim() || !input.idea.trim()) throw new ProjectError('Нужны название проекта и идея');
  const frameInputs = input.frameInputs && Object.keys(input.frameInputs).length ? input.frameInputs : null;
  const terms = input.terms && Object.keys(input.terms).length ? input.terms : null;
  // A bad input (unknown name, anchor outside the season) is refused before anything is saved.
  genreKit(kb, input.genreId, { inputs: frameInputs ?? {}, terms: terms ?? undefined });
  const id = randomUUID();
  db.insert(projects)
    .values({ id, title: input.title.trim(), genreId: input.genreId, budgetLimitUsd: input.budgetLimitUsd ?? null, frameInputs, terms })
    .run();
  new ProjectMemory(db, id).saveArtifact('idea', { text: input.idea.trim() });
  return id;
}

/** The project's overrides of its genre pack, as stored. */
export function projectOverrides(project: { frameInputs?: Record<string, number> | null; terms?: GenreOverrides['terms'] | null }): GenreOverrides {
  return { inputs: project.frameInputs ?? {}, terms: project.terms ?? undefined };
}

/** The genre pack of a project with the project's own frame inputs and hero terms applied. */
export function projectKit(db: Db, kb: Kb, projectId: string): GenreKit {
  const project = getProject(db, projectId);
  return genreKit(kb, project.genreId ?? '', projectOverrides(project));
}

export function getProject(db: Db, id: string) {
  const row = db.select().from(projects).where(eq(projects.id, id)).get();
  if (!row) throw new ProjectError(`Проект ${id} не найден`);
  return row;
}
