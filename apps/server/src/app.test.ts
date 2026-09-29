import { describe, expect, it } from 'vitest';
import { buildApp } from './app.ts';

describe('server', () => {
  it('answers health check', async () => {
    const app = buildApp();
    const res = await app.inject({ method: 'GET', url: '/api/health' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ ok: true });
    await app.close();
  });
});
