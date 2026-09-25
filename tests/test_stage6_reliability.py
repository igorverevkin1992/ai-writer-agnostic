"""Надёжность (FR-SC-2…4): идемпотентная приёмка, безопасный git, откат без побочных эффектов,
сброс счётчиков FSM, повреждённые файлы не роняют панель."""

import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import canonist, compiler, gitops, review, server, verifier1, verifier2
from konveyer.cli import app
from konveyer.config import Config
from konveyer.fsm import ChapterState, StatusFileError


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _init_repo(root: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _accepted_chapter(ws, library, chapter: int = 1) -> ChapterState:
    """Глава доведена до «принято» с пакетом Канониста (без моделей)."""
    compiler.compile_window(ws, library, chapter)
    st = ChapterState(ws, chapter)
    st.transition("собрано", "compile")
    ws.draft_path(chapter, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(chapter, 1).write_text("Каширин нашёл записку утром возле хлебницы.", encoding="utf-8")
    st.set_draft(1)
    for state, cmd in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    verifier2.save_flags(ws, chapter, [])
    review.build_review_pack(ws, chapter, 1)
    st.transition("на-приёмке", "review")
    review.save_edits(ws, chapter, [])
    shutil.copyfile(ws.draft_path(chapter, 1), ws.draft_path(chapter, 2))
    st.set_draft(2)
    st.transition("правки", "apply-edits")
    verifier1.diff_check(ws, chapter, 1, 2, [])
    st.transition("дифф-контроль", "diff-check")
    st.transition("принято", "accept")
    canonist.build_batch(ws, Config(), chapter, 2)
    return ChapterState(ws, chapter)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# ------------------------------------------------------------- 4.1 идемпотентная приёмка


def test_повторный_apply_после_сбоя_не_дублирует_записи(ws, library):
    _init_repo(library)
    _accepted_chapter(ws, library, 1)
    # в пакет — одна строка реестра, чтобы дубликат был виден
    batch = ws.chapter_dir(1) / "пакет_канона.md"
    batch.write_text(batch.read_text(encoding="utf-8") + "\n- РЕЕСТР эпистемика → | M-777 | Каширин видел записку | Каширин | 1 | гл. 1 | — |\n",
                     encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    head = gitops.head(library)
    matrix = library / "31_Матрица_знаний.md"
    assert matrix.read_text(encoding="utf-8").count("M-777") == 1

    # сбой между коммитом и сменой состояния: состояние.yaml остался «принято»
    st = ChapterState(ws, 1)
    st.data["состояние"] = "принято"
    st.data.pop("коммит_приёмки", None)
    st._save()
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    assert "уже применён" in r.output
    st = ChapterState(ws, 1)
    assert st.state == "зафиксировано" and st.data["коммит_приёмки"] == head
    assert gitops.head(library) == head  # второго коммита нет
    assert matrix.read_text(encoding="utf-8").count("M-777") == 1  # строка не продублирована


# ------------------------------------------------------------- 4.2 / 4.3 git без побочных эффектов


def test_rollback_с_опечаткой_в_to_не_трогает_канон(ws, library):
    _init_repo(library)
    _accepted_chapter(ws, library, 1)
    runner = CliRunner()
    assert runner.invoke(app, ["canonize", "1", "--apply", "-y"]).exit_code == 0
    head = gitops.head(library)
    r = runner.invoke(app, ["rollback", "1", "--to", "собранo", "-y"])  # латинская «o»
    assert r.exit_code == 1 and "неизвестное состояние" in r.output.lower()
    assert gitops.head(library) == head and ChapterState(ws, 1).state == "зафиксировано"
    assert (library / "Проза" / "Том1_Глава01.md").exists()
    r = runner.invoke(app, ["rollback", "1", "--to", "зафиксировано", "-y"])
    assert r.exit_code == 1 and gitops.head(library) == head


def test_revert_с_конфликтом_оставляет_библиотеку_чистой(ws, library):
    _init_repo(library)
    doc = library / "Проза" / "Том1_Глава09.md"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("Первая версия.\n", encoding="utf-8")
    sha = gitops.commit_all(library, "[глава 9] приёмка")
    doc.write_text("Вторая версия — конфликтует с откатом первой.\n", encoding="utf-8")
    gitops.commit_all(library, "правка поверх")
    with pytest.raises(RuntimeError):
        gitops.revert(library, sha)
    assert "<<<<<<<" not in doc.read_text(encoding="utf-8")
    assert gitops.in_progress(library) is None
    assert not gitops.dirty(library)
    assert "Вторая версия" in doc.read_text(encoding="utf-8")


def test_незавершённый_revert_блокирует_запись_в_канон(ws, library):
    _init_repo(library)
    doc = library / "Проза" / "Том1_Глава09.md"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("Первая версия.\n", encoding="utf-8")
    sha = gitops.commit_all(library, "[глава 9] приёмка")
    doc.write_text("Вторая.\n", encoding="utf-8")
    gitops.commit_all(library, "поверх")
    subprocess.run(["git", "-C", str(library), "revert", "--no-commit", sha], capture_output=True)  # конфликт вручную
    assert gitops.in_progress(library) == "revert"
    with pytest.raises(RuntimeError, match="незавершённая операция"):
        canonist.apply_batch(ws, Config(), library, 1, 1)
    runner = CliRunner()
    r = runner.invoke(app, ["canon-commit", "-m", "x", "-y"])
    assert r.exit_code == 1 and "незавершённая" in r.output
    r = runner.invoke(app, ["doctor"])
    assert "незавершённых операций git" in r.output and "✗" in r.output
    subprocess.run(["git", "-C", str(library), "revert", "--abort"], capture_output=True)
    assert gitops.in_progress(library) is None


def test_revert_подписан_автором(ws, library, monkeypatch):
    _init_repo(library)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\ncommit_author: Автор Книги <a@b.c>\n", encoding="utf-8")
    _accepted_chapter(ws, library, 1)
    runner = CliRunner()
    assert runner.invoke(app, ["canonize", "1", "--apply", "-y"]).exit_code == 0
    assert runner.invoke(app, ["rollback", "1", "-y"]).exit_code == 0
    assert _git(library, "log", "-1", "--format=%an") == "Автор Книги"


# ------------------------------------------------------------- 4.7 сброс счётчиков FSM


def test_откат_сбрасывает_бюджет_итераций_правок(ws):
    st = ChapterState(ws, 3)
    ws.chapter_dir(3).mkdir(parents=True, exist_ok=True)
    for state in ("собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки"):
        st.transition(state)
    for _ in range(3):
        st.bump_edit_iterations()
    st.bump_retries()
    assert st.data["итераций_правок"] == 3
    st.rollback("на-приёмке")
    assert st.data["итераций_правок"] == 3  # откат внутри цикла правок бюджет не трогает
    st.rollback("собрано")
    assert st.data["итераций_правок"] == 0 and st.data["авто_повторов"] == 0


# ------------------------------------------------------------- 4.8 повреждённые файлы


def test_битый_status_не_роняет_панель(ws, library):
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.status_path(1).write_text("", encoding="utf-8")  # усечённый файл
    ws.chapter_dir(2).mkdir(parents=True, exist_ok=True)
    ChapterState(ws, 2).transition("собрано")
    with pytest.raises(StatusFileError, match="повреждён"):
        ChapterState(ws, 1)
    (ws.root / "регрессия").mkdir(exist_ok=True)
    (ws.root / "регрессия" / "report.json").write_text("{битый", encoding="utf-8")
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/state", timeout=5) as r:
            state = json.loads(r.read())
    finally:
        srv.shutdown(); srv.server_close()
    by = {c["chapter"]: c for c in state["chapters"]}
    assert by[1]["state"] == "повреждено" and "состояние.yaml" in by[1]["next"]
    assert by[2]["state"] == "собрано"
    assert state["regression_green"] is None
