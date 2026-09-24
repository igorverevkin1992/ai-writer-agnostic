"""Тесты экспортёра (FR-EX-1…FR-EX-3)."""

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
    assert {b.chapter for b in briefs} == {1, 2, 3, 4, 5, 6}
    b1 = exporter.load_brief(ws.exports, 1)
    assert b1.focal == "Каширин" and b1.year == 1995 and b1.volume_words == 300
    assert b1.participants == ["Зоя"]  # фокал в участники не входит
    # корпус нормализован из Проза/
    corpus_files = sorted(f.name for f in ws.corpus.glob("*.txt"))
    assert corpus_files == ["Том1_Глава03.txt"]  # макет — не принятая глава, в корпус не входит (FR-V1-1)


def test_идемпотентность(ws, library):
    h1 = exporter.run_export(library, ws.exports, ws.logs)
    h2 = exporter.run_export(library, ws.exports, ws.logs)
    assert h1 == h2  # FR-EX-2, П-6: байт-в-байт


def test_ошибка_структуры_с_файлом_и_строкой(ws, library):
    path = library / "31_Матрица_знаний.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "| сломанная | строка |\n", encoding="utf-8"
    )
    with pytest.raises(MarkupError) as e:
        exporter.run_export(library, ws.exports, ws.logs)
    line = len(path.read_text(encoding="utf-8").splitlines())
    assert f"31_Матрица_знаний.md:{line}:" in str(e.value)  # FR-EX-3: файл и строка


def test_отсутствие_нормы_выключает_метрику(ws, library):
    """FR-MT-3: нормы только из канона; нет нормы — метрика не считается, умолчаний в коде нет."""
    from konveyer import verifier1

    path = library / "02_Стиль_и_голос.md"
    text = path.read_text(encoding="utf-8").replace(
        "| был_на_250 | «был/было/были» на 250 слов | — | 1 | — | шт/250 слов |\n", ""
    )
    path.write_text(text, encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    norms = exporter.load_norms(ws.exports)
    assert "был_на_250" not in norms
    checks = verifier1.analyze("Он был дома. Было тихо.", "", exporter.load_brief(ws.exports, 1), norms,
                               exporter.load_stoplists(ws.exports))
    assert not [c for c in checks if c.check_id == "V1.3_был"]
    assert [c for c in checks if c.check_id == "V1.2a_средняя_длина"]


def test_невалидная_выгрузка_не_пишется(ws, library):
    """FR-EX-1, FR-SC-5: при ошибке экспорт падает, старая выгрузка не перетирается мусором."""
    before = (ws.exports / "matrix.json").read_text(encoding="utf-8")
    path = library / "31_Матрица_знаний.md"
    path.write_text(path.read_text(encoding="utf-8") + "| x | y |\n", encoding="utf-8")
    with pytest.raises(MarkupError):
        exporter.run_export(library, ws.exports, ws.logs)
    assert (ws.exports / "matrix.json").read_text(encoding="utf-8") == before
    json.loads(before)
