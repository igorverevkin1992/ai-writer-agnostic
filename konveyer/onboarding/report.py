"""Отчёт готовности онбординга `онбординг/отчёт.md` (FR-ON-19): что распознано, что осталось сырьём,
чего не хватает по модулям, что делать дальше."""

from __future__ import annotations

from pathlib import Path

from .. import catalog, manifest as manifest_mod, project
from ..paths import Workspace
from . import importer, propose

REPORT = "отчёт.md"


def records_in(path: Path, spec: catalog.TypeSpec, root: Path, volume: int) -> int | str:
    if not spec.extractions:
        return "—"
    pv = propose.preview(path, spec, root, {}, volume)
    return pv.records if not pv.error else f"0 (⚠ {pv.error[:60]})"


def build(ws: Workspace, library: Path) -> str:
    root = ws.root
    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    man = manifest_mod.effective(root, library, types) if library.is_dir() else manifest_mod.Manifest()
    volume = man.проект.текущий_том
    entries = importer.load_index(ws)
    lines = ["# Отчёт онбординга", ""]

    # 1. что распознано
    lines += ["## 1. Что распознано", "", "| Файл сырья | Тип | Документ канона | Строк прочитано машиной |", "|---|---|---|---|"]
    n_canon = 0
    for e in entries:
        if e.статус != "в_каноне" or not e.документ_канона:
            continue
        n_canon += 1
        spec = types.get(e.тип or "")
        path = library / e.документ_канона
        count = records_in(path, spec, root, volume) if spec and path.exists() else "—"
        lines.append(f"| {e.файл} | {e.тип or '—'} | {e.документ_канона} | {count} |")
    if n_canon == 0:
        lines.append("| — | — | — | пока ни один документ не внесён |")

    # 2. что осталось сырьём
    lines += ["", "## 2. Что осталось сырьём", ""]
    props = {p.файл: p for p in propose.load(ws)}
    left = [e for e in entries if e.статус != "в_каноне"]
    if not left:
        lines.append("Всё сырьё разложено по библиотеке.")
    for e in left:
        pr = props.get(e.файл)
        if e.статус == "отклонено":
            reason = "отклонён автором"
        elif e.статус == "заменён":
            reason = e.причина or "заменён новой версией источника"
        elif not e.извлечено_в:
            reason = f"нет извлечения: {e.причина or e.формат}"
        elif pr and pr.решение == "сырьё":
            reason = "оставлен сырьём по решению автора (доступен поиском, в реестры не идёт)"
        elif pr and pr.тип == "сырьё":
            reason = "тип не распознан машинным слоем" + (f"; гипотезы: {', '.join(h['тип'] for h in pr.гипотезы[:2])}" if pr.гипотезы else "")
        elif pr:
            reason = f"предложен тип «{pr.тип}» ({pr.уверенность:.0%}), ждёт решения автора"
        else:
            reason = "не классифицирован — выполните `konveyer онбординг`"
        extra = " · источник исчез" if e.источник_исчез else ""
        lines.append(f"- {e.файл} — {reason}{extra}")

    # 3. чего не хватает по модулям
    lines += ["", "## 3. Чего не хватает по модулям", "", "| Модуль | Требуемые типы | Вердикт |", "|---|---|---|"]
    for mod in sorted(modules.values(), key=lambda m: (not m.base, m.name)):
        if not (mod.base or man.module_enabled(mod.name, modules)):
            continue
        if not mod.requires_types:
            continue
        verdicts = []
        for t in mod.requires_types:
            spec = types.get(t)
            docs = man.docs(library, t, volume if spec and spec.per_volume else None, types) if library.is_dir() else []
            if not docs:
                verdicts.append(f"{t}: нет")
                continue
            unfilled = [d.name for d in docs if "⚠ заполнить" in d.read_text(encoding="utf-8", errors="replace")]
            verdicts.append(f"{t}: неполно ({', '.join(unfilled)})" if unfilled else f"{t}: есть")
        lines.append(f"| {mod.name} | {', '.join(mod.requires_types)} | {'; '.join(verdicts)} |")

    # 4. что делать дальше
    lines += ["", "## 4. Что делать дальше", ""]
    steps: list[str] = []
    waiting = [e for e in left if e.извлечено_в and props.get(e.файл) and not props[e.файл].решение]
    if waiting:
        steps.append(f"Решить судьбу {len(waiting)} файлов в `онбординг/предложение.json` (поле `решение`), затем `konveyer онбординг --применить`.")
    no_extract = [e for e in left if not e.извлечено_в and e.статус != "отклонено"]
    if no_extract:
        steps.append(f"Конвертировать в .md/.docx и повторно импортировать: {', '.join(e.файл for e in no_extract[:5])}.")
    checks = project.readiness(root, library) if library.is_dir() else []
    for c in checks:
        if c.ok is not True and c.hint:
            steps.append(f"{c.label} → {c.hint}")
    steps.append("`konveyer доктор` — проверить готовность; `konveyer экспорт` — пересобрать выгрузки.")
    if any(not man.docs(library, "проза", None, types) for _ in [0]) if library.is_dir() else True:
        steps.append("Есть готовая проза — `konveyer импорт <папка>` и тип «проза»: нормы калибруются по ней (`konveyer нормы --калибровать`).")
    steps.append("Первый такт: `konveyer собрать 1` → `konveyer написать 1`.")
    lines += [f"{i}. {s}" for i, s in enumerate(steps[:7], start=1)]
    return "\n".join(lines) + "\n"


def save(ws: Workspace, library: Path) -> Path:
    d = propose.onboarding_dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    path = d / REPORT
    path.write_text(build(ws, library), encoding="utf-8")
    return path
