import { defineProject } from 'vitest/config';

export default defineProject({
  test: { name: 'server', environment: 'node', env: { AIW_RETRY_DELAYS_MS: '' } },
});
