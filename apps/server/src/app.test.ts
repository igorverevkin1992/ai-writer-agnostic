import { loadKb } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { buildApp } from './app.ts';

describe('server', () => {
  it('answers health check with knowledge base summary', async () => {
    const app = buildApp({ kb: loadKb() });
    const res = await app.inject({ method: 'GET', url: '/api/health' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true, kb: { holeTypes: 11, episodes: 60 } });
    await app.close();
  });
});
