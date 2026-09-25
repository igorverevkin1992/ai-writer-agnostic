// «Дальше: …», подписи задач и очереди — по состояниям FSM ядра (konveyer/fsm.py, steps/common.py::NEXT_STEP).
import assert from "node:assert/strict";
import { test } from "node:test";
import { JOB_LABEL, MANUAL_JOBS, nextStep, QUEUE_NEXT, TABS } from "../src/nextstep";

const STATES = [
  "не-начато", "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки",
  "дифф-контроль", "принято", "зафиксировано",
];
// команды сервера (konveyer/server.py::COMMANDS) — у каждой должна быть русская подпись
const COMMANDS = [
  "run", "export", "compile", "write", "verify1", "verify2", "review", "apply-edits", "diff-check", "diff-check-author",
  "regress", "canonize", "canonize-apply", "story-circles", "circles-canon", "lint", "lint-llm", "canon-commit",
  "import", "onboarding", "onboarding-apply", "accounting", "retest", "backup-archive", "volume-close", "volume-open",
  "snapshot", "calibrate", "doctor",
];
const ctx = { unresolved: 0, diffClean: null, hasEdits: false, hasBatch: false };

test("у каждого состояния FSM есть подсказка «Дальше» и короткая подпись очереди", () => {
  for (const s of STATES) {
    assert.ok(nextStep(s, ctx).label, s);
    assert.ok(QUEUE_NEXT[s], s);
  }
  assert.equal(nextStep("неизвестно", ctx).label, "");
});

test("подсказка ведёт во вкладку, где делается шаг", () => {
  assert.equal(nextStep("на-приёмке", { ...ctx, unresolved: 2 }).tab, "чтение");
  assert.equal(nextStep("на-приёмке", { ...ctx, hasEdits: true }).tab, "правки");
  assert.equal(nextStep("дифф-контроль", { ...ctx, diffClean: false }).tab, "приёмка");
  assert.equal(nextStep("дифф-контроль", { ...ctx, diffClean: true }).tab, "приёмка");
  assert.match(nextStep("дифф-контроль", { ...ctx, diffClean: true, unresolved: 1 }).label, /самоволки \(1\)/);
  assert.match(nextStep("принято", { ...ctx, hasBatch: true }).label, /Применить пакет/);
  for (const t of [nextStep("на-приёмке", ctx).tab, nextStep("принято", { ...ctx, hasBatch: true }).tab]) {
    assert.ok(t && TABS.includes(t));
  }
});

test("русская подпись есть у каждой команды сервера, латиницы в подписях нет", () => {
  for (const c of COMMANDS) {
    assert.ok(JOB_LABEL[c], c);
    assert.doesNotMatch(JOB_LABEL[c], /[a-z]{3,}/i, c);
  }
  assert.deepEqual(Object.keys(JOB_LABEL).sort(), [...COMMANDS].sort());
  for (const j of MANUAL_JOBS) assert.ok(COMMANDS.includes(j), j);
});
