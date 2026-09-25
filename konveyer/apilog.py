"""Журнал API-вызовов (FR-CT-1, FR-WR-5): каждая попытка — строка журналы/api.jsonl (вне git)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import guard

# Текущий том рабочей области (выставляет `steps.common._ctx()`): строки журнала несут «volume», чтобы
# `konveyer том статус` считал стоимость по главам тома; старые строки без поля — том 1.
current_volume: int = 1


def log_call(
    logs_dir: Path,
    *,
    role: str,
    model: str,
    version: str = "",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_est: float | None = None,
    chapter: int | None = None,
    duration: float | None = None,
    error: str | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "model": model,
        "version": version,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_est": cost_est,
        "chapter": chapter,
        "volume": current_volume,
        "duration": round(duration, 2) if duration is not None else None,
    }
    if error:
        entry["error"] = error
    path = logs_dir / "api.jsonl"
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    if _tail_unterminated(path):
        # прошлая запись оборвана (сбой процесса посреди строки): новая строка начинается с новой строки,
        # иначе две записи склеились бы в одну нечитаемую
        line = "\n" + line
    guard.append_text(path, line)


def _tail_unterminated(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return False
            f.seek(size - 1)
            return f.read(1) != b"\n"
    except OSError:
        return False


def read_log_report(logs_dir: Path) -> tuple[list[dict], int]:
    """Строки журнала и число нечитаемых (оборванных при сбое, правленных руками) строк.
    Одна битая строка не лишает автора учёта, статуса тома и журнала (П-5): она пропускается и считается."""
    path = logs_dir / "api.jsonl"
    if not path.exists():
        return [], 0
    rows: list[dict] = []
    bad = 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def read_log(logs_dir: Path) -> list[dict]:
    return read_log_report(logs_dir)[0]


def corrupt_lines(logs_dir: Path) -> int:
    """Число нечитаемых строк журнала — для предупреждений в `учёт`, `журнал` и докторе."""
    return read_log_report(logs_dir)[1]


def corrupt_warning(logs_dir: Path) -> str | None:
    bad = corrupt_lines(logs_dir)
    if not bad:
        return None
    return (f"в журнале журналы/api.jsonl нечитаемых строк: {bad} — они пропущены в сводках "
            "(обрыв записи при сбое или правка руками); удалите их, если нужен чистый журнал.")
