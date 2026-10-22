import { defineProject } from 'vitest/config';

export default defineProject({
  test: { name: 'server', testTimeout: 30_000, environment: 'node', env: { AIW_RETRY_DELAYS_MS: '' } },
});
