import { createHash } from 'node:crypto';
import { eq } from 'drizzle-orm';
import type { z } from 'zod';
import type { Db } from '../db/client.ts';
import { llmCache, llmCalls } from '../db/schema.ts';
import { AnthropicProvider } from './anthropic.ts';
import { assertBudget, budgetStatus, type BudgetStatus } from './budget.ts';
import type { ModelsConfig, ProviderName, RoleName } from './config.ts';
import { computeCost } from './cost.ts';
import {
  InputTooLargeError,
  InvalidOutputError,
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
  /** The request as sent, after shrinking to fit the input limit. */
  request: LlmRequest;
}

interface Resolved {
  provider: ProviderName;
  target: CallTarget;
  maxInput?: number;
  /** For the reserve: the role's primary model, whose price is used when the reserve model has none. */
  primaryModel?: string;
}

/** One finished model call, successful or not: for progress output in scripts. */
export interface CallLog {
  task?: string;
  role: RoleName;
  provider: ProviderName;
  model: string;
  durationMs: number;
  costUsd: number;
  /** `invalid` and `truncated`: the answer is being asked for again (shows why the step takes longer). */
  status: 'start' | 'ok' | 'error' | 'invalid' | 'truncated' | 'cached';
  error?: string;
}

export interface LlmClientDeps {
  config: ModelsConfig;
  db: Db;
  /** Called after every model call; scripts print progress with it. */
  onCall?: (log: CallLog) => void;
  /**
   * Reuse validated answers to identical requests (eval, pilot): a run restarted after a crash
   * pays only for what it had not got yet. Off in the app: «переделать» must ask the model again.
   */
  cache?: boolean;
  /** Pauses between attempts after a network failure, in ms; tests pass an empty list. */
  retryDelaysMs?: readonly number[];
  sleep?: (ms: number) => Promise<void>;
  /** Injected in tests. Defaults to the real SDK-backed providers. */
  providers?: Partial<Record<ProviderName, Provider>>;
  env?: NodeJS.ProcessEnv;
  now?: () => number;
}

/**
 * A dropped connection is usually gone in seconds: two more tries before the reserve or an
 * error. AIW_RETRY_DELAYS_MS (comma-separated ms, empty — no retries) overrides it; tests set it empty.
 */
function defaultRetryDelays(env: NodeJS.ProcessEnv): number[] {
  const raw = env.AIW_RETRY_DELAYS_MS;
  if (raw === undefined) return [5_000, 20_000];
  if (raw.trim() === '') return [];
  return raw.split(',').map((x) => Number(x.trim())).filter((n) => Number.isFinite(n) && n >= 0);
}

export class LlmClient {
  private readonly config: ModelsConfig;
  private readonly db: Db;
  private readonly providers: Record<ProviderName, Provider>;
  private readonly env: NodeJS.ProcessEnv;
  private readonly now: () => number;
  private readonly onCall: ((log: CallLog) => void) | undefined;
  /** Estimated cost of calls in flight: counted against the budget until they are logged. */
  private reservedUsd = 0;
  private readonly cache: boolean;

  private readonly retryDelaysMs: readonly number[];
  private readonly sleep: (ms: number) => Promise<void>;

  constructor({
    config,
    db,
    providers = {},
    env = process.env,
    now = Date.now,
    onCall,
    cache = false,
    retryDelaysMs,
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  }: LlmClientDeps) {
    this.retryDelaysMs = retryDelaysMs ?? defaultRetryDelays(process.env);
    this.sleep = sleep;
    this.config = config;
    this.db = db;
    this.env = env;
    this.now = now;
    this.onCall = onCall;
    this.cache = cache;
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
      target: { model: rc.model, maxOutput: rc.max_output, refusalFallback: rc.refusal_fallback, thinking: rc.thinking },
      maxInput: rc.max_input,
    };
  }

  /** The reserve provider, if configured in models.yaml and .env. */
  resolveFallback(role: RoleName): Resolved | undefined {
    const fb = this.config.fallback?.any;
    const model = fb && this.env[fb.model_env];
    if (!fb || !model) return undefined;
    const rc = this.config.roles[role];
    return { provider: fb.provider, target: { model, maxOutput: rc.max_output }, primaryModel: rc.model };
  }

  async complete(opts: CallOptions): Promise<CallResult> {
    const primary = this.resolve(opts.role);
    this.checkFamily(opts, primary.provider);
    if (opts.projectId) assertBudget(this.db, this.config, opts.projectId, this.reservedUsd);

    // Token counting goes to the provider too: when it is down, the reserve takes over here as well.
    let sent = opts.request;
    const attempt = async (r: Resolved, isFallback: boolean) => {
      sent = await this.fitInput(opts, r);
      return this.callLogged(opts, r, sent, isFallback);
    };

    let result: ProviderResult & { callId: number };
    let used = primary;
    let fallbackUsed = false;
    try {
      result = await this.withRetries(() => attempt(primary, false));
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
      costUsd: this.cost(result.model, used, result.usage).costUsd,
      fallbackUsed,
      budget: opts.projectId ? budgetStatus(this.db, this.config, opts.projectId) : undefined,
      request: sent,
    };
  }

  /**
   * Calls the model and validates the JSON answer with a Zod schema.
   * On failure: one retry with the error text, then an error for the producer.
   * An answer cut off at the output limit is asked for again once, shorter.
   */
  async completeJson<T extends z.ZodType>(
    schema: T,
    opts: CallOptions,
  ): Promise<CallResult & { data: z.infer<T> }> {
    const key = this.cache ? cacheKey(opts, this.resolve(opts.role).target.model) : undefined;
    if (key) {
      const hit = this.cached(schema, opts, key);
      if (hit) return hit;
    }
    const remember = <R extends CallResult>(r: R): R => {
      if (key) this.db.insert(llmCache).values({ key, role: opts.role, model: r.model, task: opts.request.task ?? null, text: r.text }).onConflictDoNothing().run();
      return r;
    };
    const first = await this.completeOrShorter(opts);
    const firstCheck = validate(schema, first.text);
    if (firstCheck.ok) return remember({ ...first, data: firstCheck.data });
    this.markInvalid(first.callId);
    this.onCall?.({ task: first.request.task, role: opts.role, provider: first.provider, model: first.model, durationMs: 0, costUsd: 0, status: 'invalid' });

    // The retry continues the request as it was sent (already shrunk, if it had to be).
    // An empty answer is not echoed back: providers reject empty assistant turns.
    const retry: CallOptions = {
      ...opts,
      request: {
        ...first.request,
        messages: [
          ...first.request.messages,
          { role: 'assistant', content: first.text.trim() || '(пустой ответ)' },
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
    if (secondCheck.ok) return remember({ ...second, data: secondCheck.data });
    this.markInvalid(second.callId);
    throw new InvalidOutputError(
      `Модель дважды прислала ответ не по форме (роль ${opts.role}). Ошибки:\n${secondCheck.error}`,
    );
  }

  /**
   * One call; when the answer is cut off at the output limit, one more call asking for a
   * shorter answer. The cut-off text is not sent back: it is useless and costs input tokens.
   * A second cut-off is an error for the producer.
   */
  private async completeOrShorter(opts: CallOptions): Promise<CallResult> {
    try {
      return await this.complete(opts);
    } catch (err) {
      if (!(err instanceof OutputTruncatedError)) throw err;
      const primary = this.resolve(opts.role);
      this.onCall?.({ task: opts.request.task, role: opts.role, provider: primary.provider, model: primary.target.model, durationMs: 0, costUsd: 0, status: 'truncated' });
      const retry: CallOptions = {
        ...opts,
        request: {
          ...opts.request,
          messages: [
            ...opts.request.messages,
            {
              role: 'user',
              content: 'Ответ не поместился в лимит. Пришли тот же ответ короче: меньше слов в каждом поле, без повторов. Только JSON.',
            },
          ],
        },
      };
      return this.complete(retry);
    }
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
    this.onCall?.({ task: request.task, role: opts.role, provider: resolved.provider, model: resolved.target.model, durationMs: 0, costUsd: 0, status: 'start' });
    // The check and the reservation happen in one synchronous step, so two parallel calls
    // cannot both pass with the same spend; the reservation is released once the call is logged.
    if (opts.projectId) assertBudget(this.db, this.config, opts.projectId, this.reservedUsd);
    const reserved = this.estimateUsd(resolved);
    this.reservedUsd += reserved;
    try {
      const result = await this.providers[resolved.provider].complete(request, resolved.target);
      const { costUsd, priceKnown } = this.cost(result.model, resolved, result.usage);
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
      this.onCall?.({ task: request.task, role: opts.role, provider: resolved.provider, model: result.model, durationMs: this.now() - started, costUsd, status: 'ok' });
      return { ...result, callId: row.id };
    } catch (err) {
      // A refused or truncated answer is still billed: its tokens count towards the budget.
      const billed = err instanceof RefusalError || err instanceof OutputTruncatedError ? err.billed : undefined;
      const cost = billed ? this.cost(billed.model, resolved, billed.usage) : undefined;
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
      this.onCall?.({
        task: request.task,
        role: opts.role,
        provider: resolved.provider,
        model: billed?.model ?? resolved.target.model,
        durationMs: this.now() - started,
        costUsd: cost?.costUsd ?? 0,
        status: 'error',
        error: err instanceof Error ? err.message : String(err),
      });
      // Adapters turn SDK and network failures into LlmError. Anything else is a bug in our
      // code: it must surface, not quietly send the work to the reserve provider.
      throw err;
    } finally {
      this.reservedUsd -= reserved;
    }
  }

  /**
   * Prices by the model that served the call; falls back to the requested model's price,
   * then (for the reserve) to the role's primary model. Without any of those the most
   * expensive known price is used, so the budget never counts an unknown model as free
   * (priceKnown: false marks every estimate that is not the served or requested model's).
   */
  private cost(servedModel: string, resolved: Resolved, usage: Usage): { costUsd: number; priceKnown: boolean } {
    const prices = this.config.prices_usd_per_mtok;
    const price = prices[servedModel] ?? prices[resolved.target.model];
    if (price) return { costUsd: computeCost(price, usage), priceKnown: true };
    const primary = resolved.primaryModel ? prices[resolved.primaryModel] : undefined;
    if (primary) return { costUsd: computeCost(primary, usage), priceKnown: false };
    const estimates = Object.values(prices).map((p) => computeCost(p, usage));
    return { costUsd: estimates.length ? Math.max(...estimates) : 0, priceKnown: false };
  }

  /** The most a call can cost in output: the full max_output at the model's output rate. */
  private estimateUsd(resolved: Resolved): number {
    const usage: Usage = { inputTokens: 0, outputTokens: resolved.target.maxOutput, cacheReadTokens: 0, cacheWriteTokens: 0 };
    return this.cost(resolved.target.model, resolved, usage).costUsd;
  }

  /** The same call again after a network failure (fetch failed, timeout, 5xx after the SDK's own retries). */
  private async withRetries<R>(call: () => Promise<R>): Promise<R> {
    for (let i = 0; ; i++) {
      try {
        return await call();
      } catch (err) {
        if (!(err instanceof ProviderUnavailableError) || i >= this.retryDelaysMs.length) throw err;
        await this.sleep(this.retryDelaysMs[i]!);
      }
    }
  }

  /** A cached answer that still passes the schema, logged as a free call. */
  private cached<T extends z.ZodType>(schema: T, opts: CallOptions, key: string): (CallResult & { data: z.infer<T> }) | undefined {
    const row = this.db.select().from(llmCache).where(eq(llmCache.key, key)).get();
    if (!row) return undefined;
    const check = validate(schema, row.text);
    if (!check.ok) return undefined;
    const r = this.resolve(opts.role);
    const usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
    const logged = this.db
      .insert(llmCalls)
      .values({ projectId: opts.projectId ?? null, step: opts.step ?? null, role: opts.role, provider: r.provider, model: row.model, costUsd: 0, durationMs: 0, status: 'cached' })
      .returning({ id: llmCalls.id })
      .get();
    this.onCall?.({ task: opts.request.task, role: opts.role, provider: r.provider, model: row.model, durationMs: 0, costUsd: 0, status: 'cached' });
    return {
      callId: logged.id,
      text: row.text,
      provider: r.provider,
      model: row.model,
      usage,
      costUsd: 0,
      fallbackUsed: false,
      budget: opts.projectId ? budgetStatus(this.db, this.config, opts.projectId) : undefined,
      request: opts.request,
      data: check.data,
    };
  }

  private markInvalid(callId: number): void {
    this.db.update(llmCalls).set({ status: 'invalid_output' }).where(eq(llmCalls.id, callId)).run();
  }
}

function authorsOf(a: CallOptions['authorProvider']): readonly ProviderName[] {
  return a === undefined ? [] : typeof a === 'string' ? [a] : a;
}

/** The request fields that decide the answer; the project id and the step do not. */
function cacheKey(opts: CallOptions, model: string): string {
  const r = opts.request;
  return createHash('sha256')
    .update(JSON.stringify([opts.role, model, r.task ?? '', r.cacheablePrefix ?? [], r.system ?? '', r.messages]))
    .digest('hex');
}

/**
 * Models sometimes wrap a right answer: an object in a one-item array, or under a single key
 * («result», «verdict», «data»). These are unwrapped before the answer counts as malformed.
 */
function unwrapCandidates(raw: unknown): unknown[] {
  const out: unknown[] = [];
  if (Array.isArray(raw) && raw.length === 1) out.push(raw[0]);
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const values = Object.values(raw as Record<string, unknown>);
    if (values.length === 1 && values[0] && typeof values[0] === 'object') out.push(values[0]);
  }
  return out;
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
  for (const candidate of unwrapCandidates(raw)) {
    const again = schema.safeParse(candidate);
    if (again.success) return { ok: true, data: again.data };
  }
  return {
    ok: false,
    error: res.error.issues.map((i) => `- ${i.path.join('.') || '(корень)'}: ${i.message}`).join('\n'),
  };
}
