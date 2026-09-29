import { resolve } from 'node:path';
import { ConfigError, LlmClient, REPO_ROOT, loadDotEnv, loadModelsConfig, openDb } from '@aiw/core';
import { KbLoadError, loadKb } from '@aiw/kb';
import { buildApp } from './app.ts';

loadDotEnv();
const port = Number(process.env.SERVER_PORT ?? 3001);

let deps;
try {
  const config = loadModelsConfig();
  const db = openDb(resolve(REPO_ROOT, 'data/app.db'));
  deps = { kb: loadKb(), config, db, llm: new LlmClient({ config, db }) };
} catch (err) {
  console.error(err instanceof KbLoadError || err instanceof ConfigError ? err.message : err);
  process.exit(1);
}

const app = buildApp(deps);

try {
  await app.listen({ port, host: '127.0.0.1' });
  console.log(`Сервер запущен: http://127.0.0.1:${port}`);
} catch (err) {
  console.error(err);
  process.exit(1);
}
