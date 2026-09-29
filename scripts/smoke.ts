// Real API smoke test: one call per role, checks keys and model ids.
// Run manually: pnpm smoke. Costs a few cents.
import { resolve } from 'node:path';
import { z } from 'zod';
import {
  LlmClient,
  OpenAiCompatibleProvider,
  REPO_ROOT,
  ROLE_NAMES,
  loadDotEnv,
  loadModelsConfig,
  openDb,
  type ProviderName,
  type RoleName,
} from '@aiw/core';

loadDotEnv();
const config = loadModelsConfig();
const db = openDb(resolve(REPO_ROOT, 'data/app.db'));
const llm = new LlmClient({ config, db });

const Pong = z.object({ ok: z.literal(true) });
const request = {
  system: 'Ты проверяешь связь. Отвечай только JSON.',
  messages: [{ role: 'user' as const, content: 'Ответь строго так: {"ok": true}' }],
};

/** Critics need an author from another family: use the provider of the role they check. */
const AUTHOR_OF: Partial<Record<RoleName, ProviderName>> = {
  critic_of_architect: config.roles.architect.provider,
  critic_of_writer: config.roles.writer.provider,
};

let failed = 0;
console.log('Проверка связи с моделями (по одному вызову на роль)\n');

for (const role of ROLE_NAMES) {
  const { provider, model } = config.roles[role];
  const started = Date.now();
  try {
    const res = await llm.completeJson(Pong, {
      role,
      projectId: 'smoke',
      step: 'smoke',
      authorProvider: AUTHOR_OF[role],
      request,
    });
    if (res.fallbackUsed) {
      // The main model did not answer: the key or the model id is wrong, even though work goes on.
      failed++;
      console.log(`✗ ${role.padEnd(20)} ${provider}/${model} — не ответила, ответил резерв ${res.model}`);
      continue;
    }
    const note = res.model !== model ? ` (ответила ${res.model})` : '';
    console.log(`✓ ${role.padEnd(20)} ${provider}/${model}${note} — ${Date.now() - started} мс, $${res.costUsd.toFixed(4)}`);
  } catch (err) {
    failed++;
    console.log(`✗ ${role.padEnd(20)} ${provider}/${model} — ${(err as Error).message}`);
  }
}

const fb = config.fallback?.any;
const reserveModel = fb && process.env[fb.model_env];
if (fb && reserveModel) {
  const reserve = new OpenAiCompatibleProvider(process.env, fb.base_url_env);
  try {
    await reserve.complete(request, { model: reserveModel, maxOutput: 1000 });
    console.log(`✓ ${'резерв'.padEnd(20)} ${fb.provider}/${reserveModel}`);
  } catch (err) {
    failed++;
    console.log(`✗ ${'резерв'.padEnd(20)} ${fb.provider}/${reserveModel} — ${(err as Error).message}`);
  }
} else {
  console.log(`– ${'резерв'.padEnd(20)} не настроен (${fb?.model_env ?? 'RESERVE_MODEL'} в .env пуст)`);
}

console.log(failed ? `\nНе прошло: ${failed}. Проверьте ключи в .env и названия моделей в config/models.yaml.` : '\nВсе роли отвечают.');
process.exit(failed ? 1 : 0);
