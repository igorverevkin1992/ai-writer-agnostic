import type { Checklist, RulesFile } from '@aiw/kb';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';
import type { CodeCheckResult } from './runner.ts';

export type ItemStatus = 'ok' | 'fail' | 'unknown';

export interface ChecklistScore {
  score: number;
  total: number;
  pass: number;
  /** Points code cannot judge (rule without a code part, or input not ready): left to the model-judge. */
  unknownPoints: number;
  /** true/false once decided by code; null when the unknown points decide it. */
  passed: boolean | null;
  items: { id: string; text: string; points: number; rule?: string; status: ItemStatus }[];
  finding?: Finding;
}

/**
 * Scores the season checklist from code check results: an item scores when its rule
 * was evaluated by code and has no blocker or major findings.
 */
export function scoreChecklist(checklist: Checklist, rules: RulesFile, result: CodeCheckResult): ChecklistScore {
  const known = new Set(rules.rules.map((r) => r.id));
  const items = checklist.items.map((item) => {
    let status: ItemStatus = 'unknown';
    if (item.rule && known.has(item.rule) && result.evaluatedRules.has(item.rule)) {
      const bad = (result.byRule[item.rule] ?? []).some((f) => f.severity !== 'minor');
      status = bad ? 'fail' : 'ok';
    }
    return { id: item.id, text: item.text, points: item.points, rule: item.rule, status };
  });
  const score = items.filter((i) => i.status === 'ok').reduce((n, i) => n + i.points, 0);
  const unknownPoints = items.filter((i) => i.status === 'unknown').reduce((n, i) => n + i.points, 0);
  const passed = score >= checklist.pass ? true : score + unknownPoints < checklist.pass ? false : null;

  const failed = items.filter((i) => i.status === 'fail');
  const finding =
    passed === false
      ? makeFinding({
          check: 'checklist.score',
          controller: 'genre',
          severity: 'blocker',
          holeType: 10,
          quote: `Чек-лист: ${score} из ${checklist.total}, порог ${checklist.pass}. Не выполнено: ${failed.map((i) => i.text).join('; ')}`,
          question: 'почему это должно меня зацепить?',
          fixes: [`Закрыть пункты чек-листа до ${checklist.pass} баллов`],
        })
      : undefined;
  return { score, total: checklist.total, pass: checklist.pass, unknownPoints, passed, items, finding };
}
