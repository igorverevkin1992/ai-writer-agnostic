import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { KbLoadError } from './errors.ts';
import { DEFAULT_KB_ROOT, UnknownGenreError, genreKit, loadKb } from './loader.ts';
import { episodeRange } from './schemas.ts';

const KB_ENTRIES = ['genres', 'rules', 'frames', 'checklist', 'methods', 'glossary.yaml', 'constraints', 'holes', 'personas', 'cases', 'prompts', 'guides'];

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
  const kit = genreKit(kb, 'revenge_thriller');

  it('loads the revenge thriller genre pack', () => {
    expect(kit.genre.title).toMatch(/триллер мести/);
    expect(kit.rules.rules.map((r) => r.id)).toEqual(
      Array.from({ length: 18 }, (_, i) => `R${String(i + 1).padStart(2, '0')}`),
    );
    expect(kit.personas.personas).toHaveLength(5);
    expect(kit.checklist.items.reduce((n, i) => n + i.points, 0)).toBe(20);
  });

  it('loads shared sections', () => {
    expect(kb.holes.holes).toHaveLength(11);
    expect(Object.keys(kb.methods).sort()).toEqual(['harmon', 'mowery', 'truby', 'weiland']);
    expect(kb.prompts['architect/concept']).toContain('JSON');
  });

  it('holds the 60-episode frame from the spec', () => {
    const f = kit.frame;
    expect(f.episodes).toBe(60);
    expect(f.free).toBe(8);
    expect(f.anchors.paywall_hook).toBe(8);
    expect(f.anchors.midpoint).toBe(30);
    expect(f.anchors.mask).toEqual([1, 3]);
    expect(f.villains?.count).toBe(5);
    expect(f.villains?.roles['1']).toBe('boss');
    expect(f.villains?.takedowns['4']).toEqual([18, 20]);
    expect(f.blocks).toHaveLength(6);
    expect(f.tolerance).toBe(1);
  });

  it('holds production limits from the spec', () => {
    expect(kit.production.limits.max_speakers_per_scene).toBe(3);
    expect(kit.production.script_metrics.max_line_words).toBe(12);
    expect(kit.production.script_metrics.max_overlay_words).toBe(7);
  });

  it('rejects an unknown genre with a clear message', () => {
    expect(() => genreKit(kb, 'sitcom')).toThrow(UnknownGenreError);
    expect(() => genreKit(kb, 'sitcom')).toThrow(/Жанр «sitcom» не найден. Есть: revenge_thriller/);
  });
});

describe('other series, other genres', () => {
  /** Adds a made-up 30-episode romance pack without villains: data only, no code. */
  function addRomance(dir: string): void {
    writeFileSync(join(dir, 'genres/romance30.yaml'), [
      'id: romance30', 'title: Романтическая комедия', 'rules: romance', 'frame: season30',
      'checklist: romance10', 'personas: viewers', 'constraints: {legal: ru_legal, production: production}', '',
    ].join('\n'));
    writeFileSync(join(dir, 'rules/romance.yaml'), [
      'module: romance', 'title: Ромком', 'rules:',
      '  - {id: R01, module: romance, text: "Встреча героев в 1-й серии", check: {code: "anchor meet == 1"}, severity: blocker}', '',
    ].join('\n'));
    writeFileSync(join(dir, 'frames/season30.yaml'), [
      'season_frame:', '  episodes: 30', '  free: 5', '  duration_s: {min: 60, max: 120, target: 90}',
      '  anchors: {meet: 1, kiss: 15, finale: 30}',
      '  rhythm: {response_within: 2, max_suffering_run: 1, max_same_hook_run: 2, emotions_per_episode: [1, 3]}',
      '  blocks: [[1, 10], [11, 20], [21, 30]]', '  tolerance: 1', '',
    ].join('\n'));
    writeFileSync(join(dir, 'checklist/romance10.yaml'), [
      'total: 10', 'pass: 7', 'items:', '  - {id: C01, text: "Встреча в 1-й", points: 10, rule: R01}', '',
    ].join('\n'));
  }

  it('a second genre with its own frame loads next to the first one', () => {
    const dir = kbCopy();
    addRomance(dir);
    const kb = loadKb(dir);
    expect(Object.keys(kb.genres).sort()).toEqual(['revenge_thriller', 'romance30', 'romantasy_revenge']);
    const romance = genreKit(kb, 'romance30');
    expect(romance.frame.episodes).toBe(30);
    expect(romance.frame.villains).toBeUndefined();
    expect(romance.rules.rules).toHaveLength(1);
    expect(genreKit(kb, 'revenge_thriller').frame.episodes).toBe(60);
  });

  it('checks references inside a genre pack', () => {
    const dir = kbCopy();
    addRomance(dir);
    edit(dir, 'genres/romance30.yaml', (t) => t.replace('frame: season30', 'frame: season31'));
    expect(loadError(dir).issues).toContainEqual({ file: 'genres/romance30.yaml', field: 'frame', message: 'Нет файла frames/season31.yaml' });
  });

  it('checks checklist rules against the genre that uses them', () => {
    const dir = kbCopy();
    addRomance(dir);
    edit(dir, 'checklist/romance10.yaml', (t) => t.replace('rule: R01', 'rule: R05'));
    expect(loadError(dir).message).toContain('Нет правила R05 в rules/romance.yaml (жанр romance30)');
  });

  it('a ladder of three villains needs exactly ranks 1–3', () => {
    const dir = kbCopy();
    edit(dir, 'frames/season60.yaml', (t) => t.replace('count: 5', 'count: 3'));
    const fields = loadError(dir).issues.map((i) => i.field);
    expect(fields).toEqual(expect.arrayContaining([
      'season_frame.villains.roles',
      'season_frame.villains.on_screen_by',
      'season_frame.villains.takedowns',
    ]));
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
    expect(issue?.message).toContain('Нужны все ранги от 1 до 5');
  });

  it('reports a constraints file that no genre uses', () => {
    const dir = kbCopy();
    writeFileSync(join(dir, 'constraints/kz_legal.yaml'), 'age_rating: "16+"\n');
    expect(loadError(dir).issues).toContainEqual(
      expect.objectContaining({ file: 'constraints/kz_legal.yaml', message: expect.stringContaining('ни в одном жанре') }),
    );
  });

  it('reports a genre whose id does not match the file name', () => {
    const dir = kbCopy();
    edit(dir, 'genres/revenge_thriller.yaml', (t) => t.replace('id: revenge_thriller', 'id: revenge'));
    expect(loadError(dir).issues[0]).toMatchObject({ file: 'genres/revenge_thriller.yaml', field: 'id' });
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
    expect(loadError(dir).message).toContain('Нет правила R99 в rules/revenge_thriller.yaml');
  });

  it('requires a full checklist once it is no longer a stub', () => {
    const dir = kbCopy();
    edit(dir, 'checklist/season20.yaml', (t) => t.replace('points: 3, rule: R15', 'points: 2, rule: R15'));
    expect(loadError(dir).message).toContain('Сумма баллов 19 не равна итогу 20');
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
    edit(dir, 'personas/viewers.yaml', (t) => t.replace('focus: "Медицинская достоверность"', 'focus: ""'));
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

describe('review fixes', () => {
  it('reports a rule or checklist item naming an anchor the frame does not have', () => {
    const dir = kbCopy();
    edit(dir, 'rules/revenge_thriller.yaml', (t) => t.replace('only: [anchor.mask]', 'only: [anchor.maks]').replace('near: [secret_turn, midpoint]', 'near: [secret_turn, midpoin]'));
    edit(dir, 'checklist/season20.yaml', (t) => t.replace('season_frame.anchor.fall', 'season_frame.anchor.fal'));
    const msgs = loadError(dir).issues.map((i) => i.message);
    expect(msgs).toEqual(expect.arrayContaining([
      'Нет опорной точки «maks» в frames/season60.yaml (жанр revenge_thriller)',
      'Нет опорной точки «midpoin» в frames/season60.yaml (жанр revenge_thriller)',
      'Нет опорной точки «fal» в frames/season60.yaml (жанр revenge_thriller)',
    ]));
  });

  it('a broken genre file does not cause false «unused constraints» errors', () => {
    const dir = kbCopy();
    edit(dir, 'genres/revenge_thriller.yaml', (t) => `${t}extra_key: 1\n`);
    expect(loadError(dir).issues.map((i) => i.file)).toEqual(['genres/revenge_thriller.yaml']);
  });
});
