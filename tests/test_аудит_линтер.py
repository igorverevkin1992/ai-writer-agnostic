"""Аудит кластера «линтер и драматургия»: ложные срабатывания на корректном, но нестандартном каноне
(шаги на одной главе, континуити другого тома, смена года, несколько томов фокала), строки находок,
разбор документа каркасов с заголовком своей методики."""

from __future__ import annotations

from pathlib import Path

import pytest

from konveyer import dramaturgy_doc, exporter, lint, manifest as manifest_mod
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


# ------------------------------------------------------------------ методики: ничего от круга Хармона в движке


def _own_methodic(ws, library, heading: str = "Каркас") -> None:
    folder = ws.root / "методики" / "своя"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "методика.yaml").write_text(
        f"методика: своя\nназвание: Своя методика\nуровни: [глава]\nзаголовок_документа: {heading}\n"
        "незаданный_шаг: \"(шаг {n} «{name}» автором не задан)\"\n"
        "шаги:\n  - {n: 1, имя: Завязка}\n  - {n: 2, имя: Развязка}\nобязательность:\n  глава: [1]\n", encoding="utf-8")
    (folder / "в_окно.j2").write_text("СВОЯ-В-ОКНЕ", encoding="utf-8")
    (folder / "в_э2.md").write_text("СВОЯ-В-Э2", encoding="utf-8")
    man = manifest_mod.load(ws.root)
    man.методики.глава = "своя"
    man.методики.обязательные_шаги = {}
    man.библиотека.append(manifest_mod.LibraryEntry(файл="21_Круги_истории_Том1.md", тип="каркасы", том=1))
    manifest_mod.save(ws.root, man)


def test_окно_и_э2_своей_методики_без_текстов_круга(ws, library):
    """Своя методика из двух шагов: окно и Э2 получают её заголовок, её пометку о незаданном шаге и имя шага
    из методики — ни «шаг 8», ни «изменение фокала», ни «Круг главы» в движке нет (П-1, FR-DR-1)."""
    from konveyer import circles, compiler, verifier2

    _own_methodic(ws, library, heading="Структура")
    acts = [Act(act=1, title="А", from_chapter=1, to_chapter=6, parts="I", steps="1–2")]
    ch = StoryCircle(scope="глава", key=1, summary="осмотр", steps=[CircleStep(n=1, name="Завязка", text="начало", chapters="сц. 1.1")])
    (library / "21_Круги_истории_Том1.md").write_text(circles.render_canon_doc([ch], acts, 1, ws), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    assert len(exporter.load_circles(ws.exports)) == 1  # заголовок «Структура главы 1» читается обратно
    w = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    section = w.split("СВОЯ-В-ОКНЕ", 1)[1].split("<!-- СЕКЦИЯ", 1)[0]
    assert "- Структура главы: осмотр" in section and "1. Завязка (сц. 1.1) — начало" in section
    assert "(шаг 2 «Развязка» автором не задан)" in section
    for alien in ("шаг 8", "изменение фокала", "Круг главы", "Изменение"):
        assert alien not in w, alien
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    system, user = verifier2.build_prompt(ws, 1, 1)
    assert "(шаг 2 «Развязка» автором не задан)" in user and "изменение фокала" not in user
    assert "СВОЯ-В-Э2" in system
    # линтер: обязательный шаг 1 задан — КРУГ-1 молчит; без шага 1 — находка по методике «Своя методика»
    assert not [f for f in _lint(ws, library).findings if f.code == "КРУГ-1"]
    _edit(library / "21_Круги_истории_Том1.md", "1. **Завязка** (сц. 1.1) — начало", "1. **Завязка** (сц. 1.1) — в материале не задано")
    f = next(f for f in _lint(ws, library).findings if f.code == "КРУГ-1")
    assert "Своя методика" in f.message and "[1]" in f.message


def test_пометка_незаданного_шага_из_методики_круга(ws, library):
    """Пометка «изменение фокала не требуется» живёт в методике круга, а не в движке; без ключа в методике —
    нейтральная пометка движка."""
    from konveyer import circles, methodics

    engine = Path(circles.__file__).parent
    for py in engine.glob("*.py"):
        assert "изменение фокала" not in py.read_text(encoding="utf-8"), py.name
    m = methodics.load_all()["круг_хармона"]
    assert "изменение фокала не требуется" in m.unset_note
    frame = {"book_steps": [], "act_steps": [], "chapter": StoryCircle(scope="глава", key=1, steps=[]), "has_any": True}
    assert circles.frame_lines(frame, optional={2}, step_names={2: "Развязка"}) == ["- Каркас главы:", "  (шаг 2 «Развязка» в каркасе главы не задан)"]


def test_пустая_методика_шаги_из_манифеста(ws, library):
    from konveyer import circles

    man = manifest_mod.load(ws.root)
    man.методики.глава = "пустая"
    man.методики.обязательные_шаги = {}
    man.методики.шаги_пустой = ["Завязка", "Кульминация"]
    manifest_mod.save(ws.root, man)
    m = circles.methodic_for(ws, "глава")
    assert m.name == "пустая" and m.step_names() == ["Завязка", "Кульминация"] and m.required_steps("глава", man) == {1, 2}
    man.методики.шаги_пустой = {"глава": ["Одно"], "том": ["Другое"]}
    manifest_mod.save(ws.root, man)
    assert circles.methodic_for(ws, "глава").step_names() == ["Одно"]
    assert "Шаги: 1" not in circles._template(ws, "глава") or "Одно" in circles._template(ws, "глава")


def test_методика_не_для_уровня_не_подменяется_кругом(ws, library):
    """Манифест «том: сцена_сиквел» (уровни [глава]): каркас тома не строится по кругу молча — понятная ошибка,
    доктор предупреждает; окно главы при этом собирается (П-5)."""
    from konveyer import circles, compiler, methodics, project

    man = manifest_mod.load(ws.root)
    man.методики.том = "сцена_сиквел"
    man.методики.акт = "нет_такой"
    man.методики.серия = "круг_хармона"
    man.методики.глава = ["круг_хармона", "сцена_сиквел"]
    manifest_mod.save(ws.root, man)
    with pytest.raises(ValueError, match="не поддерживает уровень «том»"):
        circles.methodic_for(ws, "книга")
    with pytest.raises(ValueError, match="не найдена"):
        circles.methodic_for(ws, "акт")
    problems = methodics.problems(man, ws.root)
    assert any("«серия» в этой версии не поддержан" in p for p in problems)
    assert any("не поддерживает уровень «том»" in p for p in problems)
    assert any("«нет_такой»" in p and "не найдена" in p for p in problems)
    assert any("несколько методик" in p and "«круг_хармона»" in p for p in problems)
    checks = [c for c in project.readiness(ws.root, library) if c.label.startswith("методики:")]
    assert len(checks) == 4 and all(c.ok is None for c in checks)
    assert compiler.compile_window(ws, library, 1)[0].exists()
    # без методик в манифесте — методика движка по умолчанию, без предупреждений
    man.методики = manifest_mod.Methodics()
    manifest_mod.save(ws.root, man)
    assert circles.methodic_for(ws, "книга").name == "круг_хармона" and not methodics.problems(man, ws.root)


# ------------------------------------------------------------------ хронология: год, дни месяца, месяцы


@pytest.mark.parametrize("new_date, expect", [
    ("3 января 1996", False),      # смена года с явными годами — не ошибка
    ("1 мая 1996", False),
    ("15 июня 1994", True),        # возврат на год назад ловится
    ("3 июля 1995", False),        # тот же день, что у гл. 5 — не раньше
    ("2 июля 1995", True),
])
def test_хрон2_учитывает_год(ws, library, new_date, expect):
    _edit(library / PLAN, "- Дата: 18 июля 1995", f"- Дата: {new_date}")
    codes = _codes(_lint(ws, library))
    assert ("ХРОН-2" in codes) is expect, codes


def test_хрон1_день_сверяется_с_месяцем_и_годом(ws, library):
    _edit(library / PLAN, "- Дата: 15 июня 1995", "- Дата: 31 июня 1995")
    report = _lint(ws, library)
    found = [f for f in report.findings if f.code.startswith("ХРОН")]
    assert [f.code for f in found] == ["ХРОН-1"] and "гл. 2" in found[0].message  # виновата гл. 2, а не соседняя
    _edit(library / PLAN, "- Дата: 31 июня 1995", "- Дата: 29 февраля 1995")
    assert "ХРОН-1" in _codes(_lint(ws, library))
    _edit(library / PLAN, "- Дата: 29 февраля 1995", "- Дата: 29 февраля 1996")
    assert "ХРОН-1" not in _codes(_lint(ws, library)) and "ХРОН-2" in _codes(_lint(ws, library))


def test_разбор_дат_и_месяцев():
    assert lint.parse_date("ночь с 12 на 13 июня 1995") == (6, 13)
    assert lint.parse_date("та же ночь") is None and lint.parse_year("12.06.1995") == 1995 and lint.parse_year("12 июня") is None
    assert lint._months("Маркиз и Сенька, Майор") == set()
    assert lint._months("май–июнь") == {5, 6} and lint._months("в марте") == {3} and lint._months("декабрь") == {12}
    assert lint.parse_month("Майор") is None and lint.parse_month("мая") == 5


def test_акт1_подсказка_по_направлению_и_строка(ws, library):
    doc = library / FRAMES2
    _edit(doc, "| 2 | «Архив» | 3–4 | II | 5–8 |", "| 2 | «Архив» | 3–9 | II | 5–8 |")
    f = next(f for f in _lint(ws, library, 2).findings if f.code == "АКТ-1")
    assert "до 9, а в томе 4" in f.message and "сократите последний акт" in f.message and f.line == 9
    _edit(doc, "| 2 | «Архив» | 3–9 | II | 5–8 |", "| 2 | «Архив» | 3–3 | II | 5–8 |")
    f = next(f for f in _lint(ws, library, 2).findings if f.code == "АКТ-1")
    assert "до 3, а в томе 4" in f.message and "добавьте главы в последний акт" in f.message
    _edit(doc, "| 2 | «Архив» | 3–3 | II | 5–8 |", "| 2 | «Архив» | 4–4 | II | 5–8 |")
    f = next(f for f in _lint(ws, library, 2).findings if f.code == "АКТ-1" and f.severity == "ошибка")
    assert "начинается с гл. 4, ожидалась гл. 3" in f.message and f.line == 9


# ------------------------------------------------------------------ фокалы, досье, проза, индекс


def test_фокал2_несколько_томов_в_статусе(ws, library):
    assert lint._focal_volumes("Жива; фокальна т.1, т.3.") == {1, 3}
    assert lint._focal_volumes("фокал: т.1 (гл. 1–9) и т.2") == {1, 2}
    assert lint._focal_volumes("Фокален в т.2–3.") == {2, 3}
    assert lint._focal_volumes("Жив.") is None and lint._focal_volumes("фокала не имеет") == set()
    assert {1, 2, 40} <= lint._focal_volumes("Жив т.1–2; фокален с т.1.")
    card = library / "Досье" / "Персонаж_Зоя.md"
    _edit(card, "Жива т.1–2; фокальна с т.1.", "Жива; фокальна т.1, т.2.")
    assert "ФОКАЛ-2" not in _codes(_lint(ws, library, 2))
    _edit(card, "Жива; фокальна т.1, т.2.", "Жива; фокальна т.1, т.3.")
    found = [f for f in _lint(ws, library, 2).findings if f.code == "ФОКАЛ-2"]
    assert found and all(f.severity == "предупреждение" and "т.1, т.3" in f.message for f in found)


def test_проза1_маркер_в_повествовательной_части_абзаца_с_репликой(ws, library):
    prose = library / "Проза" / "Том1_Глава03.md"
    _edit(prose, "Пронин пришёл к киоску", "— Ну и всё, — сказала Зоя и подумала, что сторож жив и прячется. Пронин пришёл к киоску")
    found = [f for f in _lint(ws, library).findings if f.code == "ПРОЗА-1"]
    assert found and found[0].line == 1 and "B-003" in found[0].message and found[0].file == "Проза/Том1_Глава03.md"
    # маркер только внутри реплики персонажа — не знание фокала
    _edit(prose, "— Ну и всё, — сказала Зоя и подумала, что сторож жив и прячется.", "— Сторож жив, — сказал Пронин.")
    assert "ПРОЗА-1" not in _codes(_lint(ws, library))


def test_досье5_номер_гаража_с_годом_не_возраст(ws, library):
    card = library / "Досье" / "Персонаж_Каширин.md"
    _edit(card, "Живёт один, привычки", "Пятнадцать лет в гараже №14 (1995 год — уже свой). Живёт один, привычки")
    assert "ДОСЬЕ-5" not in _codes(_lint(ws, library))
    _edit(card, "Пятнадцать лет в гараже", "Ему было 30 (1995). Пятнадцать лет в гараже")
    f = next(f for f in _lint(ws, library).findings if f.code == "ДОСЬЕ-5")
    assert "30 (1995" in f.message and "возраст 52" in f.message


def test_досье6_по_обязательным_секциям_типа(ws, library):
    card = library / "Досье" / "Персонаж_Лида.md"
    text = card.read_text(encoding="utf-8")
    card.write_text(text.split("## Речевой паспорт")[0] + "## Отношения\n\n| к кому | отношение |\n|---|---|\n| Каширин | опека |\n", encoding="utf-8")
    found = [f for f in _lint(ws, library).findings if f.code == "ДОСЬЕ-6"]
    assert [f.message.split(". Что")[0] for f in found] == ["Лида: у карточки нет обязательной секции «Речевой паспорт»"]
    assert found[0].file == "Досье/Персонаж_Лида.md" and (library / found[0].file).exists()  # находка ведёт к файлу
    assert "добавьте секцию «Речевой паспорт»" in found[0].message
    # заголовок-синоним («Внешность» вместо «Физика») секцию закрывает
    card2 = library / "Досье" / "Персонаж_Пронин.md"
    _edit(card2, "## Физика", "## Внешность")
    assert not [f for f in _lint(ws, library).findings if f.code == "ДОСЬЕ-6" and "Пронин" in f.message]
    # проектный тип с другим набором секций
    (ws.root / "типы").mkdir(exist_ok=True)
    src = Path(lint.__file__).parent / "типы" / "персонажи.yaml"
    (ws.root / "типы" / "персонажи.yaml").write_text(src.read_text(encoding="utf-8").replace(
        'обязательные_секции: ["Профиль", "Физика", "Речевой паспорт", "Отношения"]', 'обязательные_секции: ["Профиль", "Биография"]'), encoding="utf-8")
    found = [f for f in _lint(ws, library).findings if f.code == "ДОСЬЕ-6"]
    assert len(found) == 5 and all("«Биография»" in f.message for f in found)


def test_канон1_префикс_решений_из_журнала(ws, library):
    journal = library / "36_Журнал_решений.md"
    journal.write_text(journal.read_text(encoding="utf-8").replace("Р-", "D-"), encoding="utf-8")
    index = library / "00_ИНДЕКС_БИБЛИОТЕКИ.md"
    _edit(index, "| 36_Журнал_решений.md | журнал_решений |", "| 36_Журнал_решений.md | журнал_решений | D-001 … D-001 |")
    (ws.root / "типы").mkdir(exist_ok=True)
    src = Path(lint.__file__).parent / "типы" / "журнал_решений.yaml"
    (ws.root / "типы" / "журнал_решений.yaml").write_text(src.read_text(encoding="utf-8").replace("Р-", "D-"), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    assert exporter.load_decisions(ws.exports) and exporter.load_decisions(ws.exports)[0].decision_id.startswith("D-")
    f = next(f for f in _lint(ws, library).findings if f.code == "КАНОН-1")
    assert "дошёл до D-0" in f.message and "Р-" not in f.message


def test_тайна4_документ_снимает_проверку_только_если_несёт_тайну(ws, library):
    """Читатель узнаёт тайну в гл. 5 (фокал Каширин её не знает): документ главы 5 — рапорт без маркеров тайны,
    поэтому ТАЙНА-4 остаётся; документ с маркером тайны — раскрытие через документ, находки нет."""
    info = library / "2.2_Информрежим.md"
    _edit(info, "| B-003 | сторож Гуляев жив и прячется | — | гл. 6 |", "| B-003 | сторож Гуляев жив и прячется | — | гл. 5 |")
    assert "ТАЙНА-4" in _codes(_lint(ws, library))
    _edit(library / PLAN, "- Документы: №1 (после главы): рапорт Пронина о закрытии дела",
          "- Документы: №1 (после главы): письмо, из которого ясно, что сторож жив")
    assert "ТАЙНА-4" not in _codes(_lint(ws, library))
