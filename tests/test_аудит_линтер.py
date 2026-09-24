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


# ------------------------------------------------------------------ КОНТ-2: континуити против прозы


def test_конт2_признак_в_прозе_против_континуити(ws, library):
    prose = library / "Проза" / "Том1_Глава03.md"
    _edit(prose, "Пронин кивнул,", "Она вспомнила шрам над правой бровью Каширина. Пронин кивнул,")
    report = _lint(ws, library)
    found = [f for f in report.findings if f.code == "КОНТ-2"]
    assert len(found) == 1 and found[0].file == "Проза/Том1_Глава03.md" and found[0].line == 1
    assert "Каширин, увечья — в прозе «правое», а континуити фиксирует «левое»" in found[0].message
    assert "шрам над правой бровью" in found[0].quote and "Что сделать" in found[0].message
    # признак из карточки (не из континуити): ожоги на правой руке Гуляева против «левой» в прозе
    _edit(prose, "Она вспомнила шрам над правой бровью Каширина.", "Она вспомнила ожог на левой руке Гуляева.")
    found = [f for f in _lint(ws, library).findings if f.code == "КОНТ-2"]
    assert len(found) == 1 and "Гуляев, увечья" in found[0].message and "карточка фиксирует «правое»" in found[0].message
    # признак в реплике персонажа (абзац-реплика до атрибуции) — не показание фокала
    _edit(prose, "Она вспомнила ожог на левой руке Гуляева. ", "")
    _edit(prose, "Пронин пришёл к киоску", "— Ожог у Гуляева на левой руке, — сказал Пронин.\n\nПронин пришёл к киоску")
    assert "КОНТ-2" not in _codes(_lint(ws, library))
    # модуль «континуити» выключен — проверки нет
    _edit(prose, "— Ожог у Гуляева на левой руке, — сказал Пронин.", "Она вспомнила ожог на левой руке Гуляева.")
    assert "КОНТ-2" in _codes(_lint(ws, library))
    man = manifest_mod.load(ws.root)
    man.модули["континуити"] = "выкл"
    manifest_mod.save(ws.root, man)
    assert "КОНТ-2" not in _codes(_lint(ws, library))


# ------------------------------------------------------------------ кэш линтера: канон + конфигурация


def test_кэш_линтера_учитывает_конфигурацию_проекта(ws, library):
    _edit(library / "32_Реестр_закладок.md", "| P-002 | царапины на замке гаража №14 | т1 гл5 | т1 гл6; т2 |",
          "| P-002 | царапины на замке гаража №14 | т1 гл5 | т1 гл2; т2 |")
    r1 = lint.run_lint(library, ws.exports, ws.logs)
    assert "ЗАКЛ-2" in _codes(r1)
    assert lint.run_lint(library, ws.exports, ws.logs).ts == r1.ts  # тот же канон и конфигурация — прежний отчёт
    man = manifest_mod.load(ws.root)
    man.модули["закладки"] = "выкл"
    manifest_mod.save(ws.root, man)
    r2 = lint.run_lint(library, ws.exports, ws.logs)  # кэш включён — выключение модуля пересчитывает отчёт
    assert r2.ts != r1.ts and r2.fingerprint != r1.fingerprint and "ЗАКЛ-2" not in _codes(r2)
    # плагин линтера проекта — тоже часть конфигурации
    (ws.root / "линтер").mkdir()
    (ws.root / "линтер" / "свой.py").write_text(
        "from konveyer.schemas import LintFinding\n"
        "def checks(ctx):\n    return [LintFinding(code='СВОЙ-1', severity='заметка', file='x', message='свой')]\n", encoding="utf-8")
    r3 = lint.run_lint(library, ws.exports, ws.logs)
    assert "СВОЙ-1" in _codes(r3) and r3.fingerprint != r2.fingerprint
    # обязательные шаги методики — тоже
    man = manifest_mod.load(ws.root)
    man.методики.обязательные_шаги = {"глава": [1]}
    manifest_mod.save(ws.root, man)
    assert lint.run_lint(library, ws.exports, ws.logs).fingerprint != r3.fingerprint


# ------------------------------------------------------------------ модельный слой: промпт, отмена, ручной ответ


def test_промпт_модельного_слоя_отрендерен(ws, library, monkeypatch):
    from konveyer.config import Config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, prompts = lint.run_lint_llm(ws, Config(), library, [library / PLAN])
    text = Path(prompts[0]).read_text(encoding="utf-8")
    assert "{{" not in text and "«Гаражи»" in text


def test_отмена_между_вызовами_модельного_слоя(ws, library, monkeypatch):
    from konveyer import adapters, cancel
    from konveyer.config import Config

    calls: list[str] = []

    def fake_call(system, user, mc, api, logs_dir, *, role, **kw):
        calls.append(user.split("\n", 1)[0])
        cancel.request()
        return "[]"

    monkeypatch.setattr(adapters, "call_anthropic", fake_call)
    docs = [library / PLAN, library / "31_Матрица_знаний.md"]
    with pytest.raises(cancel.Cancelled):
        lint.run_lint_llm(ws, Config(), library, docs)
    assert len(calls) == 1
    cancel.clear()


def test_ручной_ответ_модельного_слоя_cli_и_панель(ws, library, monkeypatch):
    """Без API промпты сохранены; ответ модели по документу принимается командой `lint --ответ` и POST /api/lint/manual,
    заменяя прежние модельные находки только по этому документу."""
    import json
    import threading
    import urllib.request

    from typer.testing import CliRunner

    from konveyer import server
    from konveyer.cli import app
    from konveyer.config import Config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(ws.root)
    answer = ws.root / "ответ_план.md"
    answer.write_text('[{"quote": "Зоя впервые упоминает гаражи", "problem": "гаражи названы в гл. 1", "suggestion": "убрать", "severity": "заметка"}]',
                      encoding="utf-8")
    r = CliRunner().invoke(app, ["lint", "--ответ", f"{PLAN}={answer}"])
    assert r.exit_code == 0 and "находок принято 1" in r.output, r.output
    report = lint.load_report(ws.logs)
    assert [f for f in report.findings if f.source == "модель" and f.file == PLAN and f.line]
    r = CliRunner().invoke(app, ["lint", "--ответ", "нет.md=" + str(answer)])
    assert r.exit_code == 1 and "не принят" in r.output
    # панель: ответ по тому же документу заменяет прежний, по другому — добавляется
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/lint/manual", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())
        two = '[{"quote": "x", "problem": "а", "severity": "заметка"}, {"quote": "y", "problem": "б", "severity": "заметка"}]'
        assert post({"doc": PLAN, "text": two})["added"] == 2
        assert post({"doc": "31_Матрица_знаний.md", "text": '[{"quote": "M-001", "problem": "в", "severity": "заметка"}]'})["added"] == 1
    finally:
        srv.shutdown()
        srv.server_close()
    model = [(f.file, f.message) for f in lint.load_report(ws.logs).findings if f.source == "модель"]
    assert sorted(model) == [(PLAN, "а"), (PLAN, "б"), ("31_Матрица_знаний.md", "в")]
    assert "lint-manual" in server.PANEL_ACTIONS


# ------------------------------------------------------------------ каркасы: битый ответ модели по одной цели


def test_каркасы_битый_ответ_не_прерывает_прогон(ws, library, monkeypatch):
    import json

    from konveyer import adapters, circles
    from konveyer.config import Config

    exporter.run_export(library, ws.exports, ws.logs, 2)
    from konveyer.config import set_volume
    set_volume(ws, 2)
    ws2 = type(ws)(ws.root, 2)
    calls: list[int] = []

    def fake(system, user, mc, api, logs_dir, *, role, chapter=None):
        calls.append(1)
        if len(calls) == 1:
            return "[1, 2, 3]"
        if len(calls) == 2:
            return json.dumps({"title": "т", "steps": [{"n": "два", "name": "x", "text": "…"}]}, ensure_ascii=False)
        return json.dumps({"title": "т", "summary": "суть", "steps": [{"n": 1, "name": "Ты", "text": "…", "chapters": "гл. 3–4"}]}, ensure_ascii=False)

    monkeypatch.setattr(adapters, "call_anthropic", fake)
    result = circles.run(ws2, Config(), "всё")
    assert len(calls) == 1 + 2 + 4 and len(result["сбои"]) == 2 and len(result["готово"]) == 5
    assert "не найден корректный JSON" in result["сбои"][0] or "JSON" in result["сбои"][0]
    assert "не число" in result["сбои"][1] and len(result["промпты"]) == 2
    assert (ws2.root / "драматургия" / "промпты" / "книга.ответ_сырой.md").read_text(encoding="utf-8") == "[1, 2, 3]"
    # битый файл черновика не роняет окно, статус и внесение; доктор его видит
    (ws2.root / "драматургия" / "акт_9.json").write_text('{"scope": "акт", "key": 9, "steps": "нет"}', encoding="utf-8")
    assert len(circles.drafts(ws2)) == 5 and "акт_9" not in circles.canon_status(ws2)
    assert circles.broken_drafts(ws2) == ["акт_9.json: в ответе поле «steps» должно быть списком шагов"]


# ------------------------------------------------------------------ методики: данные комплекта, материал, арки


def _init_git(lib: Path) -> None:
    import subprocess

    for args in (["init"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(lib), *args], check=True, capture_output=True)


def test_схемы_и_промпты_методик_комплекта():
    from konveyer import methodics

    for m in methodics.load_all().values():
        if m.result_kind == "арки":
            assert "rows" in m.schema and "steps" not in m.schema
            continue
        step = (m.schema.get("steps") or [{}])[0]
        if m.steps:
            assert step.get("name") in m.step_names(), (m.name, step)
        if m.name != "круг_хармона":
            assert "круг" not in str(m.schema.get("title", "")).lower(), m.name
        assert "в материале не задано" in m.prompt and ("{{ required" in m.prompt or not m.required), m.name
    scene = methodics.load_all()["сцена_сиквел"]
    assert "сц." in scene.schema["steps"][0]["chapters"]


def test_демо_арки_заполнены_и_видны_в_окне(ws, library):
    from konveyer import compiler, project
    from konveyer.config import set_volume

    assert not [c for c in project.readiness(ws.root, library) if "⚠ заполнить" in c.label]
    exporter.run_export(library, ws.exports, ws.logs, 2)
    set_volume(ws, 2)
    w = compiler.compile_window(type(ws)(ws.root, 2), library, 3)[0].read_text(encoding="utf-8")
    assert "## Что видно снаружи (арки участников)" in w and "- Зоя: молчит там, где раньше шутила" in w


def test_материал_аналитика_по_запросу_методики(ws, library):
    from konveyer import circles, methodics

    folder = ws.root / "методики" / "своя"
    folder.mkdir(parents=True)
    (folder / "методика.yaml").write_text(
        "методика: своя\nназвание: Своя\nуровни: [том, акт, глава]\nшаги:\n  - {n: 1, имя: Завязка}\n"
        "материал:\n  том: [тема, континуити, хроника]\n  глава: [глава, континуити]\n", encoding="utf-8")
    man = manifest_mod.load(ws.root)
    man.методики.том = man.методики.акт = man.методики.глава = "своя"
    manifest_mod.save(ws.root, man)
    _, book = circles.build_material(ws, "книга")
    assert "## Континуити" in book and "## Хроника эпохи" in book and "Каширин: шрам на левой брови (т.1 гл.1)" in book
    assert "## Акты тома" not in book and "## Главы тома" not in book and "Реестр тайн" not in book
    assert ("## Тема серии" in book) == bool(circles.cycle_theme(ws))
    _, ch = circles.build_material(ws, "глава", 1)
    assert ch.startswith("## Глава 1 ·") and "### Сцены" not in ch and "Каширин: шрам на левой брови" in ch
    assert "### Закладки" not in ch
    # акт — состав по умолчанию (в методике не задан)
    exporter.run_export(library, ws.exports, ws.logs, 2)
    _, act = circles.build_material(type(ws)(ws.root, 2), "акт", 1)
    assert "## Акт 1 «Письмо»" in act and "## Арки акта 1" in act and act.startswith("## Каркас уровня выше")
    # неизвестная секция — понятная ошибка и предупреждение доктора
    (folder / "методика.yaml").write_text(
        "методика: своя\nназвание: Своя\nуровни: [том]\nшаги:\n  - {n: 1, имя: Завязка}\nматериал: [тема, погода]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="неизвестный материал"):
        circles.build_material(ws, "книга")
    assert any("«погода»" in p or "погода" in p for p in methodics.problems(man, ws.root, circles.MATERIAL_SECTIONS))
    # без ключа — прежний состав по умолчанию
    man.методики = manifest_mod.Methodics()
    manifest_mod.save(ws.root, man)
    _, book = circles.build_material(ws, "книга")
    assert "## Акты тома" in book and "## Главы тома" in book and "## Реестр тайн" in book


def test_методика_арки_исполняема_черновик_канон_окно_э2(ws, library, monkeypatch):
    """Методика «арки» на уровне акта: ответ-таблица → черновик → статус → документ арок тома через canon_change,
    строка персонаж × акт заменяет прежнюю; окно берёт вводный текст арок из методики, Э2 — её текст проверки."""
    import json

    from konveyer import adapters, circles, compiler, verifier2
    from konveyer.config import Config, set_volume

    _init_git(library)
    man = manifest_mod.load(ws.root)
    man.методики.акт = "арки"
    manifest_mod.save(ws.root, man)
    exporter.run_export(library, ws.exports, ws.logs, 2)
    set_volume(ws, 2)
    ws2 = type(ws)(ws.root, 2)
    assert circles.methodic_for(ws2, "акт").result_kind == "арки"
    system = circles._template(ws2, "акт")
    assert "rows" in system and "«Ложь»" in system
    _, material = circles.build_material(ws2, "акт", 2)
    assert "## Арки акта 2" in material and "## Континуити" in material

    def fake(system, user, mc, api, logs_dir, *, role, chapter=None):
        act = 1 if "Акт 1" in user else 2
        return json.dumps({"title": f"Арки акта {act}", "rows": [
            {"character": "Каширин", "act": act, "lie": "ЛОЖЬ-К", "want": "ж", "need": "п", "position": "начало", "visible": "ВИДНО-К"},
            {"character": "Лида", "act": act, "lie": "—", "want": "—", "need": "—", "position": "—", "visible": "хлопочет громче обычного"},
        ]}, ensure_ascii=False)

    monkeypatch.setattr(adapters, "call_anthropic", fake)
    result = circles.run(ws2, Config(), "акты")
    assert len(result["готово"]) == 2 and not result["сбои"]
    md = (ws2.root / "драматургия" / "акт_1.md").read_text(encoding="utf-8")
    assert "| Каширин | 1 | ЛОЖЬ-К |" in md
    # Каширин есть в обоих актах канона, Лиды нет нигде → оба черновика «отличаются»
    assert circles.canon_status(ws2) == {"акт_1": "отличается от канона", "акт_2": "отличается от канона"}
    assert circles.drafts(ws2) == [] and len(circles.arc_drafts(ws2)) == 4
    path, commit = circles.commit_to_canon(ws2, Config(), library)
    assert path.name == "22_Арки_Том2.md" and len(commit) >= 7
    text = path.read_text(encoding="utf-8")
    assert "| Каширин | 1 | ЛОЖЬ-К |" in text and "| Зоя | 1 |" in text  # своя строка заменена, чужая сохранена
    assert "| Лида | 2 |" in text and "## Методика: Арки персонажей" in text
    arcs = {(a.character, a.act): a for a in exporter.load_arcs(ws2.exports)}
    assert arcs[("Каширин", 1)].visible == "ВИДНО-К" and arcs[("Зоя", 2)].visible == "молчит там, где раньше шутила"
    assert circles.canon_status(ws2) == {"акт_1": "в каноне", "акт_2": "в каноне"}
    # окно главы 1 (акт 1, участники Каширин и Лида): вводный текст из методики арок, только «что видно снаружи»
    w = compiler.compile_window(ws2, library, 1)[0].read_text(encoding="utf-8")
    assert "Как участники сцены выглядят со стороны" in w and "- Каширин: ВИДНО-К" in w and "ЛОЖЬ-К" not in w
    assert (Path(circles.__file__).parent / "методики" / "арки" / "в_окно.j2").read_text(encoding="utf-8").strip() in w
    # Э2: текст проверки арок присоединён к драматургии
    ws2.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws2.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    system, _user = verifier2.build_prompt(ws2, 1, 1)
    assert "внутренний путь (ложь/желание/потребность) в тексте не проговаривается" in system
    # ручной приём ответа-таблицы и битая таблица
    circles.accept_manual(ws2, "акт", 2, '{"rows": [{"character": "Зоя", "act": 2, "visible": "тише"}]}')
    with pytest.raises(ValueError, match="не число"):
        circles.accept_manual(ws2, "акт", 2, '{"rows": [{"character": "Зоя", "act": "два"}]}')


# ------------------------------------------------------------------ 14.3.4: выключение модулей без следов


def _norm(name: str) -> str:
    import re

    return re.sub(r"[^а-яё]", "", name.lower())


def _module_sets():
    from konveyer import catalog

    mods = catalog.load_modules(None)
    optional = sorted(n for n, m in mods.items() if not m.base)
    return mods, optional


@pytest.mark.parametrize("off", [*_module_sets()[1], "все"])
def test_выключение_модуля_без_следов_в_окне_э2_и_линтере(ws, library, off):
    """Критерий приёмки 14.3.4: выключенный модуль (каждый по одному и все сразу) не оставляет в окне своих секций
    и пустых заголовков, в промпте Э2 — своего чек-листа, в линтере — своих кодов."""
    import re

    from konveyer import catalog, compiler, verifier2

    mods, optional = _module_sets()
    targets = optional if off == "все" else [off]
    man = manifest_mod.load(ws.root)
    for name in targets:
        man.модули[name] = "выкл"
    manifest_mod.save(ws.root, man)
    exporter.run_export(library, ws.exports, ws.logs)
    path, breakdown = compiler.compile_window(ws, library, 1)
    w = path.read_text(encoding="utf-8")
    present = {_norm(k) for k in breakdown}
    for name in targets:
        for section in mods[name].window_sections:
            assert _norm(section) not in present, (name, section, breakdown)
    # пустых заголовков нет: за «## …» до следующей секции есть содержательные строки
    for chunk in re.split(r"<!-- СЕКЦИЯ: [^>]+ -->", w)[1:]:
        lines = [ln for ln in chunk.strip().splitlines() if ln.strip()]
        head = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
        assert head is not None and len(lines) > head + 1, chunk[:120]
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    system, _user = verifier2.build_prompt(ws, 1, 1)
    for name in targets:
        for check in mods[name].e2_checks:
            text = verifier2._checklist_text(ws, check)
            head = text.split(".")[0][:60]
            assert not head or head not in system, (name, check)
    report = lint.run_lint(library, ws.exports, ws.logs, use_cache=False)
    off_codes = catalog.enabled_lint_codes(mods, set(mods) - set(targets))
    assert not {f.code for f in report.findings} - off_codes, [f.message for f in report.findings]
    assert report.errors == 0 and report.warnings == 0 and report.notes == 0


# ------------------------------------------------------------------ окно тома 2: без тайн, с каркасом и арками


def test_окно_тома_2_без_тайн_с_каркасом_и_арками(ws, library):
    """Демо-том 2 (каркасы, арки, свой информрежим): окно каждой главы без маркеров тайн, недоступных фокалу,
    с шагами тома/акта/главы из канона и «что видно снаружи» участников (FR-WN-3, FR-DR-5)."""
    from konveyer import compiler
    from konveyer.config import set_volume

    exporter.run_export(library, ws.exports, ws.logs, 2)
    set_volume(ws, 2)
    ws2 = type(ws)(ws.root, 2)
    bans = exporter.load_infobans(ws2.exports)
    for b in exporter.load_briefs(ws2.exports):
        w = compiler.compile_window(ws2, library, b.chapter)[0].read_text(encoding="utf-8")
        low = w.lower()
        for ban in bans:
            if not ban.secret or ban.known_to(b.focal, b.chapter):
                continue
            for marker in ban.markers:
                assert marker.lower() not in low, f"гл. {b.chapter}: маркер «{marker}» тайны {ban.ban_id} в окне"
        assert "- Том: шаг" in w and "в канон ещё не внесён" not in w
        if b.chapter <= 2:
            assert "- Акт 1 «Письмо»: шаг" in w
        if b.chapter == 1:
            assert "- Круг главы: письмо ломает утро." in w and "7. Возвращение (сц. 1.1, финал) — чай остыл." in w
            assert "8. Изменение" not in w and "изменение фокала не требуется" in w
        assert "## Что видно снаружи (арки участников)" in w
        assert compiler.compile_window(ws2, library, b.chapter)[0].read_text(encoding="utf-8") == w  # детерминизм
