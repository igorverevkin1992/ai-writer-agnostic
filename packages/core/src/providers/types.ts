import type { ProviderName } from './config.ts';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** A provider-neutral request. */
export interface LlmRequest {
  /**
   * Stable prefix: knowledge base and bible. Sent first and marked cacheable
   * where the provider supports prompt caching.
   */
  cacheablePrefix?: string[];
  /** Instructions that change per step, after the cacheable prefix. */
  system?: string;
  messages: ChatMessage[];
}

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export interface ProviderResult {
  text: string;
  usage: Usage;
  /** The model that actually served the request (may differ after a fallback). */
  model: string;
}

export interface CallTarget {
  model: string;
  maxOutput: number;
  /** Anthropic only: server-side refusal fallback. */
  refusalFallback?: 'default';
}

export interface Provider {
  readonly name: ProviderName;
  complete(req: LlmRequest, target: CallTarget): Promise<ProviderResult>;
  countTokens(req: LlmRequest, model: string): Promise<number>;
}
