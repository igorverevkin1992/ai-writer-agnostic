import type { GenreKit, Kb } from '@aiw/kb';
import type { Authors, ProviderName, RoleName } from '../../providers/config.ts';
import type { LlmClient } from '../../providers/llm.ts';
import type { Bible } from '../../schemas/bible.ts';
import type { Finding } from '../../schemas/finding.ts';
import type { SeasonPlan } from '../../schemas/season.ts';
import { knowledgeBlock, renderPrompt, schemaText } from '../../prompts/render.ts';
import { ReviewSummaryDraft } from './schemas.ts';
import { bibleText, planBlocks } from './texts.ts';

/** Who knows what and from which episode (0 = before the season). */
export interface KnowledgeRow {
  who: string;
  fact: string;
  since: number;
  how?: string;
}

/** A setup and its payoff. */
export interface GunRow {
  object: string;
  planted: number;
  fired: number | null;
  metroVisible: boolean;
}

/** Ages at a timeline event: what the story says and what the birth year gives. */
export interface AgeRow {
  year: number;
  event: string;
  ages: { who: string; stated?: number; byBirthYear?: number }[];
}

/** The tables of a review that code builds from the bible alone. */
export interface ReviewTables {
  knowledge: KnowledgeRow[];
  guns: GunRow[];
  ages: AgeRow[];
}

/** Saved as artifact `review:<step>` after every audit. */
export interface ReviewSummary {
  tables: ReviewTables;
  /** Absent when the summary call failed: the findings stand anyway. */
  summary?: ReviewSummaryDraft;
  providers: ProviderName[];
}

export function reviewTables(bible: Bible): ReviewTables {
  const facts = new Map(bible.facts.map((f) => [f.id, f.text]));
  const knowledge: KnowledgeRow[] = [
    ...bible.characters.flatMap((c) => c.knows_at_start.map((fact) => ({ who: c.name, fact, since: 0 }))),
    ...bible.villains.flatMap((v) => v.knows.map((k) => ({ who: v.name, fact: k.fact, since: k.since_ep }))),
    ...bible.knowledge.map((k) => ({ who: k.who, fact: k.fact, since: k.since_ep, ...(k.how ? { how: k.how } : {}) })),
  ]
    .map((r) => ({ ...r, fact: facts.get(r.fact) ?? r.fact }))
    .sort((a, b) => a.since - b.since || a.who.localeCompare(b.who, 'ru'));

  const guns = [...bible.guns]
    .sort((a, b) => a.planted_ep - b.planted_ep)
    .map((g) => ({ object: g.object, planted: g.planted_ep, fired: g.fired_ep ?? null, metroVisible: g.metro_visible }));

  const born = new Map(bible.characters.map((c) => [c.name, c.birth_year]));
  const ages = [...bible.timeline.events]
    .sort((a, b) => a.year - b.year)
    .map((e) => {
      const who = [...new Set([...e.participants, ...Object.keys(e.ages)])];
      return {
        year: e.year,
        event: e.text,
        ages: who.map((n) => {
          const b = born.get(n);
          return {
            who: n,
            ...(e.ages[n] !== undefined ? { stated: e.ages[n] } : {}),
            ...(b !== undefined && b <= e.year ? { byBirthYear: e.year - b } : {}),
          };
        }),
      };
    })
    .filter((r) => r.ages.length > 0);
  return { knowledge, guns, ages };
}

const LEVEL_ORDER = ['critical', 'high', 'medium', 'low'] as const;
const MAX_FINDINGS_IN_SUMMARY = 60;

function findingLine(f: Finding): string {
  const where = f.episodes ?? (f.episode ? String(f.episode) : 'библия');
  const cat = f.category?.length ? ` [${f.category.join(', ')}]` : '';
  return `- ${f.level ?? f.severity}${cat}, серии ${where}: ${f.viewerQuestion}`;
}

export interface SummaryDeps {
  llm: LlmClient;
  kb: Kb;
  kit: GenreKit;
  projectId?: string;
  step?: string;
  criticRole?: RoleName;
}

/**
 * The closing part of a review: code tables plus one call to the critic for the verdict,
 * the three most dangerous places and questions for a lawyer. A failed call leaves the tables.
 */
export async function runReviewSummary(
  deps: SummaryDeps,
  target: { bible: Bible; plan?: SeasonPlan; authorProvider: Authors },
  findings: Finding[],
): Promise<ReviewSummary> {
  const tables = reviewTables(target.bible);
  const rank = (f: Finding) => (f.level ? LEVEL_ORDER.indexOf(f.level) : f.severity === 'blocker' ? 0 : f.severity === 'major' ? 1 : 3);
  const top = [...findings].sort((a, b) => rank(a) - rank(b)).slice(0, MAX_FINDINGS_IN_SUMMARY);
  const text = target.plan ? planBlocks(target.plan, target.plan.episodes.length)[0]!.text : bibleText(target.bible);
  const system = renderPrompt(deps.kb, `${deps.criticRole ?? 'critic_of_architect'}/review_summary`, {
    target: target.plan ? 'план сезона' : 'библия',
    text,
    findings: top.map(findingLine).join('\n') || '- (дыр не найдено)',
    schema: schemaText(ReviewSummaryDraft),
  });
  try {
    const res = await deps.llm.completeJson(ReviewSummaryDraft, {
      role: deps.criticRole ?? 'critic_of_architect',
      projectId: deps.projectId,
      step: deps.step,
      authorProvider: target.authorProvider,
      request: {
        task: 'review_summary',
        cacheablePrefix: [knowledgeBlock(deps.kb, deps.kit)],
        system,
        messages: [{ role: 'user', content: 'Напиши заключение разбора. Ответ — только JSON.' }],
      },
    });
    return { tables, summary: res.data, providers: [res.provider] };
  } catch {
    return { tables, providers: [] };
  }
}
