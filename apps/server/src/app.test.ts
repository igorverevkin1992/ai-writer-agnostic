import { llmCalls, loadModelsConfig, openDb } from '@aiw/core';
import { loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { buildApp } from './app.ts';

function setup() {
  const db = openDb(':memory:');
  return { db, app: buildApp({ kb: loadKb(), db, config: loadModelsConfig() }) };
}

describe('server', () => {
  it('answers health check with knowledge base summary', async () => {
    const { app } = setup();
    const res = await app.inject({ method: 'GET', url: '/api/health' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, kb: { holeTypes: 11, episodes: 60 } });
    await app.close();
  });

  it('reports project costs against the budget', async () => {
    const { app, db } = setup();
    db.insert(llmCalls)
      .values({ projectId: 'p1', step: 'bible', role: 'architect', provider: 'anthropic', model: 'm', costUsd: 1.5, durationMs: 1, status: 'ok' })
      .run();
    const res = await app.inject({ method: 'GET', url: '/api/projects/p1/costs' });
    expect(res.json()).toMatchObject({ totalUsd: 1.5, limitUsd: 300, calls: 1, byRole: [{ role: 'architect', calls: 1 }] });
    await app.close();
  });
});
