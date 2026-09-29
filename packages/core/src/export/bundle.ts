import { genreKit, type GenreKit, type Kb } from '@aiw/kb';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { getProject } from '../pipeline/project.ts';
import { Pipeline } from '../pipeline/machine.ts';
import type { Bible } from '../schemas/bible.ts';
import type { Concept } from '../schemas/concept.ts';
import type { EpisodeCard } from '../schemas/episodeCard.ts';
import type { Logline } from '../schemas/logline.ts';
import type { SeasonPlan } from '../schemas/season.ts';
import type { Script } from '../schemas/script.ts';

/** Everything approved (or produced) in a project, for export. */
export interface ProjectBundle {
  project: { id: string; title: string; genreId: string | null };
  kit: GenreKit;
  idea?: string;
  concept?: Concept;
  logline?: Logline;
  bible?: Bible;
  plan?: SeasonPlan;
  cards: EpisodeCard[];
  scripts: Script[];
  findings: ReturnType<ProjectMemory['findingsOf']>;
  steps: ReturnType<Pipeline['states']>;
}

export function loadBundle(db: Db, kb: Kb, projectId: string): ProjectBundle {
  const project = getProject(db, projectId);
  const memory = new ProjectMemory(db, projectId);
  return {
    project: { id: project.id, title: project.title, genreId: project.genreId },
    kit: genreKit(kb, project.genreId ?? ''),
    idea: (memory.latestArtifact('idea') as { text: string } | undefined)?.text,
    concept: memory.latestArtifact('concept_choice') as Concept | undefined,
    logline: memory.latestArtifact('logline') as Logline | undefined,
    bible: memory.currentBible(),
    plan: memory.currentPlan(),
    cards: memory.cards(),
    scripts: memory.scripts(),
    findings: memory.findingsOf(),
    steps: new Pipeline(db, projectId).states(),
  };
}

/** Human name of an anchor from the genre frame, or its id. */
export function anchorLabel(kit: GenreKit, id: string): string {
  return kit.frame.anchor_labels[id] ?? id;
}
