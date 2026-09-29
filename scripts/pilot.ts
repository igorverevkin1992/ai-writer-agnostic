// M5 acceptance: cards and scripts for the first episodes of the golden project, checklist,
// script metrics, production rating. Real API calls — run manually.
// Usage: pnpm pilot [--project=muzh_krov] [--genre=revenge_thriller] [--episodes=15] [--budget=40]
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { LlmClient, REPO_ROOT, costSummary, loadDotEnv, loadGolden, loadModelsConfig, openDb, renderPilotReport, runPilot } from '@aiw/core';
import { loadKb } from '@aiw/kb';

const arg = (name: string, fallback: string) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1] ?? fallback;
const project = arg('project', 'muzh_krov');
const episodes = Number(arg('episodes', '15'));
const budget = Number(arg('budget', '40'));
const genreId = arg('genre', 'revenge_thriller');

loadDotEnv();
const config = loadModelsConfig();
const keyOf = { anthropic: 'ANTHROPIC_API_KEY', google: 'GEMINI_API_KEY', openai_compatible: 'RESERVE_API_KEY' } as const;
const missing = [...new Set(Object.values(config.roles).map((r) => keyOf[r.provider]))].filter((k) => !process.env[k]);
if (missing.length) {
  console.error(`Нет ключей в .env: ${missing.join(', ')}. Пилот делает реальные вызовы моделей — добавьте ключи и запустите снова.`);
  process.exit(1);
}

const db = openDb(resolve(REPO_ROOT, 'data/app.db'));
const kb = loadKb();
console.log(`Пилот: ${project}, серии 1–${episodes}, лимит $${budget}…`);
try {
  const report = await runPilot({ db, llm: new LlmClient({ config, db }), kb }, loadGolden(project), {
    genreId,
    episodes,
    budgetLimitUsd: budget,
    title: `Пилот ${project}`,
  });
  const date = new Date().toISOString().slice(0, 10);
  const text = renderPilotReport(report, { date, project, costUsd: costSummary(db, config, report.projectId).totalUsd });
  const dir = resolve(REPO_ROOT, 'reports');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `pilot-${date}.md`);
  writeFileSync(file, text);
  console.log(`\n${text}\nОтчёт: ${file}\nПроект в базе: ${report.projectId}`);
} catch (err) {
  console.error(`Пилот прерван: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
}
