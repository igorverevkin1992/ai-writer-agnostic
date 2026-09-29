import Fastify, { type FastifyInstance } from 'fastify';
import { APP_VERSION, costSummary, type Db, type ModelsConfig } from '@aiw/core';
import type { Kb } from '@aiw/kb';

export interface AppDeps {
  kb: Kb;
  db: Db;
  config: ModelsConfig;
}

export function buildApp({ kb, db, config }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: false });

  app.get('/api/health', async () => ({
    ok: true,
    version: APP_VERSION,
    kb: {
      rules: kb.rules.reduce((n, f) => n + f.rules.length, 0),
      holeTypes: kb.holes.holes.length,
      episodes: kb.frame.episodes,
    },
  }));

  app.get<{ Params: { id: string } }>('/api/projects/:id/costs', async (req) => costSummary(db, config, req.params.id));

  return app;
}
