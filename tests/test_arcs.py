"""Р-024 (шаг 8 круга главы необязателен), Р-025 (арки тома 2.5 → arcs.json → окно/аналитик/линтер),
Р-026 (материал аналитика кругов: тема цикла, арки, участники сцен)."""

import json
from pathlib import Path


from konveyer import circles, compiler, exporter, lint, verifier2
from tests.профиль import realcanon
from konveyer.schemas import Act, CircleStep, StoryCircle


REPO = Path(__file__).resolve().parent.parent
ACTS = [Act(act=1, title="МОКРОЕ ДЕЛО", from_chapter=1, to_chapter=5, parts="I", steps="1–2")]
NAMES = circles.STEP_NAMES


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ------------------------------------------------------------------ Р-024: шаг 8 круга главы


def _chapter_circle(key: int, step8: str | None) -> StoryCircle:
    steps = [CircleStep(n=i + 1, name=NAMES[i], text=f"гл. {key} шаг {i + 1}", chapters=f"сц. {key}.1") for i in range(7)]
    if step8 is not None:
        steps.append(CircleStep(n=8, name="Изменение", text=step8, chapters=f"сц. {key}.1, финал"))
    return StoryCircle(scope="глава", key=key, summary=f"суть {key}", steps=steps)


def test_шаг_8_главы_необязателен_окно_э2_линтер(ws, library):
    """Круг главы из семи шагов и круг с «в материале не задано»: в окне и Э2 — шаги 1–7 и пометка,
    линтер КРУГ-1 молчит; круг тома из семи шагов — по-прежнему предупреждение."""
    book = StoryCircle(scope="книга", summary="том", steps=[
        CircleStep(n=i + 1, name=NAMES[i], text=f"том {i + 1}", chapters="гл. 1–5") for i in range(7)])
    doc = circles.render_canon_doc(
        [book, _chapter_circle(1, None), _chapter_circle(5, "В материале не задано."),
         _chapter_circle(3, "иным: показал метод")], ACTS)
    # незаданный шаг 8 в документе — явной пометкой
    assert "8. **Изменение** (сц. 5.1, финал) — в материале не задано" in doc
    (library / "21_Круги_истории_Том1.md").write_text(doc, encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    parsed = {c.key: c for c in exporter.load_circles(ws.exports)}
    assert len(parsed[1].steps) == 7 and circles.step_unset(parsed[5].steps[7]) and not circles.step_unset(parsed[3].steps[7])
    assert [s.n for s in circles.chapter_steps_present(parsed[5])] == list(range(1, 8))
    assert [s.n for s in circles.chapter_steps_present(parsed[3])] == list(range(1, 9))

    for ch in (1, 5):
        w = compiler.compile_window(ws, library, ch)[0].read_text(encoding="utf-8")
        assert f"7. Возвращение (сц. {ch}.1) — гл. {ch} шаг 7" in w
        assert "8. Изменение" not in w and "в материале не задано" not in w
        assert "не задан — изменение фокала не требуется" in w
        assert "если задано в круге главы" in w and "фокал не обязан выйти из неё иным" in w
        ws.chapter_dir(ch).mkdir(parents=True, exist_ok=True)
        ws.draft_path(ch, 1).write_text("Текст.\n", encoding="utf-8")
        system, user = verifier2.build_prompt(ws, ch, 1)
        assert "8. Изменение" not in user and "изменение фокала не требуется" in user
        assert "отсутствие изменения фокала в главе без шага 8 — НЕ флаг" in system

    report = lint.run_lint(library, ws.exports, ws.logs, export=False)
    krug1 = [f.message for f in report.findings if f.code == "КРУГ-1"]
    assert len(krug1) == 1 and krug1[0].startswith("Книга (том целиком): не заданы обязательные шаги [8]")


def test_шаблон_аналитика_кругов_про_шаг_8():
    text = (REPO / "konveyer" / "методики" / "круг_хармона" / "промпт.md").read_text(encoding="utf-8")
    assert "Обязательные шаги уровня" in text and "в материале не задано" in text
    assert "тему серии" in text and "арки персонажей" in text
    meth = (REPO / "konveyer" / "методики" / "круг_хармона" / "методика.yaml").read_text(encoding="utf-8")
    assert "глава: [1, 2, 3, 4, 5, 6, 7]" in meth


# ------------------------------------------------------------------ Р-025: разбор и экспорт 2.5


def test_разбор_таблицы_арок(tmp_path):
    doc = "\n".join([
        "# 2.5. Арки тома — Том 1", "",
        "| Персонаж | Акт | Ложь | Желание | Потребность | Где на арке | Что видно снаружи |",
        "|---|---|---|---|---|---|---|",
        "| Лемм | 1 | верит в метод | закрыть дело | признать сына | — | сух, точен |",
        "| **Степан** | 3 «ТРАУР» | ⚠ заполнить | — | — | — | — |",
        "|  | 2 | без имени | — | — | — | — |",
        "| Ася | акт? | без номера | — | — | — | — |",
    ])
    path = tmp_path / "22_Арки_Том1.md"
    path.write_text(doc, encoding="utf-8")
    arcs = realcanon.parse_arcs(path)
    assert [(a.character, a.act) for a in arcs] == [("Лемм", 1), ("Степан", 3)]
    assert arcs[0].lie == "верит в метод" and arcs[0].position == "" and arcs[0].visible == "сух, точен"
    assert arcs[0].filled and not arcs[1].filled and arcs[1].lie == "⚠ заполнить"


def test_экспорт_арок_демо_без_документа(ws, library):
    assert exporter.load_arcs(ws.exports) == []
    assert "arcs.json" in exporter.load_manifest(ws.exports)
    assert json.loads((ws.exports / "arcs.json").read_text(encoding="utf-8")) == []
    # старые выгрузки/ без arcs.json — тоже пусто, не ошибка
    (ws.exports / "arcs.json").unlink()
    assert exporter.load_arcs(ws.exports) == []
    # линтер на демо — тишина по АРКА-*
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert not [f for f in report.findings if f.code.startswith("АРКА")]


def test_арки_в_окне_демо(ws, library):
    """Демо: акт из 2.1 + арки — в окне только «что видно снаружи» участников; ссылка на том — строку долой."""
    (library / "21_Круги_истории_Том1.md").write_text(circles.render_canon_doc([], ACTS), encoding="utf-8")
    (library / "22_Арки_Том1.md").write_text("\n".join([
        "# 2.5. Арки тома — Том 1", "",
        "| Персонаж | Акт | Ложь | Желание | Потребность | Где на арке | Что видно снаружи |",
        "|---|---|---|---|---|---|---|",
        "| Каширин | 1 | ЛОЖЬ-КАШИРИНА | ЖЕЛАНИЕ-КАШИРИНА | ПОТРЕБНОСТЬ-КАШИРИНА | начало | Сух и точен, не смотрит в глаза. |",
        "| Зоя | 1 | — | — | — | — | Держится тише обычного (в т.2 объяснится). |",
        "| Каширин | 2 | — | — | — | — | НЕ-ЭТОТ-АКТ |",
    ]), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    assert len(exporter.load_arcs(ws.exports)) == 3
    path, breakdown = compiler.compile_window(ws, library, 1)
    w = path.read_text(encoding="utf-8")
    assert "арки участников" in breakdown
    assert "## Что видно снаружи (арки участников)" in w and "- Каширин: Сух и точен, не смотрит в глаза." in w
    assert "Зоя:" not in w and "т.2" not in w  # ссылка на том — строка не выводится
    assert "НЕ-ЭТОТ-АКТ" not in w
    for hidden in ("ЛОЖЬ-КАШИРИНА", "ЖЕЛАНИЕ-КАШИРИНА", "ПОТРЕБНОСТЬ-КАШИРИНА", "начало"):
        assert hidden not in w.split("## Что видно снаружи")[1].split("<!-- СЕКЦИЯ")[0]
    assert "ЛОЖЬ-КАШИРИНА" not in w
    # детерминизм (FR-C4)
    assert compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8") == w
    # черновик круга не нужен: аналитик тома получает всю таблицу, включая ложь
    _, material = circles.build_material(ws, "книга")
    assert "## Арки тома (" in material and "ЛОЖЬ-КАШИРИНА" in material and "участники: Зоя" in material
    _, act_material = circles.build_material(ws, "акт", 1)
    assert "## Арки акта 1" in act_material and "НЕ-ЭТОТ-АКТ" not in act_material
    _, ch_material = circles.build_material(ws, "глава", 1)
    assert "Арки" not in ch_material


def test_арки_вне_актов_секции_нет(ws, library):
    """Без таблицы актов акт главы не определить — секции арок нет, ошибки тоже."""
    (library / "22_Арки_Том1.md").write_text("\n".join([
        "| Персонаж | Акт | Ложь | Желание | Потребность | Где на арке | Что видно снаружи |",
        "|---|---|---|---|---|---|---|",
        "| Каширин | 1 | — | — | — | — | Сух и точен. |",
    ]), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    w = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    assert "арки тома" not in w
    assert compiler.arc_lines(exporter.load_arcs(ws.exports), [], exporter.load_brief(ws.exports, 1), [], ["Каширин"]) == []


