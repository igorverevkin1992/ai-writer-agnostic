import Anthropic from '@anthropic-ai/sdk';
import { LlmError, MissingKeyError, OutputTruncatedError, ProviderUnavailableError, RefusalError } from './errors.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './types.ts';

const REFUSAL_FALLBACK_BETA = 'server-side-fallback-2026-07-01';

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

  constructor(private readonly env: NodeJS.ProcessEnv = process.env) {}

  private sdk(): Anthropic {
    if (!this.env.ANTHROPIC_API_KEY) throw new MissingKeyError('ANTHROPIC_API_KEY');
    this.client ??= new Anthropic({
      apiKey: this.env.ANTHROPIC_API_KEY,
      ...(this.env.ANTHROPIC_BASE_URL ? { baseURL: this.env.ANTHROPIC_BASE_URL } : {}),
    });
    return this.client;
  }

  async complete(req: LlmRequest, { model, maxOutput, refusalFallback }: CallTarget): Promise<ProviderResult> {
    const sdk = this.sdk();
    let msg: Anthropic.Beta.BetaMessage;
    try {
      msg = await sdk.beta.messages
        .stream({
          model,
          max_tokens: maxOutput,
          system: systemBlocks(req),
          messages: req.messages,
          ...(refusalFallback ? { betas: [REFUSAL_FALLBACK_BETA], fallbacks: refusalFallback } : {}),
        })
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

function wrap(err: unknown): Error {
  if (err instanceof LlmError) return err;
  if (err instanceof Anthropic.BadRequestError) return new LlmError(`Anthropic отклонил запрос: ${err.message}`);
  return new ProviderUnavailableError('anthropic', err);
}
