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
from ..errors import Rejected, StepError
from ..paths import Workspace, find_workspace

__all__ = [
    "COMMAND_NAMES", "NEXT_STEP", "Confirm", "_chapter_flags_summary", "_ctx", "_ensure_dir", "_is_git_url",
    "_print_variants", "_print_verdict", "_sha256", "cmd", "colors", "confirm_or_reject", "echo", "next_step", "secho",
]

# ------------------------------------------------------------------ русские имена команд (П-8, FR-CL-5)

# Основное имя команды — русское (видно в справке), латинское — скрытый синоним. Таблица одна на ядро и CLI:
# подсказки автору («Дальше: …», «выполните …») строятся `cmd()` из русских имён, а не из скрытых синонимов.
COMMAND_NAMES = {
    "export": "экспорт", "compile": "собрать", "write": "написать", "verify1": "проверить1", "verify2": "проверить2",
    "review": "приёмка", "apply-edits": "правки-внести", "diff-check": "дифф-контроль", "accept": "принять",
    "canonize": "канон", "status": "статус", "resolve": "решение", "edits": "правки", "check": "проверка",
    "diff": "дифф", "log": "журнал", "panel": "панель", "find": "найти", "circles": "каркас", "lint": "линтер",
    "snapshot": "снапшот", "doctor": "доктор", "rollback": "откат", "regress": "регрессия", "add-golden": "золотой",
    "dashboard": "дашборд", "run": "такт", "canon-commit": "канон-коммит", "library-split": "библиотека-отделить",
    "backup": "бэкап", "init": "начать", "нормы": "norms", "метрики": "metrics", "учёт": "accounting",
    "импорт": "import", "онбординг": "onboarding", "пере-тест": "retest", "типы": "types",
}
# группа `volume` и её подкоманды
GROUP_NAMES = {"volume": "том", "volume open": "том открыть", "volume close": "том закрыть", "volume status": "том статус"}


def cmd(name: str, *args: object) -> str:
    """`cmd("write", 3, "--manual")` → «konveyer написать 3 --manual»: русское имя команды из таблицы
    (латинское имя — скрытый синоним, автор его в справке не видит)."""
    ru = GROUP_NAMES.get(name) or (COMMAND_NAMES.get(name, name) if name.isascii() else name)
    return " ".join(["konveyer", ru, *(str(a) for a in args)])

Confirm = Callable[[str], bool]


def _ctx(require_project: bool = True) -> tuple[Workspace, Config, Path]:
    """Рабочая область ТЕКУЩЕГО тома (манифест `проект.yaml`, иначе `конфиг.yaml: volume`): пути глав, выгрузки,
    документы канона и журнал API привязаны к нему. Вне проекта (нет ни конфиг.yaml, ни проект.yaml, ни
    библиотеки вверх от текущей папки) — понятный отказ, а не работа в случайной папке; `require_project=False`
    (доктор) — диагностика и вне проекта."""
    ws = find_workspace()
    cfg = load_config(ws)
    from .. import manifest as manifest_mod

    man = manifest_mod.load(ws.root)
    ws = ws.for_volume(man.проект.текущий_том if man is not None else cfg.volume)
    lib = library_dir(ws, cfg)
    if require_project and man is None and not (ws.root / "конфиг.yaml").exists() and not lib.exists():
        raise StepError(
            f"здесь нет проекта конвейера: не найдены конфиг.yaml, проект.yaml и библиотека (искал от {ws.root.name}/ вверх). "
            f"Перейдите в папку проекта или создайте его: `{cmd('init', '--демо')}` либо `konveyer проект создать`."
        )
    guard.set_library_dir(lib)
    apilog.current_volume = ws.volume
    from .. import timing

    timing.set_pause_threshold(cfg.author_pause_min)
    _install_before_call(ws, cfg)
    return ws, cfg, lib


def _install_before_call(ws: Workspace, cfg: Config) -> None:
    """Хук перед КАЖДЫМ вызовом модели любой роли (FR-EC-1, FR-EC-2, FR-RT-2): оценка стоимости по размеру
    промпта и ценам роли, предупреждения порогов (стоимость главы, расход за сутки) и о смене пина роли без
    пере-теста. Печатается в тот же поток, что и шаг (панель перехватывает)."""
    from .. import accounting, adapters, pins

    def hook(role: str, role_key: str | None, mc, prompt_chars: int) -> None:
        est = accounting.estimate_before(mc, prompt_chars)
        label = f"«{role}»"
        if est is not None:
            echo(f"Оценка стоимости вызова {label}: ≈ {est:.3f} $ (по ценам конфига, промпт {prompt_chars} знаков).")
        chapter = _current_chapter.get("n")
        for w in accounting.warnings(ws, cfg, chapter, prompt_chars, mc):
            secho(f"⚠ {w}", fg=colors.YELLOW)
        if role_key in cfg.roles():
            warning = pins.warn_if_changed(ws, cfg, (role_key,))
            if warning:
                secho(f"⚠ {warning}", fg=colors.YELLOW)

    adapters.before_call = hook


# глава текущего шага для порога «стоимость главы» в хуке (выставляют шаги такта через `current_chapter`)
_current_chapter: dict[str, int | None] = {"n": None}


def current_chapter(n: int | None) -> None:
    _current_chapter["n"] = n


def confirm_or_reject(yes: bool, confirm: Confirm | None, prompt: str, *, abort: bool = False) -> None:
    """Подтверждение автора (Д-17): `yes` — без вопроса; иначе вопрос через `confirm`; без `confirm`
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


# Подсказка «что дальше» по состоянию FSM (FR-TK-5): команда и её смысл, одна строка, одинаковая в
# терминале (`статус`) и в панели (`/api/state`). `{n}` — номер главы. Контекст состояния (лимит авто-повторов,
# грязный дифф, собранный пакет) учитывает `next_step()`.
NEXT_STEP = {
    "не-начато": f"{cmd('compile', '{n}')} — собрать окно контекста главы",
    "собрано": f"{cmd('write', '{n}')} — генерация черновика Писателем (без ключей: --manual с файлом черновика)",
    "сгенерировано": f"{cmd('verify1', '{n}')} — машинные проверки Э1 по нормам стиля",
    "верифицировано-1": f"{cmd('verify2', '{n}')} — смысловые проверки Э2 (без ключей: --manual с флаги.json)",
    "верифицировано-2": f"{cmd('review', '{n}')} — пакет приёмки автора (приёмка.md, правки.md, решения.json)",
    "на-приёмке": f"заполните правки.md и решения.json → {cmd('apply-edits', '{n}')} — внесение правок",
    "правки": f"{cmd('diff-check', '{n}')} — дифф-контроль внесённых правок",
    "дифф-контроль": f"{cmd('accept', '{n}')} — приёмка главы автором (дифф чист)",
    "принято": f"{cmd('canonize', '{n}')} → {cmd('canonize', '{n}', '--apply')} — пакет записей в канон и его фиксация",
    "зафиксировано": "готово ✓ — глава зафиксирована в каноне",
}


def next_step(ws: Workspace, st, cfg: Config | None = None) -> str:
    """Подсказка следующего шага с учётом артефактов главы (FR-TK-5): после исчерпания авто-повторов Э1,
    при грязном или отсутствующем дифф-отчёте, при уже собранном пакете Канониста строка отличается от
    статичной `NEXT_STEP[state]`. Никогда не падает: повреждённые артефакты → статичная подсказка."""
    n = st.chapter
    state = st.state
    chdir = ws.chapter_dir(n)
    try:
        if state == "сгенерировано":
            limit = cfg.auto_retries_verify1 if cfg is not None else None
            retries = int(st.data.get("авто_повторов", 0) or 0)
            if limit is not None and retries >= limit:
                return (f"БРАК Э1 после {limit} авто-повторов: новый черновик_{st.draft + 1}.md → "
                        f"{cmd('write', n, '--manual')}, либо решение автора {cmd('verify1', n, '--принять-брак --причина «…»')}")
        if state == "дифф-контроль":
            path = chdir / "дифф.json"
            if not path.exists():
                return f"{cmd('diff-check', n)} — отчёта дифф-контроля нет, прогоните его заново"
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("draft_after") not in (None, st.draft):
                return f"{cmd('diff-check', n)} — отчёт дифф-контроля относится к другому черновику"
            if data.get("not_applied") or data.get("unauthorized"):
                return f"поправьте правки.md → {cmd('apply-edits', n)} — дифф-контроль не чист, цикл правок повторяется"
        if state == "принято" and (chdir / "пакет_канона.md").exists():
            return f"{cmd('canonize', n, '--apply')} — проверьте пакет_канона.md и примените его (коммит приёмки)"
    except (OSError, ValueError):
        pass
    return NEXT_STEP.get(state, "").format(n=n)


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
