export interface KbIssue {
  /** Path relative to the knowledge base root, e.g. "rules/revenge_thriller.yaml". */
  file: string;
  message: string;
  line?: number;
  column?: number;
  /** Field path inside the file, e.g. "rules[2].severity". */
  field?: string;
}

export function formatIssue(issue: KbIssue): string {
  const where = [issue.file];
  if (issue.line !== undefined) where.push(`строка ${issue.line}${issue.column ? `, столбец ${issue.column}` : ''}`);
  const field = issue.field ? ` — поле «${issue.field}»` : '';
  return `${where.join(', ')}${field}: ${issue.message}`;
}

export class KbLoadError extends Error {
  readonly issues: KbIssue[];

  constructor(issues: KbIssue[]) {
    const n = issues.length;
    const header = `База знаний не загрузилась: ${n} ${plural(n, 'ошибка', 'ошибки', 'ошибок')}.`;
    super([header, ...issues.map((i) => `  • ${formatIssue(i)}`)].join('\n'));
    this.name = 'KbLoadError';
    this.issues = issues;
  }
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
