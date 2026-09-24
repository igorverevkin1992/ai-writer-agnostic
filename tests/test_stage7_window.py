"""Этап 2 второго аудита: «Что было раньше (глазами фокала)», калибровка стиля в окне, хвост прозы вне V1.6."""

from pathlib import Path


from konveyer import compiler
from konveyer.paths import Workspace


def _window(ws: Workspace, library: Path, chapter: int) -> str:
    path, _ = compiler.compile_window(ws, library, chapter)
    return path.read_text(encoding="utf-8")


def _section(window: str, start: str, end: str | None = None) -> str:
    """Секция окна от маркера `start` до следующего маркера секции (`end` оставлен для читаемости вызовов)."""
    i = window.index(start)
    j = window.find("<!-- СЕКЦИЯ", i + len(start))
    return window[i:j if j >= 0 else len(window)]


def test_демо_секция_было_раньше(ws, library):
    w1 = _window(ws, library, 1)
    sec = _section(w1, "<!-- СЕКЦИЯ: что было раньше -->", "<!-- СЕКЦИЯ: драматургия -->")
    assert "первая глава фокала" in sec and "ХВОСТ ПРОЗЫ" not in sec
    w5 = _window(ws, library, 5)
    sec = _section(w5, "<!-- СЕКЦИЯ: что было раньше -->", "<!-- СЕКЦИЯ: драматургия -->")
    assert "гл. 1 (" in sec  # событие первой главы того же фокала


