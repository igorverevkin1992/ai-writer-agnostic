import { eq } from 'drizzle-orm';
import type { z } from 'zod';
import type { Db } from '../db/client.ts';
import { llmCalls } from '../db/schema.ts';
import { AnthropicProvider } from './anthropic.ts';
import { assertBudget, budgetStatus, type BudgetStatus } from './budget.ts';
import type { ModelsConfig, ProviderName, RoleName } from './config.ts';
import { computeCost } from './cost.ts';
import {
  InputTooLargeError,
  InvalidOutputError,
  LlmError,
  MissingKeyError,
  OutputTruncatedError,
  ProviderUnavailableError,
  RefusalError,
  SameFamilyError,
} from './errors.ts';
import { assertCrossFamily } from './family.ts';
import { GoogleProvider } from './google.ts';
import { extractJson } from './json.ts';
import { OpenAiCompatibleProvider } from './openaiCompatible.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult, Usage } from './types.ts';

const CRITIC_ROLES: ReadonlySet<RoleName> = new Set(['critic_of_architect', 'critic_of_writer']);
const MAX_SHRINK_ATTEMPTS = 3;

export interface CallOptions {
  role: RoleName;
  request: LlmRequest;
  projectId?: string;
  step?: string;
  /**
   * Provider(s) that wrote the text under review. Required for critic roles.
   * Several when parts were written by different providers (e.g. the reserve one).
   */
  authorProvider?: ProviderName | readonly ProviderName[];
  /**
   * Called when the input exceeds the role's max_input (writer: 200k tokens).
   * Must return a smaller request, e.g. with a compressed bible.
   */
  shrink?: (request: LlmRequest, tokens: number, limit: number) => LlmRequest | Promise<LlmRequest>;
}

export interface CallResult {
  /** Row id in llm_calls. */
  callId: number;
  text: string;
  provider: ProviderName;
  model: string;
  usage: Usage;
  costUsd: number;
  fallbackUsed: boolean;
  budget?: BudgetStatus;
}

interface Resolved {
  provider: ProviderName;
  target: CallTarget;
  maxInput?: number;
}

export interface LlmClientDeps {
  config: ModelsConfig;
  db: Db;
  /** Injected in tests. Defaults to the real SDK-backed providers. */
  providers?: Partial<Record<ProviderName, Provider>>;
  env?: NodeJS.ProcessEnv;
  now?: () => number;
}

export class LlmClient {
  private readonly config: ModelsConfig;
  private readonly db: Db;
  private readonly providers: Record<ProviderName, Provider>;
  private readonly env: NodeJS.ProcessEnv;
  private readonly now: () => number;

  constructor({ config, db, providers = {}, env = process.env, now = Date.now }: LlmClientDeps) {
    this.config = config;
    this.db = db;
    this.env = env;
    this.now = now;
    this.providers = {
      anthropic: providers.anthropic ?? new AnthropicProvider(env),
      google: providers.google ?? new GoogleProvider(env),
      openai_compatible:
        providers.openai_compatible ??
        new OpenAiCompatibleProvider(env, config.fallback?.any.base_url_env ?? 'RESERVE_BASE_URL'),
    };
  }

  resolve(role: RoleName): Resolved {
    const rc = this.config.roles[role];
    return {
      provider: rc.provider,
      target: { model: rc.model, maxOutput: rc.max_output, refusalFallback: rc.refusal_fallback },
      maxInput: rc.max_input,
    };
  }

  /** The reserve provider, if configured in models.yaml and .env. */
  resolveFallback(role: RoleName): Resolved | undefined {
    const fb = this.config.fallback?.any;
    const model = fb && this.env[fb.model_env];
    if (!fb || !model) return undefined;
    return { provider: fb.provider, target: { model, maxOutput: this.config.roles[role].max_output } };
  }

  async complete(opts: CallOptions): Promise<CallResult> {
    const primary = this.resolve(opts.role);
    this.checkFamily(opts, primary.provider);
    if (opts.projectId) assertBudget(this.db, this.config, opts.projectId);

    // Token counting goes to the provider too: when it is down, the reserve takes over here as well.
    const attempt = async (r: Resolved, isFallback: boolean) => this.callLogged(opts, r, await this.fitInput(opts, r), isFallback);

    let result: ProviderResult & { callId: number };
    let used = primary;
    let fallbackUsed = false;
    try {
      result = await attempt(primary, false);
    } catch (err) {
      const fallback = this.resolveFallback(opts.role);
      const canFallback = err instanceof ProviderUnavailableError || err instanceof MissingKeyError;
      if (!fallback || !canFallback || fallback.provider === primary.provider) throw err;
      this.checkFamily(opts, fallback.provider);
      used = fallback;
      fallbackUsed = true;
      try {
        result = await attempt(fallback, true);
      } catch (reserveErr) {
        throw new ProviderUnavailableError(
          primary.provider,
          new Error(`${(err as Error).message}. Резерв тоже не ответил: ${(reserveErr as Error).message}`),
        );
      }
    }

    return {
      callId: result.callId,
      text: result.text,
      provider: used.provider,
      model: result.model,
      usage: result.usage,
      costUsd: this.cost(result.model, used.target.model, result.usage).costUsd,
      fallbackUsed,
      budget: opts.projectId ? budgetStatus(this.db, this.config, opts.projectId) : undefined,
    };
  }

  /**
   * Calls the model and validates the JSON answer with a Zod schema.
   * On failure: one retry with the error text, then an error for the producer.
   */
  async completeJson<T extends z.ZodType>(
    schema: T,
    opts: CallOptions,
  ): Promise<CallResult & { data: z.infer<T> }> {
    const first = await this.complete(opts);
    const firstCheck = validate(schema, first.text);
    if (firstCheck.ok) return { ...first, data: firstCheck.data };
    this.markInvalid(first.callId);

    const retry: CallOptions = {
      ...opts,
      request: {
        ...opts.request,
        messages: [
          ...opts.request.messages,
          { role: 'assistant', content: first.text },
          {
            role: 'user',
            content:
              `Ответ не прошёл проверку:\n${firstCheck.error}\n` +
              'Пришли исправленный ответ целиком. Только JSON, без пояснений.',
          },
        ],
      },
    };
    const second = await this.complete(retry);
    const secondCheck = validate(schema, second.text);
    if (secondCheck.ok) return { ...second, data: secondCheck.data };
    this.markInvalid(second.callId);
    throw new InvalidOutputError(
      `Модель дважды прислала ответ не по форме (роль ${opts.role}). Ошибки:\n${secondCheck.error}`,
    );
  }

  private checkFamily(opts: CallOptions, criticProvider: ProviderName): void {
    if (CRITIC_ROLES.has(opts.role) && authorsOf(opts.authorProvider).length === 0) {
      throw new SameFamilyError(
        'Проверка отменена: не указано, какая модель написала текст. Без этого нельзя убедиться, что проверяющий из другого семейства.',
      );
    }
    for (const author of authorsOf(opts.authorProvider)) assertCrossFamily(author, criticProvider);
  }

  private async fitInput(opts: CallOptions, resolved: Resolved): Promise<LlmRequest> {
    const limit = resolved.maxInput;
    if (!limit) return opts.request;
    const provider = this.providers[resolved.provider];
    let request = opts.request;
    for (let attempt = 0; ; attempt++) {
      const tokens = await provider.countTokens(request, resolved.target.model);
      if (tokens <= limit) return request;
      if (!opts.shrink || attempt >= MAX_SHRINK_ATTEMPTS) {
        throw new InputTooLargeError(
          `Вход для роли ${opts.role} — ${tokens} токенов, лимит ${limit}. Нужно сократить библию или контекст.`,
        );
      }
      request = await opts.shrink(request, tokens, limit);
    }
  }

  private async callLogged(
    opts: CallOptions,
    resolved: Resolved,
    request: LlmRequest,
    fallbackUsed: boolean,
  ): Promise<ProviderResult & { callId: number }> {
    const started = this.now();
    const base = {
      projectId: opts.projectId ?? null,
      step: opts.step ?? null,
      role: opts.role,
      provider: resolved.provider,
      fallbackUsed,
    };
    try {
      const result = await this.providers[resolved.provider].complete(request, resolved.target);
      const { costUsd, priceKnown } = this.cost(result.model, resolved.target.model, result.usage);
      const row = this.db
        .insert(llmCalls)
        .values({
          ...base,
          model: result.model,
          inputTokens: result.usage.inputTokens,
          outputTokens: result.usage.outputTokens,
          cacheReadTokens: result.usage.cacheReadTokens,
          cacheWriteTokens: result.usage.cacheWriteTokens,
          costUsd,
          priceKnown,
          durationMs: this.now() - started,
          status: 'ok',
        })
        .returning({ id: llmCalls.id })
        .get();
      return { ...result, callId: row.id };
    } catch (err) {
      // A refused or truncated answer is still billed: its tokens count towards the budget.
      const billed = err instanceof RefusalError || err instanceof OutputTruncatedError ? err.billed : undefined;
      const cost = billed ? this.cost(billed.model, resolved.target.model, billed.usage) : undefined;
      this.db
        .insert(llmCalls)
        .values({
          ...base,
          model: billed?.model ?? resolved.target.model,
          ...(billed && cost
            ? {
                inputTokens: billed.usage.inputTokens,
                outputTokens: billed.usage.outputTokens,
                cacheReadTokens: billed.usage.cacheReadTokens,
                cacheWriteTokens: billed.usage.cacheWriteTokens,
                costUsd: cost.costUsd,
                priceKnown: cost.priceKnown,
              }
            : {}),
          durationMs: this.now() - started,
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
        })
        .run();
      throw err instanceof LlmError ? err : new ProviderUnavailableError(resolved.provider, err);
    }
  }

  /**
   * Prices by the model that served the call; falls back to the requested model's price.
   * Without a known price the most expensive known one is used, so the budget never
   * counts an unknown model as free (priceKnown: false marks the estimate).
   */
  private cost(servedModel: string, requestedModel: string, usage: Usage): { costUsd: number; priceKnown: boolean } {
    const prices = this.config.prices_usd_per_mtok;
    const price = prices[servedModel] ?? prices[requestedModel];
    if (price) return { costUsd: computeCost(price, usage), priceKnown: true };
    const estimates = Object.values(prices).map((p) => computeCost(p, usage));
    return { costUsd: estimates.length ? Math.max(...estimates) : 0, priceKnown: false };
  }

  private markInvalid(callId: number): void {
    this.db.update(llmCalls).set({ status: 'invalid_output' }).where(eq(llmCalls.id, callId)).run();
  }
}

function authorsOf(a: CallOptions['authorProvider']): readonly ProviderName[] {
  return a === undefined ? [] : typeof a === 'string' ? [a] : a;
}

function validate<T extends z.ZodType>(
  schema: T,
  text: string,
): { ok: true; data: z.infer<T> } | { ok: false; error: string } {
  let raw: unknown;
  try {
    raw = extractJson(text);
  } catch (err) {
    return { ok: false, error: `Это не JSON: ${(err as Error).message}` };
  }
  const res = schema.safeParse(raw);
  if (res.success) return { ok: true, data: res.data };
  return {
    ok: false,
    error: res.error.issues.map((i) => `- ${i.path.join('.') || '(корень)'}: ${i.message}`).join('\n'),
  };
}
