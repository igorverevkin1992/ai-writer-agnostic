import { describe, expect, it } from 'vitest';
import { concurrencyFromEnv, mapLimit } from './mapLimit.ts';

describe('mapLimit', () => {
  it('keeps order and never exceeds the limit', async () => {
    let inFlight = 0;
    let peak = 0;
    const out = await mapLimit([30, 10, 20, 5], 2, async (ms) => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, ms));
      inFlight--;
      return ms * 2;
    });
    expect(out).toEqual([60, 20, 40, 10]);
    expect(peak).toBe(2);
  });

  it('handles an empty list and reads the limit from the environment', async () => {
    expect(await mapLimit([], 4, async () => 1)).toEqual([]);
    expect(concurrencyFromEnv({ AIW_CONCURRENCY: '7' })).toBe(7);
    expect(concurrencyFromEnv({ AIW_CONCURRENCY: 'x' })).toBe(4);
    expect(concurrencyFromEnv({})).toBe(4);
  });
});
