"""Аудит кластера «тома и учёт»: откат зафиксированной главы (FR-TK-4, FR-SC-2, FR-SC-4), коммит канона
в подпапке репозитория (FR-CN-2), тома (FR-VL-2, FR-VL-3), сохранность (FR-BK-1…4), регрессия (FR-RG-2…4),
пере-тест (FR-RT-1, FR-RT-2), учёт и экономика (FR-CT-3, FR-EC-2, FR-EC-3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_stage6_reliability import _accepted_chapter, _git, _init_repo
from konveyer import gitops
from konveyer.cli import app
from konveyer.fsm import ChapterState
from konveyer.mdparse import MarkupError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _fixed_chapter(ws, library, n: int = 1) -> ChapterState:
    _accepted_chapter(ws, library, n)
    r = runner.invoke(app, ["canonize", str(n), "--apply", "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, n)
    assert st.state == "зафиксировано" and st.data.get("коммит_приёмки")
    return st


# ------------------------------------------------------------- откат зафиксированной главы


def test_фсм_откат_сбрасывает_счётчики(ws, library):
    """A3-26 / A5-4: выход из «зафиксировано» обнуляет счётчики и снимает поля цикла приёмки (FR-TK-4)."""
    _init_repo(library)
    st = _fixed_chapter(ws, library, 1)
    st.data["авто_повторов"] = 3
    st.data["итераций_правок"] = 2
    st.data["пакет_хэш"] = "abc"
    st.data["база_приёмки"] = 2
    st._save()
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "принято"
    assert st.data["авто_повторов"] == 0 and st.data["итераций_правок"] == 0
    for key in ("коммит_приёмки", "пакет_хэш", "база_приёмки"):
        assert key not in st.data, key
    assert st.data["история"][-1]["из"] == "зафиксировано" and st.data["история"][-1]["в"] == "принято"
    assert "выгрузки и корпус пересчитаны" in r.output


def test_откат_зафиксированной_требует_чистого_git(ws, library):
    """A5-3: грязная библиотека или чужие проиндексированные файлы блокируют откат ДО revert'а (FR-SC-2)."""
    _init_repo(library)
    _fixed_chapter(ws, library, 1)
    head = gitops.head(library)
    style = library / "02_Стиль_и_голос.md"
    style.write_text(style.read_text(encoding="utf-8") + "\nправка автора\n", encoding="utf-8")
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 1 and "незакоммиченные изменения" in r.output, r.output
    assert gitops.head(library) == head and ChapterState(ws, 1).state == "зафиксировано"
    _git(library, "checkout", "--", ".")
    # проиндексированный посторонний файл — тоже отказ, а не захват в revert-коммит
    (library / "Досье" / "чужое.md").write_text("# Чужое\n", encoding="utf-8")
    _git(library, "add", "Досье/чужое.md")
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 1 and gitops.head(library) == head, r.output
    _git(library, "reset", "-q")
    (library / "Досье" / "чужое.md").unlink()
    # чистая библиотека — откат проходит, revert-коммит трогает только файлы приёмки
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert _git(library, "log", "-1", "--format=%s").startswith("Revert")
    assert "чужое" not in _git(library, "show", "--stat", "--format=", "HEAD")


def test_откат_сообщает_о_непересчитанных_выгрузках(ws, library, monkeypatch):
    """A5-5: итоговая строка отката соответствует факту пересчёта выгрузок."""
    from konveyer.steps import canon as canon_steps

    _init_repo(library)
    _fixed_chapter(ws, library, 1)

    def broken(*a, **k):
        raise MarkupError(Path("02_Стиль.md"), 3, "сломанная таблица")

    monkeypatch.setattr(canon_steps.exporter, "run_export", broken)
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert "не пересчитаны" in r.output and "выгрузки и корпус пересчитаны" not in r.output
    assert ChapterState(ws, 1).state == "принято"


# ------------------------------------------------------------- коммит канона в подпапке репозитория


def test_commit_all_не_захватывает_индекс_вне_библиотеки(tmp_path):
    """C3-7: библиотека — подпапка репозитория; проиндексированный файл автора вне неё в коммит канона не попадает."""
    root = tmp_path / "проект"
    lib = root / "Библиотека"
    lib.mkdir(parents=True)
    (lib / "01_Мир.md").write_text("# Мир\n", encoding="utf-8")
    (root / "заметки_автора.md").write_text("заметки\n", encoding="utf-8")
    _init_repo(root)
    (root / "заметки_автора.md").write_text("заметки, ещё\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "заметки_автора.md"], check=True, capture_output=True)
    (lib / "01_Мир.md").write_text("# Мир\n\nновая строка\n", encoding="utf-8")
    (lib / "02_Стиль.md").write_text("# Стиль\n", encoding="utf-8")
    sha = gitops.commit_all(lib, "[глава 1] приёмка")
    assert sha
    files = _git(root, "show", "--stat", "--format=", "HEAD")
    assert "Библиотека/01_Мир.md" in files and "Библиотека/02_Стиль.md" in files
    assert "заметки_автора.md" not in files
    assert gitops.staged_files(lib) == ["заметки_автора.md"]  # индекс автора цел
    # revert при непустом индексе — отказ до каких-либо действий
    with pytest.raises(RuntimeError, match="в индексе git уже есть файлы"):
        gitops.revert(lib, sha)
    assert _git(root, "rev-parse", "HEAD") == sha


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(path: Path) -> dict:
    return json.loads(_read(path))
