import { describe, expect, it } from 'vitest';
import { STEP_IDS } from './index.ts';

describe('core', () => {
  it('declares the eight pipeline steps', () => {
    expect(STEP_IDS).toHaveLength(8);
    expect(STEP_IDS[0]).toBe('concept');
    expect(STEP_IDS.at(-1)).toBe('export');
  });
});
