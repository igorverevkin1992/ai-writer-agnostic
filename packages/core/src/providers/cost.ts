import type { Price } from './config.ts';
import type { Usage } from './types.ts';

const LONG_CONTEXT_THRESHOLD = 200_000;
const CACHE_WRITE_MULTIPLIER = 1.25;

/** Cost of one call in USD. Prices are per million tokens. */
export function computeCost(price: Price, usage: Usage): number {
  const promptTokens = usage.inputTokens + usage.cacheReadTokens + usage.cacheWriteTokens;
  const long = promptTokens > LONG_CONTEXT_THRESHOLD;
  const inRate = long && price.in_over_200k !== undefined ? price.in_over_200k : price.in;
  const outRate = long && price.out_over_200k !== undefined ? price.out_over_200k : price.out;
  const cacheReadRate = price.cache_read ?? inRate;
  const cacheWriteRate = price.cache_write ?? inRate * CACHE_WRITE_MULTIPLIER;
  const micro =
    usage.inputTokens * inRate +
    usage.cacheReadTokens * cacheReadRate +
    usage.cacheWriteTokens * cacheWriteRate +
    usage.outputTokens * outRate;
  return micro / 1_000_000;
}
