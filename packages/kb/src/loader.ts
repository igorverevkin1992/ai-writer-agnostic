import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LineCounter, isNode, parseDocument, type Document } from 'yaml';
import type { z } from 'zod';
import { KbLoadError, formatIssue, type KbIssue } from './errors.ts';
import { evalEpisodeExpr, resolveFrameInputs, type FrameInputs } from './frameInputs.ts';
import {
  COMMON_RULES_ID,
  Case,
  Checklist,
  Genre,
  type GenreTerms,
  Glossary,
  Guide,
  HoleCatalog,
  ReviewMethod,
  LegalConstraints,
  Method,
  Personas,
  ProductionConstraints,
  type Rule,
  type RuleOverride,
  RulesFile,
  SeasonFrame,
  episodeRange,
} from './schemas.ts';

export const DEFAULT_KB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const METHOD_IDS = ['weiland', 'truby', 'harmon', 'mowery', 'rabkin', 'vertical'] as const;

type Frame = z.infer<typeof SeasonFrame>['season_frame'];

/** Files of one kind keyed by file name without extension, e.g. frames["season60"]. */
type ById<T> = Record<string, T>;

export interface Kb {
  root: string;
  /** Genre packs keyed by id. A project picks one of them. */
  genres: ById<Genre>;
  rules: ById<RulesFile>;
  frames: ById<Frame>;
  /** Frames as written in YAML, before their inputs are resolved: a project may resolve them with its own inputs. */
  rawFrames: ById<unknown>;
  checklists: ById<Checklist>;
  personas: ById<Personas>;
  legal: ById<LegalConstraints>;
  production: ById<ProductionConstraints>;
  guides: ById<Guide>;
  /** Shared by all genres. */
  methods: Record<(typeof METHOD_IDS)[number], Method>;
  glossary: Glossary;
  holes: HoleCatalog;
  /** Method of the hole review (review/review.yaml), shared by all genres. */
  review: ReviewMethod;
  cases: Case[];
  /** Prompt texts keyed by "<role>/<step>". */
  prompts: Record<string, string>;
}

/**
 * Everything that applies to a project of one genre. Rules are the genre's own plus the common
 * ones (rules/common.yaml) with the genre's overrides; every text has its placeholders filled.
 */
export interface GenreKit {
  genre: Genre;
  rules: RulesFile;
  frame: Frame;
  checklist: Checklist;
  personas: Personas;
  legal: LegalConstraints;
  production: ProductionConstraints;
  guide?: Guide;
}

export class UnknownGenreError extends Error {
  override name = 'UnknownGenreError';
}

/** A project's frame inputs do not fit the frame: unknown name, anchor outside the season. */
export class FrameInputError extends Error {
  override name = 'FrameInputError';
}

/** What a project may change in its genre pack: frame inputs (e.g. paywall) and the words for the hero. */
export interface GenreOverrides {
  inputs?: FrameInputs;
  terms?: Partial<GenreTerms>;
}

/**
 * Resolves a genre pack. References are validated at load time, so without overrides this only
 * fails on an unknown id. With `overrides` the frame is resolved again with the project's inputs
 * and checked like a file; a bad input throws FrameInputError with a readable message.
 */
export function genreKit(kb: Kb, genreId: string, overrides?: GenreOverrides): GenreKit {
  const genre = kb.genres[genreId];
  if (!genre) {
    throw new UnknownGenreError(
      `Жанр «${genreId}» не найден. Есть: ${Object.keys(kb.genres).join(', ') || 'ни одного'}.`,
    );
  }
  // The project's frame inputs and hero terms apply before the texts are rendered, so every
  // number and every «героиня» in rules, checklist and guide follows the project.
  const inputs = overrides?.inputs ?? {};
  const terms = overrides?.terms;
  const kit: GenreKit = {
    genre: terms ? { ...genre, terms: { ...genre.terms, ...terms, hero: { ...genre.terms.hero, ...terms.hero } } } : genre,
    rules: mergeRules(kb.rules[genre.rules]!, genre.common_rules ? kb.rules[COMMON_RULES_ID] : undefined),
    frame: Object.keys(inputs).length ? frameWithInputs(kb, genre.frame, inputs) : kb.frames[genre.frame]!,
    checklist: kb.checklists[genre.checklist]!,
    personas: kb.personas[genre.personas]!,
    legal: kb.legal[genre.constraints.legal]!,
    production: kb.production[genre.constraints.production]!,
    guide: genre.guide ? kb.guides[genre.guide] : undefined,
  };
  return renderKit(kit);
}

/** The genre's rules followed by the common ones, each with the genre's override applied. */
export function mergeRules(genre: RulesFile, common: RulesFile | undefined): RulesFile {
  if (!common) return genre;
  return { ...genre, rules: [...genre.rules, ...common.rules.map((r) => applyOverride(r, genre.common[r.id]))] };
}

function applyOverride(rule: Rule, o: RuleOverride | undefined): Rule {
  if (!o) return rule;
  const check = { ...rule.check };
  if (o.check) {
    if (o.check.code === null) delete check.code;
    else if (o.check.code !== undefined) check.code = o.check.code;
    if (o.check.llm === null) delete check.llm;
    else if (o.check.llm !== undefined) check.llm = o.check.llm;
    if (o.check.run) check.run = o.check.run;
  }
  return { ...rule, text: o.text ?? rule.text, severity: o.severity ?? rule.severity, fix_hints: o.fix_hints ?? rule.fix_hints, check };
}

/** Values the texts of a kit may refer to by `{{name}}`. */
export type KitVars = Record<string, string | number>;

/**
 * One source for every number: the frame (`L`, `free`, `inputs.<name>`, `anchors.<id>` as «a–b»,
 * `anchors.<id>.min|max`, `blocks.<n>`, `rhythm.<key>`), production limits (`limits.<key>`,
 * `metrics.<key>`), legal (`age_rating`) and the hero words (`hero.nom|gen|dat`, `wound`).
 * A project that overrides frame inputs or hero terms rebuilds the kit and renders it with these vars.
 */
export function kitVars(kit: Pick<GenreKit, 'genre' | 'frame' | 'production' | 'legal'>): KitVars {
  const f = kit.frame;
  const p = kit.production;
  const t = kit.genre.terms;
  const range = (min: number, max: number) => (min === max ? min : `${min}–${max}`);
  const vars: KitVars = {
    L: f.episodes,
    episodes: f.episodes,
    free: f.free,
    tolerance: f.tolerance,
    'duration.min': f.duration_s.min,
    'duration.max': f.duration_s.max,
    'duration.target': f.duration_s.target,
    'hero.nom': t.hero.nom,
    'hero.gen': t.hero.gen,
    'hero.dat': t.hero.dat,
    wound: t.wound,
    age_rating: kit.legal.age_rating,
  };
  for (const [k, v] of Object.entries(f.inputs)) vars[`inputs.${k}`] = v;
  for (const [id, spec] of Object.entries(f.anchors)) {
    const { min, max } = episodeRange(spec);
    vars[`anchors.${id}`] = range(min, max);
    vars[`anchors.${id}.min`] = min;
    vars[`anchors.${id}.max`] = max;
  }
  f.blocks.forEach(([a, b], i) => {
    vars[`blocks.${i + 1}`] = range(a, b);
    vars[`blocks.${i + 1}.min`] = a;
    vars[`blocks.${i + 1}.max`] = b;
  });
  const numbers = (prefix: string, obj: Record<string, unknown>) => {
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === 'number') vars[`${prefix}.${k}`] = v;
      else if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === 'number')) vars[`${prefix}.${k}`] = range(v[0] as number, v[1] as number);
    }
  };
  numbers('rhythm', f.rhythm);
  numbers('limits', p.limits);
  numbers('metrics', p.script_metrics);
  if (f.villains) vars['villains.count'] = f.villains.count;
  return vars;
}

const PLACEHOLDER = /\{\{\s*([^{}]+?)\s*\}\}/gu;

/**
 * Fills `{{name}}` from vars. A placeholder with arithmetic (`{{L/30}}`, `{{0.42*L}}`) is
 * evaluated over the numeric vars and rounded. Unknown names throw: the loader turns that into
 * an issue naming the file.
 */
export function fillPlaceholders(text: string, vars: KitVars): string {
  return text.replace(PLACEHOLDER, (_, name: string) => {
    const v = vars[name];
    if (v !== undefined) return String(v);
    if (/[-+*/]/u.test(name)) return String(evalVarExpr(name, vars));
    throw new Error(`Неизвестный параметр «{{${name}}}»`);
  });
}

function evalVarExpr(expr: string, vars: KitVars): number {
  const nums: Record<string, number> = {};
  const expanded = expr.replace(/[A-Za-z_][\w.]*/gu, (name) => {
    const v = vars[name];
    if (typeof v !== 'number') throw new Error(`В выражении «{{${expr}}}» нет числа «${name}»`);
    const key = `v${Object.keys(nums).length}`;
    nums[key] = v;
    return key;
  });
  return evalEpisodeExpr(expanded, nums);
}

const NUMERIC = /^\s*-?\d+(?:\.\d+)?\s*$/u;
const ARITHMETIC = /^[\d\s.+\-*/()]+$/u;

/** Check params: a string with placeholders becomes a number when what is left is a number or arithmetic. */
function fillParam(value: unknown, vars: KitVars): unknown {
  if (typeof value === 'string') {
    if (!value.includes('{{')) return value;
    const filled = fillPlaceholders(value, vars);
    if (NUMERIC.test(filled)) return Number(filled);
    if (ARITHMETIC.test(filled) && /\d/u.test(filled)) return evalEpisodeExpr(filled, {});
    return filled;
  }
  if (Array.isArray(value)) return value.map((v) => fillParam(v, vars));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fillParam(v, vars)]));
  return value;
}

function renderRule(rule: Rule, vars: KitVars): Rule {
  const fill = (s: string) => fillPlaceholders(s, vars);
  return {
    ...rule,
    text: fill(rule.text),
    fix_hints: rule.fix_hints.map(fill),
    check: {
      ...rule.check,
      ...(rule.check.code !== undefined ? { code: fill(rule.check.code) } : {}),
      ...(rule.check.llm !== undefined ? { llm: fill(rule.check.llm) } : {}),
      run: rule.check.run.map((r) => ({ ...r, params: fillParam(r.params, vars) as Record<string, unknown> })),
    },
  };
}

/** Fills the placeholders of every text in the kit: rules, checklist, personas, guide. */
export function renderKit(kit: GenreKit, vars: KitVars = kitVars(kit)): GenreKit {
  const fill = (s: string) => fillPlaceholders(s, vars);
  return {
    ...kit,
    rules: { ...kit.rules, rules: kit.rules.rules.map((r) => renderRule(r, vars)) },
    checklist: { ...kit.checklist, items: kit.checklist.items.map((i) => ({ ...i, text: fill(i.text) })) },
    personas: { ...kit.personas, personas: kit.personas.personas.map((p) => ({ ...p, focus: fill(p.focus) })) },
    guide: kit.guide
      ? {
          ...kit.guide,
          sections: kit.guide.sections.map((s) => ({ title: fill(s.title), items: s.items.map(fill) })),
          hole_questions: Object.fromEntries(Object.entries(kit.guide.hole_questions).map(([k, qs]) => [k, qs.map(fill)])),
        }
      : undefined,
  };
}

/** The frame resolved with the project's inputs instead of its defaults, validated like a file. */
export function frameWithInputs(kb: Kb, frameId: string, inputs: FrameInputs): Frame {
  const base = kb.frames[frameId];
  const raw = kb.rawFrames[frameId];
  if (!base || raw === undefined) throw new UnknownGenreError(`Каркас «${frameId}» не найден`);
  const known = Object.keys(base.inputs);
  const unknown = Object.keys(inputs).filter((k) => !known.includes(k));
  if (unknown.length) {
    throw new FrameInputError(
      `Каркас не знает ${unknown.length === 1 ? 'параметра' : 'параметров'} ${unknown.map((k) => `«${k}»`).join(', ')}. ${known.length ? `Есть: ${known.join(', ')}` : 'У этого каркаса нет параметров'}`,
    );
  }
  for (const [k, v] of Object.entries(inputs)) {
    if (!Number.isFinite(v)) throw new FrameInputError(`Параметр «${k}» должен быть числом`);
  }
  let resolved: unknown;
  try {
    resolved = resolveFrameInputs(raw, inputs);
  } catch (e) {
    throw new FrameInputError(e instanceof Error ? e.message : String(e));
  }
  const file = `frames/${frameId}.yaml`;
  const parsed = SeasonFrame.safeParse(resolved);
  if (!parsed.success) {
    throw new FrameInputError(parsed.error.issues.map((i) => formatIssue({ file, field: formatPath(i.path), message: i.message })).join('; '));
  }
  const issues: KbIssue[] = [];
  checkFrame(file, parsed.data.season_frame, issues);
  if (issues.length) {
    throw new FrameInputError(`С такими параметрами каркас не сходится: ${issues.map((i) => `${i.field ? `${i.field} — ` : ''}${i.message}`).join('; ')}`);
  }
  return parsed.data.season_frame;
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
  const guides = readDir('guides', Guide);
  const frames = {
    loaded: Object.fromEntries(Object.entries(frameFiles.loaded).map(([id, f]) => [id, f.season_frame])) as ById<Frame>,
    failed: frameFiles.failed,
  };
  const rawFrames: ById<unknown> = Object.fromEntries(
    Object.keys(frames.loaded).map((id) => [id, parseDocument(readFileSync(join(root, `frames/${id}.yaml`), 'utf8'), { uniqueKeys: true }).toJS()]),
  );

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
  // With a broken genre file we cannot tell which constraints it used: skip, avoid false alarms.
  for (const file of genres.failed.size > 0 ? [] : listFiles(root, 'constraints', '.yaml')) {
    if (!usedConstraints.has(basename(file, '.yaml'))) {
      issues.push({ file, message: 'Файл не указан ни в одном жанре (genres/*.yaml), поэтому его нельзя проверить' });
    }
  }

  const methods = Object.fromEntries(
    METHOD_IDS.map((id) => [id, read(`methods/${id}.yaml`, Method)]),
  ) as Partial<Kb['methods']>;
  const glossary = read('glossary.yaml', Glossary);
  const holes = read('holes/catalog.yaml', HoleCatalog);
  const review = read('review/review.yaml', ReviewMethod);
  if (review) {
    const seen = new Set<string>();
    review.categories.forEach((c, i) => {
      if (seen.has(c.id)) issues.push({ file: 'review/review.yaml', field: `categories[${i}].id`, message: `Категория ${c.id} встречается дважды` });
      seen.add(c.id);
    });
    for (const h of holes?.holes ?? []) {
      if (!review.categories.some((c) => c.hole_types.includes(h.id))) {
        issues.push({ file: 'review/review.yaml', message: `Тип дыры ${h.id} («${h.name}») не получает правил ни одной категории` });
      }
    }
  }
  const cases = listFiles(root, 'cases', '.yaml')
    .map((f) => read(f, Case))
    .filter(isDefined);
  const prompts = readPrompts(root, issues);

  crossCheck(
    { genres, rules, frames, checklists, personas, legal, production, holes, methods, guides },
    issues,
  );

  if (issues.length > 0 || !glossary || !holes || !review || METHOD_IDS.some((id) => !methods[id])) {
    throw new KbLoadError(issues);
  }

  return {
    root,
    genres: genres.loaded,
    rules: rules.loaded,
    frames: frames.loaded,
    rawFrames,
    checklists: checklists.loaded,
    personas: personas.loaded,
    legal,
    production,
    guides: guides.loaded,
    methods: methods as Kb['methods'],
    glossary,
    holes,
    review,
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
    guides: Loaded<Guide>;
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
    if (g.guide && !(g.guide in kb.guides.loaded) && !kb.guides.failed.has(g.guide)) {
      issues.push({ file, field: 'guide', message: `Нет файла guides/${g.guide}.yaml` });
    }

    const own = kb.rules.loaded[g.rules];
    const common = kb.rules.loaded[COMMON_RULES_ID];
    if (g.common_rules && !common && !kb.rules.failed.has(COMMON_RULES_ID)) {
      issues.push({ file, field: 'common_rules', message: `Нет файла rules/${COMMON_RULES_ID}.yaml` });
    }
    if (own && !g.common_rules && Object.keys(own.common).length) {
      issues.push({ file, field: 'common_rules', message: `В rules/${g.rules}.yaml есть переопределения общих правил (common), а у жанра нет common_rules: true` });
    }
    const rules = own ? mergeRules(own, g.common_rules ? common : undefined) : undefined;
    /** Where a merged rule lives: the genre file for R.., the override there or common.yaml for C... */
    const ruleFile = (rule: Rule) => (rule.id.startsWith('C') && !own?.common[rule.id] ? `rules/${COMMON_RULES_ID}.yaml` : `rules/${g.rules}.yaml`);
    rules?.rules.forEach((rule) => {
      if (!rule.check.code && !rule.check.llm) {
        issues.push({ file: `rules/${g.rules}.yaml`, field: `common.${rule.id}.check`, message: `У правила ${rule.id} после переопределения не осталось ни одной проверки: code или llm` });
      }
    });
    const checklist = kb.checklists.loaded[g.checklist];
    const frame = kb.frames.loaded[g.frame];
    if (frame) {
      const anchors = new Set(Object.keys(frame.anchors));
      const bad = (where: KbIssue, id: string) =>
        issues.push({ ...where, message: `Нет опорной точки «${id}» в frames/${g.frame}.yaml (жанр ${g.id})` });
      rules?.rules.forEach((rule) =>
        rule.check.run.forEach((run, ki) => {
          const where = { file: ruleFile(rule), field: `${rule.id}.check.run[${ki}]`, message: '' };
          const named = [
            ...(run.only ?? []).filter((o) => o.startsWith('anchor.')).map((o) => o.slice('anchor.'.length)),
            ...(Array.isArray(run.params.near) ? (run.params.near as unknown[]).map(String) : []),
            ...(typeof run.params.anchor === 'string' ? [run.params.anchor] : []),
          ];
          for (const id of named) if (!anchors.has(id)) bad(where, id);
        }),
      );
      frame.rhythm.line_gap_exempt.forEach((id, i) => {
        if (!anchors.has(id)) bad({ file: `frames/${g.frame}.yaml`, field: `rhythm.line_gap_exempt[${i}]`, message: '' }, id);
      });
      if (frame.rhythm.max_without_line && Object.keys(frame.lines).length === 0) {
        issues.push({ file: `frames/${g.frame}.yaml`, field: 'rhythm.max_without_line', message: 'Задан лимит серий без линии, но линии (lines) не заданы' });
      }
      for (const id of Object.keys(frame.anchors)) {
        if (Object.keys(frame.anchor_labels).length && !frame.anchor_labels[id]) {
          issues.push({ file: `frames/${g.frame}.yaml`, field: 'anchor_labels', message: `У опорной точки «${id}» нет названия` });
        }
      }
      checklist?.items.forEach((item, i) => {
        for (const o of item.only ?? []) {
          const m = /^season_frame\.anchor\.(.+)$/u.exec(o);
          if (m && !anchors.has(m[1]!)) bad({ file: `checklist/${g.checklist}.yaml`, field: `items[${i}].only`, message: '' }, m[1]!);
        }
      });
    }
    if (rules && checklist) {
      const ids = new Set(rules.rules.map((r) => r.id));
      const where = g.common_rules ? `rules/${g.rules}.yaml и rules/${COMMON_RULES_ID}.yaml` : `rules/${g.rules}.yaml`;
      checklist.items.forEach((item, i) => {
        if (item.rule && !ids.has(item.rule)) {
          issues.push({
            file: `checklist/${g.checklist}.yaml`,
            field: `items[${i}].rule`,
            message: `Нет правила ${item.rule} в ${where} (жанр ${id})`,
          });
        }
      });
    }

    // Every placeholder in the kit's texts must resolve against this genre's numbers and words.
    const personas = kb.personas.loaded[g.personas];
    const legal = kb.legal[g.constraints.legal];
    const production = kb.production[g.constraints.production];
    const guide = g.guide ? kb.guides.loaded[g.guide] : undefined;
    if (rules && frame && checklist && personas && legal && production && (!g.guide || guide)) {
      const kit: GenreKit = { genre: g, rules, frame, checklist, personas, legal, production, guide };
      const vars = kitVars(kit);
      const tryFill = (where: KbIssue, texts: string[]) => {
        for (const text of texts) {
          try {
            fillPlaceholders(text, vars);
          } catch (e) {
            issues.push({ ...where, message: `${e instanceof Error ? e.message : String(e)} (жанр ${g.id})` });
            return;
          }
        }
      };
      rules.rules.forEach((rule) => {
        const params = rule.check.run.flatMap((r) => JSON.stringify(r.params));
        tryFill({ file: ruleFile(rule), field: rule.id, message: '' }, [rule.text, rule.check.code ?? '', rule.check.llm ?? '', ...rule.fix_hints, ...params]);
      });
      checklist.items.forEach((item, i) => tryFill({ file: `checklist/${g.checklist}.yaml`, field: `items[${i}].text`, message: '' }, [item.text]));
      personas.personas.forEach((p, i) => tryFill({ file: `personas/${g.personas}.yaml`, field: `personas[${i}].focus`, message: '' }, [p.focus]));
      guide?.sections.forEach((s, i) => tryFill({ file: `guides/${g.guide}.yaml`, field: `sections[${i}]`, message: '' }, [s.title, ...s.items]));
      for (const [k, qs] of Object.entries(guide?.hole_questions ?? {})) tryFill({ file: `guides/${g.guide}.yaml`, field: `hole_questions.${k}`, message: '' }, qs);
    }
  }

  for (const [id, file] of Object.entries(kb.rules.loaded)) {
    const name = `rules/${id}.yaml`;
    if (file.module !== id) issues.push({ file: name, field: 'module', message: `Модуль должен быть «${id}», как имя файла` });
    const seen = new Set<string>();
    const prefix = id === COMMON_RULES_ID ? 'C' : 'R';
    file.rules.forEach((rule, ri) => {
      if (seen.has(rule.id)) issues.push({ file: name, field: `rules[${ri}].id`, message: `Правило ${rule.id} встречается дважды` });
      seen.add(rule.id);
      if (rule.module !== file.module) {
        issues.push({ file: name, field: `rules[${ri}].module`, message: `Модуль правила «${rule.module}» не совпадает с модулем файла «${file.module}»` });
      }
      if (!rule.id.startsWith(prefix)) {
        issues.push({ file: name, field: `rules[${ri}].id`, message: id === COMMON_RULES_ID ? `Общие правила нумеруются C01, C02…, а не ${rule.id}` : `Правила жанра нумеруются R01, R02…; общие (C…) живут в rules/${COMMON_RULES_ID}.yaml` });
      }
    });
    const common = kb.rules.loaded[COMMON_RULES_ID];
    if (id !== COMMON_RULES_ID && common) {
      const ids = new Set(common.rules.map((r) => r.id));
      for (const key of Object.keys(file.common)) {
        if (!ids.has(key)) issues.push({ file: name, field: `common.${key}`, message: `Нет правила ${key} в rules/${COMMON_RULES_ID}.yaml` });
      }
    }
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

  for (const [id, guide] of Object.entries(kb.guides.loaded)) {
    for (const key of Object.keys(guide.hole_questions)) {
      if (kb.holes && !kb.holes.holes.some((h) => h.id === Number(key))) {
        issues.push({ file: `guides/${id}.yaml`, field: `hole_questions.${key}`, message: `Нет типа дыры ${key} в holes/catalog.yaml` });
      }
    }
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
  for (const id of Object.keys(frame.anchor_labels)) {
    if (!(id in frame.anchors)) issues.push({ file, field: `season_frame.anchor_labels.${id}`, message: `Нет опорной точки «${id}» в anchors` });
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
