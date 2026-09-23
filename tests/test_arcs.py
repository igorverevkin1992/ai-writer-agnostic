"""Р-024 (шаг 8 круга главы необязателен), Р-025 (арки тома 2.5 → arcs.json → окно/аналитик/линтер),
Р-026 (материал аналитика кругов: тема цикла, арки, участники сцен)."""

import json
import shutil
from pathlib import Path

import pytest

from konveyer import circles, compiler, exporter, guard, lint, verifier2
from tests.профиль import realcanon
from konveyer.paths import Workspace
from konveyer.schemas import Act, CircleStep, StoryCircle

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")

ACTS = [Act(act=1, title="МОКРОЕ ДЕЛО", from_chapter=1, to_chapter=5, parts="I", steps="1–2")]
NAMES = circles.STEP_NAMES


@pytest.fixture
def real_copy(tmp_path: Path) -> tuple[Workspace, Path]:
    """Временная копия реальной библиотеки (мутации не трогают канон автора) + выгрузки."""
    lib = tmp_path / "Библиотека"
    shutil.copytree(LIBRARY, lib)
    (tmp_path / "конфиг.yaml").write_text("library_dir: Библиотека\n", encoding="utf-8")
    guard.set_library_dir(lib)
    ws = Workspace(tmp_path)
    exporter.run_export(lib, ws.exports, ws.logs)
    return ws, lib


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


# ------------------------------------------------------------------ реальная библиотека


@real_only
def test_скелет_22_реальной_библиотеки(real_copy):
    ws, lib = real_copy
    arcs = exporter.load_arcs(ws.exports)
    assert {a.character for a in arcs} == {"Лемм", "Степан", "Штерн", "Заварзин", "Ася"}
    assert sorted({a.act for a in arcs}) == [1, 2, 3, 4] and len(arcs) == 20
    assert not any(a.filled for a in arcs)  # скелет: ничего не сочинено за автора
    assert {a.act for a in exporter.load_acts(ws.exports)} == {1, 2, 3, 4}
    # окно главы 5 без секции арок (скелет), линтер без АРКА-*
    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "арки тома" not in w and "⚠ заполнить" not in w
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False)
    assert not [f for f in report.findings if f.code.startswith("АРКА")]
    assert "| 2.5 |" in (lib / "00_ИНДЕКС_БИБЛИОТЕКИ.md").read_text(encoding="utf-8")


@real_only
def test_фильтр_тайн_в_строке_арки(real_copy):
    """Строка Штерна с маркером тайны Т-05 («сын») фокалу Степану не показывается; строка Лемма без маркеров — да;
    ложь/желание/потребность не выводятся никогда."""
    ws, lib = real_copy
    doc = lib / "22_Арки_Том1.md"
    _edit(doc, "| Штерн | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Штерн | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | Держится чужим; отца не поминает, сын молчит. |")
    _edit(doc, "| Лемм | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Лемм | 1 | ЛОЖЬ-ЛЕММА | ЖЕЛАНИЕ-ЛЕММА | ПОТРЕБНОСТЬ-ЛЕММА | ГДЕ-НА-АРКЕ | Сухой, точный; смотрит поверх головы собеседника. |")
    exporter.run_export(lib, ws.exports, ws.logs)
    arcs, acts, infobans = exporter.load_arcs(ws.exports), exporter.load_acts(ws.exports), exporter.load_infobans(ws.exports)
    brief = exporter.load_brief(ws.exports, 5)  # фокал Степан, акт 1
    assert brief.focal == "Степан"
    t05 = next(b for b in infobans if b.ban_id == "Т-05")
    assert "сын" in t05.markers and not t05.known_to("Степан", 5)
    brief = brief.model_copy(update={"participants": ["Лемм", "Штерн"]})
    lines = compiler.arc_lines(arcs, acts, brief, infobans, ["Лемм", "Степан", "Штерн"])
    assert lines == ["Лемм: Сухой, точный; смотрит поверх головы собеседника."]

    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "- Лемм: Сухой, точный; смотрит поверх головы собеседника." in w
    assert "сын молчит" not in w
    for hidden in ("ЛОЖЬ-ЛЕММА", "ЖЕЛАНИЕ-ЛЕММА", "ПОТРЕБНОСТЬ-ЛЕММА", "ГДЕ-НА-АРКЕ"):
        assert hidden not in w
    # аналитику кругов — всё, включая ложь и строку с маркером тайны
    _, material = circles.build_material(ws, "книга")
    assert "ЛОЖЬ-ЛЕММА" in material and "сын молчит" in material


@real_only
def test_линтер_арка_1_и_2(real_copy):
    ws, lib = real_copy
    doc = lib / "22_Арки_Том1.md"
    _edit(doc, "| Ася | 4 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Ася | 4 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |\n| Никто | 9 | — | — | — | — | — |")
    report = lint.run_lint(lib, ws.exports, ws.logs)
    found = {f.code: f for f in report.findings if f.code.startswith("АРКА")}
    assert set(found) == {"АРКА-1", "АРКА-2"} and all(f.severity == "заметка" for f in found.values())
    assert "Никто" in found["АРКА-1"].message and "22_Арки_Том1.md" in found["АРКА-1"].file
    assert "акт 9" in found["АРКА-2"].message and found["АРКА-2"].line


@real_only
def test_материал_аналитика_тома_реальный(real_copy):
    """Р-026: тема цикла из 13 §1, арки целиком, события сетки с участниками; промпт Писателю не идёт."""
    ws, lib = real_copy
    title, material = circles.build_material(ws, "книга")
    assert title == "Книга (том целиком)"
    assert "## Тема цикла (13 §1)" in material and "опеку через ложь" in material and "наследование вины" in material
    assert "## Арки тома (" in material and "| Штерн | 1 |" in material and "| Ася | 4 |" in material
    assert "гл. 13 · " in material and "фокал Штерн · участники: Степан" in material
    _, act_material = circles.build_material(ws, "акт", 2)
    assert "## Арки акта 2" in act_material and "| Лемм | 2 |" in act_material and "| Лемм | 1 |" not in act_material
    assert "участники:" in act_material
    # без документа 13 материал просто без темы (деградация)
    assert circles.cycle_theme(None) == "" and circles.cycle_theme(ws.root) == ""
    # окно Писателя материала аналитика не содержит
    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "Тема цикла" not in w and "| Ложь |" not in w and "Ядро цикла" not in w
