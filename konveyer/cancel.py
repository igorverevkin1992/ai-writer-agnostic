"""Отмена долгой задачи автором (FR-AD-7): один флаг на процесс.

Проверяется между вызовами моделей (`konveyer такт`, `konveyer каркас`, авто-повторы Э1): прерывать
вызов посреди ответа нельзя (оплаченный ответ пропал бы), а между вызовами — безопасно.
Панель ставит флаг через POST /api/job/cancel; CLI — Ctrl+C.
"""

from __future__ import annotations

import threading

_flag = threading.Event()


class Cancelled(RuntimeError):
    """Задача остановлена автором; состояние главы — на последнем завершённом шаге."""


def request() -> None:
    _flag.set()


def clear() -> None:
    _flag.clear()


def requested() -> bool:
    return _flag.is_set()


def check(where: str = "") -> None:
    """Точка отмены: между вызовами моделей. Бросает Cancelled, если автор нажал «Остановить»."""
    if _flag.is_set():
        _flag.clear()
        raise Cancelled(f"остановлено автором{f' ({where})' if where else ''} — глава осталась на последнем завершённом шаге")
