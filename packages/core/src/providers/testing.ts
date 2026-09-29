/** Test helpers: fake providers and a small config. Never calls real APIs. */
import type { ProviderName } from './config.ts';
import { parseModelsConfig, type ModelsConfig } from './config.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './types.ts';

export function testConfig(overrides: Partial<Record<string, unknown>> = {}): ModelsConfig {
  return parseModelsConfig({
    roles: {
      architect: { provider: 'anthropic', model: 'a-big' },
      architect_heavy: { provider: 'anthropic', model: 'a-heavy' },
      writer: { provider: 'google', model: 'g-pro', max_input: 1000 },
      critic_of_architect: { provider: 'google', model: 'g-pro' },
      critic_of_writer: { provider: 'anthropic', model: 'a-big' },
      helper: { provider: 'anthropic', model: 'a-small' },
    },
    fallback: { any: { provider: 'openai_compatible', base_url_env: 'RESERVE_BASE_URL', model_env: 'RESERVE_MODEL' } },
    budget: { project_limit_usd: 10, warn_at: 0.8 },
    prices_usd_per_mtok: {
      'a-big': { in: 4, out: 20, cache_read: 0.2 },
      'a-heavy': { in: 10, out: 50, cache_read: 0.25 },
      'a-small': { in: 1, out: 5, cache_read: 0.1 },
      'g-pro': { in: 2, out: 12, in_over_200k: 4, out_over_200k: 18 },
    },
    ...overrides,
  });
}

type Reply = string | Error | ((req: LlmRequest, target: CallTarget) => string);

export class FakeProvider implements Provider {
  readonly calls: { req: LlmRequest; target: CallTarget }[] = [];
  private replies: Reply[];

  constructor(
    readonly name: ProviderName,
    replies: Reply[] = ['{"ok":true}'],
    private readonly tokens: (req: LlmRequest) => number = (req) =>
      JSON.stringify(req).length,
    private readonly usage = { inputTokens: 1000, outputTokens: 500, cacheReadTokens: 0, cacheWriteTokens: 0 },
  ) {
    this.replies = replies;
  }

  async complete(req: LlmRequest, target: CallTarget): Promise<ProviderResult> {
    this.calls.push({ req, target });
    const reply = this.replies.length > 1 ? this.replies.shift()! : this.replies[0]!;
    if (reply instanceof Error) throw reply;
    const text = typeof reply === 'function' ? reply(req, target) : reply;
    return { text, model: target.model, usage: { ...this.usage } };
  }

  async countTokens(req: LlmRequest): Promise<number> {
    return this.tokens(req);
  }
}
