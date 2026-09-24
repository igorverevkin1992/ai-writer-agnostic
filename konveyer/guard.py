"""Защита записи в канон (§7.9, критерий приёмки 3).

Ни один компонент не пишет в `Библиотека/`, кроме канониста после
подтверждения автора. Все записи файлов в конвейере идут через write_text();
запись внутрь библиотеки возможна только внутри canon_write_session().
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
from pathlib import Path

_state = threading.local()  # только флаг сессии записи (на поток)
_library_dir: Path | None = None  # путь библиотеки — общий для всех потоков процесса (панель, задачи)


class CanonWriteError(PermissionError):
    pass


def set_library_dir(path: Path) -> None:
    global _library_dir
    _library_dir = path.resolve()


def _library() -> Path | None:
    return _library_dir


def _allowed() -> bool:
    return bool(getattr(_state, "canon_session", False))


@contextlib.contextmanager
def canon_write_session():
    """Открывается ТОЛЬКО канонистом после явного подтверждения автора (§7.9)."""
    _state.canon_session = True
    try:
        yield
    finally:
        _state.canon_session = False


def check_write_allowed(path: Path) -> None:
    lib = _library()
    if lib is None:
        return
    resolved = Path(path).resolve()
    if resolved == lib or lib in resolved.parents:
        if not _allowed():
            raise CanonWriteError(
                f"Запись в библиотеку канона запрещена: {path}. "
                "В Библиотека/ пишет только `konveyer canonize` после подтверждения автора (§7.9)."
            )


def write_text(path: Path, text: str) -> None:
    """Единая точка записи текстовых файлов конвейера (UTF-8, NFR-8).

    Запись атомарна: сбой посреди записи (состояние.yaml, вердикт.json, флаги.json…)
    не оставляет полуфайла — на диске либо старое содержимое, либо целиком новое.
    """
    check_write_allowed(path)
    write_atomic(path, text)


def write_atomic(path: Path, text: str) -> None:
    """Временный файл в той же папке + os.replace (атомарная подмена на POSIX и Windows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def append_text(path: Path, text: str) -> None:
    check_write_allowed(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def replace(src: Path, dst: Path) -> None:
    """Атомарная подмена файла (архив бэкапа, собранный во временном файле) — с той же защитой
    библиотеки, что у записи (§7.9)."""
    check_write_allowed(dst)
    os.replace(src, dst)


def remove(path: Path) -> None:
    """Единая точка удаления файлов конвейера: та же защита библиотеки, что и у записи (§7.9).
    Отсутствующий файл — не ошибка (устаревшие выгрузки корпуса могли исчезнуть раньше)."""
    check_write_allowed(path)
    with contextlib.suppress(FileNotFoundError):
        Path(path).unlink()
