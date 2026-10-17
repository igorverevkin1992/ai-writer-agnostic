import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { KbLoadError } from './errors.ts';
import { DEFAULT_KB_ROOT, UnknownGenreError, genreKit, loadKb } from './loader.ts';
import { episodeRange } from './schemas.ts';

const KB_ENTRIES = ['genres', 'rules', 'frames', 'checklist', 'methods', 'glossary.yaml', 'constraints', 'holes', 'personas', 'cases', 'prompts', 'guides', 'review'];

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

  it('loads the revenge thriller genre pack: 18 genre rules plus the 4 common ones', () => {
    expect(kit.genre.title).toMatch(/триллер мести/);
    expect(kit.rules.rules.map((r) => r.id)).toEqual([
      ...Array.from({ length: 18 }, (_, i) => `R${String(i + 1).padStart(2, '0')}`),
      'C01', 'C02', 'C03', 'C04',
    ]);
    expect(kit.personas.personas).toHaveLength(5);
    expect(kit.checklist.items.reduce((n, i) => n + i.points, 0)).toBe(20);
  });

  it('merges rules/common.yaml with the genre overrides and fills the hero words', () => {
    const common = kit.rules.rules.filter((r) => r.id.startsWith('C'));
    expect(common.map((r) => r.module)).toEqual(['common', 'common', 'common', 'common']);
    // The revenge pack keeps the common text and check of C01–C03 and says what its first episode promises.
    expect(common[0]!.text).toContain('что героиня получит или потеряет');
    expect(common[1]!.check.run).toEqual([{ fn: 'hero_arc', params: { anchor: 'midpoint', min_lie_acts: 3, relapse: true } }]);
    expect(common[3]!.text).toContain('предательство');
    expect(common[3]!.check.run).toEqual([{ fn: 'promise', params: { ep: 1, anchors: ['betrayal'] } }]);
    expect(common[3]!.check.llm).toContain('обещание мести');
    // The myth pack speaks of a hero, not a heroine.
    const myth = genreKit(kb, 'slavic_myth').rules.rules.find((r) => r.id === 'C03')!;
    expect(myth.text).toBe('Каждая победа героя до финального блока стоит герою чего-то в той же или следующей серии. Победа без цены останавливает историю');
    // The thriller moves the arc to its first reset and leaves the promise to the judge (R14 checks the anchor).
    const thriller = genreKit(kb, 'vertical_thriller').rules.rules;
    expect(thriller.map((r) => r.id)).toEqual([...Array.from({ length: 21 }, (_, i) => `R${String(i + 1).padStart(2, '0')}`), 'R26', 'C01', 'C02', 'C03', 'C04']);
    expect(thriller.find((r) => r.id === 'C02')!.check.run[0]!.params.anchor).toBe('reset1');
    const promise = thriller.find((r) => r.id === 'C04')!;
    expect(promise.check.code).toBeUndefined();
    expect(promise.check.run).toEqual([]);
    expect(promise.check.llm).toContain('невозможный кадр');
  });

  it('fills numbers from the frame and the production limits into texts and check params', () => {
    const thriller = genreKit(kb, 'vertical_thriller');
    const rule = (id: string) => thriller.rules.rules.find((r) => r.id === id)!;
    expect(rule('R06').text).toContain('не больше 4');
    expect(rule('R05').text).toContain('не длиннее 15 слов (больше 20 — блок)');
    expect(rule('R08').text).toContain('одинаковых подряд не больше 2');
    expect(rule('R12').check.run[0]!.params).toEqual({ max_suspense_run: 3 });
    expect(rule('R13').text).toContain('на серии барьера (10-я)');
    expect(rule('R13').text).toContain('в сериях 11–12');
    // R16: one reset per 30 episodes, gaps of at most 42% of the season — both follow from L.
    expect(rule('R16').text).toContain('на 60 серий это 2');
    expect(rule('R16').check.run[0]!.params).toEqual({ anchors: ['reset1', 'false_finale'], min: 2, max_gap: 25 });
    expect(rule('R18').check.run[0]!.params).toEqual({ by_ep: 5 });
    expect(thriller.checklist.items.find((i) => i.id === 'S08')!.text).toBe('Компактность: не больше 5 локаций, не больше 6 ключевых персонажей');
    expect(thriller.guide!.sections.find((s) => s.title.startsWith('Сетка сезона'))!.title).toBe('Сетка сезона (60 серий, пэйволл 10)');
    expect(thriller.personas.personas.find((p) => p.id === 'platform_editor')!.focus).toContain('10-я серия');
    // The raw files keep the placeholders: another paywall or season length renders other numbers.
    expect(kb.rules.vertical_thriller!.rules.find((r) => r.id === 'R16')!.text).toContain('{{L/30}}');
  });

  it('reports an unknown placeholder with its file', () => {
    const dir = kbCopy();
    edit(dir, 'rules/vertical_thriller.yaml', (t) => t.replace('{{limits.max_locations}}', '{{limits.max_locatons}}'));
    expect(loadError(dir).issues).toContainEqual(
      expect.objectContaining({ file: 'rules/vertical_thriller.yaml', field: 'R21', message: expect.stringContaining('{{limits.max_locatons}}') }),
    );
  });

  it('reports an override of a common rule that does not exist and a genre that forgot common_rules', () => {
    const dir = kbCopy();
    edit(dir, 'rules/revenge_thriller.yaml', (t) => t.replace('  C04:\n', '  C09:\n'));
    expect(loadError(dir).issues).toContainEqual(expect.objectContaining({ file: 'rules/revenge_thriller.yaml', field: 'common.C09', message: 'Нет правила C09 в rules/common.yaml' }));
    const dir2 = kbCopy();
    edit(dir2, 'genres/revenge_thriller.yaml', (t) => t.replace('common_rules: true', 'common_rules: false'));
    const issues = loadError(dir2).issues;
    expect(issues).toContainEqual(expect.objectContaining({ file: 'genres/revenge_thriller.yaml', field: 'common_rules' }));
  });

  it('does not let a genre file carry C-rules or the common file carry R-rules', () => {
    const dir = kbCopy();
    edit(dir, 'rules/common.yaml', (t) => t.replace('- id: C01', '- id: R01'));
    expect(loadError(dir).message).toContain('Общие правила нумеруются C01, C02…');
  });

  it('loads shared sections', () => {
    expect(kb.holes.holes).toHaveLength(11);
    expect(Object.keys(kb.methods).sort()).toEqual(['harmon', 'mowery', 'rabkin', 'truby', 'vertical', 'weiland']);
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
    expect(Object.keys(kb.genres).sort()).toEqual(['revenge_thriller', 'romance30', 'romantasy_revenge', 'slavic_myth', 'vertical_thriller']);
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
