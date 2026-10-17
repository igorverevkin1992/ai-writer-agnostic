import Anthropic from '@anthropic-ai/sdk';
import { LlmError, MissingKeyError, OutputTruncatedError, ProviderUnavailableError, RefusalError } from './errors.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './types.ts';

const REFUSAL_FALLBACK_BETA = 'server-side-fallback-2026-07-01';
/** A whole streamed answer must arrive within this time; a stalled stream then goes to the reserve. */
const STALL_TIMEOUT_MS = 15 * 60 * 1000;

function systemBlocks(req: LlmRequest): Anthropic.Beta.BetaTextBlockParam[] {
  const prefix: Anthropic.Beta.BetaTextBlockParam[] = (req.cacheablePrefix ?? [])
    .filter((t) => t.length > 0)
    .map((text) => ({ type: 'text', text }));
  // One breakpoint after the stable prefix (knowledge base + bible): it is cached across calls.
  const last = prefix.at(-1);
  if (last) last.cache_control = { type: 'ephemeral' };
  if (req.system) prefix.push({ type: 'text', text: req.system });
  return prefix;
}

export class AnthropicProvider implements Provider {
  readonly name = 'anthropic' as const;
  private client: Anthropic | undefined;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly stallTimeoutMs = STALL_TIMEOUT_MS,
  ) {}

  private sdk(): Anthropic {
    if (!this.env.ANTHROPIC_API_KEY) throw new MissingKeyError('ANTHROPIC_API_KEY');
    this.client ??= new Anthropic({
      apiKey: this.env.ANTHROPIC_API_KEY,
      // The SDK timeout covers the time to the first byte and is retried; the abort signal on
      // the stream (below) covers the whole answer and is not.
      timeout: this.stallTimeoutMs,
      maxRetries: 2,
      ...(this.env.ANTHROPIC_BASE_URL ? { baseURL: this.env.ANTHROPIC_BASE_URL } : {}),
    });
    return this.client;
  }

  async complete(req: LlmRequest, { model, maxOutput, refusalFallback }: CallTarget): Promise<ProviderResult> {
    const sdk = this.sdk();
    let msg: Anthropic.Beta.BetaMessage;
    try {
      msg = await sdk.beta.messages
        .stream(
          {
            model,
            max_tokens: maxOutput,
            system: systemBlocks(req),
            messages: req.messages,
            ...(refusalFallback ? { betas: [REFUSAL_FALLBACK_BETA], fallbacks: refusalFallback } : {}),
          },
          { signal: AbortSignal.timeout(this.stallTimeoutMs) },
        )
        .finalMessage();
    } catch (err) {
      throw wrap(err);
    }

    // With server-side fallbacks the content may hold the declined model's partial output,
    // then a `fallback` block, then the serving model's answer: only the last part counts.
    const lastFallback = msg.content.findLastIndex((b) => b.type === 'fallback');
    const served = lastFallback >= 0 ? (msg.content[lastFallback] as Anthropic.Beta.BetaFallbackBlock).to.model : msg.model;
    const usage = {
      inputTokens: msg.usage.input_tokens,
      outputTokens: msg.usage.output_tokens,
      cacheReadTokens: msg.usage.cache_read_input_tokens ?? 0,
      cacheWriteTokens: msg.usage.cache_creation_input_tokens ?? 0,
    };
    const billed = { model: served, usage };

    if (msg.stop_reason === 'refusal') {
      throw new RefusalError(`Модель ${served} отказалась отвечать (${msg.stop_details?.category ?? 'без категории'})`, billed);
    }
    if (msg.stop_reason === 'max_tokens') throw new OutputTruncatedError(served, maxOutput, billed);
    if (msg.stop_reason === 'model_context_window_exceeded') {
      throw new OutputTruncatedError(served, maxOutput, billed, 'на пределе контекста модели');
    }

    const text = msg.content
      .slice(lastFallback + 1)
      .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === 'text')
      .map((b) => b.text)
      .join('');
    return { text, model: served, usage };
  }

  async countTokens(req: LlmRequest, model: string): Promise<number> {
    try {
      const res = await this.sdk().messages.countTokens({
        model,
        system: systemBlocks(req).map(({ type, text }) => ({ type, text })),
        messages: req.messages,
      });
      return res.input_tokens;
    } catch (err) {
      throw wrap(err);
    }
  }
}

/** Maps SDK and network failures to our errors. Exported for tests only. */
export function wrapAnthropicError(err: unknown): Error {
  if (err instanceof LlmError) return err;
  // A stalled stream (our abort signal) or a timeout: the provider is as good as down.
  if (err instanceof Anthropic.APIUserAbortError || err instanceof Anthropic.APIConnectionTimeoutError || isTimeout(err)) {
    return new ProviderUnavailableError('anthropic', new Error(`ответ не пришёл за отведённое время (${(err as Error).message})`));
  }
  // A 400 about the account (no credit) is not a bad request: another provider can do the work.
  if (err instanceof Anthropic.BadRequestError && !/credit balance|billing/iu.test(err.message)) {
    return new LlmError(`Anthropic отклонил запрос: ${err.message}`);
  }
  return new ProviderUnavailableError('anthropic', err);
}

const wrap = wrapAnthropicError;

function isTimeout(err: unknown): boolean {
  return err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
}
