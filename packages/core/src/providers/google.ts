import { ApiError, FinishReason, GoogleGenAI, type Content } from '@google/genai';
import { LlmError, MissingKeyError, OutputTruncatedError, ProviderUnavailableError, RefusalError } from './errors.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './types.ts';

function systemText(req: LlmRequest): string {
  return [...(req.cacheablePrefix ?? []), req.system ?? ''].filter(Boolean).join('\n\n');
}

function contents(req: LlmRequest): Content[] {
  return req.messages.map((m) => ({ role: m.role === 'assistant' ? 'model' : 'user', parts: [{ text: m.content }] }));
}

const REFUSAL_REASONS = new Set<string>([
  FinishReason.SAFETY,
  FinishReason.PROHIBITED_CONTENT,
  FinishReason.BLOCKLIST,
  FinishReason.SPII,
  FinishReason.RECITATION,
]);

export class GoogleProvider implements Provider {
  readonly name = 'google' as const;
  private client: GoogleGenAI | undefined;

  constructor(private readonly env: NodeJS.ProcessEnv = process.env) {}

  private sdk(): GoogleGenAI {
    if (!this.env.GEMINI_API_KEY) throw new MissingKeyError('GEMINI_API_KEY');
    this.client ??= new GoogleGenAI({
      apiKey: this.env.GEMINI_API_KEY,
      // Retries on rate limits and 5xx; without it one hiccup fails a whole step.
      httpOptions: { retryOptions: { attempts: 3 }, ...(this.env.GEMINI_BASE_URL ? { baseUrl: this.env.GEMINI_BASE_URL } : {}) },
    });
    return this.client;
  }

  async complete(req: LlmRequest, { model, maxOutput }: CallTarget): Promise<ProviderResult> {
    const sdk = this.sdk();
    const system = systemText(req);
    let res;
    try {
      res = await sdk.models.generateContent({
        model,
        contents: contents(req),
        config: {
          ...(system ? { systemInstruction: system } : {}),
          maxOutputTokens: maxOutput,
          responseMimeType: 'application/json',
        },
      });
    } catch (err) {
      throw wrap(err);
    }

    const u = res.usageMetadata ?? {};
    const cached = u.cachedContentTokenCount ?? 0;
    const usage = {
      inputTokens: (u.promptTokenCount ?? 0) - cached,
      // Thinking tokens are billed as output.
      outputTokens: (u.candidatesTokenCount ?? 0) + (u.thoughtsTokenCount ?? 0),
      cacheReadTokens: cached,
      cacheWriteTokens: 0,
    };
    const served = res.modelVersion ?? model;
    const billed = { model: served, usage };

    const finish = res.candidates?.[0]?.finishReason;
    if (res.promptFeedback?.blockReason || (finish && REFUSAL_REASONS.has(finish))) {
      throw new RefusalError(`Модель ${model} отказалась отвечать (${res.promptFeedback?.blockReason ?? finish})`, billed);
    }
    if (finish === FinishReason.MAX_TOKENS) throw new OutputTruncatedError(model, maxOutput, billed);

    return { text: res.text ?? '', model: served, usage };
  }

  async countTokens(req: LlmRequest, model: string): Promise<number> {
    const system = systemText(req);
    const all: Content[] = system ? [{ role: 'user', parts: [{ text: system }] }, ...contents(req)] : contents(req);
    try {
      const res = await this.sdk().models.countTokens({ model, contents: all });
      return res.totalTokens ?? 0;
    } catch (err) {
      throw wrap(err);
    }
  }
}

function wrap(err: unknown): Error {
  if (err instanceof LlmError) return err;
  if (err instanceof ApiError && err.status === 400) return new LlmError(`Google отклонил запрос: ${err.message}`);
  return new ProviderUnavailableError('google', err);
}
