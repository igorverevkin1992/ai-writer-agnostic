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
import { EpisodeCard as EpisodeCardSchema, type EpisodeCard } from '../schemas/episodeCard.ts';
import type { Finding } from '../schemas/finding.ts';
import { SeasonPlan } from '../schemas/season.ts';
import { STEP_IDS } from '../steps.ts';
import { Script as ScriptSchema, type Script } from '../schemas/script.ts';

export type Author = 'producer' | 'agent';

/** Prefix of links added by hand with link(): replaceLinks never touches them. */
const MANUAL = 'manual:';

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
    // Only pipeline steps have a status row; other artifacts (idea, choices, approvals) do not.
    if ((STEP_IDS as readonly string[]).includes(step)) {
      this.db
        .insert(steps)
        .values({ projectId: p, step, version })
        .onConflictDoUpdate({ target: [steps.projectId, steps.step], set: { version, updatedAt: new Date() } })
        .run();
    }
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

  /**
   * Stores the bible and fills the fact base tables from it. Facts the producer or the agent
   * added later (answers to findings) survive a new bible unless it defines the same id.
   */
  importBible(bible: Bible): void {
    const p = this.projectId;
    const defined = new Set(bible.facts.map((f) => f.id));
    const kept = this.db
      .select()
      .from(facts)
      .where(and(eq(facts.projectId, p), inArray(facts.source, ['producer', 'agent'])))
      .all()
      .filter((f) => !defined.has(f.id));
    const keptIds = new Set(kept.map((f) => f.id));
    const keptKnowledge = this.db.select().from(knowledge).where(eq(knowledge.projectId, p)).all().filter((k) => keptIds.has(k.factId));
    this.saveArtifact('bible', bible);
    this.db.transaction((tx) => {
      for (const t of [characters, facts, events, knowledge, worldRules, guns]) tx.delete(t).where(eq(t.projectId, p)).run();
      for (const c of bible.characters) tx.insert(characters).values({ projectId: p, name: c.name, data: c }).run();
      for (const f of bible.facts) tx.insert(facts).values({ projectId: p, id: f.id, text: f.text, sinceEp: f.since_ep ?? null }).run();
      for (const f of kept) tx.insert(facts).values(f).run();
      for (const { id: _id, ...k } of keptKnowledge) {
        void _id;
        tx.insert(knowledge).values(k).run();
      }
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

  /** A changed card makes the episode's script outdated: it was written from the old card. */
  saveCard(card: EpisodeCard, extraFactIds: string[] = []): void {
    const prev = this.db
      .select({ data: episodeCards.data })
      .from(episodeCards)
      .where(and(eq(episodeCards.projectId, this.projectId), eq(episodeCards.ep, card.ep)))
      .get();
    this.saveEpisodeDoc(episodeCards, card.ep, card);
    if (prev && JSON.stringify(prev.data) !== JSON.stringify(card)) this.markStale(scripts, [card.ep]);
    this.replaceLinks('card', [{ ep: card.ep, factIds: [...card.acts_on.map((a) => a.fact), ...extraFactIds] }], [card.ep]);
  }

  /** A script relies on the same facts as its card, plus any given explicitly. */
  saveScript(script: Script, extraFactIds: string[] = []): void {
    this.saveEpisodeDoc(scripts, script.ep, script);
    const cardFacts = this.linksOf('card', script.ep);
    this.replaceLinks('script', [{ ep: script.ep, factIds: [...cardFacts, ...extraFactIds] }], [script.ep]);
  }

  /**
   * Links an episode document to facts or timeline events explicitly (e.g. a flashback to an event).
   * Explicit links are kept apart from the ones derived from acts_on, so re-saving does not drop them.
   */
  link(target: 'outline' | 'card' | 'script', ep: number, factIds: string[]): void {
    for (const factId of factIds) {
      this.db.insert(sceneFactLinks).values({ projectId: this.projectId, ep, target: `${MANUAL}${target}`, factId }).run();
    }
  }

  cards(): EpisodeCard[] {
    return this.db
      .select({ data: episodeCards.data })
      .from(episodeCards)
      .where(eq(episodeCards.projectId, this.projectId))
      .orderBy(episodeCards.ep)
      .all()
      .map((r) => EpisodeCardSchema.parse(r.data));
  }

  script(ep: number): Script | undefined {
    const row = this.db
      .select({ data: scripts.data })
      .from(scripts)
      .where(and(eq(scripts.projectId, this.projectId), eq(scripts.ep, ep)))
      .get();
    return row ? ScriptSchema.parse(row.data) : undefined;
  }

  scripts(): Script[] {
    return this.db
      .select({ data: scripts.data })
      .from(scripts)
      .where(eq(scripts.projectId, this.projectId))
      .orderBy(scripts.ep)
      .all()
      .map((r) => ScriptSchema.parse(r.data));
  }

  /** Open findings of a step and episode become obsolete when the document is rewritten. */
  closeObsolete(step: string, ep: number, verdict: string): void {
    this.db
      .update(findingsTable)
      .set({ status: 'resolved', verdict })
      .where(and(eq(findingsTable.projectId, this.projectId), eq(findingsTable.step, step), eq(findingsTable.episode, ep), eq(findingsTable.status, 'open')))
      .run();
  }

  /**
   * After a new version of a step: open findings the new checks did not raise again are
   * about the old version. They are closed without a fact, so a later run may reopen them.
   * `episodes`: only findings of these episodes (null — findings without an episode).
   */
  closeSuperseded(step: string, keepIds: Iterable<string>, verdict: string, episodes?: (number | null)[]): number {
    const keep = new Set(keepIds);
    const stale = this.findingsOf(step, 'open').filter(
      (f) => !keep.has(f.id) && (episodes === undefined || episodes.includes(f.episode)),
    );
    for (const f of stale) this.setFindingOutcome(f.id, { status: 'resolved', verdict });
    return stale.length;
  }

  logEdit(entity: string, entityId: string, before: unknown, after: unknown, meta: EditMeta): number {
    return this.logRevision(entity, entityId, before, after, meta);
  }

  /** Marks episode documents as outdated: they have to be written again. */
  markOutdated(kind: 'cards' | 'scripts', eps: number[]): number[] {
    return this.markStale(kind === 'cards' ? episodeCards : scripts, eps);
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
    if (rev.entity === 'fact' && rev.before === null) {
      // Undo an added fact: remove it and the knowledge that came with it.
      const added = rev.after as { knowledge?: KnowledgeEntry[] } | null;
      this.db.delete(facts).where(and(eq(facts.projectId, p), eq(facts.id, rev.entityId))).run();
      for (const k of added?.knowledge ?? []) {
        this.db
          .delete(knowledge)
          .where(and(eq(knowledge.projectId, p), eq(knowledge.who, k.who), eq(knowledge.factId, k.fact), eq(knowledge.sinceEp, k.since_ep)))
          .run();
      }
      const id = this.logRevision('fact', rev.entityId, rev.after, null, { ...meta, note });
      return this.propagate([rev.entityId], id);
    }
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
      // Knowledge: re-check what the affected episodes act on (plan and cards) and who knows the changed facts.
      const cards = this.cards();
      const items = [...affected].map((ep) => ({
        ep,
        acts_on: [...(plan?.episodes.find((e) => e.ep === ep)?.acts_on ?? []), ...(cards.find((c) => c.ep === ep)?.acts_on ?? [])],
      }));
      for (const f of checkKnowledge(items, bible, { onlyFacts: changedIds })) raised.push(this.attach(f, f.episode, revisionId));

      // Findings raised by earlier edits that no longer hold (e.g. after a rollback) are closed.
      const allItems = [
        ...(plan?.episodes ?? []),
        ...cards,
      ];
      const holding = new Set([...checkTimelineDetailed(bible).map((i) => i.finding.id), ...checkKnowledge(allItems, bible).map((f) => f.id)]);
      const raisedIds = new Set(raised.map((f) => f.id));
      for (const f of this.findingsOf('memory', 'open')) {
        if (!raisedIds.has(f.id) && !holding.has(f.id.split('~')[0]!)) {
          this.setFindingOutcome(f.id, { status: 'resolved', verdict: `Больше не подтверждается после правки №${revisionId}` });
        }
      }
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

  /**
   * Saves findings of a check run. A finding closed with a fact (by the producer or the judge)
   * or dismissed stays closed when the same check raises it again; one closed by the system
   * (e.g. «сценарий переписан») reopens. The step a finding belongs to never changes.
   */
  saveFindings(list: Finding[], step: string): void {
    for (const f of list) {
      const existing = this.finding(f.id);
      if (existing && (existing.status === 'dismissed' || (existing.status === 'resolved' && existing.resolutionFactId))) continue;
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
        category: f.category ?? null,
        level: f.level ?? null,
        episodes: f.episodes ?? null,
        whyNoticed: f.whyNoticed ?? null,
        agentRule: f.agentRule ?? null,
        doubt: f.doubt ?? null,
      };
      const { step: _step, ...update } = row;
      void _step;
      this.db
        .insert(findingsTable)
        .values(row)
        .onConflictDoUpdate({ target: [findingsTable.projectId, findingsTable.id], set: { ...update, verdict: null } })
        .run();
    }
  }

  /** Records the outcome of a finding: status, judge verdict, the fact that closes it. */
  setFindingOutcome(id: string, outcome: { status: Finding['status']; verdict?: string; resolutionFactId?: string }): void {
    this.db
      .update(findingsTable)
      .set({ status: outcome.status, verdict: outcome.verdict ?? null, resolutionFactId: outcome.resolutionFactId ?? null })
      .where(and(eq(findingsTable.projectId, this.projectId), eq(findingsTable.id, id)))
      .run();
  }

  finding(id: string): (typeof findingsTable.$inferSelect) | undefined {
    return this.db
      .select()
      .from(findingsTable)
      .where(and(eq(findingsTable.projectId, this.projectId), eq(findingsTable.id, id)))
      .get();
  }

  findingsOf(step?: string, status?: Finding['status']): (typeof findingsTable.$inferSelect)[] {
    const conds = [eq(findingsTable.projectId, this.projectId)];
    if (step) conds.push(eq(findingsTable.step, step));
    if (status) conds.push(eq(findingsTable.status, status));
    return this.db.select().from(findingsTable).where(and(...conds)).all();
  }

  /** Adds a new fact (and who knows it) to the fact base. */
  /** Adds a new fact. An existing one is changed only through updateFact (logged and propagated). */
  addFact(fact: Fact, known: KnowledgeEntry[], meta: EditMeta): number {
    const p = this.projectId;
    if (this.db.select().from(facts).where(and(eq(facts.projectId, p), eq(facts.id, fact.id))).get()) {
      throw new Error(`Факт ${fact.id} уже есть. Чтобы изменить его, поправьте факт, а не добавляйте новый`);
    }
    this.db
      .insert(facts)
      .values({ projectId: p, id: fact.id, text: fact.text, sinceEp: fact.since_ep ?? null, source: meta.author })
      .run();
    for (const k of known) {
      this.db.insert(knowledge).values({ projectId: p, who: k.who, factId: k.fact, sinceEp: k.since_ep, how: k.how ?? null }).run();
    }
    return this.logRevision('fact', fact.id, null, { fact, knowledge: known }, meta);
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
      .where(and(eq(sceneFactLinks.projectId, this.projectId), inArray(sceneFactLinks.target, [target, `${MANUAL}${target}`]), eq(sceneFactLinks.ep, ep)))
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
