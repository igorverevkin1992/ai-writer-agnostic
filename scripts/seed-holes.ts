// Plants holes into the golden project and writes the set to fixtures/seeded/.
// Usage: pnpm seed-holes [--seed=1] [--project=muzh_krov]
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { stringify } from 'yaml';
import { FIXTURES_DIR, detectSeeded, loadGolden, seedHoles } from '@aiw/core';
import { genreKit, loadKb } from '@aiw/kb';

const arg = (name: string, fallback: string) =>
  process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1] ?? fallback;
const seed = Number(arg('seed', '1'));
const project = arg('project', 'muzh_krov');

const golden = loadGolden(project);
const kit = genreKit(loadKb(), 'revenge_thriller');
const holes = seedHoles(golden, seed);

const dir = join(FIXTURES_DIR, 'seeded');
mkdirSync(dir, { recursive: true });
const file = join(dir, `${project}.yaml`);
writeFileSync(
  file,
  `# Посеянные дыры для проекта ${project} (seed ${seed}). Каждая дыра — правка эталона fixtures/golden/${project}.\n` +
    '# Создано командой pnpm seed-holes. Не править вручную.\n' +
    stringify({ project, seed, genre: kit.genre.id, holes }, { lineWidth: 0 }),
);

const report = detectSeeded(kit, golden, holes);
console.log(`Посеяно дыр: ${holes.length} → ${file}\n`);
console.log('Тип дыры                     посеяно  найдено кодом');
for (const [type, s] of Object.entries(report.byType)) {
  const name = kit ? (loadKb().holes.holes.find((h) => h.id === Number(type))?.name ?? '') : '';
  console.log(`${`${type}. ${name}`.padEnd(30)} ${String(s.planted).padStart(6)}  ${String(s.found).padStart(6)}`);
}
const missed = report.results.filter((r) => !r.found);
if (missed.length) {
  console.log('\nКод не нашёл (эти дыры — работа для модели-судьи):');
  for (const r of missed) console.log(`  ${r.hole.id} [тип ${r.hole.holeType}] ${r.hole.description}`);
}
