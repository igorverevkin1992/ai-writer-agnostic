"""Ядро шагов конвейера (§8.1): логика команд без typer.

Каждая функция ядра принимает обычные аргументы Python, печатает в stdout (интерфейсы перехватывают
поток: сервер — `redirect_stdout`), возвращает данные и бросает обычные исключения семейства
`StepError` (konveyer/errors.py), а также `cancel.Cancelled`, `fsm.TransitionError`, `fsm.StatusFileError`,
`mdparse.MarkupError`. Подтверждения — параметр `yes` плюс `confirm: Callable[[str], bool] | None`.
`konveyer/cli.py` (typer) и `konveyer/server.py` (панель) — тонкие обёртки: разбор опций → вызов ядра →
перевод исключений в код возврата/статус задачи.

Модули: `tact` — такт главы, `edits` — правки и решения, `quality` — качество и регрессия,
`canon` — канон и бэкап, `overview` — обзор, `setup` — init, `volume` — тома; `common` — контекст
рабочей области и общие помощники.
"""

from __future__ import annotations

import contextlib
from typing import Iterator

from .. import cancel, timing
from ..errors import ManualMode, Rejected, StepError, StepExit, describe
from ..fsm import StatusFileError, TransitionError
from ..mdparse import MarkupError

__all__ = [
    "EXPECTED_ERRORS", "ManualMode", "Rejected", "StepError", "StepExit", "describe", "job_context", "outcome",
]

# ожидаемые ошибки шага (нет файла, структура MD, недопустимый переход FSM, битый состояние.yaml, …):
# интерфейсы показывают «ОШИБКА: …» без трейсбека; всё остальное — программная ошибка
EXPECTED_ERRORS = (FileNotFoundError, MarkupError, TransitionError, StatusFileError, RuntimeError, ValueError)


def outcome(e: BaseException) -> tuple[str, int] | None:
    """Исход шага для интерфейса: (текст автору без цвета, код возврата) — ровно то, что печатает CLI;
    None — не ожидаемая ошибка (трейсбек в CLI, 500 в панели)."""
    if isinstance(e, StepError):
        return describe(e), e.code
    if isinstance(e, cancel.Cancelled):
        return f"⏹ {e}", 2
    if isinstance(e, EXPECTED_ERRORS):
        return f"ОШИБКА: {e}", 1
    return None


@contextlib.contextmanager
def job_context(name: str | None) -> Iterator[bool]:
    """Внешняя команда — одна задача для учёта времени такта (`timing.job`): переходы FSM внутри неё
    помечаются её идентификатором, интервалы между ними считаются машинными; вложенные вызовы
    (`run` → `write` → …) наследуют задачу. Запрос отмены прошлой задачи сбрасывается при старте
    внешней (`cancel.clear()`). `name=None` — без учёта времени (сервер панели живёт часами и сам не
    задача). Даёт True, если это внешняя задача."""
    outermost = timing.current_job is None
    if outermost:
        cancel.clear()  # запрос отмены прошлой задачи не должен останавливать новую
    with timing.job(name) if name else contextlib.nullcontext():
        yield outermost
