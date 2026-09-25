// Разбор правки.md на клиенте повторяет автомат konveyer/review.py::parse_edits_text (FR-RV-3).
import assert from "node:assert/strict";
import { test } from "node:test";
import { appendPair, countOccurrences, describeFound, hasParagraphBreak, parseEdits } from "../src/edits";

test("пары БЫЛО/СТАЛО и свободные указания", () => {
  const r = parseEdits("БЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\n\nУКАЗАНИЕ: короче.\n");
  assert.equal(r.errors.length, 0);
  assert.deepEqual(r.edits.map((e) => [e.seq, e.before, e.after, e.note]), [
    [1, "Чай остыл.", "Чай остыл давно.", false],
    [2, "", "короче.", true],
  ]);
});

test("«БЫЛО» без «СТАЛО» — ошибка с номером строки, как у сервера", () => {
  const r = parseEdits("БЫЛО: Чай\nСТАЛО: Кофе\nБЫЛО: x\n");
  assert.equal(r.edits.length, 1);
  assert.equal(r.errors.length, 1);
  assert.match(r.errors[0], /строка 3/);
});

test("пустое «СТАЛО» — удаление; примеры в ```-блоках не правки", () => {
  const r = parseEdits("```\nБЫЛО: пример\nСТАЛО: пример\n```\nБЫЛО: лишнее слово\nСТАЛО:\n");
  assert.equal(r.edits.length, 1);
  assert.equal(r.edits[0].after, "");
  assert.equal(r.edits[0].line, 5);
});

test("найдено дословно N раз", () => {
  assert.equal(countOccurrences("а б а", "а"), 2);
  assert.equal(countOccurrences("а б", "в"), 0);
  assert.equal(describeFound(1).cls, "ok");
  assert.equal(describeFound(0).cls, "bad");
  assert.equal(describeFound(2).cls, "warn");
  assert.match(describeFound(2).text, /2/);
});

test("пара из выделения добавляется в конец правки.md", () => {
  const next = appendPair("УКАЗАНИЕ: короче.\n", "Чай остыл.", "Чай остыл давно.");
  const r = parseEdits(next);
  assert.equal(r.errors.length, 0);
  assert.equal(r.edits.length, 2);
  assert.equal(r.edits[1].before, "Чай остыл.");
  assert.ok(hasParagraphBreak("а\n\nб"));
  assert.ok(!hasParagraphBreak("а\nб"));
});
