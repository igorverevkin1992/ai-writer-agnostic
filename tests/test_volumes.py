"""Многотомность (FR-VL-1…FR-VL-3) без ломки тома 1: `главы/001` и документы без номера тома
работают как раньше; том 2 живёт в `главы/Т2/`, берёт документы `…_Том2.md`, состояния изолированы;
`konveyer volume close/open/status`."""

import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_stage6_reliability import _accepted_chapter, _git, _init_repo
from konveyer import apilog, circles, exporter, volume as volume_mod
from konveyer.cli import app
from konveyer.config import Config, load_config, set_volume
from konveyer.fsm import ChapterState, all_states
from konveyer.paths import Workspace

POGLAVNIK_T2 = """# 23. Поглавник · Том 2

## Глава 1 — Новый год

- Дата: 5 января 1996
- Год: 1996
- Фокал: Зоя
- Участники:
  - Зоя
  - Каширин
- Объём: 300
- Сцены:
  - Квартира Зои, вечер
- Биты:
  - Зоя получает письмо без обратного адреса
- Запреты:
  - НЕ упоминать гаражи
- НЕ знает:
  - кто написал письмо
- Закладки: P-002

## Глава 2 — Вокзал

- Дата: 9 января 1996
- Год: 1996
- Фокал: Каширин
- Участники:
  - Каширин
- Объём: 300
- Сцены:
  - Вокзал, утро
- Биты:
  - Каширин встречает поезд
- Закладки:

## Глава 3 — Архив

- Дата: 12 января 1996
- Год: 1996
- Фокал: Каширин
- Участники:
  - Каширин
  - Зоя
- Объём: 300
- Сцены:
  - Архив, день
- Биты:
  - Каширин находит дело сторожа
- Закладки:
"""


def _make_volume2(library: Path) -> None:
    """Демо-библиотека уже несёт документы тома 2 (`…_Том2.md`); хелпер сохранён для читаемости тестов."""
    assert (library / "23_Поглавник_Том2.md").exists()


def _hide_volume2(library: Path) -> None:
    """Убирает документы тома 2 из библиотеки (для сценариев «тома 2 ещё нет»)."""
    hidden = library.parent / "скрыто_том2"
    hidden.mkdir(exist_ok=True)
    for p in library.glob("*_Том2.md"):
        p.rename(hidden / p.name)
    if (library / ".git").exists():
        _git(library, "add", "-A")
        _git(library, "commit", "-q", "-m", "без тома 2")


def _restore_volume2(library: Path) -> None:
    hidden = library.parent / "скрыто_том2"
    for p in hidden.glob("*.md"):
        p.rename(library / p.name)
    if (library / ".git").exists():
        _git(library, "add", "-A")
        _git(library, "commit", "-q", "-m", "том 2")


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    apilog.current_volume = 1
    yield
    apilog.current_volume = 1


# ------------------------------------------------------------- пути и документы


def test_пути_тома_1_как_были(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.volume == 1
    assert ws.chapter_dir(5) == tmp_path / "главы" / "005"
    assert ws.status_path(5) == tmp_path / "главы" / "005" / "состояние.yaml"
    assert ws.chapter_rel(5) == "главы/005"
    ws2 = ws.for_volume(2)
    assert ws2.volume == 2 and ws2.root == tmp_path
    assert ws2.chapter_dir(1) == tmp_path / "главы" / "Т2" / "001"
    assert ws2.chapter_rel(1) == "главы/Т2/001"
    assert ws.chapter_dir(1, volume=3) == tmp_path / "главы" / "Т3" / "001"


def _names(library: Path, тип: str, volume: int) -> list[str]:
    return [p.name for p in exporter.docs_of_type(library, тип, volume, library.parent)]


def test_документы_по_тому(library):
    assert exporter.doc_volume(Path("23_Поглавник_Том2.md")) == 2
    assert exporter.doc_volume(Path("УГАР_Том1_Реестр_информационного_режима.md")) == 1
    assert exporter.doc_volume(Path("21_Круги_истории_Т3.md")) == 3
    assert exporter.doc_volume(Path("23_Поглавник_Часть_I.md")) is None
    assert exporter.doc_volume(Path("ТЗ_Конвейер_УГАР.md")) is None
    # том 1 — только свой поглавник; том 2 — только свой; общие документы серии — для любого тома
    assert _names(library, "план_глав", 1) == ["23_Поглавник_Том1.md"]
    assert _names(library, "план_глав", 2) == ["23_Поглавник_Том2.md"]
    assert _names(library, "эпистемика", 2) == ["31_Матрица_знаний_Том2.md"]
    assert _names(library, "эпистемика", 1) == ["31_Матрица_знаний.md"]
    assert _names(library, "стиль", 2) == ["02_Стиль_и_голос.md"]
    # потомные документы без маркера — том 1, тому 3 они не подходят
    assert _names(library, "план_глав", 3) == []
    assert not [m for m in exporter.missing_volume_docs(library, 2) if m.startswith("план_глав")]
    assert [m for m in exporter.missing_volume_docs(library, 3) if m.startswith("план_глав")]
    assert circles.canon_doc_name(2) == "21_Каркасы_Том2.md" and circles.CANON_DOC == circles.canon_doc_name(1)


def test_том_1_без_маркера_берёт_документы_без_номера(tmp_path):
    """Документ без номера тома (`23_Поглавник_Часть_I.md`) остаётся документом тома 1,
    а появление `…_Том2.md` его не вытесняет (без манифеста — по классификации)."""
    lib = tmp_path / "проект" / "Библиотека"
    lib.mkdir(parents=True)
    src = Path(__file__).resolve().parent.parent / "konveyer" / "data" / "демо" / "Библиотека"
    (lib / "23_Поглавник_Часть_I.md").write_text((src / "23_Поглавник_Том1.md").read_text(encoding="utf-8"), encoding="utf-8")
    (lib / "23_Поглавник_Том2.md").write_text((src / "23_Поглавник_Том2.md").read_text(encoding="utf-8"), encoding="utf-8")
    assert _names(lib, "план_глав", 1) == ["23_Поглавник_Часть_I.md"]
    assert _names(lib, "план_глав", 2) == ["23_Поглавник_Том2.md"]


# ------------------------------------------------------------- экспорт и состояния


def test_экспорт_текущего_тома(ws, library):
    exporter.run_export(library, ws.exports, ws.logs, 2)
    briefs = exporter.load_briefs(ws.exports)
    assert {b.volume for b in briefs} == {2} and [b.chapter for b in briefs][:1] == [1]
    assert exporter.export_volume(ws.exports) == 2
    t2_facts = {f.fact_id for f in exporter.load_matrix(ws.exports)}
    # обратно к тому 1 — документы тома 2 в выгрузки не попадают
    exporter.run_export(library, ws.exports, ws.logs, 1)
    briefs = exporter.load_briefs(ws.exports)
    assert {b.volume for b in briefs} == {1} and [b.chapter for b in briefs] == [1, 2, 3, 4, 5, 6]
    assert {f.fact_id for f in exporter.load_matrix(ws.exports)} != t2_facts
    assert exporter.export_volume(ws.exports) == 1


def test_экспорт_тома_без_документов_отказывает(ws, library):
    _hide_volume2(library)
    with pytest.raises(Exception) as e:
        exporter.run_export(library, ws.exports, ws.logs, 2)
    assert "план_глав" in str(e.value) and "Том2" in str(e.value)


def test_состояния_глав_изолированы_по_томам(ws, library):
    _make_volume2(library)
    st1 = ChapterState(ws, 1)
    st1.transition("собрано", "compile")
    ws2 = ws.for_volume(2)
    st2 = ChapterState(ws2, 1)
    assert st2.state == "не-начато" and st2.path == ws.root / "главы" / "Т2" / "001" / "состояние.yaml"
    st2.transition("собрано", "compile")
    st2.transition("сгенерировано", "write")
    assert (ws.root / "главы" / "Т2" / "001" / "состояние.yaml").exists()
    assert json.dumps([(s.chapter, s.state) for s in all_states(ws)]) == json.dumps([(1, "собрано")])
    assert [(s.chapter, s.state, s.volume) for s in all_states(ws, 2)] == [(1, "сгенерировано", 2)]
    assert [(s.chapter, s.state) for s in all_states(ws2)] == [(1, "сгенерировано")]
    assert ChapterState(ws, 1).state == "собрано"  # том 1 не тронут
    assert ChapterState(ws2, 1).data.get("том") == 2 and "том" not in ChapterState(ws, 1).data


def test_config_volume_и_ctx(ws, library):
    _make_volume2(library)
    assert load_config(ws).volume == 1
    set_volume(ws, 2)
    text = (ws.root / "конфиг.yaml").read_text(encoding="utf-8")
    assert "library_dir: Библиотека" in text and re.search(r"^(volume|текущий_том): 2$", text, re.M)
    assert load_config(ws).volume == 2
    set_volume(ws, 3)
    text = (ws.root / "конфиг.yaml").read_text(encoding="utf-8")
    assert len(re.findall(r"^(?:volume|текущий_том):", text, re.M)) == 1
    # ключ автора сохраняется (П-8): латинский `volume:` не подменяется русским и наоборот
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nvolume: 1\n", encoding="utf-8")
    set_volume(ws, 2)
    assert "volume: 2" in (ws.root / "конфиг.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        Config(volume=0)
    set_volume(ws, 2)
    r = CliRunner().invoke(app, ["export"])
    assert r.exit_code == 0 and "том 2" in r.output, r.output
    assert {b.volume for b in exporter.load_briefs(ws.exports)} == {2}
    assert apilog.current_volume == 2
    r = CliRunner().invoke(app, ["compile", "1"])
    assert r.exit_code == 0, r.output
    assert (ws.root / "главы" / "Т2" / "001" / "окно.md").exists()
    assert not (ws.root / "главы" / "001").exists()
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0 and "Том 2" in r.output and "главы/Т2/" in r.output, r.output
    r = CliRunner().invoke(app, ["status", "--том", "1"])
    assert r.exit_code == 0 and "тома 1 в работе нет" in r.output, r.output


def test_панель_отдаёт_том(ws, library):
    set_volume(ws, 2)
    _make_volume2(library)
    from konveyer import server
    from konveyer.config import library_dir

    cfg = load_config(ws)
    st = server.PanelAPI(ws.for_volume(cfg.volume), cfg, library_dir(ws, cfg))
    assert st.state()["volume"] == 2


# ------------------------------------------------------------- volume close / open / status


def test_volume_close_отказывает_при_незафиксированных(ws, library):
    _init_repo(library)
    _accepted_chapter(ws, library, 1)  # «принято», но не «зафиксировано»
    r = CliRunner().invoke(app, ["volume", "close", "1", "-y"])
    assert r.exit_code == 1 and "не зафиксированы" in r.output and "гл. 1 (принято)" in r.output and "гл. 5 (не-начато)" in r.output, r.output
    assert not (library / "35_Снапшот_Том1.md").exists()


def test_volume_close_проходит_и_open_переключает(ws, library):
    _init_repo(library)
    _hide_volume2(library)
    runner = CliRunner()
    for n in range(1, 7):
        _accepted_chapter(ws, library, n)
        r = runner.invoke(app, ["canonize", str(n), "--apply", "-y"])
        assert r.exit_code == 0, r.output
    assert all(s.state == "зафиксировано" for s in all_states(ws))
    r = runner.invoke(app, ["volume", "status"])
    assert r.exit_code == 0 and "Зафиксировано: 6 (1, 2, 3, 4, 5, 6)" in r.output, r.output

    r = runner.invoke(app, ["volume", "close", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert "Том 1 закрыт" in r.output and "Том 2 не открыт" in r.output
    snap = library / "35_Снапшот_Том1.md"
    assert snap.exists() and "Снапшот · Том 1" in snap.read_text(encoding="utf-8")
    assert "Глава 1 (Каширин): зафиксировано" in snap.read_text(encoding="utf-8")
    assert _git(library, "status", "--porcelain") == ""  # снапшот закоммичен
    assert "том-1" in _git(library, "tag", "--list").split()
    md = ws.root / "рукопись" / "Том1.md"
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# Том 1\n") and "## Глава 1. 12 июня 1995" in text and "## Глава 5" in text
    assert text.index("## Глава 1") < text.index("## Глава 5")
    assert "Каширин нашёл записку утром возле хлебницы." in text
    stats = (ws.root / "рукопись" / "Том1_статистика.md").read_text(encoding="utf-8")
    assert "зафиксировано: 6" in stats and "Слов в принятых главах" in stats
    assert load_config(ws).volume == 1  # документов тома 2 нет — не переключились

    # повторное закрытие — отказ без --заново
    r = runner.invoke(app, ["volume", "close", "1", "-y"])
    assert r.exit_code == 1 and "--заново" in r.output, r.output
    r = runner.invoke(app, ["volume", "close", "1", "-y", "--заново", "--без-переключения"])
    assert r.exit_code == 0, r.output

    # открыть том 2: без документов — отказ со списком; с документами — переключение и выгрузки тома 2
    r = runner.invoke(app, ["volume", "open", "2"])
    assert r.exit_code == 1 and "план_глав" in r.output and "Том2" in r.output, r.output
    _restore_volume2(library)
    r = runner.invoke(app, ["volume", "open", "2"])
    assert r.exit_code == 0 and "Текущий том: 2" in r.output, r.output
    assert load_config(ws).volume == 2
    assert {b.volume for b in exporter.load_briefs(ws.exports)} == {2}
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "тома 2 в работе нет" in r.output, r.output
    # главы тома 1 на месте и не видны в очереди тома 2
    assert ChapterState(ws, 1).state == "зафиксировано"
    assert all_states(ws.for_volume(2)) == []


def test_рукопись_без_docx_даёт_подсказку(ws, library, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_docx(name, *a, **k):
        if name == "docx":
            raise ImportError("нет python-docx")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_docx)
    shutil.copyfile(library / "Проза" / "Том1_Глава03.md", library / "Проза" / "Том1_Глава01.md")
    md, docx_path, hint = volume_mod.build_manuscript(ws, library, 1)
    assert md.exists() and docx_path is None and "python-docx" in hint
    assert [n for n, _ in volume_mod.prose_files(library, 1)] == [1, 3]  # макет не берётся
