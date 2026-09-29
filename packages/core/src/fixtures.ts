import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse } from 'yaml';
import { REPO_ROOT } from './providers/config.ts';
import { Bible } from './schemas/bible.ts';
import { SeasonPlan } from './schemas/season.ts';

export const FIXTURES_DIR = join(REPO_ROOT, 'fixtures');

export interface ProjectFixture {
  bible: Bible;
  plan: SeasonPlan;
}

/** Loads a project fixture (bible.yaml + plan.yaml) from fixtures/<kind>/<name>. */
export function loadProjectFixture(dir: string): ProjectFixture {
  const read = (file: string) => {
    const path = join(dir, file);
    if (!existsSync(path)) throw new Error(`Нет файла ${path}`);
    return parse(readFileSync(path, 'utf8')) as unknown;
  };
  return { bible: Bible.parse(read('bible.yaml')), plan: SeasonPlan.parse(read('plan.yaml')) };
}

export function loadGolden(name = 'muzh_krov'): ProjectFixture {
  return loadProjectFixture(join(FIXTURES_DIR, 'golden', name));
}
