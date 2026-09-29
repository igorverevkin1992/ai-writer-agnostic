import { describe, expect, it } from 'vitest';
import { STEPS } from './steps.ts';

describe('web steps', () => {
  it('has a Russian label for every pipeline step', () => {
    expect(STEPS).toHaveLength(8);
    for (const step of STEPS) expect(step.label.length).toBeGreaterThan(0);
  });
});
