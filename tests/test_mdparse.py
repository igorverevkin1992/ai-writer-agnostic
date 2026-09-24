"""Разбор Markdown (`konveyer.mdparse`): BOM, CRLF, номера строк секций, экранирование в таблицах, дубли колонок,
ключи списков без учёта регистра и «ё/е», числа (FR-EX-3, FR-DT-5, NFR-2)."""

from __future__ import annotations

import pytest

from konveyer import mdparse
from konveyer.mdparse import MarkupError


def test_bom_не_ломает_первый_заголовок_и_таблицу(tmp_path):
    p = tmp_path / "Персонаж_Зоя.md"
    p.write_bytes("﻿# Зоя\n\n## Профиль\n\nкратко\n".encode("utf-8"))
    secs = mdparse.parse_sections(p)
    assert [(s.title, s.level) for s in secs if s.level] == [("Зоя", 1), ("Профиль", 2)]
    t = tmp_path / "т.md"
    t.write_bytes("﻿| id | что |\n|---|---|\n| 1 | a |\n".encode("utf-8"))
    tables = mdparse.parse_tables(t)
    assert len(tables) == 1 and tables[0].line == 1 and tables[0].rows == [{"id": "1", "что": "a"}]


def test_crlf(tmp_path):
    p = tmp_path / "д.md"
    p.write_bytes(b"# A\r\n\r\n| id | x |\r\n|---|---|\r\n| 1 | y |\r\n")
    tables = mdparse.parse_tables(p)
    assert tables[0].rows == [{"id": "1", "x": "y"}]


def test_номер_строки_таблицы_внутри_секции_учитывает_пустые_строки(tmp_path):
    p = tmp_path / "стиль.md"
    p.write_text("# Стиль\n\n## Нормы\n\n\n| id | мин |\n|---|---|\n| a | 1 |\n", encoding="utf-8")
    sec = mdparse.find_section(mdparse.parse_sections(p), "Нормы")
    assert sec is not None and sec.line == 3
    tables = mdparse.parse_tables(p, sec.raw_body, start_line=sec.line + 1)
    assert tables[0].line == 6          # строка заголовка таблицы
    assert tables[0].line + 2 == 8      # первая строка данных


def test_пустая_последняя_ячейка_и_экранированная_черта(tmp_path):
    p = tmp_path / "pipes.md"
    p.write_text("| a | b | c |\n|---|---|---|\n| 1 | 2 ||\n| x\\|y | 2 | 3 |\n", encoding="utf-8")
    rows = mdparse.parse_tables(p)[0].rows
    assert rows[0] == {"a": "1", "b": "2", "c": ""}
    assert rows[1]["a"] == "x|y"


def test_дубли_заголовков_колонок_ошибка_со_строкой(tmp_path):
    p = tmp_path / "дубль.md"
    p.write_text("# М\n\n| # | Факт | Зоя | Зоя |\n|---|---|---|---|\n| 1 | f | гл.1 | гл.2 |\n", encoding="utf-8")
    with pytest.raises(MarkupError) as e:
        mdparse.parse_tables(p)
    assert "дубль.md:3:" in str(e.value) and "Зоя" in str(e.value)


def test_расхождение_числа_колонок_с_номером_строки(tmp_path):
    p = tmp_path / "т.md"
    p.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n| 1 |\n", encoding="utf-8")
    with pytest.raises(MarkupError) as e:
        mdparse.parse_tables(p)
    assert "т.md:4:" in str(e.value)


def test_ключи_без_учёта_регистра_и_ё():
    body = "- Объем: 300\n- Не знает:\n  - кто убил\n- Участники: Анна, Пётр (появление, в финале)\n"
    assert mdparse.parse_kv(body, "Объём") == "300"
    assert mdparse.parse_list_items(body, "НЕ знает") == ["кто убил"]
    assert mdparse.parse_list_items(body, "Участники", ";,") == ["Анна", "Пётр (появление, в финале)"]
    assert mdparse.parse_list_items(body, "Участники") == ["Анна, Пётр (появление, в финале)"]


def test_числа():
    assert mdparse.parse_number("1 000") == 1000
    assert mdparse.parse_number("0,45") == 0.45
    assert mdparse.parse_number("30%") == 30
    assert mdparse.parse_number("−3") == -3
    assert mdparse.parse_number("—") is None
    assert mdparse.parse_number("много") is None


def test_вложенные_секции_входят_в_тело(tmp_path):
    p = tmp_path / "к.md"
    p.write_text("# Марк\n\n## Профиль\n\nкратко\n\n### Детство\n\nдетство текст\n\n## Арка\n\nарка\n", encoding="utf-8")
    secs = mdparse.parse_sections(p)
    prof = mdparse.find_section(secs, "[Пп]рофил", min_level=2)
    assert prof is not None and "детство текст" in mdparse.nested_body(secs, prof)
    assert "арка" not in mdparse.nested_body(secs, prof)
    # «[Аа]рк» не должно находить заголовок 1-го уровня «Марк»
    assert mdparse.find_section(secs, "[Аа]рк", min_level=2).title == "Арка"
    assert mdparse.find_section(secs, "[Аа]рк").title == "Марк"  # без ограничения уровня — как раньше
