// Подсветка цитат (как konveyer/htmlreview.py), построчный дифф и текст ошибок без префикса «ApiError: ».
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError, describeError, errText, isConflict, isOffline } from "../src/api";
import { compactDiff, lineDiff } from "../src/diff";
import { esc, highlight, planMarks } from "../src/highlight";

test("пересечения отбрасываются, текст без тегов не меняется", () => {
  const raw = "Чай остыл давно. Зоя <б> молчала и ждала. Потом ушла.";
  const marks = [
    { quote: "остыл давно. Зоя", cls: "FLAG", id: "a-1", title: "пересекается" },
    { quote: "Чай остыл давно.", cls: "samovolka", id: "a-F-1", title: "самоволка" },
    { quote: "Потом ушла.", cls: "violation", id: "a-F-2", title: "отдельная" },
    { quote: "нет такого", cls: "FLAG", id: "a-9", title: "не найдена" },
  ];
  assert.deepEqual(planMarks(raw, marks).map((p) => p.mark.id), ["a-F-1", "a-F-2"]);
  const html = highlight(raw, marks);
  assert.equal((html.match(/<mark/g) || []).length, (html.match(/<\/mark>/g) || []).length);
  assert.equal(html.replace(/<\/?mark[^>]*>/g, ""), esc(raw));
  assert.ok(html.includes("&lt;б&gt;"));
});

test("атрибуты экранируются", () => {
  const html = highlight("Слово.", [{ quote: "Слово", cls: "x\" onmouseover=\"alert(1)", id: "a-\"><img>", title: "t \"q\" <b>" }]);
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&quot;"));
});

test("построчный дифф и его сжатие", () => {
  const d = lineDiff("а\nб\nв\nг\nд\nе\nж\n", "а\nб\nв\nг\nд\nе\nз\n");
  assert.equal(d.approximate, false);
  const kinds = d.lines.map((l) => l.kind).join("");
  assert.ok(kinds.includes("-") && kinds.includes("+"));
  const rows = compactDiff(d.lines);
  assert.ok(rows.some((r) => r.kind === "…"));
  assert.ok(rows.some((r) => r.kind === "+" && r.text === "з"));
});

test("ошибки API: русское описание по коду, без префикса «ApiError: »", () => {
  const e = new ApiError(describeError(409, "документ изменён"), 409, "конфликт");
  assert.equal(String(e).startsWith("ApiError:"), true);
  assert.equal(errText(e), "конфликт (уже есть или изменено): документ изменён");
  assert.ok(isConflict(e) && !isOffline(e));
  assert.ok(isOffline(new ApiError("нет связи", 0)));
  assert.equal(errText("Error: x"), "x");
  assert.match(describeError(423, "дождитесь"), /^сервер занят/);
  assert.match(describeError(404, "нет"), /^нет такой главы/);
});
