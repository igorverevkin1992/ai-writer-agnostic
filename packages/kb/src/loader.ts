import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LineCounter, isNode, parseDocument, type Document } from 'yaml';
import type { z } from 'zod';
import { KbLoadError, type KbIssue } from './errors.ts';
import {
  Case,
  Checklist,
  Genre,
  Glossary,
  HoleCatalog,
  LegalConstraints,
  Method,
  Personas,
  ProductionConstraints,
  RulesFile,
  SeasonFrame,
  episodeRange,
} from './schemas.ts';

export const DEFAULT_KB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const METHOD_IDS = ['weiland', 'truby', 'harmon', 'mowery'] as const;

type Frame = z.infer<typeof SeasonFrame>['season_frame'];

/** Files of one kind keyed by file name without extension, e.g. frames["season60"]. */
type ById<T> = Record<string, T>;

export interface Kb {
  root: string;
  /** Genre packs keyed by id. A project picks one of them. */
  genres: ById<Genre>;
  rules: ById<RulesFile>;
  frames: ById<Frame>;
  checklists: ById<Checklist>;
  personas: ById<Personas>;
  legal: ById<LegalConstraints>;
  production: ById<ProductionConstraints>;
  /** Shared by all genres. */
  methods: Record<(typeof METHOD_IDS)[number], Method>;
  glossary: Glossary;
  holes: HoleCatalog;
  cases: Case[];
  /** Prompt texts keyed by "<role>/<step>". */
  prompts: Record<string, string>;
}

/** Everything that applies to a project of one genre. */
export interface GenreKit {
  genre: Genre;
  rules: RulesFile;
  frame: Frame;
  checklist: Checklist;
  personas: Personas;
  legal: LegalConstraints;
  production: ProductionConstraints;
}

export class UnknownGenreError extends Error {
  override name = 'UnknownGenreError';
}

/** Resolves a genre pack. References are validated at load time, so this only fails on an unknown id. */
export function genreKit(kb: Kb, genreId: string): GenreKit {
  const genre = kb.genres[genreId];
  if (!genre) {
    throw new UnknownGenreError(
      `Жанр «${genreId}» не найден. Есть: ${Object.keys(kb.genres).join(', ') || 'ни одного'}.`,
    );
  }
  return {
    genre,
    rules: kb.rules[genre.rules]!,
    frame: kb.frames[genre.frame]!,
    checklist: kb.checklists[genre.checklist]!,
    personas: kb.personas[genre.personas]!,
    legal: kb.legal[genre.constraints.legal]!,
    production: kb.production[genre.constraints.production]!,
  };
}

/**
 * Loads and validates the whole knowledge base. Collects every problem
 * across all files and throws a single KbLoadError listing them.
 */
export function loadKb(root: string = DEFAULT_KB_ROOT): Kb {
  const issues: KbIssue[] = [];
  const read = <T extends z.ZodType>(file: string, schema: T): z.infer<T> | undefined =>
    readYaml(root, file, schema, issues);

  const readDir = <T extends z.ZodType>(dir: string, schema: T) => {
    const loaded: ById<z.infer<T>> = {};
    const failed = new Set<string>();
    for (const file of listFiles(root, dir, '.yaml')) {
      const id = basename(file, '.yaml');
      const value = read(file, schema);
      if (value === undefined) failed.add(id);
      else loaded[id] = value;
    }
    return { loaded, failed };
  };

  const genres = readDir('genres', Genre);
  const rules = readDir('rules', RulesFile);
  const frameFiles = readDir('frames', SeasonFrame);
  const checklists = readDir('checklist', Checklist);
  const personas = readDir('personas', Personas);
  const frames = {
    loaded: Object.fromEntries(Object.entries(frameFiles.loaded).map(([id, f]) => [id, f.season_frame])) as ById<Frame>,
    failed: frameFiles.failed,
  };

  if (Object.keys(genres.loaded).length === 0 && genres.failed.size === 0) {
    issues.push({ file: 'genres/', message: 'Нет ни одного жанрового пакета (genres/<id>.yaml)' });
  }

  // Constraint files are validated by the kind a genre gives them.
  const legal: ById<LegalConstraints> = {};
  const production: ById<ProductionConstraints> = {};
  const usedConstraints = new Set<string>();
  for (const g of Object.values(genres.loaded)) {
    for (const [kind, id] of Object.entries(g.constraints) as ['legal' | 'production', string][]) {
      usedConstraints.add(id);
      const target = kind === 'legal' ? legal : production;
      if (id in target) continue;
      const file = `constraints/${id}.yaml`;
      const value = kind === 'legal' ? read(file, LegalConstraints) : read(file, ProductionConstraints);
      if (value) (target as ById<unknown>)[id] = value;
    }
  }
  for (const file of listFiles(root, 'constraints', '.yaml')) {
    if (!usedConstraints.has(basename(file, '.yaml'))) {
      issues.push({ file, message: 'Файл не указан ни в одном жанре (genres/*.yaml), поэтому его нельзя проверить' });
    }
  }

  const methods = Object.fromEntries(
    METHOD_IDS.map((id) => [id, read(`methods/${id}.yaml`, Method)]),
  ) as Partial<Kb['methods']>;
  const glossary = read('glossary.yaml', Glossary);
  const holes = read('holes/catalog.yaml', HoleCatalog);
  const cases = listFiles(root, 'cases', '.yaml')
    .map((f) => read(f, Case))
    .filter(isDefined);
  const prompts = readPrompts(root, issues);

  crossCheck(
    { genres, rules, frames, checklists, personas, legal, production, holes, methods },
    issues,
  );

  if (issues.length > 0 || !glossary || !holes || METHOD_IDS.some((id) => !methods[id])) {
    throw new KbLoadError(issues);
  }

  return {
    root,
    genres: genres.loaded,
    rules: rules.loaded,
    frames: frames.loaded,
    checklists: checklists.loaded,
    personas: personas.loaded,
    legal,
    production,
    methods: methods as Kb['methods'],
    glossary,
    holes,
    cases,
    prompts,
  };
}

function readYaml<T extends z.ZodType>(
  root: string,
  file: string,
  schema: T,
  issues: KbIssue[],
): z.infer<T> | undefined {
  const path = join(root, file);
  if (!existsSync(path)) {
    issues.push({ file, message: 'Файл не найден' });
    return undefined;
  }
  const lineCounter = new LineCounter();
  const doc = parseDocument(readFileSync(path, 'utf8'), { lineCounter, prettyErrors: false, uniqueKeys: true });

  if (doc.errors.length > 0) {
    for (const err of doc.errors) {
      const pos = lineCounter.linePos(err.pos[0]);
      issues.push({ file, line: pos.line, column: pos.col, message: `Ошибка разметки YAML: ${yamlMessage(err.code, err.message)}` });
    }
    return undefined;
  }

  const result = schema.safeParse(doc.toJS());
  if (result.success) return result.data;

  for (const issue of result.error.issues) {
    const pos = locate(doc, lineCounter, issue.path);
    issues.push({ file, field: formatPath(issue.path), message: issue.message, ...pos });
  }
  return undefined;
}

const YAML_MESSAGES: Record<string, string> = {
  BAD_INDENT: 'неверный отступ',
  MISSING_CHAR: 'не хватает символа (скобки или кавычки)',
  DUPLICATE_KEY: 'ключ повторяется',
  BLOCK_AS_IMPLICIT_KEY: 'неверная структура: похоже, пропущено двоеточие или отступ',
  MULTILINE_IMPLICIT_KEY: 'ключ разорван на несколько строк',
  UNEXPECTED_TOKEN: 'неожиданный символ',
  TAB_AS_INDENT: 'отступ табуляцией — нужны пробелы',
};

function yamlMessage(code: string, original: string): string {
  const firstLine = original.split('\n')[0] ?? original;
  return YAML_MESSAGES[code] ? `${YAML_MESSAGES[code]} (${firstLine})` : firstLine;
}

/** Finds the line of the deepest existing YAML node on the issue path. */
function locate(
  doc: Document,
  lineCounter: LineCounter,
  path: PropertyKey[],
): { line?: number; column?: number } {
  for (let len = path.length; len >= 0; len--) {
    const keys = path.slice(0, len).map((k) => (typeof k === 'string' && /^\d+$/.test(k) ? Number(k) : k));
    const node = doc.getIn(keys, true) ?? doc.getIn(path.slice(0, len), true);
    if (isNode(node) && node.range) {
      const pos = lineCounter.linePos(node.range[0]);
      return { line: pos.line, column: pos.col };
    }
  }
  return {};
}

function formatPath(path: PropertyKey[]): string | undefined {
  if (path.length === 0) return undefined;
  return path
    .map((p, i) => (typeof p === 'number' ? `[${p}]` : `${i === 0 ? '' : '.'}${String(p)}`))
    .join('');
}

function listFiles(root: string, dir: string, ext: string): string[] {
  const full = join(root, dir);
  if (!existsSync(full)) return [];
  return readdirSync(full)
    .filter((f) => f.endsWith(ext))
    .sort()
    .map((f) => `${dir}/${f}`);
}

function readPrompts(root: string, issues: KbIssue[]): Record<string, string> {
  const base = join(root, 'prompts');
  const prompts: Record<string, string> = {};
  if (!existsSync(base)) return prompts;
  for (const role of readdirSync(base).sort()) {
    const roleDir = join(base, role);
    if (!statSync(roleDir).isDirectory()) {
      issues.push({ file: relative(root, roleDir), message: 'Промпты лежат в папках по ролям: prompts/<роль>/<шаг>.md' });
      continue;
    }
    for (const file of readdirSync(roleDir).sort()) {
      const rel = `prompts/${role}/${file}`;
      if (!file.endsWith('.md')) {
        issues.push({ file: rel, message: 'Промпт должен быть файлом .md' });
        continue;
      }
      const text = readFileSync(join(roleDir, file), 'utf8').trim();
      if (!text) issues.push({ file: rel, message: 'Промпт пустой' });
      prompts[`${role}/${file.slice(0, -3)}`] = text;
    }
  }
  return prompts;
}

interface Loaded<T> {
  loaded: ById<T>;
  failed: Set<string>;
}

function crossCheck(
  kb: {
    genres: Loaded<Genre>;
    rules: Loaded<RulesFile>;
    frames: Loaded<Frame>;
    checklists: Loaded<Checklist>;
    personas: Loaded<Personas>;
    legal: ById<LegalConstraints>;
    production: ById<ProductionConstraints>;
    holes: HoleCatalog | undefined;
    methods: Partial<Kb['methods']>;
  },
  issues: KbIssue[],
): void {
  for (const [id, g] of Object.entries(kb.genres.loaded)) {
    const file = `genres/${id}.yaml`;
    if (g.id !== id) issues.push({ file, field: 'id', message: `id должен быть «${id}», как имя файла` });
    const refs: [string, string, Loaded<unknown>][] = [
      ['rules', 'rules', kb.rules],
      ['frame', 'frames', kb.frames],
      ['checklist', 'checklist', kb.checklists],
      ['personas', 'personas', kb.personas],
    ];
    for (const [field, dir, set] of refs) {
      const ref = g[field as 'rules' | 'frame' | 'checklist' | 'personas'];
      if (!(ref in set.loaded) && !set.failed.has(ref)) {
        issues.push({ file, field, message: `Нет файла ${dir}/${ref}.yaml` });
      }
    }
    // A missing constraint file is already reported by readYaml as "not found".

    const rules = kb.rules.loaded[g.rules];
    const checklist = kb.checklists.loaded[g.checklist];
    if (rules && checklist) {
      const ids = new Set(rules.rules.map((r) => r.id));
      checklist.items.forEach((item, i) => {
        if (item.rule && !ids.has(item.rule)) {
          issues.push({
            file: `checklist/${g.checklist}.yaml`,
            field: `items[${i}].rule`,
            message: `Нет правила ${item.rule} в rules/${g.rules}.yaml (жанр ${id})`,
          });
        }
      });
    }
  }

  for (const [id, file] of Object.entries(kb.rules.loaded)) {
    const name = `rules/${id}.yaml`;
    if (file.module !== id) issues.push({ file: name, field: 'module', message: `Модуль должен быть «${id}», как имя файла` });
    const seen = new Set<string>();
    file.rules.forEach((rule, ri) => {
      if (seen.has(rule.id)) issues.push({ file: name, field: `rules[${ri}].id`, message: `Правило ${rule.id} встречается дважды` });
      seen.add(rule.id);
      if (rule.module !== file.module) {
        issues.push({ file: name, field: `rules[${ri}].module`, message: `Модуль правила «${rule.module}» не совпадает с модулем файла «${file.module}»` });
      }
    });
  }

  for (const [id, checklist] of Object.entries(kb.checklists.loaded)) {
    const file = `checklist/${id}.yaml`;
    const sum = checklist.items.reduce((s, i) => s + i.points, 0);
    if (sum > checklist.total) issues.push({ file, message: `Сумма баллов ${sum} больше итога ${checklist.total}` });
    if (!checklist.stub && sum !== checklist.total) {
      issues.push({ file, message: `Сумма баллов ${sum} не равна итогу ${checklist.total}` });
    }
    if (checklist.pass > checklist.total) issues.push({ file, field: 'pass', message: 'Порог больше итога' });
  }

  if (kb.holes) {
    const ids = kb.holes.holes.map((h) => h.id).sort((a, b) => a - b);
    if (ids.some((id, i) => id !== i + 1)) {
      issues.push({ file: 'holes/catalog.yaml', message: 'Типы дыр должны идти по номерам 1–11 без пропусков' });
    }
  }

  for (const id of METHOD_IDS) {
    const m = kb.methods[id];
    if (m && m.id !== id) issues.push({ file: `methods/${id}.yaml`, field: 'id', message: `id должен быть «${id}», как имя файла` });
  }

  for (const [id, legal] of Object.entries(kb.legal)) {
    const principles = new Set(legal.principles.map((p) => p.id));
    legal.markers.forEach((m, i) => {
      if (!principles.has(m.principle)) {
        issues.push({ file: `constraints/${id}.yaml`, field: `markers[${i}].principle`, message: `Нет принципа ${m.principle}` });
      }
    });
  }

  for (const [id, frame] of Object.entries(kb.frames.loaded)) checkFrame(`frames/${id}.yaml`, frame, issues);
}

function checkFrame(file: string, frame: Frame, issues: KbIssue[]): void {
  const inSeason = (n: number) => n >= 1 && n <= frame.episodes;
  for (const [name, spec] of Object.entries(frame.anchors)) {
    const r = episodeRange(spec);
    if (!inSeason(r.min) || !inSeason(r.max)) {
      issues.push({ file, field: `season_frame.anchors.${name}`, message: `Опорная точка вне сезона (1–${frame.episodes})` });
    }
  }
  if (frame.free >= frame.episodes) issues.push({ file, field: 'season_frame.free', message: 'Бесплатных серий больше, чем всего' });

  let expected = 1;
  frame.blocks.forEach(([a, b], i) => {
    if (a !== expected || b < a) {
      issues.push({ file, field: `season_frame.blocks[${i}]`, message: `Блок должен начинаться с серии ${expected}` });
    }
    expected = b + 1;
  });
  if (expected !== frame.episodes + 1) {
    issues.push({ file, field: 'season_frame.blocks', message: `Блоки должны покрывать серии 1–${frame.episodes}` });
  }

  const v = frame.villains;
  if (!v) return;
  const ranks = Array.from({ length: v.count }, (_, i) => String(i + 1));
  const byRank = { roles: v.roles, on_screen_by: v.on_screen_by, takedowns: v.takedowns };
  for (const [field, map] of Object.entries(byRank)) {
    const keys = Object.keys(map).sort();
    if (keys.join() !== [...ranks].sort().join()) {
      issues.push({
        file,
        field: `season_frame.villains.${field}`,
        message: `Нужны все ранги от 1 до ${v.count} (злодеев ${v.count}), без лишних`,
      });
    }
  }
  for (const [rank, spec] of Object.entries(v.takedowns)) {
    if (!inSeason(episodeRange(spec).max)) {
      issues.push({ file, field: `season_frame.villains.takedowns.${rank}`, message: 'Снятие вне сезона' });
    }
  }
  for (const [field, list] of [['turned_ally_ranks', v.turned_ally_ranks], ['public_and_legal', v.public_and_legal]] as const) {
    if (list.some((r) => r > v.count)) {
      issues.push({ file, field: `season_frame.villains.${field}`, message: `Ранг больше числа злодеев (${v.count})` });
    }
  }
}

function isDefined<T>(v: T | undefined): v is T {
  return v !== undefined;
}
