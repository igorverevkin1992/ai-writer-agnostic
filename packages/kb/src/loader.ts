import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LineCounter, isNode, parseDocument, type Document } from 'yaml';
import type { z } from 'zod';
import { KbLoadError, type KbIssue } from './errors.ts';
import {
  Case,
  Checklist,
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

export interface Kb {
  root: string;
  rules: RulesFile[];
  frame: SeasonFrame;
  checklist: Checklist;
  methods: Record<(typeof METHOD_IDS)[number], Method>;
  glossary: Glossary;
  legal: LegalConstraints;
  production: ProductionConstraints;
  holes: HoleCatalog;
  personas: Personas;
  cases: Case[];
  /** Prompt texts keyed by "<role>/<step>". */
  prompts: Record<string, string>;
}

/**
 * Loads and validates the whole knowledge base. Collects every problem
 * across all files and throws a single KbLoadError listing them.
 */
export function loadKb(root: string = DEFAULT_KB_ROOT): Kb {
  const issues: KbIssue[] = [];

  const read = <T extends z.ZodType>(file: string, schema: T): z.infer<T> | undefined =>
    readYaml(root, file, schema, issues);

  const ruleFiles = listFiles(root, 'rules', '.yaml');
  if (!ruleFiles.includes('rules/revenge_thriller.yaml')) {
    issues.push({ file: 'rules/revenge_thriller.yaml', message: 'Файл не найден' });
  }
  const parsedRules = ruleFiles.map((f) => read(f, RulesFile));
  const rules = parsedRules.filter(isDefined);
  const rulesComplete = rules.length === parsedRules.length && rules.length > 0;
  const frameFile = read('frames/season60.yaml', SeasonFrame);
  const checklist = read('checklist/season20.yaml', Checklist);
  const methods = Object.fromEntries(
    METHOD_IDS.map((id) => [id, read(`methods/${id}.yaml`, Method)]),
  ) as Partial<Kb['methods']>;
  const glossary = read('glossary.yaml', Glossary);
  const legal = read('constraints/ru_legal.yaml', LegalConstraints);
  const production = read('constraints/production.yaml', ProductionConstraints);
  const holes = read('holes/catalog.yaml', HoleCatalog);
  const personas = read('personas/viewers.yaml', Personas);
  const cases = listFiles(root, 'cases', '.yaml')
    .map((f) => read(f, Case))
    .filter(isDefined);
  const prompts = readPrompts(root, issues);

  const frame = frameFile?.season_frame;
  crossCheck({ rules, ruleFiles, rulesComplete, frame, checklist, holes, methods, legal }, issues);

  if (
    issues.length > 0 ||
    !frame ||
    !checklist ||
    !glossary ||
    !legal ||
    !production ||
    !holes ||
    !personas ||
    METHOD_IDS.some((id) => !methods[id])
  ) {
    throw new KbLoadError(issues);
  }

  return {
    root,
    rules,
    frame,
    checklist,
    methods: methods as Kb['methods'],
    glossary,
    legal,
    production,
    holes,
    personas,
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

function crossCheck(
  kb: {
    rules: RulesFile[];
    ruleFiles: string[];
    /** False when some rule file failed to load: skip reference checks to avoid noise. */
    rulesComplete: boolean;
    frame: SeasonFrame | undefined;
    checklist: Checklist | undefined;
    holes: HoleCatalog | undefined;
    methods: Partial<Kb['methods']>;
    legal: LegalConstraints | undefined;
  },
  issues: KbIssue[],
): void {
  const ruleIds = new Set<string>();
  kb.rules.forEach((file, fi) => {
    const name = kb.ruleFiles[fi] ?? 'rules';
    file.rules.forEach((rule, ri) => {
      if (ruleIds.has(rule.id)) issues.push({ file: name, field: `rules[${ri}].id`, message: `Правило ${rule.id} встречается дважды` });
      ruleIds.add(rule.id);
      if (rule.module !== file.module) {
        issues.push({ file: name, field: `rules[${ri}].module`, message: `Модуль правила «${rule.module}» не совпадает с модулем файла «${file.module}»` });
      }
    });
  });

  const { checklist } = kb;
  if (checklist) {
    const file = 'checklist/season20.yaml';
    const sum = checklist.items.reduce((s, i) => s + i.points, 0);
    if (sum > checklist.total) issues.push({ file, message: `Сумма баллов ${sum} больше итога ${checklist.total}` });
    if (!checklist.stub && sum !== checklist.total) {
      issues.push({ file, message: `Сумма баллов ${sum} не равна итогу ${checklist.total}` });
    }
    if (checklist.pass > checklist.total) issues.push({ file, field: 'pass', message: 'Порог больше итога' });
    checklist.items.forEach((item, i) => {
      if (kb.rulesComplete && item.rule && !ruleIds.has(item.rule)) {
        issues.push({ file, field: `items[${i}].rule`, message: `Нет правила ${item.rule} в rules/` });
      }
    });
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

  if (kb.legal) {
    const principles = new Set(kb.legal.principles.map((p) => p.id));
    kb.legal.markers.forEach((m, i) => {
      if (!principles.has(m.principle)) {
        issues.push({ file: 'constraints/ru_legal.yaml', field: `markers[${i}].principle`, message: `Нет принципа ${m.principle}` });
      }
    });
  }

  const { frame } = kb;
  if (frame) {
    const file = 'frames/season60.yaml';
    const inSeason = (n: number) => n >= 1 && n <= frame.episodes;
    for (const [name, spec] of Object.entries(frame.anchors)) {
      const r = episodeRange(spec);
      if (!inSeason(r.min) || !inSeason(r.max)) {
        issues.push({ file, field: `season_frame.anchors.${name}`, message: `Опорная точка вне сезона (1–${frame.episodes})` });
      }
    }
    for (const [rank, spec] of Object.entries(frame.villains.takedowns)) {
      const r = episodeRange(spec);
      if (!inSeason(r.max)) issues.push({ file, field: `season_frame.villains.takedowns.${rank}`, message: 'Снятие вне сезона' });
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
  }
}

function isDefined<T>(v: T | undefined): v is T {
  return v !== undefined;
}
