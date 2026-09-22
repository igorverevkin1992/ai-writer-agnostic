"""Журнал API-вызовов (§6.3, NFR-5): каждая попытка — строка журналы/api.jsonl (вне git)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import guard

# Текущий том рабочей области (выставляет `steps.common._ctx()`): строки журнала несут «volume», чтобы
# `konveyer volume status` считал стоимость по главам тома; старые строки без поля — том 1.
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
    guard.append_text(logs_dir / "api.jsonl", json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(logs_dir: Path) -> list[dict]:
    path = logs_dir / "api.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
