"""Общие помощники тестов (не фикстуры): git в библиотеке, глава, доведённая до «принято», живой сервер панели.
Фикстуры `ws`, `library` и автоматическое отключение ключей моделей — в conftest.py."""

from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from konveyer import canonist, compiler, review, server, verifier1, verifier2
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.paths import Workspace


def _git(root: Path, *args: str) -> str:
    """git в папке `root`; вывод без хвостовых пробелов (кириллические пути — как есть)."""
    return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _init_repo(root: Path) -> None:
    """Библиотека под git с тестовым авторством и первым коммитом."""
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        _git(root, *args)


def _accepted_chapter(ws: Workspace, library: Path, chapter: int = 1) -> ChapterState:
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


@contextmanager
def panel_server(ws: Workspace, library: Path, *, watch: bool = True):
    """Живой сервер панели на свободном порту в фоновом потоке; отдаёт (порт, сервер)."""
    srv = server.serve(ws, Config(), library, port=0, **({} if watch else {"watch": False}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1], srv
    finally:
        srv.shutdown()
        srv.server_close()
