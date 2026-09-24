"""Декларативный разбор (`konveyer.declparse`): конвертеры, сопоставление колонок, форматы на крайних входах,
переопределения из манифеста, заглушки стартового комплекта, плагины (FR-DT-4, FR-DT-5, FR-EX-3, §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from konveyer import declparse
from konveyer.declparse import ParseContext, conv_chapter, conv_number, conv_place, conv_years, match_columns
from konveyer.mdparse import MarkupError


def _doc(tmp_path: Path, text: str, name: str = "д.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ конвертеры


def test_место_понимает_точки_и_слова():
    assert conv_place("т.1 гл.5") == {"vol": 1, "ch": 5}
    assert conv_place("том 1, гл. 5") == {"vol": 1, "ch": 5}
    assert conv_place("т1 гл1") == {"vol": 1, "ch": 1}
    assert conv_place("Т.2") == {"vol": 2}
    assert conv_place("—") == {}
    with pytest.raises(ValueError):
        conv_place("где-то в середине")


def test_годы_регистр_после_с_десятилетие():
    assert conv_years("До 1999") == {"year": {"before": 1999}}
    assert conv_years("после 1999") == {"year": {"from": 2000}}
    assert conv_years("с 2000") == {"year": {"from": 2000}}
    assert conv_years("1990-е") == {"year": {"from": 1990, "to": 1999}}
    assert conv_years("1990–1999") == {"year": {"from": 1990, "to": 1999}}
    assert conv_years("1995") == {"year": {"from": 1995, "to": 1995}}
    assert conv_years("все") == {"all": True} and conv_years("") == {"all": True}
    with pytest.raises(ValueError):
        conv_years("вчера")


def test_глава_из_ссылки_с_томом():
    assert conv_chapter("т.2 гл.3") == 3
    assert conv_chapter("гл. 7") == 7
    assert conv_chapter("5") == 5
    assert conv_chapter("т.2") is None
    assert conv_chapter("—") is None
    with pytest.raises(ValueError):
        conv_chapter("много")


def test_нечисловое_значение_ошибка():
    with pytest.raises(ValueError):
        conv_number("один")
    assert conv_number("") is None and conv_number("1 000") == 1000


# ------------------------------------------------------------------ колонки


def test_сопоставление_колонок_без_ложных_подстрок():
    cols = {"том": {"синонимы": ["том"]}, "тема": {"синонимы": ["тема"]}, "гл": {"синонимы": ["гл"]}}
    found = match_columns(["№", "Автомат", "Заголовок", "тема"], cols, {})
    assert found == {"тема": "тема"}  # «том» ≠ «Автомат», «гл» ≠ «Заголовок»
    # точное совпадение важнее подстроки другого синонима; «ё» = «е»; с начала слова — да
    cols = {"объём": {"синонимы": ["объём", "слов"]}, "том": {"синонимы": ["том"]}}
    assert match_columns(["Объем (слов)", "Том 2"], cols, {}) == {"объём": "Объем (слов)", "том": "Том 2"}


def test_синонимы_по_порядку_и_имя_поля_как_запасной_синоним():
    cols = {"запрет": {"синонимы": ["запрет", "тайна", "что"], "обязательна": True},
            "тайна": {"синонимы": ["тайна?", "секрет"]}}
    # каркас стартового комплекта: заголовки — имена полей каталога
    assert match_columns(["ban_id", "запрет", "тайна"], cols, {}) == {"запрет": "запрет", "тайна": "тайна"}


def test_неоднозначная_колонка_ошибка_и_переопределение_манифеста():
    cols = {"дата": {"синонимы": ["дата"]}}
    with pytest.raises(ValueError) as e:
        match_columns(["Дата начала", "Дата конца"], cols, {})
    assert "неоднозначная колонка" in str(e.value)
    assert match_columns(["Дата начала", "Дата конца"], cols, {"дата": "Дата конца"}) == {"дата": "Дата конца"}
    # обязательной колонки нет — None (формат «не мой»)
    assert match_columns(["x"], {"дата": {"синонимы": ["дата"], "обязательна": True}}, {}) is None


# ------------------------------------------------------------------ форматы


TABLE_FMT = {
    "вид": "таблица",
    "колонки": {
        "plant_id": {"синонимы": ["plant_id", "id"], "обязательна": True, "роль": "ключ"},
        "что": {"синонимы": ["что"], "обязательна": True},
        "положена": {"синонимы": ["положена"], "тип": "место"},
    },
}


def test_все_таблицы_одной_формы_читаются(tmp_path):
    p = _doc(tmp_path, "# Закладки\n\n## Акт I\n\n| plant_id | что | положена |\n|---|---|---|\n| P-001 | a | т1 гл1 |\n\n"
                       "## Акт II\n\n| plant_id | что | положена |\n|---|---|---|\n| P-009 | вторая | т1 гл5 |\n")
    recs = declparse.fmt_table(p, TABLE_FMT, ParseContext())
    assert [r["plant_id"] for r in recs] == ["P-001", "P-009"]
    assert recs[1]["_строка"] == 13 and recs[1]["положена"] == {"vol": 1, "ch": 5}


def test_ошибка_значения_с_файлом_строкой_и_полем(tmp_path):
    p = _doc(tmp_path, "| plant_id | что | положена |\n|---|---|---|\n| P-001 | a | где-то |\n")
    with pytest.raises(MarkupError) as e:
        declparse.fmt_table(p, TABLE_FMT, ParseContext())
    assert "д.md:3:" in str(e.value) and "«положена»" in str(e.value)


def test_строка_заглушка_не_даёт_записи(tmp_path):
    p = _doc(tmp_path, "| plant_id | что | положена |\n|---|---|---|\n| P-001 | ⚠ заполнить | ⚠ заполнить |\n"
                       "| P-002 | настоящая | — |\n")
    recs = declparse.fmt_table(p, TABLE_FMT, ParseContext())
    assert [r["plant_id"] for r in recs] == ["P-002"] and recs[0]["положена"] == {}


def test_секция_таблицы_переопределяется_манифестом_и_списком(tmp_path):
    p = _doc(tmp_path, "# Стиль\n\n## Пороги прозы\n\n| id | мин |\n|---|---|\n| a | 1 |\n\n## Ещё пороги\n\n"
                       "| id | мин |\n|---|---|\n| b | 2 |\n")
    fmt = {"вид": "таблица", "секция": "§\\s*5", "колонки": {"id": {"синонимы": ["id"], "обязательна": True, "роль": "ключ"},
                                                                "мин": {"синонимы": ["мин"], "тип": "число"}}}
    assert declparse.fmt_table(p, fmt, ParseContext(extraction="нормы")) is None
    ctx = ParseContext(extraction="нормы", sections={"нормы": "Пороги"})
    assert [r["id"] for r in declparse.fmt_table(p, fmt, ctx)] == ["a"]
    ctx = ParseContext(extraction="нормы", sections={"нормы": ["Пороги прозы", "Ещё"]})
    assert [r["id"] for r in declparse.fmt_table(p, fmt, ctx)] == ["a", "b"]
    with pytest.raises(MarkupError):
        declparse.fmt_table(p, fmt, ParseContext(extraction="нормы", sections={"нормы": {"x": 1}}))


def test_шаблон_постоянных_не_конфликтует_с_полем_том(tmp_path):
    p = _doc(tmp_path, "| id | том |\n|---|---|\n| a | 7 |\n")
    fmt = {"вид": "таблица", "колонки": {"id": {"синонимы": ["id"], "обязательна": True}, "том": {"синонимы": ["том"], "тип": "целое"}},
           "постоянные": {"volume": "{том}", "src": "{файл}"}}
    rec = declparse.fmt_table(p, fmt, ParseContext(volume=2))[0]
    assert rec["volume"] == "2" and rec["src"] == "д.md" and rec["том"] == 7


WIDE_FMT = {"вид": "широкая_таблица", "ключ": {"синонимы": ["факт"]}, "номер": {"синонимы": ["#"]}, "минимум_колонок": 3,
            "ячейка_знания": {"всегда|с начала|пролог": 0, "—|-|нет": None, "гл.N[ / источник]": "N", "*курсив*": "частичное_знание"}}


def test_широкая_таблица_правила_ячеек_и_заглушки(tmp_path):
    p = _doc(tmp_path, "| # | Факт | Зоя | Читатель |\n|---|---|---|---|\n"
                       "| 1 | ⚠ заполнить | ⚠ заполнить | — |\n"
                       "| 2 | факт два | всегда (с пролога) | — (см. факт 13) |\n"
                       "| 3 | факт три | гл. 5 / письмо | *не механику — гл.24* |\n"
                       "| 4 | факт четыре | гл.41 / опознание по прологу | улики с гл.4; расчётная разгадка ≈гл.20 |\n")
    ctx = ParseContext(params={"pseudo": {"Читатель"},
                               "тип": {"ячейка_знания_псевдосубъекта": {"разгадк[а-я]*\\D{0,25}?гл\\.?\\s*(\\d+)": "N", "улик": "пометка"}}})
    facts = {(f["fact_id"], f["subject"]): f for f in declparse.fmt_wide_table(p, WIDE_FMT, ctx)}
    assert not any(k[0] == "М-01" for k in facts)  # строка-заглушка пропущена
    assert facts[("М-02", "Зоя")]["from_chapter"] == 0
    assert facts[("М-02", "Читатель")]["from_chapter"] is None and facts[("М-02", "Читатель")]["note"] == "— (см. факт 13)"
    f3 = facts[("М-03", "Зоя")]
    assert f3["from_chapter"] == 5 and f3["source"] == "письмо"
    assert facts[("М-03", "Читатель")]["from_chapter"] == 24 and facts[("М-03", "Читатель")]["note"].startswith("частично")
    assert facts[("М-04", "Зоя")]["from_chapter"] == 41  # «пролог» в середине — не «всегда»
    assert facts[("М-04", "Читатель")]["from_chapter"] == 20  # правило псевдосубъекта из типа
    ctx_plain = ParseContext(params={"pseudo": {"Читатель"}})
    plain = {(f["fact_id"], f["subject"]): f for f in declparse.fmt_wide_table(p, WIDE_FMT, ctx_plain)}
    assert plain[("М-04", "Читатель")]["from_chapter"] == 4  # без правил профиля — первое «гл.N»


def test_иная_нотация_ячеек_через_спецификацию(tmp_path):
    p = _doc(tmp_path, "| # | Факт | Зоя |\n|---|---|---|\n| 1 | факт | глава 5 |\n| 2 | факт2 | не знает |\n| 3 | ф3 | с 7-й |\n")
    fmt = dict(WIDE_FMT, **{"ячейка_знания": {"глава N": "N", "не знает": None, "с N-й": "N"}})
    facts = declparse.fmt_wide_table(p, fmt, ParseContext())
    assert [f["from_chapter"] for f in facts] == [5, None, 7]


SECTIONS_FMT = {"вид": "секции", "имя": "заголовок_1",
                "поля": {"profile": {"секция": "[Пп]рофил"}, "relations": {"секция": "[Оо]тношени", "тип": "таблица_пар"},
                         "arc": {"секция": "[Аа]рк"}}}


def test_карточка_подсекции_и_имя_марк(tmp_path):
    p = _doc(tmp_path, "# Марк\n\nвступление\n\n## Профиль\n\nкратко\n\n### Детство\n\nдетство текст\n\n## Арка\n\nарка\n",
             "Персонаж_Марк.md")
    rec = declparse.fmt_sections(p, SECTIONS_FMT, ParseContext())[0]
    assert rec["name"] == "Марк" and "детство текст" in rec["profile"] and rec["arc"] == "арка"
    assert rec["_секции"]["arc"] == 13


def test_каркас_карточки_не_даёт_записи(tmp_path):
    p = _doc(tmp_path, "# Имя\n\n## Профиль\n\n⚠ заполнить\n\n## Отношения\n\n| к кому | отношение |\n|---|---|\n| — | — |\n",
             "Персонаж_Имя.md")
    assert declparse.fmt_sections(p, SECTIONS_FMT, ParseContext()) == []
    p2 = _doc(tmp_path, "# Зоя\n\n## Профиль\n\nживая\n\n## Отношения\n\n| к кому | отношение |\n|---|---|\n| Марк | ⚠ заполнить |\n",
              "Персонаж_Зоя.md")
    rec = declparse.fmt_sections(p2, SECTIONS_FMT, ParseContext())[0]
    assert rec["profile"] == "живая" and rec["relations"] == {"Марк": ""}


def test_секции_с_ключами_регистр_и_запятые(tmp_path):
    p = _doc(tmp_path, "# План\n\n## Глава 1 — А\n\n- Дата: 1.1\n- Объем: 300\n- Не знает:\n  - тайна\n- Участники: Анна, Пётр\n\n"
                       "## Глава 2 — Каркас\n\n- Дата: ⚠ заполнить\n- Фокал: ⚠ заполнить\n")
    fmt = {"вид": "секции_с_ключами", "заголовок": "Глава\\s+(\\d+)",
           "ключи": {"дата": {"ключ": "Дата"}, "объём": {"ключ": "Объём", "тип": "целое"},
                     "не_знает": {"ключ": "НЕ знает", "тип": "список"}, "участники": {"ключ": "Участники", "тип": "список_запятая"}}}
    recs = declparse.fmt_keyed_sections(p, fmt, ParseContext())
    assert len(recs) == 1  # глава-каркас записи не даёт
    assert recs[0]["объём"] == 300 and recs[0]["не_знает"] == ["тайна"] and recs[0]["участники"] == ["Анна", "Пётр"]


# ------------------------------------------------------------------ плагины


def test_плагин_только_из_папки_проекта_или_движка(tmp_path):
    ctx = ParseContext(project_root=tmp_path)
    assert declparse.resolve_plugin("os:getcwd", ctx) is None
    assert declparse.resolve_plugin("subprocess:run", ctx) is None
    assert declparse.resolve_plugin("konveyer.mdparse:parse_number", ctx) is not None
    (tmp_path / "типы" / "парсеры").mkdir(parents=True)
    (tmp_path / "типы" / "парсеры" / "мой.py").write_text(
        "def parse(path, *, volume, ctx):\n    return [{'x': volume, 'f': path.name, 'e': ctx.extraction}]\n", encoding="utf-8")
    fn = declparse.resolve_plugin("мой:parse", ctx)
    assert fn is not None
    ctx = ParseContext(project_root=tmp_path, volume=3, extraction="e1")
    out = declparse.fmt_plugin(tmp_path / "д.md", {"вид": "плагин", "функция": "мой:parse"}, ctx)
    assert out == [{"x": 3, "f": "д.md", "e": "e1"}]  # keyword-only параметры передаются
