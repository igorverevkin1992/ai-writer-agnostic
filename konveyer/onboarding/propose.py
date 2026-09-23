"""Предложение «файл → тип» по сырью (FR-ON-7…FR-ON-14): `konveyer онбординг`.

Машинный слой работает всегда (сигнатуры типов из каталога); модельный слой — роль «Архивариус» — по запросу,
кэшируется по хэшу входа (FR-ON-8, FR-ON-10). Для каждого файла считаются: гипотезы типов, сопоставление
колонок таблицы с колонками типа (FR-ON-13), предложение разбить по разделам (FR-ON-9), предпросмотр разбора —
те самые строки, что станут машинными данными, и что из них попадёт в окно Писателя (FR-ON-11), вопросы автору
(FR-ON-14). Результат — `онбординг/предложение.json` (решения автора правятся в нём же: поле «решение»)
и `онбординг/предложение.md`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import catalog, declparse, manifest as manifest_mod, mdparse
from ..paths import Workspace
from . import classify, importer, normalize

PROPOSAL_JSON = "предложение.json"
PROPOSAL_MD = "предложение.md"
MODEL_CACHE = "кэш_модели"
PREVIEW_DIR = "предпросмотр"
MIN_CONFIDENCE = 0.35          # ниже — предлагается «сырьё»
SPLIT_MIN_SECTIONS = 2
PREVIEW_ROWS = 3
DECISIONS = ("принять", "сырьё", "отклонить", "разбить")  # либо «тип:<имя>»


@dataclass
class Preview:
    """Что машина прочитает из документа (FR-ON-11): записи разбора и поля, идущие в окно Писателя."""
    format: str = ""                    # вид формата, которым разобран
    records: int = 0
    rows: list[dict] = field(default_factory=list)      # первые записи (поле → значение)
    to_window: list[str] = field(default_factory=list)  # что из этого увидит Писатель (из «окно.показывать» типа)
    internal: list[str] = field(default_factory=list)   # что останется внутренним («окно.запрещено»)
    error: str = ""                     # почему разбор не удался


@dataclass
class ColumnMap:
    """Сопоставление колонок таблицы файла с колонками типа (FR-ON-13)."""
    extraction: str
    mapping: dict[str, str] = field(default_factory=dict)   # поле типа → заголовок в файле
    missing: list[str] = field(default_factory=list)        # обязательные поля без колонки
    generated: str = ""                                     # ключевая колонка, которую заполнит нормализация
    sample: list[dict] = field(default_factory=list)        # результат на трёх строках


@dataclass
class SplitPart:
    heading: str
    line: int
    type: str
    confidence: float


@dataclass
class Proposal:
    файл: str                        # имя в сырьё/оригиналы
    извлечено_в: str | None
    гипотезы: list[dict] = field(default_factory=list)   # [{тип, уверенность, почему}]
    тип: str = "сырьё"               # предложенный тип (лучшая гипотеза выше порога) или «сырьё»
    уверенность: float = 0.0
    источник_решения: str = "машина" # машина | модель | автор
    колонки: dict | None = None      # ColumnMap
    разбить: list[dict] = field(default_factory=list)   # SplitPart
    предпросмотр: dict | None = None # Preview
    вопросы: list[str] = field(default_factory=list)    # FR-ON-14
    том: int | None = None           # для потомных типов
    имя_документа: str = ""          # предлагаемое имя файла в библиотеке
    решение: str = ""                # автор: принять | тип:<имя> | сырьё | отклонить | разбить
    модель: dict | None = None       # ответ Архивариуса (если был)


def onboarding_dir(ws: Workspace) -> Path:
    return ws.root / "онбординг"


# ------------------------------------------------------------------ сопоставление колонок и предпросмотр


def _table_formats(spec: catalog.TypeSpec) -> list[tuple[str, dict]]:
    return [(ext.get("имя", ""), fmt) for ext in spec.extractions for fmt in ext.get("форматы", [])
            if fmt.get("вид") == "таблица" and fmt.get("колонки")]


def _closest(field: str, spec: dict, headers: list[str]) -> str | None:
    """Ближайший заголовок к полю: общая основа ≥ 4 знаков (без регистра)."""
    cands = [field] + [str(s) for s in (spec.get("синонимы") or [])] if isinstance(spec, dict) else [field]
    for h in headers:
        hl = h.lower()
        for c in cands:
            c = c.lower()
            if len(c) >= 4 and (c[:4] in hl or hl[:4] in c):
                return h
    return None


def column_map(path: Path, spec: catalog.TypeSpec, overrides: dict | None = None) -> ColumnMap | None:
    """Первая таблица файла против первого табличного формата типа; None — у типа нет таблиц или в файле их нет."""
    formats = _table_formats(spec)
    if not formats:
        return None
    try:
        tables = mdparse.parse_tables(path)
    except mdparse.MarkupError:
        return None
    if not tables:
        return None
    name, fmt = formats[0]
    headers = tables[0].headers
    # нестрогое сопоставление: обязательность колонок не мешает увидеть, что уже совпало
    lenient = {f: ({**s, "обязательна": False} if isinstance(s, dict) else s) for f, s in fmt["колонки"].items()}
    found = declparse.match_columns(headers, lenient, overrides or {}) or {}
    cm = ColumnMap(extraction=name, mapping=dict(found))
    for fld, cspec in fmt["колонки"].items():
        if fld in found:
            continue
        guess = _closest(fld, cspec, [h for h in headers if h not in found.values()])
        if guess:
            cm.mapping[fld] = guess
        elif isinstance(cspec, dict) and cspec.get("роль") == "ключ":
            cm.generated = fld  # ключевая колонка без соответствия — идентификаторы сгенерирует нормализация
        elif isinstance(cspec, dict) and cspec.get("обязательна"):
            cm.missing.append(fld)
    for row in tables[0].rows[:PREVIEW_ROWS]:
        cm.sample.append({fld: row.get(h, "") for fld, h in cm.mapping.items()})
    return cm


def normalized_preview_path(ws: Workspace, spec: catalog.TypeSpec, path: Path, mapping: dict[str, str] | None,
                            source: str) -> Path:
    """Нормализованный документ для предпросмотра — ровно тот текст, что уйдёт в библиотеку при применении."""
    d = onboarding_dir(ws) / PREVIEW_DIR
    d.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="replace")
    norm = normalize.normalize(text, spec, source=source, mapping=mapping, title=path.stem)
    target = d / (path.name if path.name.endswith(".md") else path.name + ".md")
    target.write_text(norm.text, encoding="utf-8")
    return target


def preview(path: Path, spec: catalog.TypeSpec, root: Path, overrides: dict | None = None, volume: int = 1) -> Preview:
    """Разбор документа форматами типа: те же примитивы, что и экспорт (предпросмотр = выгрузка)."""
    pv = Preview()
    window = spec.window or {}
    pv.to_window = [str(x) for x in (window.get("показывать") or [])]
    pv.internal = [str(x) for x in (window.get("запрещено") or [])]
    if not spec.extractions:
        pv.error = "тип без машинного чтения: документ попадёт в библиотеку целиком, машина его не разбирает"
        return pv
    ctx = declparse.ParseContext(volume=volume, overrides=overrides or {}, project_root=root, library=path.parent,
                                 params={"known_names": set(), "exports": {}, "pseudo": set()})
    total = 0
    for ext in spec.extractions:
        try:
            records, fmt = declparse.parse_document(path, list(ext.get("форматы") or []), ctx)
        except (ValueError, KeyError) as e:
            pv.error = f"{ext.get('имя')}: {e}"
            continue
        if records is None:
            if not ext.get("необязательно"):
                pv.error = f"{ext.get('имя')}: " + declparse.describe_expected(list(ext.get("форматы") or []))
            continue
        items = list(records.values()) if isinstance(records, dict) else list(records)
        total += len(items)
        if not pv.format:
            pv.format = str((fmt or {}).get("вид", ""))
        for r in items[:PREVIEW_ROWS]:
            data = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            pv.rows.append({k: _short(v) for k, v in data.items() if not str(k).startswith("_")})
    pv.records = total
    return pv


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ("" if v is None else str(v))
    return s if len(s) <= 80 else s[:77] + "…"


# ------------------------------------------------------------------ разбиение


def split_candidates(path: Path, types: dict[str, catalog.TypeSpec]) -> list[SplitPart]:
    """Разделы 2-го уровня, классифицируемые в РАЗНЫЕ типы, — кандидаты на разбиение (FR-ON-9)."""
    sections = [s for s in mdparse.parse_sections(path) if s.level == 2 and s.body]
    if len(sections) < SPLIT_MIN_SECTIONS:
        return []
    parts: list[SplitPart] = []
    for s in sections:
        text = f"# {s.title}\n\n{s.body}\n"
        hyps = classify.classify_file(path.with_name(re.sub(r"[^\w]+", "_", s.title) + ".md"), types, text)
        if hyps and hyps[0].confidence >= MIN_CONFIDENCE:
            parts.append(SplitPart(s.title, s.line, hyps[0].type, hyps[0].confidence))
    if len({p.type for p in parts}) < 2:
        return []
    return parts


# ------------------------------------------------------------------ вопросы автору


def questions_for(path: Path, spec: catalog.TypeSpec | None, pv: Preview | None, cm: ColumnMap | None) -> list[str]:
    out: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # имя в двух написаниях: «Ё/Е» и регистр
    names = {m.group(0) for m in re.finditer(r"\b[А-ЯЁ][а-яё]{2,}\b", text)}
    by_key: dict[str, set[str]] = {}
    for n in names:
        by_key.setdefault(n.replace("ё", "е").replace("Ё", "Е").lower(), set()).add(n)
    for k, variants in sorted(by_key.items()):
        if len(variants) > 1 and not all(v.lower() == v for v in variants):
            out.append(f"{path.name}: имя в разных написаниях: {', '.join(sorted(variants))}")
    # две даты в одной строке таблицы
    for i, ln in enumerate(lines, start=1):
        if ln.startswith("|") and len(re.findall(r"\b\d{1,2}\.\d{2}\.\d{4}\b", ln)) >= 2:
            out.append(f"{path.name}:{i}: две даты в одной строке — какая относится к событию?")
    if cm and cm.missing:
        out.append(f"{path.name}: в таблице нет обязательных колонок типа «{spec.name if spec else ''}»: {', '.join(cm.missing)}")
    if pv and pv.error:
        out.append(f"{path.name}: {pv.error}")
    if spec and spec.name == "стиль":
        for i, ln in enumerate(lines, start=1):
            if ln.startswith("|") and re.search(r"\d", ln) and not re.search(r"\d+(?:[.,]\d+)?\s*(слов|%|на\s*\d+|знак)", ln) \
                    and re.search(r"норм|порог|брак", text.lower()) and i > 2 and "---" not in ln:
                cells = [c.strip() for c in ln.strip("|").split("|")]
                if not any(re.fullmatch(r"[\d.,—-]+", c) for c in cells[2:]):
                    out.append(f"{path.name}:{i}: число не похоже на порог (нет единицы измерения)")
                    break
    return out[:20]


# ------------------------------------------------------------------ модельный слой (Архивариус)


def _model_input(path: Path, types: dict[str, catalog.TypeSpec]) -> str:
    sections = mdparse.parse_sections(path)
    toc = [f"{'#' * max(1, s.level)} {s.title}" for s in sections if s.level]
    heads = [f"{'#' * max(1, s.level)} {s.title}\n{s.body[:2000]}" for s in sections if s.level][:12]
    tables: list[str] = []
    try:
        for t in mdparse.parse_tables(path)[:6]:
            tables.append("| " + " | ".join(t.headers) + " |\n" + "\n".join(
                "| " + " | ".join(r.get(h, "") for h in t.headers) + " |" for r in t.rows[:2]))
    except mdparse.MarkupError:
        pass
    return "\n\n".join([f"# Файл: {path.name}", "## Оглавление", "\n".join(toc) or "(нет заголовков)",
                        "## Фрагменты разделов", "\n\n".join(heads) or path.read_text(encoding="utf-8", errors="replace")[:2000],
                        "## Таблицы (заголовки и две строки)", "\n\n".join(tables) or "(таблиц нет)"])


def model_layer(ws: Workspace, cfg, files: list[Path], types: dict[str, catalog.TypeSpec]) -> tuple[dict[str, dict], str]:
    """Ответы Архивариуса по файлам: {имя: {тип, уверенность, обоснование, …}}; кэш по хэшу входа (FR-ON-8, FR-ON-10).
    Возвращает (ответы, причина отсутствия модели — пусто, если модель работала)."""
    from importlib import resources

    from jinja2 import Environment

    from .. import adapters, llmjson

    cache_dir = onboarding_dir(ws) / MODEL_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    man = manifest_mod.load(ws.root)
    series = man.проект.имя if man else "серия"
    tpl_path = ws.root / "промпты" / "архивариус.md"
    template = tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else \
        resources.files("konveyer").joinpath("шаблоны/архивариус_система.md").read_text(encoding="utf-8")
    system = Environment().from_string(template).render(series=series, types=[
        {"name": t.name, "purpose": t.purpose} for t in sorted(types.values(), key=lambda t: t.name) if t.extractions])
    out: dict[str, dict] = {}
    reason = ""
    for path in files:
        user = _model_input(path, types)
        key = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()
        cached = cache_dir / f"{key}.json"
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
        else:
            try:
                raw = adapters.call_role(cfg, "архивариус", system, user, ws.logs, role="архивариус")
                data = llmjson.extract_json(raw, list)
            except adapters.ManualModeNeeded as e:
                reason = reason or f"модельный слой недоступен: {e}"
                continue
            except Exception as e:  # noqa: BLE001 — сеть/биллинг/разбор: машинный слой остаётся
                reason = reason or f"модельный слой не ответил: {type(e).__name__}: {str(e)[:120]}"
                continue
            cached.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("тип"):
                out[path.name] = item
    return out, reason


# ------------------------------------------------------------------ сборка предложения


def _proposed_name(spec: catalog.TypeSpec, source: str, volume: int | None, taken: set[str]) -> str:
    """Имя документа библиотеки по правилу проекта (FR-ON-16): имя по умолчанию типа; занято — NN_Название[_ТомN].md."""
    if spec.default_name and spec.default_name.endswith("/"):
        stem = re.sub(r"\.[^.]+(\.md)?$", "", source.split("__")[-1])
        return spec.default_name + re.sub(r"[^\w\- ]+", "_", stem).strip("_") + ".md"
    default = (spec.default_name or f"{spec.name}.md").replace("{том}", str(volume or 1))
    if default not in taken:
        return default
    prefix = re.match(r"^(\d+(?:\.\d+)?)_", default)
    num = prefix.group(1) + "_" if prefix else ""
    stem = re.sub(r"\.[^.]+(\.md)?$", "", source.split("__")[-1])
    stem = re.sub(r"[^\w\- ]+", "_", stem).replace(" ", "_").strip("_")
    vol = f"_Том{volume}" if spec.per_volume and volume else ""
    name = f"{num}{stem}{vol}.md"
    n = 2
    while name in taken:
        name = f"{num}{stem}{vol}_{n}.md"
        n += 1
    return name


def build(ws: Workspace, *, cfg=None, use_model: bool = False, only: list[str] | None = None) -> tuple[list[Proposal], str]:
    """Предложение по всему сырью со статусом «сырьё» (или по `only`). Возвращает (предложения, заметка о модели)."""
    root = ws.root
    types = catalog.load_types(root)
    man = manifest_mod.load(root)
    volume = man.проект.текущий_том if man else 1
    entries = [e for e in importer.load_index(ws) if e.извлечено_в and e.статус == "сырьё"
               and (only is None or e.файл in only)]
    previous = {p.файл: p for p in load(ws)}
    taken = {e.файл for e in (man.библиотека if man else [])}
    files = [(e, root / e.извлечено_в) for e in entries if (root / e.извлечено_в).exists()]
    model_answers: dict[str, dict] = {}
    model_note = ""
    if use_model and files:
        model_answers, model_note = model_layer(ws, cfg, [p for _, p in files], types)
    elif use_model:
        model_note = "сырья для модельного слоя нет"
    else:
        model_note = "модельный слой не запрашивался (машинный слой по сигнатурам)"
    proposals: list[Proposal] = []
    for entry, path in files:
        hyps = classify.classify_file(path, types)
        pr = Proposal(файл=entry.файл, извлечено_в=entry.извлечено_в,
                      гипотезы=[{"тип": h.type, "уверенность": h.confidence, "почему": "; ".join(h.reasons)} for h in hyps[:5]])
        if hyps and hyps[0].confidence >= MIN_CONFIDENCE:
            pr.тип, pr.уверенность = hyps[0].type, hyps[0].confidence
        answer = model_answers.get(path.name)
        if answer:
            pr.модель = answer
            mt = str(answer.get("тип", "")).strip()
            mc = float(answer.get("уверенность", 0) or 0)
            if mt in types and mc >= max(pr.уверенность, MIN_CONFIDENCE):
                pr.тип, pr.уверенность, pr.источник_решения = mt, round(mc, 3), "модель"
            elif mt == "сырьё" and mc > pr.уверенность:
                pr.тип, pr.уверенность, pr.источник_решения = "сырьё", round(mc, 3), "модель"
        # решение автора из прежнего предложения сохраняется (FR-ON-10: воспроизводимость + правки автора)
        prev = previous.get(entry.файл)
        if prev and prev.решение:
            pr.решение = prev.решение
            if prev.решение.startswith("тип:"):
                pr.тип, pr.источник_решения = prev.решение[4:].strip(), "автор"
        spec = types.get(pr.тип)
        if spec is not None:
            pr.том = volume if spec.per_volume else None
            overrides = dict((prev.колонки or {}).get("mapping", {})) if prev and prev.колонки else {}
            cm = column_map(path, spec, overrides)
            pr.колонки = asdict(cm) if cm else None
            shown = normalized_preview_path(ws, spec, path, cm.mapping if cm else None, f"сырьё/оригиналы/{entry.файл}")
            pv = preview(shown, spec, root, {}, volume)
            pr.предпросмотр = asdict(pv)
            pr.вопросы = questions_for(path, spec, pv, cm)
            pr.имя_документа = _proposed_name(spec, entry.файл, pr.том, taken)
        else:
            pr.вопросы = questions_for(path, None, None, None)
        pr.разбить = [asdict(p) for p in split_candidates(path, types)]
        proposals.append(pr)
    return proposals, model_note


# ------------------------------------------------------------------ сохранение


def save(ws: Workspace, proposals: list[Proposal], model_note: str = "") -> tuple[Path, Path]:
    d = onboarding_dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    data = {"версия": 1, "модель": model_note, "предложения": [asdict(p) for p in proposals]}
    pj = d / PROPOSAL_JSON
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pm = d / PROPOSAL_MD
    pm.write_text(render_md(proposals, model_note), encoding="utf-8")
    return pj, pm


def load(ws: Workspace) -> list[Proposal]:
    pj = onboarding_dir(ws) / PROPOSAL_JSON
    if not pj.exists():
        return []
    data = json.loads(pj.read_text(encoding="utf-8"))
    out = []
    for d in data.get("предложения", []):
        out.append(Proposal(**{k: v for k, v in d.items() if k in Proposal.__dataclass_fields__}))
    return out


def render_md(proposals: list[Proposal], model_note: str = "") -> str:
    lines = ["# Предложение онбординга", "",
             f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_ · {model_note or 'машинный слой'}", "",
             "Решение по каждому файлу — в `онбординг/предложение.json`, поле `решение`: `принять`, `тип:<имя типа>`, "
             "`сырьё` (доступен поиском, в реестры не идёт), `отклонить`, `разбить`. Затем `konveyer онбординг --применить`.", "",
             "| Файл | Тип | Уверенность | Записей | Документ канона | Решение |", "|---|---|---|---|---|---|"]
    for p in proposals:
        recs = (p.предпросмотр or {}).get("records", "—") if p.предпросмотр else "—"
        lines.append(f"| {p.файл} | {p.тип} | {p.уверенность:.0%} | {recs} | {p.имя_документа or '—'} | {p.решение or '—'} |")
    for p in proposals:
        lines += ["", f"## {p.файл} → `{p.тип}` ({p.уверенность:.0%}, {p.источник_решения})", ""]
        if p.гипотезы:
            lines.append("Гипотезы: " + "; ".join(f"{h['тип']} {h['уверенность']:.0%}" for h in p.гипотезы[:4]))
        pv = p.предпросмотр or {}
        if pv:
            lines += ["", f"**Что прочитает машина** ({pv.get('format') or '—'}; записей: {pv.get('records', 0)}):"]
            for row in pv.get("rows", []):
                lines.append("- " + "; ".join(f"{k}: {v}" for k, v in row.items() if v not in ("", None, [], {})))
            if pv.get("error"):
                lines.append(f"- ⚠ {pv['error']}")
            if pv.get("to_window"):
                lines.append("**В окно Писателя:** " + "; ".join(pv["to_window"]))
            if pv.get("internal"):
                lines.append("**Останется внутренним:** " + "; ".join(pv["internal"]))
        if p.колонки:
            cm = p.колонки
            lines += ["", "**Сопоставление колонок:** " + ", ".join(f"{k} ← «{v}»" for k, v in cm.get("mapping", {}).items())]
            if cm.get("missing"):
                lines.append(f"⚠ нет обязательных колонок: {', '.join(cm['missing'])}")
            if cm.get("generated"):
                lines.append(f"колонка «{cm['generated']}» будет заполнена порядковыми идентификаторами")
        if p.разбить:
            lines += ["", "**Можно разбить:** " + "; ".join(f"«{s['heading']}» → {s['type']}" for s in p.разбить)]
        if p.модель:
            lines += ["", f"**Архивариус:** {p.модель.get('обоснование', '')} "
                          f"(читать: {p.модель.get('что_машина_сможет_читать', '—')}; не хватает: {p.модель.get('чего_не_хватает', '—')})"]
        if p.вопросы:
            lines += ["", "**Вопросы автору:**"] + [f"- {q}" for q in p.вопросы]
    return "\n".join(lines) + "\n"


def set_decision(ws: Workspace, file: str, decision: str) -> Proposal:
    """Решение автора по файлу (FR-ON-12) — записывается в предложение.json."""
    if not (decision in DECISIONS or decision.startswith("тип:")):
        raise ValueError(f"решение «{decision}»: допустимо {', '.join(DECISIONS)} или тип:<имя>")
    proposals = load(ws)
    pr = next((p for p in proposals if p.файл == file), None)
    if pr is None:
        raise KeyError(f"файла «{file}» нет в предложении — выполните `konveyer онбординг`")
    pr.решение = decision
    if decision.startswith("тип:"):
        pr.тип, pr.источник_решения = decision[4:].strip(), "автор"
    data = json.loads((onboarding_dir(ws) / PROPOSAL_JSON).read_text(encoding="utf-8"))
    save(ws, proposals, data.get("модель", ""))
    return pr
