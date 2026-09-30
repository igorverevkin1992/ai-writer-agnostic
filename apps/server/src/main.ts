import { resolve } from 'node:path';
import { ConfigError, LlmClient, REPO_ROOT, demoProviders, loadDotEnv, loadModelsConfig, openDb, recoverStaleSteps } from '@aiw/core';
import { KbLoadError, loadKb } from '@aiw/kb';
import { buildApp } from './app.ts';

loadDotEnv();
const port = Number(process.env.SERVER_PORT ?? 3001);

let deps;
try {
  const config = loadModelsConfig();
  const demo = process.env.DEMO === '1' || process.argv.includes('--demo');
  const db = openDb(resolve(REPO_ROOT, demo ? 'data/demo.db' : 'data/app.db'));
  // Runs that were in progress when the app stopped will not finish: mark them.
  const stale = recoverStaleSteps(db);
  if (stale) console.log(`Прерванных шагов после перезапуска: ${stale}. Их можно запустить ещё раз.`);
  const kb = loadKb();
  const llm = new LlmClient({ config, db, ...(demo ? { providers: demoProviders(kb) } : {}) });
  deps = { kb, config, db, llm, demo };
  if (demo) console.log('Демо-режим: модели отвечают заготовками по эталону, ключи не нужны. База — data/demo.db.');
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
