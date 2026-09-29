import Fastify, { type FastifyInstance } from 'fastify';
import { APP_VERSION } from '@aiw/core';

export function buildApp(): FastifyInstance {
  const app = Fastify({ logger: false });

  app.get('/api/health', async () => ({ ok: true, version: APP_VERSION }));

  return app;
}
