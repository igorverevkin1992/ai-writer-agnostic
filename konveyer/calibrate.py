"""Калибровка норм стиля (FR-V1-7): `konveyer нормы --калибровать [файлы]`.

Считает все метрики реестра по образцам автора (или по принятым главам корпуса), предлагает коридоры по децилям
(мин — 10-й перцентиль, макс — 90-й; брак — 5-й для нижней границы и 95-й для верхней) и готовит правку таблицы
норм документа стиля вместе с записью в журнал решений. Применяет только по подтверждению автора — через
единственную точку записи в канон (`canonchange.canon_change`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import canonchange, exporter, guard, lang as lang_mod, metrics, textutils
from .config import Config
from .paths import Workspace
from .schemas import Brief, Norm

# метрики, для которых калибровка предлагает коридор: (нижняя граница нужна, верхняя граница нужна)
CALIBRATED: dict[str, tuple[bool, bool]] = {
    "средняя_длина": (True, True), "доля_коротких": (True, True), "доля_длинных": (False, True),
    "максимум_длины": (False, True), "объём_главы": (True, True), "был_на_250": (False, True),
    "усилители_на_1000": (False, True), "доля_диалога": (True, True), "фраз_в_абзаце": (True, True),
}
DECIMALS = {"доля_коротких": 2, "доля_длинных": 2, "доля_диалога": 2, "средняя_длина": 1, "был_на_250": 1,
            "усилители_на_1000": 1, "фраз_в_абзаце": 1}
TABLE_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M)


@dataclass
class Sample:
    name: str
    values: dict[str, float] = field(default_factory=dict)


@dataclass
class Proposal:
    samples: list[Sample]
    corridors: dict[str, Norm]          # предложенные нормы по калибруемым метрикам
    current: dict[str, Norm]            # текущие нормы (все)
    report: str = ""


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _round(metric_id: str, v: float) -> float:
    d = DECIMALS.get(metric_id, 0)
    return round(v, d) if d else float(round(v))


def measure(text: str, norms: dict[str, Norm], language: lang_mod.Language, stoplists: list | None = None) -> dict[str, float]:
    """Значения калибруемых метрик по одному тексту (нормы нужны только как параметры порогов)."""
    values: dict[str, float] = {}
    L = language
    body = textutils.strip_markdown(L.strip_document_inserts(text))
    sentences = L.split_sentences(body)
    lengths = [len(L.words(s)) for s in sentences if L.words(s)]
    tokens = L.normalize(body)
    paras = [p for p in L.paragraphs(body) if p.strip()]
    n = len(tokens)
    if lengths:
        values["средняя_длина"] = round(sum(lengths) / len(lengths), 2)
        values["максимум_длины"] = max(lengths)
        short = norms.get("короткая_фраза_порог")
        long_ = norms.get("длинная_фраза_порог")
        if short is not None and (short.max or short.min) is not None:
            thr = short.max if short.max is not None else short.min
            values["доля_коротких"] = round(sum(1 for x in lengths if x <= thr) / len(lengths), 3)
        if long_ is not None and (long_.max or long_.min) is not None:
            thr = long_.max if long_.max is not None else long_.min
            values["доля_длинных"] = round(sum(1 for x in lengths if x >= thr) / len(lengths), 3)
    values["объём_главы"] = n
    if n:
        forms = L.lexemes("был")
        values["был_на_250"] = round(sum(1 for t in tokens if t in forms) / n * 250, 2)
        ints = {L.normalize_word(w) for r in (stoplists or []) if r.kind == "усилитель" for w in r.items}
        if ints:
            values["усилители_на_1000"] = round(sum(1 for t in tokens if t in ints) / n * 1000, 2)
    if paras:
        values["доля_диалога"] = round(sum(1 for p in paras if L.is_dialogue_paragraph(p)) / len(paras), 3)
        per = [len([s for s in L.split_sentences(p) if L.words(s)]) for p in paras]
        values["фраз_в_абзаце"] = round(sum(per) / len(per), 2)
    return values


def propose(samples: list[tuple[str, str]], norms: dict[str, Norm], language: lang_mod.Language,
            stoplists: list | None = None, source: str = "калибровка") -> Proposal:
    """Коридоры по образцам: [(имя, текст)] → нормы с мин/макс/брак по децилям."""
    measured = [Sample(name, measure(text, norms, language, stoplists)) for name, text in samples]
    corridors: dict[str, Norm] = {}
    for mid, (need_min, need_max) in CALIBRATED.items():
        vals = [s.values[mid] for s in measured if mid in s.values]
        if not vals:
            continue
        unit = norms[mid].unit if mid in norms else metrics.REGISTRY[mid].unit
        lo, hi = _percentile(vals, 0.10), _percentile(vals, 0.90)
        brak = None
        if need_min and not need_max:
            brak = _percentile(vals, 0.05)
        elif need_max and not need_min:
            brak = _percentile(vals, 0.95)
        elif need_min and need_max and mid == "средняя_длина":
            brak = _percentile(vals, 0.05)
        corridors[mid] = Norm(min=_round(mid, lo) if need_min else None, max=_round(mid, hi) if need_max else None,
                              brak=_round(mid, brak) if brak is not None and len(vals) >= 3 else None,
                              unit=unit, source=source)
    pr = Proposal(samples=measured, corridors=corridors, current=norms)
    pr.report = render(pr)
    return pr


def render(pr: Proposal) -> str:
    ids = [m for m in CALIBRATED if any(m in s.values for s in pr.samples)]
    lines = ["# Калибровка норм", "", f"Образцов: {len(pr.samples)}. Коридоры — 10-й и 90-й перцентили; брак — 5-й/95-й "
             "(при ≥ 3 образцах). Утверждает автор: `konveyer нормы --калибровать … --утвердить`.", "",
             "## Значения по образцам", "", "| образец | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    for s in pr.samples:
        lines.append(f"| {s.name} | " + " | ".join(f"{s.values[m]:g}" if m in s.values else "—" for m in ids) + " |")
    lines += ["", "## Предложение (сейчас → предложено)", "", "| id | мин | макс | брак | единица | сейчас |", "|---|---|---|---|---|---|"]
    for mid, n in pr.corridors.items():
        cur = pr.current.get(mid)
        now = metrics.corridor(cur) if cur else "нормы нет"
        lines.append(f"| {mid} | {_fmt(n.min)} | {_fmt(n.max)} | {_fmt(n.brak)} | {n.unit} | {now} |")
    return "\n".join(lines) + "\n"


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:g}"


# ------------------------------------------------------------------ применение


def merged_table(text: str, corridors: dict[str, Norm]) -> str:
    """Таблица норм документа стиля с заменёнными строками калиброванных метрик (новые — добавляются в конец)."""
    m = None
    for cand in TABLE_RE.finditer(text):
        head = cand.group(0).splitlines()[0].lower()
        if "мин" in head and "макс" in head:
            m = cand
            break
    if m is None:
        raise ValueError("в документе стиля нет таблицы норм с колонками «мин | макс» — заведите её (каркас типа «стиль»)")
    lines = m.group(0).rstrip("\n").splitlines()
    headers = [c.strip().lower() for c in lines[0].strip().strip("|").split("|")]
    col = {h: i for i, h in enumerate(headers)}
    id_i = next((i for h, i in col.items() if h in ("id", "метрика", "идентификатор")), 0)
    mi, ma, br = col.get("мин"), col.get("макс"), col.get("брак")
    seen: set[str] = set()
    out = lines[:2]
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        mid = cells[id_i] if len(cells) > id_i else ""
        if mid in corridors:
            n = corridors[mid]
            if mi is not None and mi < len(cells):
                cells[mi] = _fmt(n.min)
            if ma is not None and ma < len(cells):
                cells[ma] = _fmt(n.max)
            if br is not None and br < len(cells):
                cells[br] = _fmt(n.brak)
            seen.add(mid)
        out.append("| " + " | ".join(cells) + " |")
    for mid, n in corridors.items():
        if mid in seen:
            continue
        cells = [""] * len(headers)
        cells[id_i] = mid
        param_i = col.get("параметр")
        if param_i is not None:
            cells[param_i] = metrics.REGISTRY[mid].description if mid in metrics.REGISTRY else mid
        if mi is not None:
            cells[mi] = _fmt(n.min)
        if ma is not None:
            cells[ma] = _fmt(n.max)
        if br is not None:
            cells[br] = _fmt(n.brak)
        unit_i = next((i for h, i in col.items() if h.startswith("единиц")), None)
        if unit_i is not None:
            cells[unit_i] = n.unit
        out.append("| " + " | ".join(c or "—" for c in cells) + " |")
    return text[: m.start()] + "\n".join(out) + "\n" + text[m.end():]


def next_decision_id(journal_text: str) -> str:
    nums = [int(x) for x in re.findall(r"Р-(\d+)", journal_text)]
    return f"Р-{(max(nums) + 1) if nums else 1:03d}"


def apply(ws: Workspace, cfg: Config, library: Path, pr: Proposal, *, author_confirmed: bool, commit: bool = True,
          rationale: str = "калибровка по образцам автора") -> canonchange.ChangeResult:
    """Правка таблицы норм стиля + запись в журнал решений — одной сессией записи в канон."""
    if not author_confirmed:
        raise PermissionError("калибровка меняет канон только по подтверждению автора (Д-8).")
    style = exporter.docs_of_type(library, "стиль", None, ws.root)
    if not style:
        raise FileNotFoundError("документа стиля в библиотеке нет — калибровать нечего")
    style_path = style[0]
    journal = exporter.docs_of_type(library, "журнал_решений", None, ws.root)
    new_text = merged_table(style_path.read_text(encoding="utf-8"), pr.corridors)
    ids = ", ".join(pr.corridors)

    def writer() -> None:
        guard.write_text(style_path, new_text)
        if journal:
            jt = journal[0].read_text(encoding="utf-8")
            did = next_decision_id(jt)
            entry = (f"\n## {did}\n\n- Дата: {date.today().strftime('%d.%m.%Y')}\n"
                     f"- Решение: нормы стиля откалиброваны по {len(pr.samples)} образцам: {ids}.\n"
                     f"- Обоснование: {rationale}; коридоры — 10-й и 90-й перцентили значений образцов.\n")
            guard.append_text(journal[0], entry)

    return canonchange.canon_change(ws, cfg, library, writer, f"[нормы] калибровка: {ids}", commit=commit,
                                    author_confirmed=True, require_clean=False, action="калибровка норм")


def samples_from_corpus(ws: Workspace, library: Path) -> list[tuple[str, str]]:
    """Образцы — принятые главы тома (документы типа «проза», без макетов)."""
    return [(p.stem, p.read_text(encoding="utf-8")) for _n, p in exporter.prose_files(library, None, ws.root)]


def samples_from_files(files: list[Path]) -> list[tuple[str, str]]:
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def brief_stub() -> Brief:
    return Brief(chapter=0)
