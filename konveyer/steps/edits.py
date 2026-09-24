"""Правки и решения автора: resolve (решения по самоволкам, FR-V2.5), edits (предпросмотр правки.md),
diff (дифф черновиков)."""

from __future__ import annotations

from .. import review as review_mod, verifier2
from ..errors import StepError
from ..fsm import ChapterState
from .common import _ctx, colors, echo, secho


def resolve(chapter: int, flag_id: str | None = None, decision: str | None = None, registry: str | None = None,
            reason: str = "") -> list:
    """Решения по самоволкам без ручной правки JSON (FR-V2.5).

    Без флага — список; с флагом и решением — записывает решение. Возвращает решения главы.
    """
    ws, cfg, lib = _ctx()
    resolutions = review_mod.load_resolutions(ws, chapter)
    if flag_id is None:
        if not resolutions:
            echo("Самоволок нет.")
            return resolutions
        flags = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
        for r in resolutions:
            quote = flags[r.flag_id].quote[:70] if r.flag_id in flags else ""
            state = r.decision or "БЕЗ РЕШЕНИЯ"
            target = f" → {r.target_registry}" if r.target_registry else ""
            echo(f"  {r.flag_id}: {state}{target}  «{quote}»")
        return resolutions
    if decision not in ("вычеркнуть", "канонизировать", "отклонить"):
        raise StepError("решение должно быть «вычеркнуть», «канонизировать» или «отклонить» (с --причина).")
    if decision == "отклонить" and not reason.strip():
        raise StepError("отклонение флага требует причины: --причина «…» (FR-RV-2).")
    if decision == "канонизировать":
        from ..canonist import registries

        regs = sorted(registries(ws.root))
        if registry not in regs:
            raise StepError(f"канонизация требует целевой реестр: --реестр один из {', '.join(regs)}.")
    flags_by_id = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
    if decision == "отклонить" and flag_id not in {r.flag_id for r in resolutions} and flag_id in flags_by_id:
        resolutions.append(review_mod.Resolution(flag_id=flag_id))  # отклонить можно и флаг-нарушение, не только самоволку
    for r in resolutions:
        if r.flag_id == flag_id:
            r.decision = decision  # type: ignore[assignment]
            r.target_registry = registry if decision == "канонизировать" else None
            r.reason = reason.strip() if decision == "отклонить" else ""
            if decision == "отклонить":
                review_mod.log_rejected(ws, chapter, flags_by_id.get(flag_id), flag_id, reason.strip())
            review_mod.save_resolutions(ws, chapter, resolutions)
            left = review_mod.unresolved_samovolki(ws, chapter)
            secho(f"{flag_id}: {decision}{' → ' + registry if registry else ''}.", fg=colors.GREEN)
            if left:
                echo(f"Осталось без решения: {', '.join(left)}")
            return resolutions
    raise StepError(f"самоволка {flag_id} не найдена (см. `konveyer resolve {chapter}`).")


def resolve_all(chapter: int, decision: str, registry: str | None = None) -> list[str]:
    """Одно решение для всех самоволок без решения («Вычеркнуть все» панели; FR-PN-7 — то же командой).
    «Отклонить» скопом невозможно: отклонение — по одному флагу и с причиной. Возвращает id решённых флагов."""
    ws, cfg, lib = _ctx()
    if decision not in ("вычеркнуть", "канонизировать"):
        raise StepError("для всех самоволок разом допустимо «вычеркнуть» или «канонизировать»; «отклонить» — по одному флагу с причиной.")
    if decision == "канонизировать":
        from ..canonist import registries

        regs = sorted(registries(ws.root))
        if registry not in regs:
            raise StepError(f"канонизация требует целевой реестр: --реестр один из {', '.join(regs)}.")
    resolutions = review_mod.load_resolutions(ws, chapter)
    todo = [r for r in resolutions if r.decision is None]
    for r in todo:
        r.decision = decision  # type: ignore[assignment]
        r.target_registry = registry if decision == "канонизировать" else None
    if todo:
        review_mod.save_resolutions(ws, chapter, resolutions)
    secho(f"Решено самоволок: {len(todo)} → {decision}{' → ' + registry if registry else ''}.", fg=colors.GREEN)
    return [r.flag_id for r in todo]


def edits(chapter: int) -> list:
    """Предпросмотр правок: как парсер понял правки.md (без вызова Писателя). Возвращает правки."""
    ws, cfg, lib = _ctx()
    parsed = review_mod.parse_edits_md(ws, chapter)
    if not parsed:
        echo("Правок не распознано (пары «БЫЛО:/СТАЛО:» и строки «УКАЗАНИЕ:»).")
        return parsed
    draft = ws.draft_path(chapter, ChapterState(ws, chapter).draft)
    text = draft.read_text(encoding="utf-8") if draft.exists() else ""
    for e in parsed:
        if e.before:
            found = "✓ найдено в черновике" if e.before in text else "✗ НЕ найдено в черновике дословно"
            echo(f"  {e.seq}. БЫЛО: {e.before[:70]}")
            echo(f"     СТАЛО: {e.after[:70]}   [{found}]")
        else:
            echo(f"  {e.seq}. УКАЗАНИЕ: {e.after[:70]}")
    bad = [e.seq for e in parsed if e.before and e.before not in text]
    if bad:
        secho(
            f"⚠ Правки {bad}: «было» не найдено дословно — Писатель может их не внести. "
            "Скопируйте цитату из черновика точно.",
            fg=colors.YELLOW,
        )
    else:
        secho(f"Распознано {len(parsed)} правок. Далее: `konveyer apply-edits {chapter}`.", fg=colors.GREEN)
    return parsed


def diff(chapter: int, k1: int | None = None, k2: int | None = None) -> list[str]:
    """Дифф черновиков главы (по умолчанию — два последних). Возвращает строки unified diff."""
    import difflib

    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    if k2 is None:
        k2 = st.draft
    if k1 is None:
        k1 = int(st.data.get("база_правок", k2 - 1))
    a = ws.draft_path(chapter, k1).read_text(encoding="utf-8").splitlines()
    b = ws.draft_path(chapter, k2).read_text(encoding="utf-8").splitlines()
    lines = list(difflib.unified_diff(a, b, f"черновик_{k1}", f"черновик_{k2}", lineterm="", n=1))
    if not lines:
        echo(f"черновик_{k1} и черновик_{k2} идентичны.")
        return lines
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            secho(line, fg=colors.GREEN)
        elif line.startswith("-") and not line.startswith("---"):
            secho(line, fg=colors.RED)
        else:
            echo(line)
    return lines
