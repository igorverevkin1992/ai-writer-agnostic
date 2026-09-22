"""Этап 5 (п. 25–26): единый конвейер изменения канона (`canonchange.canon_change`), запрет новых файлов
в Проза/ и корне библиотеки из панели, инкрементальный экспорт, кэш состояния панели."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from konveyer import canonchange, exporter, gitops, guard, server
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.mdparse import MarkupError


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _init_repo(root: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        _git(root, *args)


def _append(path: Path, text: str) -> None:
    guard.write_text(path, path.read_text(encoding="utf-8") + text)


def _mtimes(exports: Path) -> dict[str, int]:
    return {p.relative_to(exports).as_posix(): p.stat().st_mtime_ns for p in exports.rglob("*") if p.is_file()}


# ------------------------------------------------------------- п. 25: canon_change


def test_canon_change_с_коммитом(ws, library):
    _init_repo(library)
    head = gitops.head(library)
    doc = library / "02_Стиль_и_голос.md"
    result = canonchange.canon_change(
        ws, Config(), library, lambda: _append(doc, "\n<!-- правка -->\n"), "правка стиля (Р-001)",
        commit=True, author_confirmed=True,
    )
    assert result.commit and result.commit != head and not result.uncommitted
    assert not gitops.dirty(library) and _git(library, "log", "-1", "--format=%s") == "правка стиля (Р-001)"
    assert result.lint is not None and (ws.logs / "линтер.json").exists()  # линт прошёл по свежим выгрузкам
    assert "norms.json" in result.export_hashes and "закоммичен" in result.message


def test_canon_change_без_коммита_даёт_незакоммичено(ws, library):
    _init_repo(library)
    doc = library / "02_Стиль_и_голос.md"
    result = canonchange.canon_change(
        ws, Config(), library, lambda: _append(doc, "\n<!-- правка -->\n"), "правка из панели",
        commit=False, author_confirmed=True,
    )
    assert result.commit is None and result.uncommitted
    assert result.dirty_files == ["02_Стиль_и_голос.md"]
    assert "незакоммиченные изменения" in result.message
    assert gitops.dirty(library)
    # затем коммит поверх правок автора (сценарий Б, `konveyer canon-commit`): writer пуст, чистый git не требуется
    result2 = canonchange.canon_change(
        ws, Config(), library, lambda: None, "коммит правок", commit=True, author_confirmed=True, require_clean=False,
    )
    assert result2.commit and not gitops.dirty(library)
    # а с require_clean (приёмка, круги) грязная библиотека (правка на диске) — отказ ДО записи
    doc.write_text(doc.read_text(encoding="utf-8") + "\nещё\n", encoding="utf-8")
    called = []
    with pytest.raises(RuntimeError, match="чистого git"):
        canonchange.canon_change(ws, Config(), library, lambda: called.append(1), "x", commit=True, author_confirmed=True)
    assert not called


def test_canon_change_откат_при_сбое_writer(ws, library):
    _init_repo(library)
    head = gitops.head(library)
    new = library / "Проза" / "Том1_Глава77.md"
    doc = library / "02_Стиль_и_голос.md"

    def broken() -> None:
        guard.write_text(new, "текст\n")
        _append(doc, "\nправка\n")
        raise ValueError("сбой посреди записи")

    with pytest.raises(ValueError, match="сбой посреди"):
        canonchange.canon_change(ws, Config(), library, broken, "x", commit=False, author_confirmed=True)
    assert not new.exists() and "правка" not in doc.read_text(encoding="utf-8")
    assert not gitops.dirty(library) and gitops.head(library) == head
    # сбой экспорта (MarkupError) — тот же откат
    with pytest.raises(MarkupError):
        canonchange.canon_change(
            ws, Config(), library, lambda: _append(library / "31_Матрица_знаний.md", "| x | y |\n"), "x",
            commit=True, author_confirmed=True,
        )
    assert not gitops.dirty(library) and gitops.head(library) == head


def test_canon_change_не_откатывает_чужие_правки(ws, library):
    """Библиотека грязная на входе (правки автора на диске) — сбой writer НЕ делает `git checkout`."""
    _init_repo(library)
    doc = library / "02_Стиль_и_голос.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\nправка автора на диске\n", encoding="utf-8")  # чужой редактор

    def broken() -> None:
        raise ValueError("сбой")

    with pytest.raises(ValueError):
        canonchange.canon_change(ws, Config(), library, broken, "x", commit=False, author_confirmed=True)
    assert "правка автора на диске" in doc.read_text(encoding="utf-8")


def test_canon_change_отказ_при_незавершённом_revert(ws, library):
    _init_repo(library)
    doc = library / "Проза" / "Том1_Глава09.md"
    doc.write_text("Первая версия.\n", encoding="utf-8")
    sha = gitops.commit_all(library, "[глава 9] приёмка")
    doc.write_text("Вторая.\n", encoding="utf-8")
    gitops.commit_all(library, "поверх")
    subprocess.run(["git", "-C", str(library), "revert", "--no-commit", sha], capture_output=True)  # конфликт
    assert gitops.in_progress(library) == "revert"
    called = []
    for commit in (True, False):
        with pytest.raises(RuntimeError, match="незавершённая операция"):
            canonchange.canon_change(ws, Config(), library, lambda: called.append(1), "x", commit=commit, author_confirmed=True)
    assert not called
    subprocess.run(["git", "-C", str(library), "revert", "--abort"], capture_output=True)


def test_canon_change_требует_подтверждения(ws, library):
    with pytest.raises(PermissionError, match="подтверждения автора"):
        canonchange.canon_change(ws, Config(), library, lambda: None, "x", commit=False, author_confirmed=False)


def test_canon_change_без_git(ws, library):
    """Библиотека не под git: запись проходит, коммита нет, откат невозможен (сообщение автору)."""
    doc = library / "02_Стиль_и_голос.md"
    result = canonchange.canon_change(
        ws, Config(), library, lambda: _append(doc, "\nправка\n"), "x", commit=True, author_confirmed=True,
    )
    assert result.commit is None and not result.uncommitted and "не под git" in result.message
    assert "правка" in doc.read_text(encoding="utf-8")


def test_сбой_линтера_не_теряет_запись(ws, library, monkeypatch):
    _init_repo(library)
    from konveyer import lint

    def boom(*a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

    monkeypatch.setattr(lint, "run_lint", boom)
    result = canonchange.canon_change(
        ws, Config(), library, lambda: _append(library / "02_Стиль_и_голос.md", "\nправка\n"), "x",
        commit=True, author_confirmed=True,
    )
    assert result.commit and result.lint is not None and result.lint.findings[0].code == "ЛИНТ-0"


# ------------------------------------------------------------- панель: запрет новых файлов, незакоммичено


@pytest.fixture
def api(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    _init_repo(library)
    api = server.PanelAPI(ws, Config(), library)
    yield api
    api.stop_lint_worker()


def test_панель_не_создаёт_новые_файлы_в_Проза_и_корне(api, library):
    for rel in ("Проза/Том1_Глава99.md", "Новый_документ.md"):
        with pytest.raises(ValueError, match="не создаётся"):
            api.save_canon_doc(rel, "# текст\n", None)
        assert not (library / rel).exists()
    # правка существующего документа — можно; новый файл в подпапке (не Проза/) — можно
    r = api.save_canon_doc("02_Стиль_и_голос.md", (library / "02_Стиль_и_голос.md").read_text(encoding="utf-8") + "\nправка\n", None)
    assert r["canon_uncommitted"] and "02_Стиль_и_голос.md" in r["canon_uncommitted_files"] and r["lint"]
    r = api.save_canon_doc("Досье/Персонаж_Новый.md", "# Досье: Новый\n", None)
    assert (library / "Досье" / "Персонаж_Новый.md").exists() and r["canon_uncommitted"]
    # чтение несуществующего документа по-прежнему 404, а не «запрет создания»
    with pytest.raises(FileNotFoundError):
        api.canon_doc("нет.md")


def test_панель_показывает_незакоммиченный_канон(api, library):
    st = api.state()
    assert st["canon_uncommitted"] is False and st["canon_uncommitted_files"] == []
    api.save_canon_doc("02_Стиль_и_голос.md", (library / "02_Стиль_и_голос.md").read_text(encoding="utf-8") + "\nправка\n", None)
    st = api.state()
    assert st["canon_uncommitted"] is True and st["canon_uncommitted_files"] == ["02_Стиль_и_голос.md"]
    assert st["lint"] is not None  # сводка линта — из конвейера, без очереди наблюдателя
    # коммит из терминала — виден после сброса кэша (задача/операция) или по сроку годности
    gitops.commit_all(library, "правка")
    api.invalidate_caches()
    assert api.state()["canon_uncommitted"] is False


def test_панель_исправление_линтера_идёт_конвейером(api, library):
    p = library / "Досье" / "Персонаж_Зоя.md"
    line = next(i for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "24 года" in l)
    r = api.apply_lint_fix({"file": "Досье/Персонаж_Зоя.md", "line": line, "old": "24 года", "new": "25 лет"})
    assert "25 лет" in p.read_text(encoding="utf-8") and r["canon_uncommitted"]
    assert r["canon_uncommitted_files"] == ["Досье/Персонаж_Зоя.md"]
    # устаревшее исправление — отказ, библиотека остаётся как была (правка не откатывается: она уже «чужая»)
    with pytest.raises(ValueError, match="изменилась"):
        api.apply_lint_fix({"file": "Досье/Персонаж_Зоя.md", "line": line, "old": "24 года", "new": "25 лет"})
    assert "25 лет" in p.read_text(encoding="utf-8")


# ------------------------------------------------------------- п. 26а: инкрементальный экспорт


def test_экспорт_не_перезаписывает_неизменённые_файлы(ws, library):
    before = _mtimes(ws.exports)
    assert "корпус/.index.json" in before and "индекс.json" in before
    h1 = exporter.run_export(library, ws.exports, ws.logs)
    assert _mtimes(ws.exports) == before  # ни одного файла не тронуто (mtime тот же)
    # правка одной главы прозы: перезаписаны только её файл корпуса, индекс и manifest
    prose = sorted((library / "Проза").glob("*.md"))[0]
    prose.write_text(prose.read_text(encoding="utf-8") + "\nНовая фраза для корпуса.\n", encoding="utf-8")
    h2 = exporter.run_export(library, ws.exports, ws.logs)
    after = _mtimes(ws.exports)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {f"корпус/{prose.stem}.txt", "корпус/.index.json", "индекс.json"}, changed
    assert h1[f"{prose.stem}.txt"] != h2[f"{prose.stem}.txt"] and h1["norms.json"] == h2["norms.json"]
    assert "новая фраза" in (ws.corpus / f"{prose.stem}.txt").read_text(encoding="utf-8")
    # удалённая глава прозы — файл корпуса удалён, индекс без неё
    prose.unlink()
    exporter.run_export(library, ws.exports, ws.logs)
    assert not (ws.corpus / f"{prose.stem}.txt").exists()
    assert prose.stem + ".txt" not in json.loads((ws.corpus / ".index.json").read_text(encoding="utf-8"))


def test_экспорт_при_ошибке_разметки_ничего_не_пишет(ws, library):
    before = _mtimes(ws.exports)
    # ломаем матрицу (разбирается ПОСЛЕ норм и стоп-листов) и одновременно меняем прозу и нормы
    p31 = library / "31_Матрица_знаний.md"
    p31.write_text(p31.read_text(encoding="utf-8") + "| x | y |\n", encoding="utf-8")
    p02 = library / "02_Стиль_и_голос.md"
    p02.write_text(p02.read_text(encoding="utf-8").replace("| — | 1 |", "| — | 9 |"), encoding="utf-8")
    prose = sorted((library / "Проза").glob("*.md"))[0]
    prose.write_text(prose.read_text(encoding="utf-8") + "\nНовая фраза.\n", encoding="utf-8")
    with pytest.raises(MarkupError):
        exporter.run_export(library, ws.exports, ws.logs)
    assert _mtimes(ws.exports) == before  # атомарность: ни выгрузки, ни корпус, ни manifest не тронуты


def test_экспорт_перетирает_правку_выгрузки_руками(ws, library):
    """Выгрузки генерируются только экспортёром: подменённый файл при неизменном каноне восстанавливается."""
    p = ws.exports / "norms.json"
    good = p.read_text(encoding="utf-8")
    p.write_text("{}\n", encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    assert p.read_text(encoding="utf-8") == good


# ------------------------------------------------------------- п. 26б: кэш состояния панели


def test_кэш_state_видит_правку_status_yaml_на_диске(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    for n in (1, 2):
        st = ChapterState(ws, n)
        st.transition("собрано", "compile")
    api = server.PanelAPI(ws, Config(), library)
    try:
        s1 = api.state()
        assert [c["state"] for c in s1["chapters"]] == ["собрано", "собрано"]
        assert set(api._chapter_cache) == {1, 2}
        # правка на диске (другой процесс: CLI в терминале) — видна при следующем опросе без сброса кэша
        p = ws.status_path(2)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        data["состояние"] = "сгенерировано"
        data["черновик"] = 1
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        s2 = api.state()
        assert [c["state"] for c in s2["chapters"]] == ["собрано", "сгенерировано"]
        assert s2["chapters"][1]["draft"] == 1 and s2["chapters"][1]["next"] == "konveyer verify1 2"
        # повреждённый файл — карточка «повреждено», остальные главы живы; починка — снова видна
        p.write_text("", encoding="utf-8")
        s3 = api.state()
        assert s3["chapters"][1]["state"] == "повреждено" and s3["chapters"][0]["state"] == "собрано"
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        assert api.state()["chapters"][1]["state"] == "сгенерировано"
        # синхронная операция сбрасывает кэш целиком (страховка к mtime)
        with api.jobs.exclusive():
            pass
        assert api._chapter_cache == {}
        assert api.state()["chapters"][1]["state"] == "сгенерировано"
        # регрессия: значение кэшируется и совпадает с regression.is_green; смена конфиг.yaml — пересчёт
        from konveyer import regression

        assert api._regression_green() == regression.is_green(ws) and api._regression_cache is not None
        key = api._regression_cache[0]
        (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nwriter:\n  model: другая\n", encoding="utf-8")
        assert api._regression_green() == regression.is_green(ws) and api._regression_cache[0] != key
    finally:
        api.stop_lint_worker()
