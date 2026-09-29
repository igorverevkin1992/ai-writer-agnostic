import { and, desc, eq, inArray, max } from 'drizzle-orm';
import { checkKnowledge } from '../checks/code/knowledge.ts';
import { checkTimelineDetailed } from '../checks/code/timeline.ts';
import type { Db } from '../db/client.ts';
import {
  artifacts,
  characters,
  checkQueue,
  episodeCards,
  events,
  facts,
  findings as findingsTable,
  guns,
  knowledge,
  revisions,
  sceneFactLinks,
  scripts,
  steps,
  worldRules,
} from '../db/schema.ts';
import { Bible, type Fact, type KnowledgeEntry, type TimelineEvent } from '../schemas/bible.ts';
import type { EpisodeCard } from '../schemas/episodeCard.ts';
import type { Finding } from '../schemas/finding.ts';
import { SeasonPlan } from '../schemas/season.ts';
import type { Script } from '../schemas/script.ts';

export type Author = 'producer' | 'agent';

export interface EditMeta {
  author: Author;
  note?: string;
}

export interface PropagationResult {
  revisionId: number;
  /** Episodes that rely on the changed fact or event. */
  affectedEpisodes: number[];
  /** Cards and scripts marked stale. */
  staleCards: number[];
  staleScripts: number[];
  /** Code findings raised right away, attached to the affected episodes. */
  findings: Finding[];
  /** Model checks put in the queue. */
  queued: number;
}

/**
 * Project memory: the fact base (facts, events, knowledge) plus episode documents,
 * with edit propagation. A changed fact marks every card and script that relies on it
 * as stale, runs code checks at once and queues model checks.
 */
export class ProjectMemory {
  constructor(
    private readonly db: Db,
    readonly projectId: string,
  ) {}

  // ---- artifacts and steps ----

  saveArtifact(step: string, data: unknown): number {
    const p = this.projectId;
    const last = this.db
      .select({ v: max(artifacts.version) })
      .from(artifacts)
      .where(and(eq(artifacts.projectId, p), eq(artifacts.step, step)))
      .get();
    const version = (last?.v ?? 0) + 1;
    this.db.insert(artifacts).values({ projectId: p, step, version, data }).run();
    this.db
      .insert(steps)
      .values({ projectId: p, step, version })
      .onConflictDoUpdate({ target: [steps.projectId, steps.step], set: { version, updatedAt: new Date() } })
      .run();
    return version;
  }

  latestArtifact(step: string): unknown {
    return this.db
      .select({ data: artifacts.data })
      .from(artifacts)
      .where(and(eq(artifacts.projectId, this.projectId), eq(artifacts.step, step)))
      .orderBy(desc(artifacts.version))
      .limit(1)
      .get()?.data;
  }

  // ---- bible and fact base ----

  /** Stores the bible and fills the fact base tables from it. */
  importBible(bible: Bible): void {
    const p = this.projectId;
    this.saveArtifact('bible', bible);
    this.db.transaction((tx) => {
      for (const t of [characters, facts, events, knowledge, worldRules, guns]) tx.delete(t).where(eq(t.projectId, p)).run();
      for (const c of bible.characters) tx.insert(characters).values({ projectId: p, name: c.name, data: c }).run();
      for (const f of bible.facts) tx.insert(facts).values({ projectId: p, id: f.id, text: f.text, sinceEp: f.since_ep ?? null }).run();
      for (const e of bible.timeline.events) tx.insert(events).values({ projectId: p, ...e }).run();
      for (const k of bible.knowledge) {
        tx.insert(knowledge).values({ projectId: p, who: k.who, factId: k.fact, sinceEp: k.since_ep, how: k.how ?? null }).run();
      }
      for (const r of bible.world_rules) tx.insert(worldRules).values({ projectId: p, data: r }).run();
      for (const g of bible.guns) tx.insert(guns).values({ projectId: p, id: g.id, data: g }).run();
    });
  }

  /** The bible as it is now: the stored artifact with facts, events and knowledge from the fact base. */
  currentBible(): Bible | undefined {
    const stored = this.latestArtifact('bible');
    if (!stored) return undefined;
    const p = this.projectId;
    const base = Bible.parse(stored);
    const factRows = this.db.select().from(facts).where(eq(facts.projectId, p)).all();
    const eventRows = this.db.select().from(events).where(eq(events.projectId, p)).all();
    const knowRows = this.db.select().from(knowledge).where(eq(knowledge.projectId, p)).all();
    return Bible.parse({
      ...base,
      facts: factRows.map((f): Fact => ({ id: f.id, text: f.text, ...(f.sinceEp !== null ? { since_ep: f.sinceEp } : {}) })),
      timeline: {
        ...base.timeline,
        events: eventRows.map(
          (e): TimelineEvent => ({ id: e.id, year: e.year, text: e.text, kind: e.kind as TimelineEvent['kind'], participants: e.participants, ages: e.ages, after: e.after }),
        ),
      },
      knowledge: knowRows.map((k): KnowledgeEntry => ({ who: k.who, fact: k.factId, since_ep: k.sinceEp, ...(k.how ? { how: k.how } : {}) })),
    });
  }

  // ---- episode documents and links ----

  /** Stores the season plan and links each outline to the facts it acts on. */
  importPlan(plan: SeasonPlan): void {
    this.saveArtifact('season_plan', plan);
    this.replaceLinks('outline', plan.episodes.map((e) => ({ ep: e.ep, factIds: e.acts_on.map((a) => a.fact) })));
  }

  currentPlan(): SeasonPlan | undefined {
    const stored = this.latestArtifact('season_plan');
    return stored ? SeasonPlan.parse(stored) : undefined;
  }

  saveCard(card: EpisodeCard, extraFactIds: string[] = []): void {
    this.saveEpisodeDoc(episodeCards, card.ep, card);
    this.replaceLinks('card', [{ ep: card.ep, factIds: [...card.acts_on.map((a) => a.fact), ...extraFactIds] }], [card.ep]);
  }

  /** A script relies on the same facts as its card, plus any given explicitly. */
  saveScript(script: Script, extraFactIds: string[] = []): void {
    this.saveEpisodeDoc(scripts, script.ep, script);
    const cardFacts = this.linksOf('card', script.ep);
    this.replaceLinks('script', [{ ep: script.ep, factIds: [...cardFacts, ...extraFactIds] }], [script.ep]);
  }

  /** Links an episode document to facts or timeline events explicitly (e.g. a flashback to an event). */
  link(target: 'outline' | 'card' | 'script', ep: number, factIds: string[]): void {
    for (const factId of factIds) this.db.insert(sceneFactLinks).values({ projectId: this.projectId, ep, target, factId }).run();
  }

  staleEpisodes(): { cards: number[]; scripts: number[] } {
    const p = this.projectId;
    const pick = (t: typeof episodeCards) =>
      this.db.select({ ep: t.ep }).from(t).where(and(eq(t.projectId, p), eq(t.stale, true))).all().map((r) => r.ep);
    return { cards: pick(episodeCards), scripts: pick(scripts) };
  }

  // ---- edits and propagation ----

  /** Changes a fact, logs the revision, propagates the change. */
  updateFact(id: string, patch: { text?: string; since_ep?: number | null }, meta: EditMeta): PropagationResult {
    const p = this.projectId;
    const row = this.db.select().from(facts).where(and(eq(facts.projectId, p), eq(facts.id, id))).get();
    if (!row) throw new Error(`Факт ${id} не найден`);
    const before = { text: row.text, since_ep: row.sinceEp };
    const after = { text: patch.text ?? row.text, since_ep: patch.since_ep === undefined ? row.sinceEp : patch.since_ep };
    this.db
      .update(facts)
      .set({ text: after.text, sinceEp: after.since_ep, source: meta.author, updatedAt: new Date() })
      .where(and(eq(facts.projectId, p), eq(facts.id, id)))
      .run();
    const revisionId = this.logRevision('fact', id, before, after, meta);
    return this.propagate([id], revisionId);
  }

  /** Changes a timeline event (e.g. a year), logs the revision, propagates the change. */
  updateEvent(id: string, patch: Partial<Omit<TimelineEvent, 'id'>>, meta: EditMeta): PropagationResult {
    const p = this.projectId;
    const row = this.db.select().from(events).where(and(eq(events.projectId, p), eq(events.id, id))).get();
    if (!row) throw new Error(`Событие ${id} не найдено`);
    const { projectId: _p, ...before } = row;
    void _p;
    const after = { ...before, ...patch };
    this.db.update(events).set(after).where(and(eq(events.projectId, p), eq(events.id, id))).run();
    const revisionId = this.logRevision('event', id, before, after, meta);
    return this.propagate([id], revisionId);
  }

  /** Undoes a revision: restores the old value and propagates again. */
  rollback(revisionId: number, meta: EditMeta): PropagationResult {
    const p = this.projectId;
    const rev = this.db.select().from(revisions).where(and(eq(revisions.projectId, p), eq(revisions.id, revisionId))).get();
    if (!rev) throw new Error(`Правка ${revisionId} не найдена`);
    const note = meta.note ?? `Откат правки ${revisionId}`;
    if (rev.entity === 'fact') {
      const b = rev.before as { text: string; since_ep: number | null };
      return this.updateFact(rev.entityId, b, { ...meta, note });
    }
    if (rev.entity === 'event') {
      const { id: _id, ...b } = rev.before as TimelineEvent;
      void _id;
      return this.updateEvent(rev.entityId, b, { ...meta, note });
    }
    throw new Error(`Откат для «${rev.entity}» не поддерживается`);
  }

  /**
   * Marks dependent cards and scripts stale, runs chronology and knowledge checks now,
   * attaches their findings to the affected episodes and queues model checks.
   */
  private propagate(changedIds: string[], revisionId: number): PropagationResult {
    const p = this.projectId;
    const links = this.db
      .select()
      .from(sceneFactLinks)
      .where(and(eq(sceneFactLinks.projectId, p), inArray(sceneFactLinks.factId, changedIds)))
      .all();
    const affected = new Set(links.map((l) => l.ep));

    const staleCards = this.markStale(episodeCards, [...affected]);
    const staleScripts = this.markStale(scripts, [...affected]);
    for (const ep of staleCards) this.queue('card', ep, `Изменён факт: ${changedIds.join(', ')}`);
    for (const ep of staleScripts) this.queue('script', ep, `Изменён факт: ${changedIds.join(', ')}`);

    const bible = this.currentBible();
    const plan = this.currentPlan();
    const raised: Finding[] = [];
    if (bible) {
      // Chronology: an issue touching a changed event reaches every episode linked to its events.
      for (const issue of checkTimelineDetailed(bible)) {
        if (!issue.events.some((e) => changedIds.includes(e) || links.some((l) => l.factId === e))) continue;
        const eps = this.episodesLinkedTo(issue.events);
        for (const ep of eps.length ? eps : [undefined]) raised.push(this.attach(issue.finding, ep, revisionId));
      }
      // Knowledge: re-check the affected episodes.
      const items = [...affected].map((ep) => ({ ep, acts_on: plan?.episodes.find((e) => e.ep === ep)?.acts_on ?? [] }));
      for (const f of checkKnowledge(items, bible)) raised.push(this.attach(f, f.episode, revisionId));
    }
    this.saveFindings(raised, 'memory');
    return {
      revisionId,
      affectedEpisodes: [...affected].sort((a, b) => a - b),
      staleCards,
      staleScripts,
      findings: raised,
      queued: staleCards.length + staleScripts.length,
    };
  }

  private attach(f: Finding, ep: number | undefined, revisionId: number): Finding {
    const fixes: [string, string] = [ep ? `Поправить ${ep}-ю серию` : (f.fixes[0] ?? 'Исправить'), `Откатить правку №${revisionId}`];
    return { ...f, id: ep ? `${f.id}~${ep}` : f.id, episode: ep ?? f.episode, fixes };
  }

  private episodesLinkedTo(factIds: string[]): number[] {
    if (factIds.length === 0) return [];
    const rows = this.db
      .select({ ep: sceneFactLinks.ep })
      .from(sceneFactLinks)
      .where(and(eq(sceneFactLinks.projectId, this.projectId), inArray(sceneFactLinks.factId, factIds)))
      .all();
    return [...new Set(rows.map((r) => r.ep))].sort((a, b) => a - b);
  }

  // ---- findings ----

  saveFindings(list: Finding[], step: string): void {
    for (const f of list) {
      const row = {
        projectId: this.projectId,
        id: f.id,
        step,
        controller: f.controller,
        holeType: f.holeType ?? null,
        severity: f.severity,
        episode: f.episode ?? null,
        quote: f.quote,
        viewerQuestion: f.viewerQuestion,
        fixes: f.fixes,
        status: f.status,
        resolutionFactId: f.resolutionFactId ?? null,
        rule: f.rule ?? null,
        check: f.check ?? null,
      };
      this.db.insert(findingsTable).values(row).onConflictDoUpdate({ target: [findingsTable.projectId, findingsTable.id], set: row }).run();
    }
  }

  openFindings(): (typeof findingsTable.$inferSelect)[] {
    return this.db
      .select()
      .from(findingsTable)
      .where(and(eq(findingsTable.projectId, this.projectId), eq(findingsTable.status, 'open')))
      .all();
  }

  revisionsLog(author?: Author): (typeof revisions.$inferSelect)[] {
    const where = author
      ? and(eq(revisions.projectId, this.projectId), eq(revisions.author, author))
      : eq(revisions.projectId, this.projectId);
    return this.db.select().from(revisions).where(where).orderBy(revisions.id).all();
  }

  // ---- helpers ----

  private saveEpisodeDoc(table: typeof episodeCards, ep: number, data: unknown): void {
    const p = this.projectId;
    const prev = this.db.select({ v: table.version }).from(table).where(and(eq(table.projectId, p), eq(table.ep, ep))).get();
    const version = (prev?.v ?? 0) + 1;
    this.db
      .insert(table)
      .values({ projectId: p, ep, data, version, stale: false })
      .onConflictDoUpdate({ target: [table.projectId, table.ep], set: { data, version, stale: false, updatedAt: new Date() } })
      .run();
  }

  private replaceLinks(target: string, items: { ep: number; factIds: string[] }[], onlyEps?: number[]): void {
    const p = this.projectId;
    const scope = and(eq(sceneFactLinks.projectId, p), eq(sceneFactLinks.target, target));
    this.db
      .delete(sceneFactLinks)
      .where(onlyEps ? and(scope, inArray(sceneFactLinks.ep, onlyEps)) : scope)
      .run();
    for (const { ep, factIds } of items) {
      for (const factId of new Set(factIds)) this.db.insert(sceneFactLinks).values({ projectId: p, ep, target, factId }).run();
    }
  }

  private linksOf(target: string, ep: number): string[] {
    return this.db
      .select({ f: sceneFactLinks.factId })
      .from(sceneFactLinks)
      .where(and(eq(sceneFactLinks.projectId, this.projectId), eq(sceneFactLinks.target, target), eq(sceneFactLinks.ep, ep)))
      .all()
      .map((r) => r.f);
  }

  private markStale(table: typeof episodeCards, eps: number[]): number[] {
    if (eps.length === 0) return [];
    const p = this.projectId;
    const rows = this.db
      .update(table)
      .set({ stale: true })
      .where(and(eq(table.projectId, p), inArray(table.ep, eps)))
      .returning({ ep: table.ep })
      .all();
    return rows.map((r) => r.ep).sort((a, b) => a - b);
  }

  private queue(target: 'card' | 'script', ep: number, reason: string): void {
    this.db.insert(checkQueue).values({ projectId: this.projectId, target, ep, reason }).run();
  }

  private logRevision(entity: string, entityId: string, before: unknown, after: unknown, meta: EditMeta): number {
    return this.db
      .insert(revisions)
      .values({ projectId: this.projectId, entity, entityId, before, after, author: meta.author, note: meta.note ?? null })
      .returning({ id: revisions.id })
      .get().id;
  }
}
