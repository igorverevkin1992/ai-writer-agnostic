import Fastify, { type FastifyInstance } from 'fastify';
import { APP_VERSION, costSummary, type Db, type ModelsConfig } from '@aiw/core';
import { genreKit, type Kb } from '@aiw/kb';

export interface AppDeps {
  kb: Kb;
  db: Db;
  config: ModelsConfig;
}

export function buildApp({ kb, db, config }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: false });

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

  app.get<{ Params: { id: string } }>('/api/projects/:id/costs', async (req) => costSummary(db, config, req.params.id));

  return app;
}
