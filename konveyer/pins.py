"""Пины моделей (FR-RT-2): отпечаток «провайдер/модель/параметры» по ролям фиксируется пере-тестом
(`konveyer пере-тест --зафиксировать`) в `журналы/пины.json`. Смена пина или параметра без пере-теста —
предупреждение при следующей генерации и строка в `журналы/смена_пина.jsonl` (черновик записи для журнала решений)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import guard
from .config import Config
from .paths import Workspace

PINS = "пины.json"
CHANGES = "смена_пина.jsonl"


def fingerprint(cfg: Config) -> dict[str, str]:
    """{роль: отпечаток} по провайдеру, модели и параметрам генерации (цены и режим обучения не входят)."""
    out: dict[str, str] = {}
    for role, mc in cfg.roles().items():
        key = json.dumps({"provider": mc.provider, "model": mc.model, "params": mc.params}, ensure_ascii=False, sort_keys=True)
        out[role] = f"{mc.provider}/{mc.model}#" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return out


def path_of(ws: Workspace) -> Path:
    return ws.logs / PINS


def privacy(cfg: Config) -> dict[str, dict]:
    """{роль: {провайдер, модель, режим_без_обучения}} — фиксация провайдера и режима (FR-SC-10)."""
    return {r: {"провайдер": m.provider, "модель": m.model, "режим_без_обучения": bool(m.no_training) or m.manual}
            for r, m in cfg.roles().items()}


def record(ws: Workspace, cfg: Config, note: str = "пере-тест", extra: dict | None = None) -> Path:
    data = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "основание": note, "пины": fingerprint(cfg),
            "приватность": privacy(cfg), **(extra or {})}
    p = path_of(ws)
    guard.write_text(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return p


def load(ws: Workspace) -> dict | None:
    p = path_of(ws)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def changed_roles(ws: Workspace, cfg: Config, roles: tuple[str, ...] | None = None) -> list[str]:
    """Роли, чей пин отличается от зафиксированного пере-тестом; без фиксации — пусто (нечего сравнивать)."""
    saved = load(ws)
    if not saved:
        return []
    current = fingerprint(cfg)
    pinned = saved.get("пины", {})
    return [r for r in (roles or tuple(current)) if r in pinned and pinned[r] != current[r]]


def warn_if_changed(ws: Workspace, cfg: Config, roles: tuple[str, ...] = ("писатель",)) -> str | None:
    """Предупреждение о смене пина без пере-теста + строка в журнал смен (один раз на отпечаток)."""
    changed = changed_roles(ws, cfg, roles)
    if not changed:
        return None
    current = fingerprint(cfg)
    saved = load(ws) or {}
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "роли": {r: {"было": saved.get("пины", {}).get(r), "стало": current[r]} for r in changed}}
    log = ws.logs / CHANGES
    already = log.exists() and json.dumps(entry["роли"], ensure_ascii=False, sort_keys=True) in log.read_text(encoding="utf-8")
    if not already:
        guard.append_text(log, json.dumps(entry, ensure_ascii=False) + "\n")
    what = "; ".join(f"{r}: {entry['роли'][r]['было']} → {entry['роли'][r]['стало']}" for r in changed)
    return (f"пин модели изменён без пере-теста ({what}). Прогоните `konveyer пере-тест` и зафиксируйте результат "
            f"(`--зафиксировать`), решение — записью в журнал решений; черновик строки — журналы/{CHANGES}.")
