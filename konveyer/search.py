"""Поиск по канону и выгрузкам (`konveyer find`): типизированная выдача.

Отвечает на постоянные вопросы автора: «где определён факт M-002?»,
«в какой главе закладка P-001?», «чьё правило запрещает это слово?» —
без ручного grep по MD.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import exporter

MAX_PER_GROUP = 20


@dataclass
class Hit:
    kind: str      # факт | закладка | правило | бриф | досье | хронология | информрежим | проза
    ref: str       # идентификатор/адрес
    text: str      # строка выдачи


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def find(ws_exports: Path, library: Path, query: str) -> list[Hit]:
    needle = _norm(query)
    hits: list[Hit] = []

    def match(*parts: str) -> bool:
        return any(needle in _norm(p) for p in parts if p)

    for f in exporter.load_matrix(ws_exports):
        if match(f.fact_id, f.fact, f.subject, f.note):
            knows = f"узнаёт в гл. {f.from_chapter}" if f.from_chapter is not None else "НЕ знает"
            hits.append(Hit("факт", f.fact_id, f"{f.subject}: {f.fact} ({knows})"))

    for p in exporter.load_plants(ws_exports):
        if match(p.plant_id, p.what, p.status):
            place = f"т{p.placed.get('vol', '?')} гл{p.placed.get('ch', '?')}"
            fires = "; ".join(f"т{x.get('vol')}" + (f" гл{x['ch']}" if "ch" in x else "") for x in p.fires)
            hits.append(Hit("закладка", p.plant_id, f"{p.what} — положена {place}, выстрел: {fires or '—'} [{p.status}]"))

    for r in exporter.load_stoplists(ws_exports):
        matched = [w for w in r.items if needle in _norm(w)]
        if matched or match(r.rule_id):
            scope = r.applies_to.get("focal") or ("усилители" if r.kind == "усилитель" else r.scope_label)
            hits.append(Hit("правило", r.rule_id, f"{scope}: {'; '.join(matched or r.items[:5])} ({r.action})"))

    for b in exporter.load_briefs(ws_exports):
        fields = b.scenes + b.beats + b.bans + b.not_knows + [b.focal, b.date]
        matched = [x for x in fields if x and needle in _norm(x)]
        if matched:
            hits.append(Hit("бриф", f"т{b.volume} гл. {b.chapter}", "; ".join(matched[:3])))

    for d in exporter.load_dossiers(ws_exports):
        if match(d.name, d.profile, d.physique, d.speech, *d.relations.values()):
            hits.append(Hit("досье", d.name, d.profile.splitlines()[0] if d.profile else ""))

    for c in exporter.load_continuity(ws_exports):
        if match(c.date, c.event, c.note):
            hits.append(Hit("хронология", c.date, f"{c.event}" + (f" (гл. {c.chapters})" if c.chapters else "")))

    for b in exporter.load_infobans(ws_exports):
        if match(b.ban_id, b.text):
            until = f"до тома {b.until_volume}" if b.until_volume else "бессрочно"
            hits.append(Hit("информрежим", b.ban_id, f"{b.text} ({until})"))

    for path in exporter.docs_of_type(library, "проза", None):  # все документы прозы, включая макеты
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if needle in _norm(line):
                hits.append(Hit("проза", f"{path.stem}:{i}", line.strip()[:100]))

    return hits


def grouped(hits: list[Hit]) -> dict[str, list[Hit]]:
    groups: dict[str, list[Hit]] = {}
    for h in hits:
        groups.setdefault(h.kind, []).append(h)
    return {k: v[:MAX_PER_GROUP] for k, v in groups.items()}
