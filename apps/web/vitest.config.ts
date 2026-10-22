import { defineProject } from 'vitest/config';

export default defineProject({
  test: { name: 'web', testTimeout: 30_000, environment: 'node' },
});
