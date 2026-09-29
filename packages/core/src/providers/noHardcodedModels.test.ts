import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { REPO_ROOT } from './config.ts';

const SOURCE_DIRS = ['apps', 'packages', 'scripts'];
const SKIP_DIRS = new Set(['node_modules', 'dist', 'drizzle', 'kb']);
const MODEL_ID = /\b(?:claude|gemini|gpt|o\d)-[a-z0-9][\w.-]*/iu;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return SKIP_DIRS.has(name) ? [] : sourceFiles(path);
    return /\.(ts|tsx)$/u.test(name) ? [path] : [];
  });
}

describe('model ids live only in config/models.yaml', () => {
  it('no source file hardcodes a model id', () => {
    const offenders = SOURCE_DIRS.flatMap((d) => sourceFiles(join(REPO_ROOT, d)))
      .filter((f) => !f.endsWith('noHardcodedModels.test.ts'))
      .flatMap((f) =>
        readFileSync(f, 'utf8')
          .split('\n')
          .flatMap((line, i) => {
            const m = MODEL_ID.exec(line);
            return m ? [`${relative(REPO_ROOT, f)}:${i + 1}: ${m[0]}`] : [];
          }),
      );
    expect(offenders).toEqual([]);
  });
});
