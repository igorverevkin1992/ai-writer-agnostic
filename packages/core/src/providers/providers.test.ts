import { eq } from 'drizzle-orm';
import { beforeEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { openDb, type Db } from '../db/client.ts';
import { llmCalls, projects } from '../db/schema.ts';
import { assertBudget, budgetStatus, costSummary } from './budget.ts';
import { DEFAULT_MODELS_CONFIG, ConfigError, loadModelsConfig, parseModelsConfig } from './config.ts';
import { computeCost } from './cost.ts';
import {
  BudgetExceededError,
  InputTooLargeError,
  InvalidOutputError,
  ProviderUnavailableError,
  SameFamilyError,
} from './errors.ts';
import { assertCrossFamily } from './family.ts';
import { extractJson } from './json.ts';
import { LlmClient } from './llm.ts';
import { FakeProvider, testConfig } from './testing.ts';

const ask = { messages: [{ role: 'user' as const, content: 'Привет' }] };

let db: Db;
let anthropic: FakeProvider;
let google: FakeProvider;
let reserve: FakeProvider;

function client(env: NodeJS.ProcessEnv = {}, config = testConfig()) {
  return new LlmClient({ config, db, env, providers: { anthropic, google, openai_compatible: reserve } });
}

function rows() {
  return db.select().from(llmCalls).all();
}

beforeEach(() => {
  db = openDb(':memory:');
  anthropic = new FakeProvider('anthropic');
  google = new FakeProvider('google');
  reserve = new FakeProvider('openai_compatible');
});

describe('models config', () => {
  it('loads config/models.yaml with every role priced', () => {
    const cfg = loadModelsConfig(DEFAULT_MODELS_CONFIG);
    expect(Object.keys(cfg.roles).sort()).toEqual(
      ['architect', 'architect_heavy', 'critic_of_architect', 'critic_of_writer', 'helper', 'writer'],
    );
    expect(cfg.roles.writer.max_input).toBe(200000);
    expect(cfg.budget.project_limit_usd).toBe(300);
    for (const rc of Object.values(cfg.roles)) expect(cfg.prices_usd_per_mtok[rc.model]).toBeDefined();
  });

  it('keeps critics in a different family than their authors', () => {
    const { roles } = loadModelsConfig(DEFAULT_MODELS_CONFIG);
    expect(roles.critic_of_architect.provider).not.toBe(roles.architect.provider);
    expect(roles.critic_of_architect.provider).not.toBe(roles.architect_heavy.provider);
    expect(roles.critic_of_writer.provider).not.toBe(roles.writer.provider);
  });

  it('rejects a role without a price', () => {
    const bad = { ...testConfig(), prices_usd_per_mtok: {} };
    expect(() => parseModelsConfig(bad)).toThrow(ConfigError);
    expect(() => parseModelsConfig(bad)).toThrow(/Нет цены для модели a-big/);
  });

  it('rejects an unknown provider', () => {
    const cfg = testConfig();
    const bad = { ...cfg, roles: { ...cfg.roles, helper: { provider: 'mistral', model: 'a-small' } } };
    expect(() => parseModelsConfig(bad)).toThrow(/roles\.helper\.provider/);
  });
});

describe('family rule: a model never checks itself', () => {
  it('refuses a critic of the same family as the author', () => {
    expect(() => assertCrossFamily('anthropic', 'anthropic')).toThrow(SameFamilyError);
    expect(() => assertCrossFamily('google', 'anthropic')).not.toThrow();
  });

  it('orchestrator refuses to run a check when the critic shares the author provider', async () => {
    const cfg = testConfig();
    const same = parseModelsConfig({
      ...cfg,
      roles: { ...cfg.roles, critic_of_architect: { provider: 'anthropic', model: 'a-big' } },
    });
    await expect(
      client({}, same).complete({ role: 'critic_of_architect', authorProvider: 'anthropic', request: ask }),
    ).rejects.toThrow(SameFamilyError);
    expect(anthropic.calls).toHaveLength(0);
    expect(rows()).toHaveLength(0);
  });

  it('refuses a check without knowing the author', async () => {
    await expect(client().complete({ role: 'critic_of_writer', request: ask })).rejects.toThrow(SameFamilyError);
    expect(anthropic.calls).toHaveLength(0);
  });

  it('runs a cross-family check', async () => {
    const res = await client().complete({ role: 'critic_of_writer', authorProvider: 'google', request: ask });
    expect(res.provider).toBe('anthropic');
  });

  it('refuses when the reserve provider would be the same family as the author', async () => {
    google = new FakeProvider('google', [new ProviderUnavailableError('google', new Error('down'))]);
    const env = { RESERVE_MODEL: 'r-1' };
    const c = client(env);
    await expect(
      c.complete({ role: 'critic_of_architect', authorProvider: 'openai_compatible', request: ask }),
    ).rejects.toThrow(SameFamilyError);
    expect(reserve.calls).toHaveLength(0);
  });
});

describe('cost', () => {
  it('prices input, output and prompt cache', () => {
    const price = { in: 4, out: 20, cache_read: 0.2 };
    const usage = { inputTokens: 1_000_000, outputTokens: 100_000, cacheReadTokens: 1_000_000, cacheWriteTokens: 1_000_000 };
    // 4 + 20*0.1 + 0.2 + 4*1.25
    expect(computeCost(price, usage)).toBeCloseTo(11.2, 6);
  });

  it('charges the long-context rate above 200k input tokens', () => {
    const price = { in: 2, out: 12, in_over_200k: 4, out_over_200k: 18 };
    const small = { inputTokens: 200_000, outputTokens: 1_000_000, cacheReadTokens: 0, cacheWriteTokens: 0 };
    const big = { ...small, inputTokens: 200_001 };
    expect(computeCost(price, small)).toBeCloseTo(0.4 + 12, 6);
    expect(computeCost(price, big)).toBeCloseTo(200_001 * 4e-6 + 18, 6);
  });
});

describe('llm_calls log', () => {
  it('writes role, model, tokens, cost and time for every call', async () => {
    let t = 1000;
    const c = new LlmClient({
      config: testConfig(),
      db,
      env: {},
      now: () => (t += 250),
      providers: { anthropic, google, openai_compatible: reserve },
    });
    const res = await c.complete({ role: 'architect', projectId: 'p1', step: 'bible', request: ask });
    const [row] = rows();
    expect(row).toMatchObject({
      id: res.callId,
      projectId: 'p1',
      step: 'bible',
      role: 'architect',
      provider: 'anthropic',
      model: 'a-big',
      inputTokens: 1000,
      outputTokens: 500,
      durationMs: 250,
      status: 'ok',
      fallbackUsed: false,
      priceKnown: true,
    });
    expect(row?.costUsd).toBeCloseTo((1000 * 4 + 500 * 20) / 1e6, 9);
    expect(res.costUsd).toBeCloseTo(row!.costUsd, 9);
  });

  it('logs a failed call as an error with zero cost', async () => {
    anthropic = new FakeProvider('anthropic', [new ProviderUnavailableError('anthropic', new Error('503'))]);
    await expect(client().complete({ role: 'helper', request: ask })).rejects.toThrow(ProviderUnavailableError);
    expect(rows()).toMatchObject([{ status: 'error', costUsd: 0, role: 'helper' }]);
  });
});

describe('reserve provider', () => {
  it('takes over when the main provider is unavailable', async () => {
    anthropic = new FakeProvider('anthropic', [new ProviderUnavailableError('anthropic', new Error('blocked'))]);
    const res = await client({ RESERVE_MODEL: 'r-1' }).complete({ role: 'architect', request: ask });
    expect(res.provider).toBe('openai_compatible');
    expect(res.fallbackUsed).toBe(true);
    expect(reserve.calls[0]?.target.model).toBe('r-1');
    expect(rows().map((r) => [r.provider, r.status, r.fallbackUsed])).toEqual([
      ['anthropic', 'error', false],
      ['openai_compatible', 'ok', true],
    ]);
    expect(rows()[1]?.priceKnown).toBe(false);
  });

  it('is not used when RESERVE_MODEL is not set', async () => {
    anthropic = new FakeProvider('anthropic', [new ProviderUnavailableError('anthropic', new Error('blocked'))]);
    await expect(client({}).complete({ role: 'architect', request: ask })).rejects.toThrow(ProviderUnavailableError);
    expect(reserve.calls).toHaveLength(0);
  });
});

describe('budget', () => {
  function spend(projectId: string, costUsd: number) {
    db.insert(llmCalls)
      .values({ projectId, role: 'architect', provider: 'anthropic', model: 'a-big', costUsd, durationMs: 1, status: 'ok' })
      .run();
  }

  it('stops before a call once the limit is reached', async () => {
    spend('p1', 10);
    await expect(client().complete({ role: 'architect', projectId: 'p1', request: ask })).rejects.toThrow(
      BudgetExceededError,
    );
    expect(anthropic.calls).toHaveLength(0);
  });

  it('explains the stop to the producer', () => {
    spend('p1', 12.5);
    expect(() => assertBudget(db, testConfig(), 'p1')).toThrow(/потрачено \$12\.50 из \$10\.00.*решение продюсера/);
  });

  it('warns at 80% of the limit', async () => {
    spend('p1', 7.9);
    expect(budgetStatus(db, testConfig(), 'p1').warning).toBe(false);
    spend('p1', 0.1);
    const res = await client().complete({ role: 'architect', projectId: 'p1', request: ask });
    expect(res.budget).toMatchObject({ warning: true, exceeded: false, limitUsd: 10 });
  });

  it('counts only the given project', async () => {
    spend('other', 100);
    await expect(client().complete({ role: 'architect', projectId: 'p1', request: ask })).resolves.toBeDefined();
  });

  it('uses a raised limit from the project', async () => {
    spend('p1', 10);
    db.insert(projects).values({ id: 'p1', title: 'Сезон', budgetLimitUsd: 20 }).run();
    await expect(client().complete({ role: 'architect', projectId: 'p1', request: ask })).resolves.toBeDefined();
  });

  it('summarises costs by role and step', async () => {
    const c = client();
    await c.complete({ role: 'architect', projectId: 'p1', step: 'bible', request: ask });
    await c.complete({ role: 'helper', projectId: 'p1', step: 'bible', request: ask });
    const s = costSummary(db, testConfig(), 'p1');
    expect(s.calls).toBe(2);
    expect(s.byRole.map((r) => r.role).sort()).toEqual(['architect', 'helper']);
    expect(s.byStep).toMatchObject([{ step: 'bible', calls: 2 }]);
    expect(s.totalUsd).toBeCloseTo(s.byRole.reduce((n, r) => n + r.costUsd, 0), 9);
  });
});

describe('writer input limit', () => {
  const big = { messages: [{ role: 'user' as const, content: 'x'.repeat(5000) }] };

  it('counts tokens before the writer call and shrinks an oversized input', async () => {
    const shrinks: number[] = [];
    const res = await client().complete({
      role: 'writer',
      request: big,
      shrink: (req, tokens, limit) => {
        shrinks.push(tokens);
        expect(limit).toBe(1000);
        return { messages: [{ role: 'user', content: 'сжатая библия' }] };
      },
    });
    expect(res.provider).toBe('google');
    expect(shrinks).toHaveLength(1);
    expect(google.calls[0]?.req.messages[0]?.content).toBe('сжатая библия');
  });

  it('refuses to send an oversized input it cannot shrink', async () => {
    await expect(client().complete({ role: 'writer', request: big })).rejects.toThrow(InputTooLargeError);
    expect(google.calls).toHaveLength(0);
  });

  it('gives up after several shrink attempts', async () => {
    await expect(client().complete({ role: 'writer', request: big, shrink: (r) => r })).rejects.toThrow(
      InputTooLargeError,
    );
  });

  it('does not count tokens for roles without a limit', async () => {
    await client().complete({ role: 'architect', request: big });
    expect(anthropic.calls).toHaveLength(1);
  });
});

describe('JSON answers', () => {
  const Answer = z.object({ ok: z.boolean() });

  it('accepts valid JSON, including fenced', async () => {
    anthropic = new FakeProvider('anthropic', ['```json\n{"ok": true}\n```']);
    const res = await client().completeJson(Answer, { role: 'architect', request: ask });
    expect(res.data).toEqual({ ok: true });
  });

  it('retries once with the error text', async () => {
    anthropic = new FakeProvider('anthropic', ['{"ok": "да"}', '{"ok": true}']);
    const res = await client().completeJson(Answer, { role: 'architect', request: ask });
    expect(res.data).toEqual({ ok: true });
    const retryMsgs = anthropic.calls[1]!.req.messages;
    expect(retryMsgs.at(-2)).toEqual({ role: 'assistant', content: '{"ok": "да"}' });
    expect(retryMsgs.at(-1)?.content).toMatch(/Ответ не прошёл проверку:\n- ok:/);
    expect(rows().map((r) => r.status)).toEqual(['invalid_output', 'ok']);
  });

  it('gives up after the second invalid answer', async () => {
    anthropic = new FakeProvider('anthropic', ['не json']);
    await expect(client().completeJson(Answer, { role: 'architect', request: ask })).rejects.toThrow(InvalidOutputError);
    expect(anthropic.calls).toHaveLength(2);
    expect(rows().every((r) => r.status === 'invalid_output')).toBe(true);
  });

  it('extracts JSON surrounded by prose', () => {
    expect(extractJson('Вот ответ: {"a": [1, 2]} Готово.')).toEqual({ a: [1, 2] });
    expect(() => extractJson('без данных')).toThrow(SyntaxError);
  });
});

describe('database', () => {
  it('stores and reads projects', () => {
    db.insert(projects).values({ id: 'p1', title: 'Муж женился ради крови' }).run();
    const row = db.select().from(projects).where(eq(projects.id, 'p1')).get();
    expect(row?.title).toBe('Муж женился ради крови');
    expect(row?.createdAt).toBeInstanceOf(Date);
  });
});
