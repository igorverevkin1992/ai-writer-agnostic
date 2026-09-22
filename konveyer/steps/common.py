"""Общее для ядра шагов: контекст рабочей области, подтверждения, печать, подсказки «что дальше».

Единственное, что ядро берёт у typer, — `echo`/`secho`/`colors` (печать с цветом, typer 0.27 несёт
click внутри себя); `typer.Exit`, `typer.confirm`, `typer.Option` в ядре не используются.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from typer import colors, echo, secho

from .. import apilog, guard, verifier2
from ..config import Config, library_dir, load_config
from ..errors import Rejected
from ..paths import Workspace, find_workspace

__all__ = [
    "NEXT_STEP", "Confirm", "_chapter_flags_summary", "_ctx", "_ensure_dir", "_is_git_url", "_print_variants",
    "_print_verdict", "_sha256", "colors", "confirm_or_reject", "echo", "secho",
]

Confirm = Callable[[str], bool]


def _ctx() -> tuple[Workspace, Config, Path]:
    """Рабочая область ТЕКУЩЕГО тома (`конфиг.yaml: volume`; аудит 2, п. 27): пути глав, выгрузки,
    документы канона и журнал API привязаны к нему."""
    ws = find_workspace()
    cfg = load_config(ws)
    ws = ws.for_volume(cfg.volume)
    lib = library_dir(ws, cfg)
    guard.set_library_dir(lib)
    apilog.current_volume = ws.volume
    return ws, cfg, lib


def confirm_or_reject(yes: bool, confirm: Confirm | None, prompt: str, *, abort: bool = False) -> None:
    """Подтверждение автора (Д-8): `yes` — без вопроса; иначе вопрос через `confirm`; без `confirm`
    или при отказе — `Rejected` (код 0; с `abort` — «Aborted!», код 1)."""
    if yes:
        return
    if confirm is None or not confirm(prompt):
        raise Rejected(abort=abort)


def _ensure_dir(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_git_url(value: str) -> bool:
    """URL удалённого репозитория (https://, ssh://, git@host:path) — в отличие от локальной папки."""
    return "://" in value or bool(re.match(r"^[\w.-]+@[\w.-]+:", value))


# подсказка «что дальше» по состоянию FSM
NEXT_STEP = {
    "не-начато": "konveyer compile {n}",
    "собрано": "konveyer write {n}",
    "сгенерировано": "konveyer verify1 {n}",
    "верифицировано-1": "konveyer verify2 {n}",
    "верифицировано-2": "konveyer review {n}",
    "на-приёмке": "заполните правки.md и решения.json → konveyer apply-edits {n}",
    "правки": "konveyer diff-check {n}",
    "дифф-контроль": "konveyer accept {n} (если чисто)",
    "принято": "konveyer canonize {n} → konveyer canonize {n} --apply",
    "зафиксировано": "готово ✓",
}


def _chapter_flags_summary(ws: Workspace, chapter: int) -> tuple[str, str]:
    """(сводка Э1, сводка Э2) по артефактам главы."""
    e1 = "—"
    verdict_path = ws.chapter_dir(chapter) / "вердикт.json"
    if verdict_path.exists():
        checks = json.loads(verdict_path.read_text(encoding="utf-8"))["checks"]
        brak = sum(1 for c in checks if c["status"] == "BRAK")
        flag = sum(1 for c in checks if c["status"] == "FLAG")
        e1 = (f"брак {brak}, " if brak else "") + f"флагов {flag}"
    e2 = "—"
    flags = verifier2.load_flags(ws, chapter)
    if (ws.chapter_dir(chapter) / "флаги.json").exists():
        sam = sum(1 for f in flags if f.kind == "samovolka")
        e2 = f"флагов {len(flags) - sam}, самоволок {sam}"
    return e1, e2


def _print_variants(summary: dict) -> None:
    """Таблица метрик Э1 по вариантам: строки — проверки, столбцы — варианты."""
    rows = summary.get("варианты", [])
    if not rows:
        return
    echo("Метрики Э1 по вариантам (главы/N/варианты.json):")
    echo(f"  {'проверка':<28}" + "".join(f"{r['вариант']:>16}" for r in rows))
    echo(f"  {'слов':<28}" + "".join(f"{r['слов']:>16}" for r in rows))
    echo(f"  {'брак / флагов':<28}" + "".join(f"{str(r['брак']) + ' / ' + str(r['флагов']):>16}" for r in rows))
    ids: list[str] = []
    for r in rows:
        ids += [i for i in r["метрики"] if i not in ids]
    for check_id in ids:
        cells = []
        for r in rows:
            m = r["метрики"].get(check_id)
            cells.append(f"{(m['actual'] + ' ' + m['status']) if m else '—':>16}")
        echo(f"  {check_id[:28]:<28}" + "".join(cells))


def _print_verdict(verdict) -> None:
    for c in verdict.checks:
        color = {"PASS": colors.GREEN, "FLAG": colors.YELLOW, "BRAK": colors.RED}[c.status]
        secho(f"  [{c.status}] {c.check_id}: {c.actual} (порог: {c.threshold})", fg=color)
