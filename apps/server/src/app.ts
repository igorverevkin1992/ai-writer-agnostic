import Fastify, { type FastifyInstance } from 'fastify';
import { desc, eq } from 'drizzle-orm';
import {
  APP_VERSION,
  EXPORT_FORMATS,
  EpisodeCard,
  Script,
  TimelineEvent,
  artifacts,
  characters,
  checkQueue,
  checkKnowledge,
  checkLegalMarkers,
  checkLimits,
  checkScriptMetrics,
  collectTexts,
  episodeCards,
  events,
  exportProject,
  facts,
  findings,
  guns,
  knowledge,
  llmCalls,
  missingScripts,
  nextTask,
  planParts,
  projectKit,
  projectSpent,
  projects,
  revisions,
  sceneFactLinks,
  scripts,
  steps,
  worldRules,
  type ExportFormat,
  type NextTask,
  BudgetExceededError,
  LlmError,
  Pipeline,
  PipelineError,
  ProjectError,
  RUNNABLE_STEPS,
  ProjectMemory,
  STEP_IDS,
  STEP_LABELS,
  approveStep,
  cardBlocks,
  choosePolish,
  costSummary,
  proposePolish,
  renderScript,
  createProject,
  dismissByProducer,
  getProject,
  resolveByProducer,
  runStep,
  skipStep,
  type Db,
  type LlmClient,
  type ModelsConfig,
  type StepId,
} from '@aiw/core';
import { FrameInputError, UnknownGenreError, genreKit, type Kb } from '@aiw/kb';
import { z, ZodError } from 'zod';

export interface AppDeps {
  kb: Kb;
  db: Db;
  config: ModelsConfig;
  llm: LlmClient;
  /** Demo mode: models answer from the golden project, no API keys needed. */
  demo?: boolean;
  /**
   * Step runs take minutes: by default POST .../run starts the run and answers at once,
   * the screens follow it through the overview. false — the request waits (tests).
   */
  runsInBackground?: boolean;
}

/** Progress of a run in units the producer understands: episodes (scripts), blocks (cards), parts (plan). */
interface Progress {
  unit: 'episode' | 'block' | 'part' | 'call';
  /** Units finished. */
  done: number;
  total: number;
  /** The episode (or the last episode of the block) being written now. */
  current?: number;
  /** The last episode finished, for «остановлено после серии N». */
  lastEpisode?: number;
}

/** The last run started for a project: the screens show its progress and error. */
interface Job {
  step: StepId;
  running: boolean;
  error?: string;
  startedAt: string;
  /** The producer asked to stop: the run finishes the current model call and stops at the next unit. */
  cancelRequested: boolean;
  /** The run was stopped by the producer (not an error). */
  stopped?: boolean;
  /** The run hit the project budget: the producer can raise it and run again. */
  budgetExceeded?: boolean;
  progress: Progress;
  /** Spent before the run started: the overview shows the cost of this run. */
  spentBeforeUsd: number;
}

const Id = z.string().min(1);
const EpisodeNo = z.int().min(1);
const Money = z.number().positive();
const FrameInputsBody = z.record(z.string().regex(/^[a-z_]+$/u), z.number());
const HeroTermsBody = z.object({ nom: z.string().min(1), gen: z.string().min(1), dat: z.string().min(1) });
const TermsBody = z.object({ hero: HeroTermsBody.optional(), wound: z.string().min(1).optional() });
const ProjectBody = z.object({
  title: z.string(),
  genreId: z.string(),
  idea: z.string(),
  budgetLimitUsd: Money.optional(),
  frameInputs: FrameInputsBody.optional(),
  terms: TermsBody.optional(),
});
const ProjectPatch = z.object({
  title: z.string().min(1).max(200).optional(),
  budgetLimitUsd: Money.optional(),
  frameInputs: FrameInputsBody.optional(),
  terms: TermsBody.optional(),
});
const RunBody = z.object({ episodes: z.array(EpisodeNo).min(1).optional(), fix: z.boolean().optional(), wait: z.boolean().optional() });
const ApproveBody = z.object({ choice: z.int().min(0).optional(), block: EpisodeNo.optional() });
const ResolveBody = z.object({
  projectId: Id,
  factId: z.string().optional(),
  fact: z.object({ id: z.string(), text: z.string(), since_ep: EpisodeNo.optional() }).optional(),
  knowledge: z.array(z.object({ who: z.string(), fact: z.string(), since_ep: z.int().min(0), how: z.string().optional() })).optional(),
});
const DismissBody = z.object({ projectId: Id, factId: z.string().optional(), reason: z.string().max(2000).optional() });
const FactPatch = z.object({ text: z.string().min(1).optional(), since_ep: EpisodeNo.nullable().optional(), note: z.string().max(500).optional() });
const EventPatch = TimelineEvent.omit({ id: true }).partial().extend({ note: z.string().max(500).optional() });
const PolishBody = z.object({ ep: EpisodeNo, from: z.int().min(0), to: z.int().min(0), note: z.string().max(2000).optional() });
const ChooseBody = z.object({ variant: z.int().min(0) });

/** The server is local: it answers only to this computer, not to other sites or hosts. */
const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/u;
function isLocalOrigin(origin: string): boolean {
  try {
    return LOCAL_HOST.test(new URL(origin).host);
  } catch {
    return false;
  }
}

const SEVERITY_ORDER: Record<string, number> = { blocker: 0, major: 1, minor: 2 };

function asStep(step: string): StepId {
  if (!(STEP_IDS as readonly string[]).includes(step)) throw new PipelineError(`Нет шага «${step}»`);
  return step as StepId;
}

const TYPE_NAMES: Record<string, string> = {
  number: 'число',
  int: 'целое число',
  string: 'текст',
  boolean: 'да или нет',
  array: 'список',
  object: 'объект',
  undefined: 'пусто',
  null: 'пусто',
};

/** A Zod issue in the producer's language: the field and what is wrong, no English. */
function zodIssueText(issue: z.core.$ZodIssue): string {
  const field = issue.path.map(String).join('.') || 'запрос';
  switch (issue.code) {
    case 'invalid_type': {
      const expected = TYPE_NAMES[issue.expected] ?? issue.expected;
      return `${field} — ожидается ${expected}${/(received|получено) undefined$/u.test(issue.message) ? ', поле не заполнено' : ''}`;
    }
    case 'too_small':
      if (issue.origin === 'string') return `${field} — не заполнено`;
      if (issue.origin === 'array') return `${field} — список пустой`;
      return `${field} — ${issue.inclusive === false ? 'должно быть больше' : 'не меньше'} ${issue.minimum}`;
    case 'too_big':
      return `${field} — ${issue.origin === 'string' ? 'слишком длинный текст' : `не больше ${issue.maximum}`}`;
    case 'invalid_value':
      return `${field} — допустимо только: ${issue.values.map((v) => String(v)).join(', ')}`;
    case 'unrecognized_keys':
      return `${field} — лишние поля: ${issue.keys.join(', ')}`;
    case 'invalid_format':
      return `${field} — неверный формат`;
    case 'invalid_union':
      return `${field} — не подходит ни один из допустимых вариантов`;
    default:
      return `${field} — неверное значение`;
  }
}

export function zodErrorText(err: ZodError): string {
  return `Неверные данные: ${[...new Set(err.issues.map(zodIssueText))].join('; ')}`;
}

/** Tasks that start a new unit of work; the cancel flag is checked when one begins. */
const UNIT_TASK = /^(script|episode_cards|season_plan):(\d+)(?:-(\d+))?$/u;

export function buildApp({ kb, db, config, llm, demo = false, runsInBackground = true }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: false });
  const jobs = new Map<string, Job>();

  app.addHook('onRequest', async (req, reply) => {
    const origin = req.headers.origin;
    if (!LOCAL_HOST.test(req.headers.host ?? '') || (origin && !isLocalOrigin(origin))) {
      return reply.status(403).send({ error: 'Запрос не с этого компьютера' });
    }
  });

  app.setErrorHandler((err, _req, reply) => {
    if (err instanceof BudgetExceededError) return reply.status(409).send({ error: err.message, budgetExceeded: true });
    if (err instanceof ProjectError && /не найден/u.test(err.message)) return reply.status(404).send({ error: err.message });
    if (
      err instanceof PipelineError ||
      err instanceof ProjectError ||
      err instanceof UnknownGenreError ||
      err instanceof FrameInputError ||
      err instanceof LlmError
    ) {
      return reply.status(400).send({ error: err.message });
    }
    if (err instanceof ZodError) return reply.status(400).send({ error: zodErrorText(err) });
    // Fastify's own errors (bad JSON, too large body) carry their status.
    const status = (err as { statusCode?: number }).statusCode;
    if (status && status >= 400 && status < 500) return reply.status(status).send({ error: `Неверный запрос: ${(err as Error).message}` });
    return reply.status(500).send({ error: err instanceof Error ? err.message : String(err) });
  });

  /** What the screens see of a job: progress plus the cost of this run so far. */
  const jobView = (projectId: string) => {
    const job = jobs.get(projectId);
    if (!job) return null;
    const { spentBeforeUsd, ...rest } = job;
    return { ...rest, progress: { ...job.progress, costUsd: Math.max(0, projectSpent(db, projectId) - spentBeforeUsd) } };
  };

  const kitOf = (projectId: string) => projectKit(db, kb, projectId);

  /** How many units a run has, so the progress line can say «серия 7 из 60». */
  const planProgress = (projectId: string, step: StepId, episodes?: number[]): Progress => {
    const memory = new ProjectMemory(db, projectId);
    if (step === 'scripts') {
      const have = memory.cards().map((c) => c.ep);
      const eps = (episodes ?? have).filter((ep) => have.includes(ep));
      return { unit: 'episode', done: 0, total: eps.length };
    }
    if (step === 'episode_cards') {
      const eps = memory.currentPlan()?.episodes.map((e) => e.ep) ?? [];
      const blocks = cardBlocks(eps).filter((b) => !episodes || b.some((ep) => episodes.includes(ep)));
      return { unit: 'block', done: 0, total: blocks.length };
    }
    if (step === 'season_plan') {
      const f = kitOf(projectId).frame;
      return { unit: 'part', done: 0, total: planParts(f.episodes, f.blocks).length };
    }
    return { unit: 'call', done: 0, total: 1 };
  };

  /**
   * The model client of a background run: it follows the progress of the run and stops it
   * cooperatively. The flag is checked when a new unit (episode, block, part) begins, so the
   * current model call always finishes and nothing paid for is lost.
   */
  const stoppable = (job: Job): LlmClient => {
    let unitsStarted = 0;
    const completeJson: LlmClient['completeJson'] = (schema, opts) => {
      const m = UNIT_TASK.exec(opts.request.task ?? '');
      if (m) {
        // A new unit begins: the previous one is done.
        const lastEpisode = unitsStarted > 0 ? job.progress.current : undefined;
        if (job.cancelRequested) {
          job.stopped = true;
          job.progress = { ...job.progress, done: unitsStarted, lastEpisode };
          throw new PipelineError(lastEpisode ? `Остановлено продюсером после серии ${lastEpisode}` : 'Остановлено продюсером до первой серии');
        }
        unitsStarted += 1;
        job.progress = { ...job.progress, done: unitsStarted - 1, current: Number(m[3] ?? m[2]), lastEpisode };
      }
      return llm.completeJson(schema, opts);
    };
    return new Proxy(llm, {
      get(target, prop, receiver) {
        if (prop === 'completeJson') return completeJson;
        const value = Reflect.get(target, prop, receiver);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
  };

  app.get('/api/health', async () => ({ ok: true, version: APP_VERSION, demo, genres: Object.keys(kb.genres) }));

  /** Genre packs a new project can be based on, with the frame inputs and hero terms a project may change. */
  app.get('/api/genres', async () =>
    Object.keys(kb.genres).map((id) => {
      const g = genreKit(kb, id);
      return {
        id,
        title: g.genre.title,
        platform: g.genre.platform ?? null,
        episodes: g.frame.episodes,
        free: g.frame.free,
        villains: g.frame.villains?.count ?? 0,
        rules: g.rules.rules.length,
        inputs: g.frame.inputs,
        terms: g.genre.terms,
        budgetLimitUsd: config.budget.project_limit_usd,
      };
    }),
  );

  app.get('/api/projects', async () =>
    db.select().from(projects).orderBy(desc(projects.createdAt)).all().filter((p) => !p.id.startsWith('eval-')),
  );

  /** Everything the screens need about a project, and the one task for «Сейчас». */
  app.get<{ Params: { id: string } }>('/api/projects/:id/overview', async (req) => {
    const project = getProject(db, req.params.id);
    const memory = new ProjectMemory(db, project.id);
    // A project without a genre (made outside the app) still opens: the screens show what there is.
    const kit = project.genreId ? kitOf(project.id) : null;
    const next: NextTask = kit
      ? nextTask(db, kb, project.id)
      : { step: 'concept', stepLabel: STEP_LABELS.concept, action: 'run', task: 'У проекта не выбран жанр: агент не может работать с ним', criterion: 'Проект создан через приложение с жанром' };
    return {
      project,
      demo,
      genre: kit
        ? {
            id: kit.genre.id,
            title: kit.genre.title,
            episodes: kit.frame.episodes,
            free: kit.frame.free,
            anchors: kit.frame.anchors,
            anchorLabels: kit.frame.anchor_labels,
            lines: kit.frame.lines,
            terms: kit.genre.terms,
            inputs: kit.frame.inputs,
            pass: kit.checklist.pass,
            total: kit.checklist.total,
          }
        : null,
      steps: new Pipeline(db, project.id).states(),
      next,
      idea: (memory.latestArtifact('idea') as { text: string } | undefined)?.text ?? null,
      concepts: memory.latestArtifact('concept') ?? null,
      concept: memory.latestArtifact('concept_choice') ?? null,
      logline: memory.latestArtifact('logline') ?? null,
      bible: memory.currentBible() ?? null,
      plan: memory.currentPlan() ?? null,
      cardBlocks: (memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? [],
      cards: memory.cards().map((c) => c.ep),
      scripts: memory.scripts().map((s) => s.ep),
      stale: memory.staleEpisodes(),
      polish: memory.latestArtifact('polish') ?? null,
      budget: costSummary(db, config, project.id),
      job: jobView(project.id),
      models: Object.fromEntries(Object.entries(config.roles).map(([role, r]) => [role, r.model])),
    };
  });

  app.post('/api/projects', async (req, reply) => {
    const id = createProject(db, kb, ProjectBody.parse(req.body ?? {}));
    return reply.status(201).send({ id });
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id', async (req) => {
    const project = getProject(db, req.params.id);
    return { project, steps: new Pipeline(db, project.id).states() };
  });

  /** Rename, raise the budget, change frame inputs or hero terms (the last two until the season plan exists). */
  app.patch<{ Params: { id: string } }>('/api/projects/:id', async (req) => {
    const project = getProject(db, req.params.id);
    const body = ProjectPatch.parse(req.body ?? {});
    const patch: Partial<typeof projects.$inferInsert> = {};
    if (body.title !== undefined) {
      if (!body.title.trim()) throw new ProjectError('Название не может быть пустым');
      patch.title = body.title.trim();
    }
    if (body.budgetLimitUsd !== undefined) patch.budgetLimitUsd = body.budgetLimitUsd;
    if (body.frameInputs !== undefined || body.terms !== undefined) {
      if (new Pipeline(db, project.id).get('season_plan').version > 0) {
        throw new ProjectError('Параметры каркаса и термины можно менять, пока не написан план сезона');
      }
      const frameInputs = body.frameInputs !== undefined ? (Object.keys(body.frameInputs).length ? body.frameInputs : null) : project.frameInputs;
      const terms = body.terms !== undefined ? (Object.keys(body.terms).length ? body.terms : null) : project.terms;
      // Refused before saving when the inputs do not fit the frame.
      genreKit(kb, project.genreId ?? '', { inputs: frameInputs ?? {}, terms: terms ?? undefined });
      patch.frameInputs = frameInputs;
      patch.terms = terms;
    }
    if (Object.keys(patch).length) db.update(projects).set(patch).where(eq(projects.id, project.id)).run();
    return { project: getProject(db, project.id), budget: costSummary(db, config, project.id) };
  });

  /** Deletes the project with everything it has. Refused while the agent works on it. */
  app.delete<{ Params: { id: string } }>('/api/projects/:id', async (req, reply) => {
    const project = getProject(db, req.params.id);
    if (jobs.get(project.id)?.running) throw new PipelineError('Агент ещё работает над проектом. Сначала остановите шаг');
    db.transaction((tx) => {
      for (const t of [steps, artifacts, characters, facts, events, knowledge, worldRules, guns, episodeCards, scripts, sceneFactLinks, findings, revisions, checkQueue, llmCalls]) {
        tx.delete(t).where(eq(t.projectId, project.id)).run();
      }
      tx.delete(projects).where(eq(projects.id, project.id)).run();
    });
    jobs.delete(project.id);
    return reply.status(204).send();
  });

  /**
   * Body for cards and scripts: {episodes?: number[], fix?: boolean}. Answers 202 at once;
   * the run goes on in the background (overview.job). {wait: true} — answer with the result.
   */
  app.post<{ Params: { id: string; step: string } }>('/api/projects/:id/steps/:step/run', async (req, reply) => {
    const projectId = getProject(db, req.params.id).id;
    const step = asStep(req.params.step);
    const body = RunBody.parse(req.body ?? {});
    const opts = { episodes: body.episodes, fix: body.fix };
    if (!runsInBackground || body.wait) return runStep({ db, llm, kb, projectId }, step, opts);
    // Refuse at once what cannot start (order of steps, another run going on).
    if (!RUNNABLE_STEPS.includes(step)) throw new PipelineError(`Шаг «${step}» агент не выполняет`);
    new Pipeline(db, projectId).assertCanRun(step);
    const job: Job = {
      step,
      running: true,
      startedAt: new Date().toISOString(),
      cancelRequested: false,
      progress: planProgress(projectId, step, body.episodes),
      spentBeforeUsd: projectSpent(db, projectId),
    };
    jobs.set(projectId, job);
    runStep({ db, llm: stoppable(job), kb, projectId }, step, opts)
      .then(() => {
        job.progress = { ...job.progress, done: job.progress.total, lastEpisode: job.progress.current };
      })
      .catch((err: unknown) => {
        job.error = err instanceof Error ? err.message : String(err);
        if (err instanceof BudgetExceededError) job.budgetExceeded = true;
      })
      .finally(() => {
        job.running = false;
      });
    return reply.status(202).send({ step, started: true });
  });

  /** The producer stops a running step: the current model call finishes, the next episode or block does not start. */
  app.post<{ Params: { id: string; step: string } }>('/api/projects/:id/steps/:step/cancel', async (req) => {
    const projectId = getProject(db, req.params.id).id;
    const step = asStep(req.params.step);
    const job = jobs.get(projectId);
    if (!job?.running || job.step !== step) throw new PipelineError(`Шаг «${STEP_LABELS[step]}» сейчас не выполняется`);
    job.cancelRequested = true;
    return { step, cancelling: true, message: 'Агент закончит текущий вызов модели и остановится' };
  });

  /** Body: {choice} for concepts, {block} (first episode) for episode cards. */
  app.post<{ Params: { id: string; step: string } }>(
    '/api/projects/:id/steps/:step/approve',
    async (req) => {
      getProject(db, req.params.id);
      const step = asStep(req.params.step);
      if (step === 'scripts') {
        // The season is approved as a whole: every card needs a current, checked script.
        const missing = missingScripts(new ProjectMemory(db, req.params.id));
        if (missing.length) {
          throw new PipelineError(`Нельзя утвердить «Сценарии»: нет готовых сценариев у ${missing.length} серий (${missing.slice(0, 10).join(', ')}${missing.length > 10 ? '…' : ''})`);
        }
      }
      approveStep({ db, kb, projectId: req.params.id }, step, ApproveBody.parse(req.body ?? {}));
      return new Pipeline(db, req.params.id).get(step);
    },
  );

  app.post<{ Params: { id: string; step: string } }>('/api/projects/:id/steps/:step/skip', async (req) => {
    getProject(db, req.params.id);
    skipStep({ db, kb, projectId: req.params.id }, asStep(req.params.step));
    return new Pipeline(db, req.params.id).get(asStep(req.params.step));
  });

  /** Findings, blockers first. ?status=open&limit=3 gives what the screen shows. */
  app.get<{ Params: { id: string }; Querystring: { status?: 'open' | 'resolved' | 'dismissed'; step?: string; limit?: string } }>(
    '/api/projects/:id/findings',
    async (req) => {
      getProject(db, req.params.id);
      const list = new ProjectMemory(db, req.params.id)
        .findingsOf(req.query.step, req.query.status)
        .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) || (a.episode ?? 0) - (b.episode ?? 0));
      const limit = Number(req.query.limit);
      const names = new Map(kb.review.categories.map((c) => [c.id, c.name]));
      // Review categories with their names from the knowledge base: the screen shows «А — …».
      return (Number.isFinite(limit) && limit > 0 ? list.slice(0, limit) : list).map((f) => ({
        ...f,
        categoryNames: (f.category ?? []).map((id) => `${id} — ${names.get(id) ?? ''}`.replace(/ — $/u, '')),
      }));
    },
  );

  app.post<{ Params: { id: string } }>('/api/findings/:id/resolve', async (req) => {
    const body = ResolveBody.parse(req.body ?? {});
    getProject(db, body.projectId);
    resolveByProducer(db, body.projectId, req.params.id, body);
    return new ProjectMemory(db, body.projectId).finding(req.params.id);
  });

  /** «Это не дыра»: with a fact that answers it, or with the producer's reason in free text. */
  app.post<{ Params: { id: string } }>('/api/findings/:id/dismiss', async (req) => {
    const body = DismissBody.parse(req.body ?? {});
    getProject(db, body.projectId);
    dismissByProducer(db, body.projectId, req.params.id, { factId: body.factId, reason: body.reason });
    return new ProjectMemory(db, body.projectId).finding(req.params.id);
  });

  // ---- hand edits: facts, events, cards, scripts ----

  /** Changes a fact; cards and scripts that rely on it become outdated, code checks run at once. */
  app.patch<{ Params: { id: string; factId: string } }>('/api/projects/:id/facts/:factId', async (req) => {
    getProject(db, req.params.id);
    const { note, ...patch } = FactPatch.parse(req.body ?? {});
    const memory = new ProjectMemory(db, req.params.id);
    if (!memory.currentBible()?.facts.some((f) => f.id === req.params.factId)) throw new PipelineError(`Факта ${req.params.factId} нет в базе`);
    return memory.updateFact(req.params.factId, patch, { author: 'producer', note: note ?? 'Правка продюсера' });
  });

  /** Changes a timeline event (a year, participants, ages); chronology is re-checked at once. */
  app.patch<{ Params: { id: string; eventId: string } }>('/api/projects/:id/events/:eventId', async (req) => {
    getProject(db, req.params.id);
    const { note, ...patch } = EventPatch.parse(req.body ?? {});
    const memory = new ProjectMemory(db, req.params.id);
    if (!memory.currentBible()?.timeline.events.some((e) => e.id === req.params.eventId)) throw new PipelineError(`События ${req.params.eventId} нет в хронологии`);
    return memory.updateEvent(req.params.eventId, patch, { author: 'producer', note: note ?? 'Правка продюсера' });
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id/cards', async (req) => {
    getProject(db, req.params.id);
    return new ProjectMemory(db, req.params.id).cards();
  });

  /** The producer rewrites a card by hand: saved as a new version, code checks run without the model. */
  app.put<{ Params: { id: string; ep: string } }>('/api/projects/:id/cards/:ep', async (req) => {
    const project = getProject(db, req.params.id);
    const ep = EpisodeNo.parse(Number(req.params.ep));
    const memory = new ProjectMemory(db, project.id);
    const pipeline = new Pipeline(db, project.id);
    if (pipeline.states().some((s) => s.status === 'checking')) throw new PipelineError('Агент ещё работает. Дождитесь окончания шага');
    const before = memory.cards().find((c) => c.ep === ep);
    if (!before) throw new PipelineError(`Карточки ${ep}-й серии нет`);
    const card = EpisodeCard.parse({ ...(req.body as object), ep });
    const bible = memory.currentBible();
    if (!bible) throw new PipelineError('Нет библии проекта');
    const kit = kitOf(project.id);
    memory.closeObsolete('episode_cards', ep, 'Карточка исправлена продюсером');
    memory.saveCard(card);
    memory.logEdit('card', String(ep), before, card, { author: 'producer', note: 'Карточка исправлена вручную' });
    const cards = memory.cards();
    const found = [
      ...checkLimits(bible, cards, kit.production),
      ...checkKnowledge(cards, bible),
      ...checkLegalMarkers(collectTexts({ cards: [card] }), kit.legal),
    ].filter((f) => f.episode === ep || f.episode === undefined);
    memory.saveFindings(found, 'episode_cards');
    memory.closeSuperseded('episode_cards', found.map((f) => f.id), 'Больше не подтверждается', [ep]);
    // The block of this card needs approval again.
    const approved = new Set((memory.latestArtifact('card_blocks') as { approved: number[] } | undefined)?.approved ?? []);
    const start = cardBlocks(memory.currentPlan()?.episodes.map((e) => e.ep) ?? cards.map((c) => c.ep)).find((b) => b.includes(ep))?.[0];
    if (start !== undefined && approved.delete(start)) memory.saveArtifact('card_blocks', { approved: [...approved].sort((a, b) => a - b) });
    const status = pipeline.reopen('episode_cards');
    return { card, findings: found, status };
  });

  app.get<{ Params: { id: string; ep: string } }>('/api/projects/:id/scripts/:ep', async (req, reply) => {
    getProject(db, req.params.id);
    const script = new ProjectMemory(db, req.params.id).script(Number(req.params.ep));
    if (!script) return reply.status(404).send({ error: `Нет сценария ${req.params.ep}-й серии` });
    return { script, text: renderScript(script) };
  });

  /** The producer rewrites a script by hand: saved as a new version, the script linter runs without the model. */
  app.put<{ Params: { id: string; ep: string } }>('/api/projects/:id/scripts/:ep', async (req) => {
    const project = getProject(db, req.params.id);
    const ep = EpisodeNo.parse(Number(req.params.ep));
    const memory = new ProjectMemory(db, project.id);
    const pipeline = new Pipeline(db, project.id);
    if (pipeline.states().some((s) => s.status === 'checking')) throw new PipelineError('Агент ещё работает. Дождитесь окончания шага');
    const before = memory.script(ep);
    if (!before) throw new PipelineError(`Сценария ${ep}-й серии нет`);
    const script = Script.parse({ ...(req.body as object), ep });
    const kit = kitOf(project.id);
    memory.closeObsolete('scripts', ep, 'Сценарий исправлен продюсером');
    memory.saveScript(script);
    memory.logEdit('script', String(ep), before, script, { author: 'producer', note: 'Сценарий исправлен вручную' });
    const found = [...checkScriptMetrics(script, kit.production, kit.frame), ...checkLegalMarkers(collectTexts({ scripts: [script] }), kit.legal)];
    memory.saveFindings(found, 'scripts');
    memory.closeSuperseded('scripts', found.map((f) => f.id), 'Больше не подтверждается', [ep]);
    const status = pipeline.reopen('scripts');
    return { script, text: renderScript(script), findings: found, status };
  });

  app.post<{ Params: { id: string } }>('/api/projects/:id/polish', async (req) => {
    getProject(db, req.params.id);
    return proposePolish({ db, llm, kb, projectId: req.params.id }, PolishBody.parse(req.body ?? {}));
  });

  app.post<{ Params: { id: string } }>('/api/projects/:id/polish/choose', async (req) => {
    getProject(db, req.params.id);
    const { script, findings } = choosePolish({ db, kb, projectId: req.params.id }, ChooseBody.parse(req.body ?? {}).variant);
    return { script, text: renderScript(script), findings };
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id/costs', async (req) => costSummary(db, config, getProject(db, req.params.id).id));

  /** ?format=docx|review|xlsx|json|md|video; &exclude=1 leaves out outdated and unapproved episodes. */
  app.get<{ Params: { id: string }; Querystring: { format?: string; exclude?: string } }>('/api/projects/:id/export', async (req, reply) => {
    getProject(db, req.params.id);
    const format = req.query.format ?? '';
    if (!(EXPORT_FORMATS as readonly string[]).includes(format)) {
      return reply.status(400).send({ error: `Формат: ${EXPORT_FORMATS.join(', ')}` });
    }
    const file = await exportProject(db, kb, req.params.id, format as ExportFormat, { excludeUnready: req.query.exclude === '1' || req.query.exclude === 'true' });
    // The first download completes the export step once everything before it is done.
    const pipeline = new Pipeline(db, req.params.id);
    if (pipeline.get('export').status !== 'approved') {
      try {
        pipeline.assertCanRun('export');
        new ProjectMemory(db, req.params.id).saveArtifact('export', { format, at: new Date().toISOString() });
        pipeline.approve('export');
      } catch {
        // Earlier steps are not finished: the file is still given, the step stays open.
      }
    }
    return reply
      .header('Content-Type', file.contentType)
      .header('Content-Disposition', `attachment; filename="${file.filename}"`)
      .send(file.body);
  });

  return app;
}
