import { defineProject } from 'vitest/config';

export default defineProject({
  test: { name: 'kb', testTimeout: 30_000, environment: 'node' },
});
