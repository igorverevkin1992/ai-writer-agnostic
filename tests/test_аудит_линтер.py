"""Аудит кластера «линтер и драматургия»: ложные срабатывания на корректном, но нестандартном каноне
(шаги на одной главе, континуити другого тома, смена года, несколько томов фокала), строки находок,
разбор документа каркасов с заголовком своей методики."""

from __future__ import annotations

from pathlib import Path

import pytest

from konveyer import dramaturgy_doc, exporter, lint
from konveyer.schemas import Act, CircleStep, StoryCircle

PLAN = "23_Поглавник_Том1.md"
FRAMES2 = "21_Круги_истории_Том2.md"


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


def _lint(ws, library, volume: int = 1):
    exporter.run_export(library, ws.exports, ws.logs, volume)
    return lint.run_lint(library, ws.exports, ws.logs, volume=volume, use_cache=False)


# ------------------------------------------------------------------ каркасы: КРУГ-2


def test_круг2_шаги_на_одной_главе_не_наезд_а_шаг_назад_ловится(ws, library):
    """Демо-том 2: восемь шагов на четыре главы — по два шага на главу, находок нет; шаг, начинающийся
    раньше предыдущего, и шаг за границей акта — предупреждения со строкой шага."""
    assert not [f for f in _lint(ws, library, 2).findings if f.code == "КРУГ-2"]
    doc = library / FRAMES2
    _edit(doc, "3. **Переход** (гл. 2) — Зоя встречает поезд.", "3. **Переход** (гл. 1) — Зоя встречает поезд.")   # 1,1,1 — не назад
    _edit(doc, "7. **Возвращение** (гл. 4) — трое в гараже №14.", "7. **Возвращение** (гл. 2) — трое в гараже №14.")  # назад
    _edit(doc, "5. **Обретение** (гл. 2) — Зоя видит отца.", "5. **Обретение** (гл. 3) — Зоя видит отца.")   # акт 1 — гл. 1–2
    report = _lint(ws, library, 2)
    found = [f for f in report.findings if f.code == "КРУГ-2"]
    assert [f.line for f in found] == [19, 29], [f.message for f in found]
    assert found[0].message.startswith("Книга (том целиком), шаг 7 «Возвращение» (гл. 2) начинается раньше предыдущего шага (гл. 3)")
    assert found[1].message.startswith("Акт 1 «Письмо», шаг 5 «Обретение» (гл. 3) выходит за границы 1–2")


def test_круг1_находка_со_строкой_заголовка(ws, library):
    doc = library / FRAMES2
    _edit(doc, "3. **Переход** (гл. 2) — Зоя встречает поезд.\n", "")
    f = next(f for f in _lint(ws, library, 2).findings if f.code == "КРУГ-1")
    assert f.line == 11 and f.message.startswith("Книга (том целиком): не заданы обязательные шаги [3]")


# ------------------------------------------------------------------ континуити: КОНТ-1 по тому


def test_конт1_запись_другого_тома_не_проверяется_по_главам_текущего(ws, library):
    """«т.1 гл.5» при работе над томом 2 (4 главы) — не находка; запись тома 2 с гл. 9 — находка со строкой."""
    assert "КОНТ-1" not in _codes(_lint(ws, library, 2))
    doc = library / "33_Континуити.md"
    _edit(doc, "| т.1 гл.5 | замок гаража №14 со свежими царапинами | 5 | — |",
          "| т.1 гл.5 | замок гаража №14 со свежими царапинами | 5 | — |\n| т.2 гл.9 | Гуляев: седой ёжик | 9 | — |")
    found = [f for f in _lint(ws, library, 2).findings if f.code == "КОНТ-1"]
    assert len(found) == 1 and found[0].line == 9 and "гл. 9, а в томе 4 глав" in found[0].message
    # запись без тома проверяется по текущему тому
    _edit(doc, "| т.2 гл.9 |", "| гараж |")
    assert len([f for f in _lint(ws, library, 2).findings if f.code == "КОНТ-1"]) == 1


# ------------------------------------------------------------------ строки находок по записям реестров


def test_находки_матрицы_и_закладок_указывают_на_строку_записи(ws, library):
    matrix = library / "31_Матрица_знаний.md"
    _edit(matrix, "| M-002 | у Зои есть ключ от гаража №14 | Каширин | 5 | гл. 5 | — |",
          "| M-002 | у Зои есть ключ от гаража №14 | Каширин | 5 | гл. 5 | — |\n| M-002 | у Зои есть ключ от гаража №14 | Лида | 4 | гл. 4 | — |")
    report = _lint(ws, library)
    f = next(f for f in report.findings if f.code == "МАТР-2")
    lines = matrix.read_text(encoding="utf-8").splitlines()
    assert "Лида" in f.message and "| Лида |" in lines[f.line - 1]
    plants = library / "32_Реестр_закладок.md"
    _edit(plants, "| P-003 | бирка «14» на ключе Зои | т1 гл4 | т2 гл4 | 🔧 |", "| P-003 | бирка «14» на ключе Зои | т1 гл4 | т2 гл4; т1 гл2 | 🔧 |")
    f = next(f for f in _lint(ws, library).findings if f.code == "ЗАКЛ-2")
    assert "| P-003 |" in plants.read_text(encoding="utf-8").splitlines()[f.line - 1]


# ------------------------------------------------------------------ документ каркасов


def test_римские_номера_актов_без_ограничения(tmp_path):
    assert dramaturgy_doc.roman_to_int("XI") == 11 and dramaturgy_doc.roman_to_int("IV") == 4 and dramaturgy_doc.roman_to_int("12") == 12
    assert dramaturgy_doc.roman_to_int("Ы") is None and dramaturgy_doc.roman_to_int("") is None
    doc = tmp_path / "21.md"
    doc.write_text("## Круг акта XI «Х»\n1. **Ты** (гл. 30) — текст\n", encoding="utf-8")
    parsed = dramaturgy_doc.parse_frames(doc)
    assert [(c.scope, c.key, c.line) for c in parsed] == [("акт", 11, 1)]
    doc.write_text("## Круг акта Ы «Х»\n1. **Ты** (гл. 30) — текст\n", encoding="utf-8")
    with pytest.raises(dramaturgy_doc.MarkupError, match="номер акта"):
        dramaturgy_doc.parse_frames(doc)


def test_заголовок_своей_методики_разбирается_обратно(tmp_path):
    """Методика проекта с заголовком «Структура»: документ, который пишет конвейер, читается им же."""
    acts = [Act(act=1, title="А", from_chapter=1, to_chapter=2, parts="I", steps="1")]
    circles = [StoryCircle(scope="книга", summary="том", steps=[CircleStep(n=1, name="Завязка", text="т", chapters="гл. 1–2")]),
               StoryCircle(scope="глава", key=1, steps=[CircleStep(n=1, name="Завязка", text="г", chapters="сц. 1.1")])]
    text = dramaturgy_doc.render_doc(circles, acts, 1, method_name="своя", heading="Структура")
    assert "## Структура тома" in text and "## Структура главы 1" in text
    doc = tmp_path / "21.md"
    doc.write_text(text, encoding="utf-8")
    parsed = dramaturgy_doc.parse_frames(doc)
    assert [(c.scope, c.key) for c in parsed] == [("книга", None), ("глава", 1)]
    assert parsed[0].steps[0].name == "Завязка" and parsed[0].line == text[: text.index("\n## Структура тома")].count("\n") + 2
