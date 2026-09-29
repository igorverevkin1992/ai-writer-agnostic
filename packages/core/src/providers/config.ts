import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';
import { z } from 'zod';

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
export const DEFAULT_MODELS_CONFIG = resolve(REPO_ROOT, 'config/models.yaml');

export const ProviderName = z.enum(['anthropic', 'google', 'openai_compatible']);
export type ProviderName = z.infer<typeof ProviderName>;

export const ROLE_NAMES = [
  'architect',
  'architect_heavy',
  'writer',
  'critic_of_architect',
  'critic_of_writer',
  'helper',
] as const;
export const RoleName = z.enum(ROLE_NAMES);
export type RoleName = z.infer<typeof RoleName>;

const Tokens = z.int().positive();

const RoleConfig = z.strictObject({
  provider: ProviderName,
  model: z.string().min(1),
  max_output: Tokens.default(16000),
  max_input: Tokens.optional(),
  /** Anthropic only: server-side refusal fallback to another Claude model. */
  refusal_fallback: z.literal('default').optional(),
});
export type RoleConfig = z.infer<typeof RoleConfig>;

const FallbackConfig = z.strictObject({
  provider: ProviderName,
  base_url_env: z.string().min(1).optional(),
  model_env: z.string().min(1),
});
export type FallbackConfig = z.infer<typeof FallbackConfig>;

const Price = z.strictObject({
  in: z.number().min(0),
  out: z.number().min(0),
  cache_read: z.number().min(0).optional(),
  /** Defaults to 1.25 × in (5-minute prompt cache write). */
  cache_write: z.number().min(0).optional(),
  in_over_200k: z.number().min(0).optional(),
  out_over_200k: z.number().min(0).optional(),
});
export type Price = z.infer<typeof Price>;

export const ModelsConfig = z
  .strictObject({
    roles: z.record(RoleName, RoleConfig),
    fallback: z.strictObject({ any: FallbackConfig }).optional(),
    budget: z.strictObject({
      project_limit_usd: z.number().positive(),
      warn_at: z.number().gt(0).max(1),
    }),
    prices_usd_per_mtok: z.record(z.string(), Price),
  })
  .superRefine((cfg, ctx) => {
    for (const [role, rc] of Object.entries(cfg.roles)) {
      if (!cfg.prices_usd_per_mtok[rc.model]) {
        ctx.addIssue({
          code: 'custom',
          path: ['prices_usd_per_mtok', rc.model],
          message: `Нет цены для модели ${rc.model} (роль ${role})`,
        });
      }
    }
  });
export type ModelsConfig = z.infer<typeof ModelsConfig>;

export class ConfigError extends Error {
  override name = 'ConfigError';
}

export function parseModelsConfig(raw: unknown, source = 'config/models.yaml'): ModelsConfig {
  const res = ModelsConfig.safeParse(raw);
  if (!res.success) {
    const lines = res.error.issues.map((i) => `  • ${i.path.join('.') || '(корень)'}: ${i.message}`);
    throw new ConfigError([`Файл ${source} заполнен с ошибками:`, ...lines].join('\n'));
  }
  return res.data;
}

export function loadModelsConfig(path = DEFAULT_MODELS_CONFIG): ModelsConfig {
  if (!existsSync(path)) throw new ConfigError(`Не найден файл настроек моделей: ${path}`);
  let raw: unknown;
  try {
    raw = parse(readFileSync(path, 'utf8'));
  } catch (err) {
    throw new ConfigError(`Файл ${path} не читается как YAML: ${(err as Error).message}`);
  }
  return parseModelsConfig(raw, path);
}

/** Loads .env from the repo root if present. Keys stay out of the code. */
export function loadDotEnv(path = resolve(REPO_ROOT, '.env')): void {
  if (existsSync(path)) process.loadEnvFile(path);
}
