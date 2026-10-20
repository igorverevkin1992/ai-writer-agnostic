// Auditor evaluation on the golden set and seeded holes. Real API calls — run manually.
// Usage: pnpm eval [--project=muzh_krov] [--genre=revenge_thriller] [--architect=heavy] [--budget=25] [--seeded=all|missed|none] [--resolve=10] [--fresh]
// Answers are cached: a run restarted after a crash pays only for what it had not got. --fresh asks the models again.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parse } from 'yaml';
import {
  FIXTURES_DIR,
  LlmClient,
  REPO_ROOT,
  costSummary,
  loadDotEnv,
  loadGolden,
  loadModelsConfig,
  loadProducerHoles,
  openDb,
  progressLogger,
  projects,
  renderEvalReport,
  runAuditEval,
  type SeededHole,
} from '@aiw/core';
import { genreKit, loadKb } from '@aiw/kb';

const arg = (name: string, fallback: string) =>
  process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1] ?? fallback;
/** A positive number from `--name=`; a typo must not silently turn the budget off. */
const num = (name: string, fallback: string): number => {
  const n = Number(arg(name, fallback));
  if (!Number.isFinite(n) || n <= 0) {
    console.error(`Параметр --${name} должен быть положительным числом, получено «${arg(name, fallback)}».`);
    process.exit(1);
  }
  return n;
};
const project = arg('project', 'muzh_krov');
const heavy = arg('architect', 'default') === 'heavy';
const budget = num('budget', '25');
const seededModel = arg('seeded', 'all') as 'all' | 'missed' | 'none';

loadDotEnv();
const config = loadModelsConfig();
const kb = loadKb();
const kit = genreKit(kb, arg('genre', 'revenge_thriller'));
const db = openDb(resolve(REPO_ROOT, 'data/app.db'));
const date = new Date().toISOString().slice(0, 10);
const projectId = `eval-${project}-${heavy ? 'heavy' : 'default'}-${Date.now()}`;
db.insert(projects).values({ id: projectId, title: `Оценка ${project}`, genreId: kit.genre.id, budgetLimitUsd: budget }).run();

// A golden project with its own holes (e.g. kupala) has no seeded set: only the producer's list counts.
const seededPath = join(FIXTURES_DIR, 'seeded', `${project}.yaml`);
const seededFile = existsSync(seededPath) ? (parse(readFileSync(seededPath, 'utf8')) as { holes: SeededHole[] }) : { holes: [] };
if (!existsSync(seededPath)) console.log(`Нет fixtures/seeded/${project}.yaml: посеянные дыры не проверяются.`);
const producer = loadProducerHoles(project);
if (!producer) console.log(`Нет fixtures/golden/${project}/holes.yaml: метрика «найдено дыр продюсера» будет пустой.`);

const llm = new LlmClient({ config, db, onCall: progressLogger(), cache: !process.argv.includes('--fresh') });
const architectRole = heavy ? 'architect_heavy' : 'architect';
console.log(`Оценка аудитора: ${project}, архитектор ${config.roles[architectRole].model}, лимит $${budget}…`);

const needed = new Set(Object.values(config.roles).map((r) => r.provider));
const keyOf = { anthropic: 'ANTHROPIC_API_KEY', google: 'GEMINI_API_KEY', openai_compatible: 'RESERVE_API_KEY' } as const;
const missing = [...needed].map((p) => keyOf[p]).filter((k) => !process.env[k]);
if (missing.length) {
  console.error(`Нет ключей в .env: ${missing.join(', ')}. Оценка делает реальные вызовы моделей — добавьте ключи и запустите снова.`);
  process.exit(1);
}

let report;
try {
  report = await runAuditEval({
    llm,
    kb,
    kit,
    golden: loadGolden(project),
    producer,
    seeded: seededFile.holes,
    projectId,
    seededModel,
    architectRole,
    resolveLimit: process.argv.some((a) => a.startsWith('--resolve=')) ? Math.floor(Number(arg('resolve', '10'))) || 0 : undefined,
  });
} catch (err) {
  console.error(`Оценка прервана: ${err instanceof Error ? err.message : String(err)}`);
  console.error('Запустите ту же команду ещё раз: уже полученные ответы возьмутся из кэша бесплатно.');
  console.error(`Потрачено к этому моменту: $${costSummary(db, config, projectId).totalUsd.toFixed(2)} (проект в базе: ${projectId}).`);
  process.exit(1);
}

const text = renderEvalReport(report, {
  date,
  project,
  costUsd: costSummary(db, config, projectId).totalUsd,
  architectModel: config.roles[architectRole].model,
});
const dir = resolve(REPO_ROOT, 'reports');
mkdirSync(dir, { recursive: true });
const file = join(dir, `eval-${project}-${date}${heavy ? '-heavy' : ''}.md`);
writeFileSync(file, text);
console.log(`\n${text}\nОтчёт: ${file}`);
