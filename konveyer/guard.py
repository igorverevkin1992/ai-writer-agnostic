"""Защита записи в канон (FR-SC-1, FR-SC-12; критерий приёмки 3).

Ни один компонент не пишет в `Библиотека/`, кроме канониста после
подтверждения автора. Все записи файлов в конвейере идут через write_text();
запись внутрь библиотеки возможна только внутри canon_write_session().

Путь проверяется и «как написан» (лексически), и после разрешения символьных ссылок:
ссылка внутри библиотеки на файл снаружи — всё равно библиотека (иначе запись подменила бы
саму ссылку обычным файлом), а путь снаружи, ведущий через ссылку внутрь, — тоже библиотека.
Символьные ссылки записью не заменяются вовсе.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
from pathlib import Path

_state = threading.local()  # только флаг сессии записи (на поток)
_library_dir: Path | None = None       # путь библиотеки после разрешения ссылок — общий для всех потоков процесса
_library_dir_raw: Path | None = None   # путь библиотеки как задан (абсолютный, без разрешения ссылок)


class CanonWriteError(PermissionError):
    pass


def set_library_dir(path: Path) -> None:
    global _library_dir, _library_dir_raw
    _library_dir_raw = Path(path).absolute()
    _library_dir = _library_dir_raw.resolve()


def _library() -> Path | None:
    return _library_dir


def _allowed() -> bool:
    return bool(getattr(_state, "canon_session", False))


@contextlib.contextmanager
def canon_write_session():
    """Открывается ТОЛЬКО модулем смены канона после явного подтверждения автора (FR-CN-2).
    Вложенный вход не закрывает внешнюю сессию: по выходу восстанавливается прежнее состояние."""
    previous = _allowed()
    _state.canon_session = True
    try:
        yield
    finally:
        _state.canon_session = previous


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def in_library(path: Path) -> bool:
    """Лежит ли путь в библиотеке — лексически или после разрешения ссылок (любой из вариантов)."""
    lib, raw = _library_dir, _library_dir_raw
    if lib is None:
        return False
    lexical = Path(path).absolute()
    resolved = lexical.resolve()
    roots = {lib, raw} if raw is not None else {lib}
    return any(_inside(p, r) for p in (lexical, resolved) for r in roots)


def _display(path: Path) -> str:
    """Путь в сообщении об ошибке — относительно библиотеки или рабочей области, без абсолютного пути машины
    автора (FR-SC-9)."""
    lexical = Path(path).absolute()
    for root, word in ((_library_dir_raw, "библиотека"), (_library_dir, "библиотека"),
                       (_library_dir.parent if _library_dir else None, "рабочая область")):
        if root is None:
            continue
        for p in (lexical, lexical.resolve()):
            if _inside(p, root):
                return f"{word}/{p.relative_to(root).as_posix()}" if p != root else word
    return Path(path).name


def check_write_allowed(path: Path) -> None:
    if in_library(path) and not _allowed():
        raise CanonWriteError(
            f"Запись в библиотеку канона запрещена: {_display(path)}. "
            "В Библиотека/ пишет только модуль смены канона после подтверждения автора (FR-SC-1)."
        )


def write_text(path: Path, text: str) -> None:
    """Единая точка записи текстовых файлов конвейера (UTF-8, NFR-8).

    Запись атомарна: сбой посреди записи (состояние.yaml, вердикт.json, флаги.json…)
    не оставляет полуфайла — на диске либо старое содержимое, либо целиком новое.
    """
    _write_atomic(path, text)


def _write_atomic(path: Path, text: str) -> None:
    """Временный файл в той же папке + os.replace (атомарная подмена на POSIX и Windows).
    Защита библиотеки проверяется здесь же, а не только в write_text: другого пути записи нет."""
    check_write_allowed(path)
    path = Path(path)
    if path.is_symlink():
        raise CanonWriteError(
            f"Запись через символьную ссылку запрещена: {_display(path)} — ссылка была бы заменена обычным файлом."
        )
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
    библиотеки, что у записи (FR-SC-1)."""
    check_write_allowed(dst)
    os.replace(src, dst)


def remove(path: Path) -> None:
    """Единая точка удаления файлов конвейера: та же защита библиотеки, что и у записи (FR-SC-1).
    Отсутствующий файл — не ошибка (устаревшие выгрузки корпуса могли исчезнуть раньше)."""
    check_write_allowed(path)
    with contextlib.suppress(FileNotFoundError):
        Path(path).unlink()
