"""Машинный слой классификации документов (FR-ON-7): сигнатуры типов из каталога, без API.

Сигнатуры объявляются данными в `типы/<тип>.yaml`: подстроки имени файла, слова заголовков, характерные колонки
таблиц, слова текста, регэкспы строк, признак широкой таблицы, отсутствие таблиц. Каждая сигнатура даёт вес;
итог — ранжированный список гипотез с уверенностью 0…1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import catalog, mdparse

WEIGHTS = {"имя_файла": 0.35, "заголовки": 0.2, "колонки": 0.25, "слова": 0.15, "строки": 0.25,
           "широкая_таблица": 0.15, "без_таблиц": 0.05, "секции": 0.2, "маркеры": 0.2}


@dataclass
class Hypothesis:
    type: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class DocProfile:
    """Что машина видит в документе: заголовки, колонки таблиц, слова, число таблиц, ширина таблиц."""

    name: str
    text: str
    headings: list[str]
    columns: list[str]
    tables: int
    max_width: int
    lines: list[str]

    @property
    def low_text(self) -> str:
        return self.text.lower()


def profile(path: Path, text: str | None = None) -> DocProfile:
    text = text if text is not None else path.read_text(encoding="utf-8", errors="replace")
    headings = [m.group(2).strip() for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.M)]
    columns: list[str] = []
    tables = 0
    max_width = 0
    try:
        for t in mdparse.parse_tables(path, text):
            tables += 1
            columns += t.headers
            max_width = max(max_width, len(t.headers))
    except mdparse.MarkupError:
        # битая таблица — считаем по строкам-заголовкам
        for m in re.finditer(r"^\|(.+)\|\s*$", text, re.M):
            cells = [c.strip() for c in m.group(1).split("|")]
            if cells and not all(set(c) <= set(":-") for c in cells):
                tables += 1
                columns += cells
                max_width = max(max_width, len(cells))
    return DocProfile(name=path.name, text=text, headings=headings, columns=columns, tables=tables,
                      max_width=max_width, lines=text.splitlines())


def _frac(hits: int, total: int) -> float:
    return 0.0 if total == 0 else min(1.0, hits / total)


def score(prof: DocProfile, spec: catalog.TypeSpec) -> Hypothesis | None:
    sig = spec.signatures
    if not sig:
        return None
    total_w = 0.0
    got = 0.0
    reasons: list[str] = []
    low_name = prof.name.lower()
    low_head = " ".join(prof.headings).lower()
    low_cols = " ".join(prof.columns).lower()
    low_text = prof.low_text

    def add(key: str, hits: list[str], candidates: list[str], mode: str = "any") -> None:
        nonlocal total_w, got
        w = WEIGHTS[key] * float(sig.get("вес", 1.0))
        total_w += w
        if not candidates:
            return
        frac = 1.0 if (mode == "any" and hits) else _frac(len(hits), len(candidates))
        if key in ("заголовки", "слова", "колонки"):
            frac = min(1.0, len(hits) / max(1, min(len(candidates), 3)))
        if frac:
            got += w * frac
            reasons.append(f"{key}: {', '.join(hits[:4])}")

    if "имя_файла" in sig:
        cand = [str(s).lower() for s in sig["имя_файла"]]
        add("имя_файла", [s for s in cand if s in low_name], cand)
    if "заголовки" in sig:
        cand = [str(s).lower() for s in sig["заголовки"]]
        add("заголовки", [s for s in cand if s in low_head], cand, "frac")
    if "колонки" in sig:
        cand = [str(s).lower() for s in sig["колонки"]]
        add("колонки", [s for s in cand if s in low_cols], cand, "frac")
    if "слова" in sig:
        cand = [str(s).lower() for s in sig["слова"]]
        add("слова", [s for s in cand if s in low_text], cand, "frac")
    if "строки" in sig:
        cand = [str(s) for s in sig["строки"]]
        hits = [p for p in cand if any(re.search(p, ln) for ln in prof.lines[:400])]
        add("строки", hits, cand)
    if "секции" in sig:
        cand = [str(s).lower() for s in sig["секции"]]
        hits = [s for s in cand if any(re.search(s, h.lower()) for h in prof.headings)]
        add("секции", hits, cand, "frac")
    if "маркеры" in sig:
        cand = [str(s) for s in sig["маркеры"]]
        add("маркеры", [s for s in cand if s in prof.text], cand)
    if "широкая_таблица" in sig:
        need = int(sig["широкая_таблица"])
        add("широкая_таблица", ["широкая таблица"] if prof.max_width >= need else [], ["широкая"])
    if sig.get("без_таблиц"):
        add("без_таблиц", ["без таблиц"] if prof.tables == 0 else [], ["без таблиц"])
    for w in sig.get("не_слова") or []:
        if str(w).lower() in low_text:
            got -= WEIGHTS["слова"] * 0.5
            reasons.append(f"минус: «{w}»")
    if total_w == 0:
        return None
    conf = max(0.0, min(1.0, got / total_w))
    for pat in sig.get("не_строки") or []:
        if any(re.search(str(pat), ln) for ln in prof.lines[:400]):
            conf *= 0.3
            reasons.append(f"минус: строка по образцу {pat}")
            break
    return Hypothesis(spec.name, round(conf, 3), reasons)


def classify_file(path: Path, types: dict[str, catalog.TypeSpec], text: str | None = None) -> list[Hypothesis]:
    """Ранжированные гипотезы типа документа (уверенность по убыванию)."""
    prof = profile(path, text)
    hyps = [h for h in (score(prof, spec) for spec in types.values()) if h and h.confidence > 0]
    hyps.sort(key=lambda h: (-h.confidence, h.type))
    return hyps
