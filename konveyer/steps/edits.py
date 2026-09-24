"""Правки и решения автора: resolve (решения по флагам, FR-RV-2), edits (предпросмотр правки.md),
diff (дифф черновиков)."""

from __future__ import annotations

from .. import review as review_mod, verifier2, writer
from ..errors import StepError
from ..fsm import ChapterState
from .common import _ctx, colors, echo, secho


def resolve(chapter: int, flag_id: str | None = None, decision: str | None = None, registry: str | None = None,
            reason: str = "") -> list:
    """Решения по флагам без ручной правки JSON (FR-RV-2): самоволку — вычеркнуть или канонизировать,
    любой флаг — отклонить с причиной или принять рекомендацию (указанием в правки.md).

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
    try:
        resolutions = review_mod.decide(ws, chapter, flag_id, decision, registry=registry, reason=reason)
    except ValueError as e:
        raise StepError(str(e)) from e
    left = review_mod.unresolved_samovolki(ws, chapter)
    secho(f"{flag_id}: {decision}{' → ' + registry if registry else ''}.", fg=colors.GREEN)
    if decision == "принять":
        echo(f"Рекомендация внесена указанием в {ws.chapter_rel(chapter)}/правки.md — поправьте формулировку при желании.")
    if left:
        echo(f"Осталось без решения: {', '.join(left)}")
    return resolutions


def edits(chapter: int) -> list:
    """Предпросмотр правок: как парсер понял правки.md (без вызова Писателя). Возвращает правки."""
    ws, cfg, lib = _ctx()
    parsed = review_mod.parse_edits_md(ws, chapter)
    if not parsed:
        echo("Правок не распознано (пары «БЫЛО:/СТАЛО:» и строки «УКАЗАНИЕ:»).")
        return parsed
    draft = ws.draft_path(chapter, ChapterState(ws, chapter).draft)
    text = draft.read_text(encoding="utf-8") if draft.exists() else ""
    # та же терпимость к пробелам/переносам и то же правило «ровно один раз», что при применении (FR-ED-1, FR-RV-3)
    hits = {e.seq: len(writer.find_quote(text, e.before)) for e in parsed if e.before}
    for e in parsed:
        if e.before:
            n = hits[e.seq]
            found = ("✓ найдено 1 раз — применится кодом" if n == 1 else
                     "✗ НЕ найдено в черновике — уйдёт Писателю" if n == 0 else
                     f"⚠ найдено {n} раза(-) — неоднозначно, уйдёт Писателю")
            echo(f"  {e.seq}. БЫЛО: {e.before[:70]}")
            echo(f"     СТАЛО: {e.after[:70]}   [{found}]")
        else:
            echo(f"  {e.seq}. УКАЗАНИЕ: {e.after[:70]}")
    bad = [seq for seq, n in hits.items() if n != 1]
    if bad:
        secho(
            f"⚠ Правки {bad}: «было» не найдено ровно один раз — их внесёт Писатель (вызов модели), "
            "и он может внести их неточно. Скопируйте цитату из черновика точно и однозначно.",
            fg=colors.YELLOW,
        )
    else:
        secho(f"Распознано {len(parsed)} правок, все применятся кодом. Далее: `konveyer apply-edits {chapter}`.", fg=colors.GREEN)
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
    if k2 < 1 or k1 < 1:
        raise StepError(f"у главы {chapter} ещё нет двух черновиков — дифф сравнивать не с чем.")
    for k in (k1, k2):
        if not ws.draft_path(chapter, k).exists():
            raise StepError(f"нет черновика {k} у главы {chapter} ({ws.chapter_rel(chapter)}/черновик_{k}.md).")
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
