/** Default number of model calls in flight for independent passes (an audit's categories). */
export const DEFAULT_CONCURRENCY = 4;

/** Runs `fn` over `items` with at most `limit` in flight; results keep the items' order. */
export async function mapLimit<T, R>(items: readonly T[], limit: number, fn: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i]!, i);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker));
  return results;
}

/** Concurrency from the environment (AIW_CONCURRENCY), else the default. */
export function concurrencyFromEnv(env: NodeJS.ProcessEnv = process.env): number {
  const n = Number(env.AIW_CONCURRENCY);
  return Number.isInteger(n) && n > 0 ? n : DEFAULT_CONCURRENCY;
}
