import { genreKit, type GenreKit, type Kb } from '@aiw/kb';
import type { ReviewSummary } from '../checks/llm/reviewSummary.ts';
import type { Db } from '../db/client.ts';
import { ProjectMemory } from '../memory/store.ts';
import { getProject, projectOverrides } from '../pipeline/project.ts';
import { Pipeline } from '../pipeline/machine.ts';
import { CARD_BLOCK } from '../pipeline/runners.ts';
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
  /** Closing parts of the latest audits (verdict, tables), by step. */
  reviews: { bible?: ReviewSummary; season_plan?: ReviewSummary };
  steps: ReturnType<Pipeline['states']>;
  /** Cards and scripts a changed fact made outdated. */
  stale: { cards: number[]; scripts: number[] };
  /** First episodes of the card blocks the producer approved. */
  approvedCardBlocks: number[];
}

/** How an episode document is marked in exports: outdated, not yet approved, or ready (empty). */
export type EpisodeMark = '' | 'устарело' | 'не утверждено';

export function cardMark(b: ProjectBundle, ep: number): EpisodeMark {
  if (b.stale.cards.includes(ep)) return 'устарело';
  const block = b.approvedCardBlocks.find((start) => ep >= start && ep < start + CARD_BLOCK);
  return b.steps.find((s) => s.step === 'episode_cards')?.status === 'approved' || block !== undefined ? '' : 'не утверждено';
}

export function scriptMark(b: ProjectBundle, ep: number): EpisodeMark {
  if (b.stale.scripts.includes(ep)) return 'устарело';
  return b.steps.find((s) => s.step === 'scripts')?.status === 'approved' ? '' : 'не утверждено';
}

/** A heading suffix: « — устарело» or nothing. */
export function markSuffix(mark: EpisodeMark): string {
  return mark ? ` — ${mark}` : '';
}

/** The bundle without outdated and unapproved cards and scripts. */
export function withoutUnready(b: ProjectBundle): ProjectBundle {
  return { ...b, cards: b.cards.filter((c) => !cardMark(b, c.ep)), scripts: b.scripts.filter((s) => !scriptMark(b, s.ep)) };
}

export function loadBundle(db: Db, kb: Kb, projectId: string): ProjectBundle {
  const project = getProject(db, projectId);
  const memory = new ProjectMemory(db, projectId);
  return {
    project: { id: project.id, title: project.title, genreId: project.genreId },
    kit: genreKit(kb, project.genreId ?? '', projectOverrides(project)),
    idea: (memory.latestArtifact('idea') as { text: string } | undefined)?.text,
    concept: memory.latestArtifact('concept_choice') as Concept | undefined,
    logline: memory.latestArtifact('logline') as Logline | undefined,
    bible: memory.currentBible(),
    plan: memory.currentPlan(),
    cards: memory.cards(),
    scripts: memory.scripts(),
    findings: memory.findingsOf(),
    reviews: {
      bible: memory.latestArtifact('review:bible') as ReviewSummary | undefined,
      season_plan: memory.latestArtifact('review:season_plan') as ReviewSummary | undefined,
    },
    steps: new Pipeline(db, projectId).states(),
    stale: memory.staleEpisodes(),
    approvedCardBlocks: (memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? [],
  };
}

/** Human name of an anchor from the genre frame, or its id. */
export function anchorLabel(kit: GenreKit, id: string): string {
  return kit.frame.anchor_labels[id] ?? id;
}

/** Characters XML (Word, Excel) cannot hold: control characters except tab and line breaks. */
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/gu;

/** Text safe for Word and Excel files. */
export function cleanText(text: string): string {
  return text.replace(CONTROL, '');
}
