import type { Checklist, RulesFile } from '@aiw/kb';
import type { Finding } from '../../schemas/finding.ts';
import { makeFinding } from './finding.ts';
import { CODE_CHECKS, codeMatches, UnknownCheckError, type CodeCheckResult } from './runner.ts';

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
 * Scores the season checklist from code check results. Code can fail an item (its rule has
 * blocker or major findings, or any finding when the rule is also judged by a model);
 * it can pass an item only when the rule has no model part. The rest goes to the model-judge.
 */
export function scoreChecklist(checklist: Checklist, rules: RulesFile, result: CodeCheckResult): ChecklistScore {
  const byId = new Map(rules.rules.map((r) => [r.id, r]));
  const items = checklist.items.map((item) => {
    let status: ItemStatus = 'unknown';
    const rule = item.rule ? byId.get(item.rule) : undefined;
    if (rule && item.only) assertOnlyCodes(item.id, item.only, rule.check.run.map((r) => r.fn));
    if (rule && result.evaluatedRules.has(rule.id)) {
      const only = item.only;
      const found = (result.byRule[rule.id] ?? []).filter((f) => !only || only.some((o) => codeMatches(f.check, o)));
      // A model-judged rule: even a suspicion (e.g. a legal marker) means the item is not done.
      const bad = rule.check.llm ? found.length > 0 : found.some((f) => f.severity !== 'minor');
      status = bad ? 'fail' : rule.check.llm ? 'unknown' : 'ok';
    }
    return { id: item.id, text: item.text, points: item.points, rule: item.rule, status };
  });
  const score = items.filter((i) => i.status === 'ok').reduce((n, i) => n + i.points, 0);
  const unknownPoints = items.filter((i) => i.status === 'unknown').reduce((n, i) => n + i.points, 0);
  const passed = score >= checklist.pass ? true : score + unknownPoints < checklist.pass ? false : null;

  const base = { score, total: checklist.total, pass: checklist.pass, unknownPoints, passed, items };
  return { ...base, finding: checklistFinding(base) };
}

/**
 * Blocking finding when the checklist is below the pass mark. `undecided` — also when
 * points nobody could confirm decide it (after the model-judge had its say).
 */
export function checklistFinding(score: Omit<ChecklistScore, 'finding'>, undecided = false): Finding | undefined {
  if (score.passed === true || (score.passed === null && !undecided)) return undefined;
  const failed = score.items.filter((i) => i.status === 'fail');
  const unknown = score.items.filter((i) => i.status === 'unknown');
  return makeFinding({
    check: 'checklist.score',
    controller: 'genre',
    severity: 'blocker',
    holeType: 10,
    quote:
      `Чек-лист: ${score.score} из ${score.total}, порог ${score.pass}.` +
      (failed.length ? ` Не выполнено: ${failed.map((i) => i.text).join('; ')}.` : '') +
      (unknown.length ? ` Не подтверждено: ${unknown.map((i) => i.text).join('; ')}.` : ''),
    question: 'почему это должно меня зацепить?',
    fixes: [`Закрыть пункты чек-листа до ${score.pass} баллов`],
  });
}

/** A checklist filter must name a code one of the rule's checks can emit, or it silently scores nothing. */
function assertOnlyCodes(itemId: string, only: string[], fns: string[]): void {
  for (const o of only) {
    const ok = fns.some((fn) => {
      const codes = CODE_CHECKS[fn]?.codes;
      if (!o.startsWith(`${fn}.`)) return false;
      const code = o.slice(fn.length + 1);
      return codes === null || codes === undefined ? !!CODE_CHECKS[fn] : codes.some((c) => code === c || code.startsWith(`${c}.`));
    });
    if (!ok) throw new UnknownCheckError(`Пункт чек-листа ${itemId}: фильтр «${o}» не совпадает ни с одним кодом проверок его правила`);
  }
}
