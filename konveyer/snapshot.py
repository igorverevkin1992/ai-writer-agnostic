"""Снапшот тома (FR-VL-2): срез мира на конец тома.

Генерирует ЧЕРНОВИК среза в рабочей области (снапшоты/) — в канон его вносит
автор через правку библиотеки и `konveyer канон-коммит` (§7.9 не нарушается).
"""

from __future__ import annotations

from pathlib import Path

from . import exporter, guard
from .fsm import all_states
from .paths import Workspace


def build_snapshot(ws: Workspace, volume: int) -> Path:
    briefs = [b for b in exporter.load_briefs(ws.exports) if b.volume == volume]
    if not briefs:
        raise FileNotFoundError(f"В поглавнике нет глав тома {volume}.")
    last_chapter = max(b.chapter for b in briefs)
    matrix = exporter.load_matrix(ws.exports)
    plants = exporter.load_plants(ws.exports)
    continuity = exporter.load_continuity(ws.exports)
    states = {st.chapter: st.state for st in all_states(ws, volume)}

    lines = [
        f"# Снапшот · Том {volume}",
        "",
        f"Срез состояния мира на конец тома {volume} (главы ≤ {last_chapter}).",
        "Черновик сгенерирован конвейером; вносится в канон правкой библиотеки + `konveyer канон-коммит`.",
        "",
        "## Кто что знает (по эпистемике)",
        "",
    ]
    subjects = sorted({f.subject for f in matrix})
    for subj in subjects:
        known = [f for f in matrix if f.subject == subj and f.from_chapter is not None and f.from_chapter <= last_chapter]
        unknown = [f for f in matrix if f.subject == subj and (f.from_chapter is None or f.from_chapter > last_chapter)]
        lines.append(f"### {subj}")
        for f in known:
            lines.append(f"- ✓ [{f.fact_id}] {f.fact} (с гл. {f.from_chapter})")
        for f in unknown:
            lines.append(f"- ✗ [{f.fact_id}] НЕ знает: {f.fact}")
        lines.append("")

    lines += ["## Закладки тома", ""]
    for p in plants:
        if p.placed.get("vol") != volume:
            continue
        fires = "; ".join(
            f"т{x.get('vol')}" + (f" гл{x['ch']}" if "ch" in x else "") for x in p.fires
        )
        lines.append(f"- [{p.plant_id}] {p.what} — положена гл. {p.placed.get('ch', '?')}; выстрел: {fires or '—'}; статус: {p.status or '—'}")

    lines += ["", "## Континуити", ""]
    for c in continuity:
        lines.append(f"- {c.date}: {c.event}" + (f" (гл. {c.chapters})" if c.chapters else ""))

    lines += ["", "## Главы тома и их состояния", ""]
    for b in sorted(briefs, key=lambda b: b.chapter):
        lines.append(f"- Глава {b.chapter} ({b.focal}): {states.get(b.chapter, 'не-начато')}")

    path = ws.snapshots / f"Том{volume}_срез.md"
    guard.write_text(path, "\n".join(lines) + "\n")
    return path
