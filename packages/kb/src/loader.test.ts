import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { KbLoadError } from './errors.ts';
import { DEFAULT_KB_ROOT, loadKb } from './loader.ts';
import { episodeRange } from './schemas.ts';

const KB_ENTRIES = ['rules', 'frames', 'checklist', 'methods', 'glossary.yaml', 'constraints', 'holes', 'personas', 'cases', 'prompts'];

let dirs: string[] = [];

/** Copies the real knowledge base into a temp dir so a test can break it. */
function kbCopy(): string {
  const dir = mkdtempSync(join(tmpdir(), 'kb-'));
  for (const entry of KB_ENTRIES) cpSync(join(DEFAULT_KB_ROOT, entry), join(dir, entry), { recursive: true });
  dirs.push(dir);
  return dir;
}

function edit(dir: string, file: string, change: (text: string) => string): void {
  const path = join(dir, file);
  writeFileSync(path, change(readFileSync(path, 'utf8')));
}

function loadError(dir: string): KbLoadError {
  try {
    loadKb(dir);
  } catch (err) {
    if (err instanceof KbLoadError) return err;
    throw err;
  }
  throw new Error('loadKb did not fail');
}

afterEach(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
  dirs = [];
});

describe('loadKb on the real knowledge base', () => {
  const kb = loadKb();

  it('loads every section', () => {
    expect(kb.rules[0]?.module).toBe('revenge_thriller');
    const ids = kb.rules[0]?.rules.map((r) => r.id);
    expect(ids).toHaveLength(18);
    expect(ids).toEqual(Array.from({ length: 18 }, (_, i) => `R${String(i + 1).padStart(2, '0')}`));
    expect(kb.holes.holes).toHaveLength(11);
    expect(kb.personas.personas).toHaveLength(5);
    expect(Object.keys(kb.methods).sort()).toEqual(['harmon', 'mowery', 'truby', 'weiland']);
    expect(kb.prompts['architect/concept']).toContain('JSON');
  });

  it('holds the 60-episode frame from the spec', () => {
    const f = kb.frame;
    expect(f.episodes).toBe(60);
    expect(f.free).toBe(8);
    expect(f.anchors.paywall_hook).toBe(8);
    expect(f.anchors.midpoint).toBe(30);
    expect(f.villains.count).toBe(5);
    expect(f.villains.takedowns['4']).toEqual([18, 20]);
    expect(f.blocks).toHaveLength(6);
    expect(f.tolerance).toBe(1);
  });

  it('holds production limits from the spec', () => {
    expect(kb.production.limits.max_speakers_per_scene).toBe(3);
    expect(kb.production.script_metrics.max_line_words).toBe(12);
    expect(kb.production.script_metrics.max_overlay_words).toBe(7);
  });
});

describe('loadKb errors are clear', () => {
  it('reports broken YAML with file and line', () => {
    const dir = kbCopy();
    edit(dir, 'frames/season60.yaml', (t) => t.replace('  free: 8', '  free: [8'));
    const err = loadError(dir);
    expect(err.message).toMatch(/^База знаний не загрузилась: 1 ошибка\./);
    const issue = err.issues[0];
    expect(issue?.file).toBe('frames/season60.yaml');
    expect(issue?.message).toMatch(/Ошибка разметки YAML/);
    expect(issue?.line).toBeGreaterThan(0);
  });

  it('reports a wrong value with field path and line', () => {
    const dir = kbCopy();
    edit(dir, 'rules/revenge_thriller.yaml', (t) => t.replace('severity: blocker', 'severity: blokker'));
    const err = loadError(dir);
    const issue = err.issues.find((i) => i.field === 'rules[0].severity');
    expect(issue?.file).toBe('rules/revenge_thriller.yaml');
    const line = readFileSync(join(dir, 'rules/revenge_thriller.yaml'), 'utf8').split('\n').findIndex((l) => l.includes('blokker')) + 1;
    expect(issue?.line).toBe(line);
    expect(err.message).toContain(`rules/revenge_thriller.yaml, строка ${line}`);
    expect(err.message).toContain('blocker');
  });

  it('does not add follow-up noise when a rule file is broken', () => {
    const dir = kbCopy();
    edit(dir, 'rules/revenge_thriller.yaml', (t) => t.replace('severity: blocker', 'severity: 1'));
    expect(loadError(dir).issues.every((i) => i.file === 'rules/revenge_thriller.yaml')).toBe(true);
  });

  it('reports a typo in a key (unknown field)', () => {
    const dir = kbCopy();
    edit(dir, 'constraints/production.yaml', (t) => t.replace('max_line_words', 'max_line_wrds'));
    const fields = loadError(dir).issues.map((i) => i.field);
    expect(fields).toContain('script_metrics');
  });

  it('reports a missing file', () => {
    const dir = kbCopy();
    rmSync(join(dir, 'glossary.yaml'));
    expect(loadError(dir).issues).toContainEqual({ file: 'glossary.yaml', message: 'Файл не найден' });
  });

  it('reports a missing villain rank in the frame', () => {
    const dir = kbCopy();
    edit(dir, 'frames/season60.yaml', (t) => t.replace('takedowns: {5: 3, ', 'takedowns: {'));
    const issue = loadError(dir).issues[0];
    expect(issue?.field).toBe('season_frame.villains.takedowns');
    expect(issue?.message).toContain('пять рангов');
  });

  it('reports an anchor outside the season', () => {
    const dir = kbCopy();
    edit(dir, 'frames/season60.yaml', (t) => t.replace('midpoint: 30', 'midpoint: 70'));
    expect(loadError(dir).issues[0]?.field).toBe('season_frame.anchors.midpoint');
  });

  it('reports a duplicate rule id', () => {
    const dir = kbCopy();
    edit(dir, 'rules/revenge_thriller.yaml', (t) => t.replace('- id: R01', '- id: R05'));
    expect(loadError(dir).message).toContain('Правило R05 встречается дважды');
  });

  it('reports a checklist item pointing to an unknown rule', () => {
    const dir = kbCopy();
    edit(dir, 'checklist/season20.yaml', (t) => t.replace('rule: R05', 'rule: R99'));
    expect(loadError(dir).message).toContain('Нет правила R99');
  });

  it('requires a full checklist once it is no longer a stub', () => {
    const dir = kbCopy();
    edit(dir, 'checklist/season20.yaml', (t) => t.replace('stub: true', 'stub: false'));
    expect(loadError(dir).message).toContain('не равна итогу 20');
  });

  it('requires exactly 11 hole types', () => {
    const dir = kbCopy();
    edit(dir, 'holes/catalog.yaml', (t) => t.slice(0, t.indexOf('  - id: 11')));
    expect(loadError(dir).message).toContain('ровно 11 типов');
  });

  it('reports an empty prompt', () => {
    const dir = kbCopy();
    writeFileSync(join(dir, 'prompts/writer/polish.md'), '  \n');
    expect(loadError(dir).issues).toContainEqual({ file: 'prompts/writer/polish.md', message: 'Промпт пустой' });
  });

  it('collects errors from several files at once', () => {
    const dir = kbCopy();
    rmSync(join(dir, 'glossary.yaml'));
    edit(dir, 'personas/viewers.yaml', (t) => t.replace(/ {2}- \{id: teen_mom.*\n/, ''));
    const files = loadError(dir).issues.map((i) => i.file);
    expect(files).toEqual(expect.arrayContaining(['glossary.yaml', 'personas/viewers.yaml']));
  });
});

describe('episodeRange', () => {
  it('resolves every frame notation', () => {
    expect(episodeRange(8)).toEqual({ min: 8, max: 8 });
    expect(episodeRange('<=3')).toEqual({ min: 1, max: 3 });
    expect(episodeRange([45, 49])).toEqual({ min: 45, max: 49 });
  });
});
