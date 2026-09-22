"""Тесты экспортёра (FR-X1…FR-X3)."""

import json

import pytest

from konveyer import exporter
from konveyer.mdparse import MarkupError


def test_выгрузки_созданы_и_валидны(ws):
    for name in [
        "norms.json", "stoplists.json", "matrix.json", "plants.json",
        "continuity.json", "briefs.json", "dossiers.json", "infobans.json", "индекс.json",
    ]:
        assert (ws.exports / name).exists(), name
    norms = exporter.load_norms(ws.exports)
    assert norms["средняя_длина"].brak == 7 and norms["средняя_длина"].min == 9
    briefs = exporter.load_briefs(ws.exports)
    assert {b.chapter for b in briefs} == {1, 5}
    b1 = exporter.load_brief(ws.exports, 1)
    assert b1.focal == "Каширин" and b1.year == 1995 and b1.volume_words == 300
    assert b1.participants == ["Каширин", "Зоя"]
    # корпус нормализован из Проза/
    corpus_files = sorted(f.name for f in ws.corpus.glob("*.txt"))
    assert corpus_files == ["Том1_Глава03.txt", "Том1_Глава04_МАКЕТ.txt"]


def test_идемпотентность(ws, library):
    h1 = exporter.run_export(library, ws.exports, ws.logs)
    h2 = exporter.run_export(library, ws.exports, ws.logs)
    assert h1 == h2  # FR-X3: байт-в-байт


def test_ошибка_структуры_с_файлом_и_строкой(ws, library):
    path = library / "31_Матрица_знаний.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "| сломанная | строка |\n", encoding="utf-8"
    )
    with pytest.raises(MarkupError) as e:
        exporter.run_export(library, ws.exports, ws.logs)
    assert "31_Матрица_знаний.md" in str(e.value)  # FR-X1: файл и строка


def test_отсутствие_обязательной_нормы(ws, library):
    path = library / "02_Стиль_и_голос.md"
    text = path.read_text(encoding="utf-8").replace(
        "| был_на_250 | «был/было/были» на 250 слов | — | 1 | — | шт/250 слов |\n", ""
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MarkupError, match="был_на_250"):
        exporter.export_norms(library)


def test_невалидная_выгрузка_не_пишется(ws, library):
    """FR-X2: при ошибке экспорт падает, старая выгрузка не перетирается мусором."""
    before = (ws.exports / "matrix.json").read_text(encoding="utf-8")
    path = library / "31_Матрица_знаний.md"
    path.write_text(path.read_text(encoding="utf-8") + "| x | y |\n", encoding="utf-8")
    with pytest.raises(MarkupError):
        exporter.run_export(library, ws.exports, ws.logs)
    assert (ws.exports / "matrix.json").read_text(encoding="utf-8") == before
    json.loads(before)
