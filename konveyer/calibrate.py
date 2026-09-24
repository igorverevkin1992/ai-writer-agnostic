"""Калибровка норм стиля (FR-V1-7): `konveyer нормы --калибровать [файлы]`.

Считает калибруемые метрики реестра (`Metric.calibrate`) по образцам автора (или по принятым главам корпуса) теми же
вычислителями, что и Э1, предлагает коридоры по децилям (мин — 10-й перцентиль, макс — 90-й; брак — 5-й для нижней
границы и 95-й для верхней) и готовит правку таблицы норм документа стиля вместе с записью в журнал решений.
Значения, которые калибровка не предлагает (брак при < 3 образцах, односторонние границы), наследуются из текущей
нормы автора, если согласуются с новым коридором, — иначе снимаются с пометкой в отчёте. Применяет только по
подтверждению автора — через единственную точку записи в канон (`canonchange.canon_change`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import canonchange, catalog, exporter, guard, lang as lang_mod, metrics
from .config import Config
from .paths import Workspace
from .schemas import Brief, Norm

TABLE_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M)
MIN_SAMPLES_FOR_BRAK = 3
DEFAULT_COLUMNS: dict[str, list[str]] = {  # логическая колонка → синонимы заголовка (если тип «стиль» их не объявил)
    "id": ["id", "метрика", "идентификатор"], "мин": ["мин", "min"], "макс": ["макс", "max"], "брак": ["брак"],
    "единица": ["единиц", "ед."], "параметр": ["параметр", "описание"],
}
DEFAULT_DECISION_PREFIX = "Р-"


@dataclass
class Sample:
    name: str
    values: dict[str, float] = field(default_factory=dict)


@dataclass
class Proposal:
    samples: list[Sample]
    corridors: dict[str, Norm]          # предложенные нормы по калибруемым метрикам (с унаследованными значениями)
    current: dict[str, Norm]            # текущие нормы (все)
    notes: dict[str, list[str]] = field(default_factory=dict)  # что унаследовано / снято по каждой метрике
    report: str = ""


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _round(m: metrics.Metric, v: float) -> float:
    return round(v, m.decimals) if m.decimals else float(round(v))


def calibrated_metrics(norms: dict[str, Norm]) -> list[metrics.Metric]:
    """Калибруемые метрики: реестр (`Metric.calibrate`) плюс лексемные нормы документа стиля."""
    out = [m for m in metrics.REGISTRY.values() if m.calibrate is not None]
    for nid in sorted(norms):
        if nid not in metrics.REGISTRY:
            lm = metrics.lexeme_metric(nid, norms[nid])
            if lm is not None and lm.calibrate is not None:
                out.append(lm)
    return out


def measure(text: str, norms: dict[str, Norm], language: lang_mod.Language, stoplists: list | None = None) -> dict[str, float]:
    """Значения калибруемых метрик по одному тексту — вычислителями реестра Э1 (нормы нужны как параметры порогов;
    самим метрикам подставляются фиктивные нормы, чтобы получить факт)."""
    cal = calibrated_metrics(norms)
    ctx_norms = {k: v for k, v in norms.items() if metrics.REGISTRY.get(k) and metrics.REGISTRY[k].kind == "параметр"}
    ctx_norms.update({m.id: Norm(unit=m.unit, source="калибровка") for m in cal})
    ctx = metrics.MetricContext(raw=text, brief=brief_stub(), norms=ctx_norms, stoplists=list(stoplists or []),
                                language=language)
    values: dict[str, float] = {}
    for m in cal:
        for r in m.compute(ctx):
            if r.check_id == m.check_id:
                try:
                    values[m.id] = float(r.actual.split()[0])
                except (ValueError, IndexError):
                    pass
    return values


def _inherit(cur: Norm | None, proposed: Norm, need_min: bool, need_max: bool) -> list[str]:
    """Наследование авторских значений в предложение: брак/мин/макс, которые калибровка не предлагает, остаются,
    если согласуются с новым коридором; иначе снимаются с пометкой. Возвращает пометки для отчёта."""
    notes: list[str] = []
    if cur is None:
        return notes
    if proposed.min is None and cur.min is not None:
        if proposed.max is None or cur.min <= proposed.max:
            proposed.min = cur.min
            notes.append(f"мин {cur.min:g} сохранён")
        else:
            notes.append(f"мин {cur.min:g} снят: выше нового макс {proposed.max:g}")
    if proposed.max is None and cur.max is not None:
        if proposed.min is None or cur.max >= proposed.min:
            proposed.max = cur.max
            notes.append(f"макс {cur.max:g} сохранён")
        else:
            notes.append(f"макс {cur.max:g} снят: ниже нового мин {proposed.min:g}")
    if proposed.brak is None and cur.brak is not None:
        side_ok = ((need_min and not need_max and proposed.min is not None and cur.brak <= proposed.min)
                   or (need_max and not need_min and proposed.max is not None and cur.brak >= proposed.max)
                   or (need_min and need_max and metrics.brak_side(Norm(min=proposed.min, max=proposed.max, brak=cur.brak))))
        if side_ok:
            proposed.brak = cur.brak
            notes.append(f"брак {cur.brak:g} сохранён (образцов меньше {MIN_SAMPLES_FOR_BRAK})")
        else:
            notes.append(f"брак {cur.brak:g} снят: внутри нового коридора — задайте вручную")
    return notes


def propose(samples: list[tuple[str, str]], norms: dict[str, Norm], language: lang_mod.Language,
            stoplists: list | None = None, source: str = "калибровка") -> Proposal:
    """Коридоры по образцам: [(имя, текст)] → нормы с мин/макс/брак по децилям."""
    measured = [Sample(name, measure(text, norms, language, stoplists)) for name, text in samples]
    corridors: dict[str, Norm] = {}
    notes: dict[str, list[str]] = {}
    for m in calibrated_metrics(norms):
        need_min, need_max = m.calibrate  # type: ignore[misc]
        vals = [s.values[m.id] for s in measured if m.id in s.values]
        if not vals:
            continue
        unit = norms[m.id].unit if m.id in norms else m.unit
        lo, hi = _percentile(vals, 0.10), _percentile(vals, 0.90)
        brak = None
        if need_min and not need_max:
            brak = _percentile(vals, 0.05)
        elif need_max and not need_min:
            brak = _percentile(vals, 0.95)
        elif need_min and need_max and m.id == "средняя_длина":
            brak = _percentile(vals, 0.05)
        n = Norm(min=_round(m, lo) if need_min else None, max=_round(m, hi) if need_max else None,
                 brak=_round(m, brak) if brak is not None and len(vals) >= MIN_SAMPLES_FOR_BRAK else None,
                 unit=unit, source=source)
        kept = _inherit(norms.get(m.id), n, need_min, need_max)
        corridors[m.id] = n
        if kept:
            notes[m.id] = kept
    pr = Proposal(samples=measured, corridors=corridors, current=norms, notes=notes)
    pr.report = render(pr)
    return pr


def render(pr: Proposal) -> str:
    ids = [m for m in pr.corridors]
    lines = ["# Калибровка норм", "", f"Образцов: {len(pr.samples)}. Коридоры — 10-й и 90-й перцентили; брак — 5-й/95-й "
             f"(при ≥ {MIN_SAMPLES_FOR_BRAK} образцах); значения, которых калибровка не предлагает, наследуются из текущих норм. "
             "Утверждает автор: `konveyer нормы --калибровать … --утвердить`.", "",
             "## Значения по образцам", "", "| образец | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    for s in pr.samples:
        lines.append(f"| {s.name} | " + " | ".join(f"{s.values[m]:g}" if m in s.values else "—" for m in ids) + " |")
    lines += ["", "## Предложение (сейчас → предложено)", "", "| id | мин | макс | брак | единица | сейчас | примечание |",
              "|---|---|---|---|---|---|---|"]
    for mid, n in pr.corridors.items():
        cur = pr.current.get(mid)
        now = metrics.corridor(cur) if cur else "нормы нет"
        lines.append(f"| {mid} | {_fmt(n.min)} | {_fmt(n.max)} | {_fmt(n.brak)} | {n.unit} | {now} | "
                     f"{'; '.join(pr.notes.get(mid, [])) or '—'} |")
    return "\n".join(lines) + "\n"


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:g}"


# ------------------------------------------------------------------ применение


def norm_columns(ws: Workspace | None) -> dict[str, list[str]]:
    """Синонимы колонок таблицы норм — из извлечения «нормы» типа «стиль» проекта (иначе умолчания)."""
    cols = {k: list(v) for k, v in DEFAULT_COLUMNS.items()}
    if ws is None:
        return cols
    spec = catalog.load_types(ws.root).get("стиль")
    if spec is None:
        return cols
    for ext in spec.extractions:
        if ext.get("имя") != "нормы":
            continue
        for fmt in ext.get("форматы") or []:
            for name, col in (fmt.get("колонки") or {}).items():
                syn = col.get("синонимы") if isinstance(col, dict) else None
                if syn:
                    cols[name] = [str(x).lower() for x in syn]
    return cols


def _find_col(headers: list[str], synonyms: list[str]) -> int | None:
    for i, h in enumerate(headers):
        if any(h == s or h.startswith(s) for s in synonyms):
            return i
    return None


def merged_table(text: str, corridors: dict[str, Norm], columns: dict[str, list[str]] | None = None) -> str:
    """Таблица норм документа стиля с заменёнными строками калиброванных метрик (новые — добавляются в конец).
    Колонки узнаются по синонимам типа «стиль» (мин/min, макс/max…)."""
    columns = columns or DEFAULT_COLUMNS
    m = None
    headers: list[str] = []
    for cand in TABLE_RE.finditer(text):
        headers = [c.strip().lower() for c in cand.group(0).splitlines()[0].strip().strip("|").split("|")]
        if _find_col(headers, columns["мин"]) is not None and _find_col(headers, columns["макс"]) is not None:
            m = cand
            break
    if m is None:
        raise ValueError("в документе стиля нет таблицы норм с колонками «мин | макс» — заведите её (каркас типа «стиль»)")
    lines = m.group(0).rstrip("\n").splitlines()
    id_i = _find_col(headers, columns["id"]) or 0
    mi, ma, br = _find_col(headers, columns["мин"]), _find_col(headers, columns["макс"]), _find_col(headers, columns["брак"])
    param_i, unit_i = _find_col(headers, columns["параметр"]), _find_col(headers, columns["единица"])
    seen: set[str] = set()
    out = lines[:2]
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        mid = cells[id_i] if len(cells) > id_i else ""
        if mid in corridors:
            n = corridors[mid]
            for idx, value in ((mi, n.min), (ma, n.max), (br, n.brak)):
                if idx is not None and idx < len(cells):
                    cells[idx] = _fmt(value)
            seen.add(mid)
        out.append("| " + " | ".join(cells) + " |")
    for mid, n in corridors.items():
        if mid in seen:
            continue
        cells = [""] * len(headers)
        cells[id_i] = mid
        if param_i is not None:
            cells[param_i] = metrics.describe(mid, n) or mid
        for idx, value in ((mi, n.min), (ma, n.max), (br, n.brak)):
            if idx is not None:
                cells[idx] = _fmt(value)
        if unit_i is not None:
            cells[unit_i] = n.unit
        out.append("| " + " | ".join(c or "—" for c in cells) + " |")
    return text[: m.start()] + "\n".join(out) + "\n" + text[m.end():]


def decision_id_prefix(ws: Workspace | None) -> str:
    """Префикс номера решения — из образца заголовка типа «журнал_решений» («(Р-\\d+)» → «Р-»)."""
    if ws is None:
        return DEFAULT_DECISION_PREFIX
    spec = catalog.load_types(ws.root).get("журнал_решений")
    for ext in (spec.extractions if spec else ()):
        for fmt in ext.get("форматы") or []:
            head = fmt.get("заголовок")
            if isinstance(head, str):
                m = re.search(r"([^\\()\[\]?*+|^$]+?)\\d", head)
                if m:
                    return m.group(1)
    return DEFAULT_DECISION_PREFIX


def next_decision_id(journal_text: str, prefix: str = DEFAULT_DECISION_PREFIX) -> str:
    nums = [int(x) for x in re.findall(re.escape(prefix) + r"(\d+)", journal_text)]
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"


def apply(ws: Workspace, cfg: Config, library: Path, pr: Proposal, *, author_confirmed: bool, commit: bool = True,
          rationale: str = "калибровка по образцам автора") -> canonchange.ChangeResult:
    """Правка таблицы норм стиля + запись в журнал решений — одной сессией записи в канон."""
    if not author_confirmed:
        raise PermissionError("калибровка меняет канон только по подтверждению автора (§10: запись в канон — решение автора).")
    style = exporter.docs_of_type(library, "стиль", None, ws.root)
    if not style:
        raise FileNotFoundError("документа стиля в библиотеке нет — калибровать нечего")
    style_path = style[0]
    journal = exporter.docs_of_type(library, "журнал_решений", None, ws.root)
    new_text = merged_table(style_path.read_text(encoding="utf-8"), pr.corridors, norm_columns(ws))
    ids = ", ".join(pr.corridors)
    prefix = decision_id_prefix(ws)

    def writer() -> None:
        guard.write_text(style_path, new_text)
        if journal:
            jt = journal[0].read_text(encoding="utf-8")
            did = next_decision_id(jt, prefix)
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
