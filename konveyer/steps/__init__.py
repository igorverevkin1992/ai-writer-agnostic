"""Ядро шагов конвейера: логика команд без typer.

Каждая функция ядра принимает обычные аргументы Python, печатает в stdout (интерфейсы перехватывают
поток: сервер — `redirect_stdout`), возвращает данные и бросает обычные исключения семейства
`StepError` (konveyer/errors.py), а также `cancel.Cancelled`, `fsm.TransitionError`, `fsm.StatusFileError`,
`mdparse.MarkupError`. Подтверждения — параметр `yes` плюс `confirm: Callable[[str], bool] | None`.
`konveyer/cli.py` (typer) и `konveyer/server.py` (панель) — тонкие обёртки: разбор опций → вызов ядра →
перевод исключений в код возврата/статус задачи.

Модули: `tact` — такт главы, `edits` — правки и решения, `quality` — качество и регрессия,
`canon` — канон и бэкап, `overview` — обзор, `setup` — init, `volume` — тома; `common` — контекст
рабочей области и общие помощники.

Параллельность запрещена (FR-TK-7): внешняя задача держит файловый замок проекта
`журналы/.задача.lock`; вторая команда (из другого терминала или панели) получает понятный отказ,
а не порчу состояние.yaml и черновиков.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .. import cancel, guard, timing
from ..errors import ManualMode, Rejected, StepError, StepExit, describe
from ..fsm import StatusFileError, TransitionError
from ..mdparse import MarkupError
from ..paths import find_workspace

__all__ = [
    "EXPECTED_ERRORS", "JOB_LOCK", "ManualMode", "Rejected", "StepError", "StepExit", "describe", "job_context",
    "outcome",
]

# ожидаемые ошибки шага (нет файла, структура MD, недопустимый переход FSM, битый состояние.yaml, …):
# интерфейсы показывают «ОШИБКА: …» без трейсбека; всё остальное — программная ошибка
EXPECTED_ERRORS = (FileNotFoundError, MarkupError, TransitionError, StatusFileError, RuntimeError, ValueError)

JOB_LOCK = ".задача.lock"


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


# ------------------------------------------------------------------ замок проекта (FR-TK-7)


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс: POSIX — сигнал 0; Windows — OpenProcess + код завершения (os.kill там убивает)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_path() -> Path | None:
    """Замок лежит в журналах проекта; вне проекта (нет конфиг.yaml/проект.yaml) замка нет —
    команда всё равно откажет в `_ctx()`, а мусор в случайной папке не появится."""
    try:
        ws = find_workspace()
    except FileNotFoundError:
        return None
    if not ((ws.root / "конфиг.yaml").exists() or (ws.root / "проект.yaml").exists()):
        return None
    return ws.logs / JOB_LOCK


def _read_lock(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def acquire_job_lock(name: str) -> Path | None:
    """Занять замок проекта на время внешней задачи. Замок мёртвого процесса (сбой, kill) снимается сам;
    живой чужой процесс — `StepError` с именем задачи и PID."""
    path = _lock_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "задача": name, "время": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                         ensure_ascii=False)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read_lock(path)
            pid = info.get("pid")
            pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else 0
            if pid == os.getpid() or not _pid_alive(pid):
                # осиротевший замок (процесс упал) или наш же процесс (задача в панели): снимаем
                with contextlib.suppress(OSError):
                    guard.remove(path)
                continue
            rel = path.relative_to(find_workspace().root).as_posix()
            raise StepError(
                f"в проекте уже выполняется задача «{info.get('задача', '?')}» (PID {pid}, с {info.get('время', '?')}) — "
                f"дождитесь её окончания или остановите её; вторая задача одновременно запрещена (FR-TK-7). "
                f"Если тот процесс уже завершён, удалите {rel}."
            )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        return path
    raise StepError(f"не удалось занять замок проекта {path.name} — повторите команду.")


def release_job_lock(path: Path | None) -> None:
    if path is None:
        return
    info = _read_lock(path)
    if info.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            guard.remove(path)


@contextlib.contextmanager
def job_context(name: str | None) -> Iterator[bool]:
    """Внешняя команда — одна задача для учёта времени такта (`timing.job`): переходы FSM внутри неё
    помечаются её идентификатором, интервалы между ними считаются машинными; вложенные вызовы
    (`run` → `write` → …) наследуют задачу. Запрос отмены прошлой задачи сбрасывается при старте
    внешней (`cancel.clear()`). Внешняя задача держит замок проекта (FR-TK-7). `name=None` — без учёта
    времени и без замка (сервер панели живёт часами и сам не задача). Даёт True, если это внешняя задача."""
    outermost = timing.current_job is None
    lock = None
    if outermost:
        cancel.clear()  # запрос отмены прошлой задачи не должен останавливать новую
        if name:
            lock = acquire_job_lock(name)
    try:
        with timing.job(name) if name else contextlib.nullcontext():
            yield outermost
    finally:
        release_job_lock(lock)
