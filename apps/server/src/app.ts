import Fastify, { type FastifyInstance } from 'fastify';
import {
  APP_VERSION,
  BudgetExceededError,
  LlmError,
  Pipeline,
  PipelineError,
  ProjectError,
  ProjectMemory,
  STEP_IDS,
  approveStep,
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
import { UnknownGenreError, genreKit, type Kb } from '@aiw/kb';
import { ZodError } from 'zod';

export interface AppDeps {
  kb: Kb;
  db: Db;
  config: ModelsConfig;
  llm: LlmClient;
}

const SEVERITY_ORDER: Record<string, number> = { blocker: 0, major: 1, minor: 2 };

function asStep(step: string): StepId {
  if (!(STEP_IDS as readonly string[]).includes(step)) throw new PipelineError(`Нет шага «${step}»`);
  return step as StepId;
}

export function buildApp({ kb, db, config, llm }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: false });

  app.setErrorHandler((err, _req, reply) => {
    if (err instanceof BudgetExceededError) return reply.status(409).send({ error: err.message });
    if (err instanceof ProjectError && /не найден/u.test(err.message)) return reply.status(404).send({ error: err.message });
    if (err instanceof PipelineError || err instanceof ProjectError || err instanceof UnknownGenreError || err instanceof LlmError) {
      return reply.status(400).send({ error: err.message });
    }
    if (err instanceof ZodError) return reply.status(400).send({ error: err.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ') });
    return reply.status(500).send({ error: err instanceof Error ? err.message : String(err) });
  });

  app.get('/api/health', async () => ({ ok: true, version: APP_VERSION, genres: Object.keys(kb.genres) }));

  /** Genre packs a new project can be based on. */
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
      };
    }),
  );

  app.post<{ Body: { title: string; genreId: string; idea: string; budgetLimitUsd?: number } }>('/api/projects', async (req, reply) => {
    const id = createProject(db, kb, req.body ?? ({} as never));
    return reply.status(201).send({ id });
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id', async (req) => {
    const project = getProject(db, req.params.id);
    return { project, steps: new Pipeline(db, project.id).states() };
  });

  /** Body for cards and scripts: {episodes?: number[], fix?: boolean}. */
  app.post<{ Params: { id: string; step: string }; Body: { episodes?: number[]; fix?: boolean } | undefined }>(
    '/api/projects/:id/steps/:step/run',
    async (req) => {
      getProject(db, req.params.id);
      return runStep({ db, llm, kb, projectId: req.params.id }, asStep(req.params.step), { episodes: req.body?.episodes, fix: req.body?.fix });
    },
  );

  /** Body: {choice} for concepts, {block} (first episode) for episode cards. */
  app.post<{ Params: { id: string; step: string }; Body: { choice?: number; block?: number } | undefined }>(
    '/api/projects/:id/steps/:step/approve',
    async (req) => {
      getProject(db, req.params.id);
      approveStep({ db, kb, projectId: req.params.id }, asStep(req.params.step), { choice: req.body?.choice, block: req.body?.block });
      return new Pipeline(db, req.params.id).get(asStep(req.params.step));
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
      return Number.isFinite(limit) && limit > 0 ? list.slice(0, limit) : list;
    },
  );

  app.post<{ Params: { id: string }; Body: { projectId: string; factId?: string; fact?: never; knowledge?: never } }>(
    '/api/findings/:id/resolve',
    async (req) => {
      getProject(db, req.body?.projectId ?? '');
      resolveByProducer(db, req.body.projectId, req.params.id, req.body);
      return new ProjectMemory(db, req.body.projectId).finding(req.params.id);
    },
  );

  app.post<{ Params: { id: string }; Body: { projectId: string; factId: string } }>('/api/findings/:id/dismiss', async (req) => {
    getProject(db, req.body?.projectId ?? '');
    dismissByProducer(db, req.body.projectId, req.params.id, req.body.factId ?? '');
    return new ProjectMemory(db, req.body.projectId).finding(req.params.id);
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id/cards', async (req) => {
    getProject(db, req.params.id);
    return new ProjectMemory(db, req.params.id).cards();
  });

  app.get<{ Params: { id: string; ep: string } }>('/api/projects/:id/scripts/:ep', async (req, reply) => {
    getProject(db, req.params.id);
    const script = new ProjectMemory(db, req.params.id).script(Number(req.params.ep));
    if (!script) return reply.status(404).send({ error: `Нет сценария ${req.params.ep}-й серии` });
    return { script, text: renderScript(script) };
  });

  app.post<{ Params: { id: string }; Body: { ep: number; from: number; to: number; note?: string } }>('/api/projects/:id/polish', async (req) => {
    getProject(db, req.params.id);
    return proposePolish({ db, llm, kb, projectId: req.params.id }, req.body);
  });

  app.post<{ Params: { id: string }; Body: { variant: number } }>('/api/projects/:id/polish/choose', async (req) => {
    getProject(db, req.params.id);
    const { script, findings } = choosePolish({ db, kb, projectId: req.params.id }, req.body?.variant ?? -1);
    return { script, text: renderScript(script), findings };
  });

  app.get<{ Params: { id: string } }>('/api/projects/:id/costs', async (req) => costSummary(db, config, req.params.id));

  app.get('/api/projects/:id/export', async (_req, reply) => reply.status(501).send({ error: 'Экспорт появится на вехе M6' }));

  return app;
}
