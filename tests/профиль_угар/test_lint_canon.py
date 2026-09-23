"""Линтер канона, второй машинный слой (`konveyer/lint_canon.py`): дозы и документы, время,
досье против континуити и хронологии, вопросы к решениям автора; экспорт хронологии 12.

Каждый класс проверяется мутацией временной копии РЕАЛЬНОЙ библиотеки (позитивный контроль)
и отсутствием находок на нетронутой копии (отсутствие ложных срабатываний).
"""

import shutil
from pathlib import Path

import pytest

from konveyer import exporter, guard, lint
from tests.профиль import realcanon
from konveyer.paths import Workspace

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")

REGISTRY = "УГАР_Том1_Реестр_информационного_режима.md"
NEW_CODES = {"ДОЗА-1", "ДОК-1", "ХРОН-3", "ХРОН-4", "ХРОН-5", "ДОСЬЕ-3", "ДОСЬЕ-4", "ДОСЬЕ-5"}


@pytest.fixture
def real(tmp_path: Path):
    """Копия реальной библиотеки в рабочей области: мутации не трогают канон автора."""
    if not LIBRARY.exists():
        pytest.skip("реальная библиотека не подключена")
    lib = tmp_path / "Библиотека"
    shutil.copytree(LIBRARY, lib)
    (tmp_path / "конфиг.yaml").write_text("library_dir: Библиотека\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    guard.set_library_dir(lib)
    return lib, ws


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _run(real):
    lib, ws = real
    return lint.run_lint(lib, ws.exports, ws.logs)


def _codes(report, code: str):
    return [f for f in report.findings if f.code == code]


# ------------------------------------------------- экспорт хронологии 12


@real_only
def test_хронология_12_выгружается(real):
    lib, ws = real
    exporter.run_export(lib, ws.exports, ws.logs)
    events = exporter.load_chronology(ws.exports)
    assert (ws.exports / "chronology.json").exists()
    by_id = {e.event_id: e for e in events}
    assert len(events) > 50 and "Ф-1926-02" in by_id and "Ф-1947-05" in by_id

    e = by_id["Ф-1926-02"]
    assert e.date == "15.04" and e.year == 1926 and e.volume == 1 and e.section_years == [1926]
    assert e.chapters == [3, 6] and "Континенталя" in e.event and e.note.startswith("Закладка")

    # видимость: тома и главы, «[Скрыто]» и «[Фон]», историческая пометка «(ист.)»
    assert by_id["Ф-1916-02"].hidden and by_id["Ф-1916-02"].volumes == [1, 6]
    assert by_id["Ф-1918-01"].background and by_id["Ф-1918-01"].historical
    assert by_id["Ф-1913-01"].participants == "Лемм, Александр" and by_id["Ф-1913-01"].chapters == [12, 32]
    # карта «том → год» из заголовков разделов — единственная в каноне
    assert by_id["Ф-1947-03"].volume == 11 and by_id["Ф-1947-03"].section_years == [1946, 1947]
    # у фоновой исторической строки поля даты нет — событие не уезжает в дату
    assert by_id["Ф-1937-01"].date == "" and by_id["Ф-1937-01"].event.startswith("(ист.)")
    assert by_id["Ф-1924-01"].open_question


def test_хронологии_нет_в_демо(ws, library):
    """Демо-библиотека документа 12 не имеет — пустой список, а не ошибка разбора."""
    assert exporter.export_chronology(library) == []
    assert exporter.load_chronology(ws.exports) == []


# ------------------------------------------------------ ДОЗА-1 и ДОК-1


@real_only
def test_доза_1_глава_расходится(real):
    lib, _ = real
    _edit(lib / REGISTRY, "| №1 | 12 |", "| №1 | 13 |")
    report = _run(real)
    found = _codes(report, "ДОЗА-1")
    assert len(found) == 1 and found[0].severity == "ошибка"
    assert "таблица §5 — гл. 13" in found[0].message
    assert "сетка 2.2 — гл. 12" in found[0].message and "§7 закладок — гл. 12" in found[0].message
    assert found[0].file == REGISTRY and found[0].line


@real_only
def test_документ_2_после_другой_главы(real):
    lib, _ = real
    _edit(lib / REGISTRY, "| 2 | 8 |", "| 2 | 9 |")
    report = _run(real)
    found = _codes(report, "ДОК-1")
    assert len(found) == 1 and found[0].severity == "ошибка"
    assert "таблица §6 — после гл. 9" in found[0].message
    assert "сетка 2.2 — после гл. 8" in found[0].message
    assert "поглавник 2.3 — после гл. 8" in found[0].message


# ------------------------------------------------------------ ХРОН-3/4/5


@real_only
def test_дата_главы_вне_периода_части(real):
    lib, _ = real
    _edit(lib / REGISTRY, "| 10 | Лемм | 05.05 |", "| 10 | Лемм | 27.04 |")
    report = _run(real)
    found = _codes(report, "ХРОН-3")
    assert len(found) == 1 and found[0].severity == "ошибка"
    assert "гл. 10" in found[0].message and "ДЕКОРАЦИЯ" in found[0].message
    assert "май–июнь 1926" in found[0].message


@real_only
def test_историческое_событие_против_хроники(real):
    lib, _ = real
    _edit(lib / REGISTRY, "| 19 | Степан | 20.07 |", "| 19 | Степан | 15.07 |")
    report = _run(real)
    found = _codes(report, "ХРОН-4")
    assert len(found) == 1 and found[0].severity == "предупреждение"
    assert "гл. 19" in found[0].message and "Дзержинского" in found[0].message and "20.07" in found[0].message


@real_only
def test_день_недели_против_календаря(real):
    lib, _ = real
    _edit(lib / REGISTRY, "| 1 | Лемм | 12.04 | Кража со взломом",
          "| 1 | Лемм | 12.04 | Кража со взломом в пятницу")
    report = _run(real)
    found = _codes(report, "ХРОН-5")
    assert len(found) == 1 and found[0].severity == "ошибка"
    assert "в пятницу" in found[0].message and "понедельник" in found[0].message


# ------------------------------------------------------------ ДОСЬЕ-3/4/5


@real_only
def test_физика_досье_против_континуити(real):
    lib, _ = real
    _edit(lib / "Досье" / "Лемм.md", "глухота на левое ухо", "глухота на правое ухо")
    report = _run(real)
    found = _codes(report, "ДОСЬЕ-3")
    assert len(found) == 1 and found[0].severity == "ошибка"
    assert "Лемм" in found[0].message and "правое" in found[0].message and "левое" in found[0].message
    assert found[0].file == "Досье/Лемм.md" and found[0].line


@real_only
def test_статус_досье_против_хронологии(real):
    lib, _ = real
    _edit(lib / "Досье" / "Заварзин_Ася_Бугаев_Ольга.md",
          "## Статус: жив т.1–5, гибнет т.6.", "## Статус: жив т.1–3, гибнет т.4.")
    report = _run(real)
    found = _codes(report, "ДОСЬЕ-4")
    assert len(found) == 1 and found[0].severity == "предупреждение"
    assert "Заварзин" in found[0].message and "Ф-1931-04" in found[0].message and "том 6" in found[0].message


@real_only
def test_возраст_в_абсолютном_году(real):
    lib, _ = real
    _edit(lib / "Досье" / "Штерн_Александр_Лемм.md", "Рожд. 1896 (17 лет в 1913-м)",
          "Рожд. 1896 (19 лет в 1913-м)")
    report = _run(real)
    found = _codes(report, "ДОСЬЕ-5")
    assert len(found) == 1 and found[0].severity == "предупреждение"
    assert "19 лет в 1913" in found[0].message and "1913 год возраст 17" in found[0].message


@real_only
def test_возраст_в_чужом_томе(real):
    """Год тома берётся из разделов хронологии 12 («## Том 11 — 1946–1947»)."""
    lib, _ = real
    _edit(lib / "Досье" / "Заварзин_Ася_Бугаев_Ольга.md", "71 год в т.11", "81 год в т.11")
    report = _run(real)
    found = _codes(report, "ДОСЬЕ-5")
    assert len(found) == 1 and "том 11" in found[0].message and "70–71" in found[0].message


@real_only
def test_досье_1_не_сломано(real):
    """ДОСЬЕ-5 не подменяет ДОСЬЕ-1: возраст текущего тома по-прежнему проверяет ДОСЬЕ-1."""
    lib, _ = real
    _edit(lib / "Досье" / "Степан_Кожух.md", "24 (т.1)", "34 (т.1)")
    report = _run(real)
    assert len(_codes(report, "ДОСЬЕ-1")) == 1 and not _codes(report, "ДОСЬЕ-5")
    assert _codes(report, "ДОСЬЕ-1")[0].fix is not None


# -------------------------------------------------------------- КАНОН-1


@real_only
def test_вопросы_к_решениям_автора(real):
    """КАНОН-1 — заметки по расхождениям аудита 7.1, 7.3, 7.7, 7.11 (в каноне сняты Р-029, Р-030,
    Р-033; здесь возвращаются в копию библиотеки)."""
    lib, _ = real
    assert not _codes(_run(real), "КАНОН-1")
    reg = lib / "УГАР_Том1_Реестр_информационного_режима.md"
    _edit(reg, "Заварзин — тома 2–3, Ася — с тома 4 (Р-029).", "Их собственные линии открываются в томах 2–3.")
    _edit(reg, "46 глав по 700–800 слов (Р-019), ~35 тыс. слов, ~120–150 стр. (Р-030).", "46 глав, ~520–560 стр.")
    _edit(lib / "12_Генеральная_хронология_фабулы.md", "**Ф-1926-10** · конец сентября — начало октября ·",
          "**Ф-1926-10** · октябрь ·")
    _edit(lib / "00_ИНДЕКС_БИБЛИОТЕКИ.md", "✅ ведётся (Р-001…Р-034)", "✅ ведётся (Р-001…Р-014)")
    report = _run(real)
    notes = _codes(report, "КАНОН-1")
    assert all(f.severity == "заметка" for f in notes)
    messages = " | ".join(f.message for f in notes)
    assert "Ася: фокал открывается в разных томах" in messages          # 7.1
    assert "реестр §1 — т. 2" in messages and "таблица 03 — т. 4" in messages and "досье — т. 4" in messages
    assert "объём тома: 46 глав" in messages and "520–560 стр." in messages  # 7.3
    assert "Ф-1926-10" in messages and "гл. 31" in messages                  # 7.7
    assert "Р-001…Р-014" in messages and "Р-034" in messages                 # 7.11 (журнал дошёл до Р-034)


@real_only
def test_ссылка_на_событие_хронологии_в_чужом_томе(real):
    """Аудит 7.7: том, названный рядом со ссылкой «Ф-19xx-NN» в досье, против тома события в 12."""
    lib, _ = real
    _edit(lib / "Досье" / "Заварзин_Ася_Бугаев_Ольга.md",
          "гибель в чистках т.6 (Ф-1931-04)", "гибель в чистках т.5 (Ф-1931-04)")
    report = _run(real)
    found = [f for f in _codes(report, "КАНОН-1") if "Ф-1931-04" in f.message]
    assert len(found) == 1 and found[0].severity == "заметка"
    assert "в том 6" in found[0].message and found[0].file == "Досье/Заварзин_Ася_Бугаев_Ольга.md"


# ----------------------------------------- отсутствие ложных срабатываний


@real_only
def test_нетронутая_библиотека_без_новых_ошибок(real):
    report = _run(real)
    assert report.errors == 0, [f.message for f in report.findings if f.severity == "ошибка"]
    assert not [f for f in report.findings if f.code in NEW_CODES], \
        [f"{f.code}: {f.message}" for f in report.findings if f.code in NEW_CODES]


def test_демо_библиотека_без_новых_находок(ws, library):
    """Демо-канон не имеет ни реестра информрежима, ни хроник — новые проверки молчат."""
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert report.errors == 0 and report.warnings == 0
    assert not [f for f in report.findings if f.code in NEW_CODES or f.code == "КАНОН-1"]


# ------------------------------------------------------------ разбор 12


@real_only
def test_разбор_строки_хронологии_без_видимости(tmp_path):
    """Строка без поля видимости не теряет событие и не считает его датой."""
    doc = tmp_path / "12_Хронология.md"
    doc.write_text(
        "## Том 4 — 1929 «Казначей»\n\n"
        "**Ф-1929-09** · 1929 · Пробное событие без видимости\n"
        "**Ф-1929-10** · Событие, у которого нет ни даты, ни видимости, зато очень длинный текст\n",
        encoding="utf-8",
    )
    events = realcanon.parse_chronology(doc)
    assert [e.event_id for e in events] == ["Ф-1929-09", "Ф-1929-10"]
    assert events[0].date == "1929" and events[0].event == "Пробное событие без видимости"
    assert events[1].date == "" and events[1].event.startswith("Событие, у которого")
    assert all(e.volume == 4 and e.section_years == [1929] for e in events)
