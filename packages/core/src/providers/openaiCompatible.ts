import OpenAI from 'openai';
import { LlmError, MissingKeyError, OutputTruncatedError, ProviderUnavailableError, RefusalError } from './errors.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './types.ts';

/** Rough token estimate for providers without a counting endpoint. */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3);
}

/** Reserve provider: any OpenAI-compatible API (e.g. a Russian model). */
export class OpenAiCompatibleProvider implements Provider {
  readonly name = 'openai_compatible' as const;
  private client: OpenAI | undefined;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly baseUrlEnv = 'RESERVE_BASE_URL',
  ) {}

  private sdk(): OpenAI {
    if (!this.env.RESERVE_API_KEY) throw new MissingKeyError('RESERVE_API_KEY');
    const baseURL = this.env[this.baseUrlEnv];
    if (!baseURL) throw new MissingKeyError(this.baseUrlEnv);
    this.client ??= new OpenAI({ apiKey: this.env.RESERVE_API_KEY, baseURL });
    return this.client;
  }

  private messages(req: LlmRequest): OpenAI.ChatCompletionMessageParam[] {
    const system = [...(req.cacheablePrefix ?? []), req.system ?? ''].filter(Boolean).join('\n\n');
    return [...(system ? [{ role: 'system' as const, content: system }] : []), ...req.messages];
  }

  async complete(req: LlmRequest, { model, maxOutput }: CallTarget): Promise<ProviderResult> {
    const sdk = this.sdk();
    let res: OpenAI.ChatCompletion;
    try {
      res = await sdk.chat.completions.create({ model, messages: this.messages(req), max_tokens: maxOutput });
    } catch (err) {
      if (err instanceof OpenAI.BadRequestError) throw new LlmError(`Резервный поставщик отклонил запрос: ${err.message}`);
      throw new ProviderUnavailableError('openai_compatible', err);
    }
    const choice = res.choices[0];
    const cached = res.usage?.prompt_tokens_details?.cached_tokens ?? 0;
    const usage = {
      inputTokens: (res.usage?.prompt_tokens ?? 0) - cached,
      outputTokens: res.usage?.completion_tokens ?? 0,
      cacheReadTokens: cached,
      cacheWriteTokens: 0,
    };
    const served = res.model || model;
    if (choice?.finish_reason === 'content_filter') throw new RefusalError(`Модель ${model} отказалась отвечать`, { model: served, usage });
    if (choice?.finish_reason === 'length') throw new OutputTruncatedError(model, maxOutput, { model: served, usage });
    return { text: choice?.message.content ?? '', model: served, usage };
  }

  async countTokens(req: LlmRequest): Promise<number> {
    return estimateTokens(this.messages(req).map((m) => String(m.content)).join('\n'));
  }
}
