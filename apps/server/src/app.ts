import Fastify, { type FastifyInstance } from 'fastify';
import { APP_VERSION } from '@aiw/core';
import type { Kb } from '@aiw/kb';

export interface AppDeps {
  kb: Kb;
}

export function buildApp({ kb }: AppDeps): FastifyInstance {
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

  return app;
}
