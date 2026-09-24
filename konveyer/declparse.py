"""Декларативный разбор документов канона по спецификации типа (FR-DT-4, Д-5).

Примитивы: `таблица` (pipe-таблица с сопоставлением колонок и синонимами), `широкая_таблица` (первая колонка —
ключ, остальные — динамические субъекты), `секции_с_ключами` (секции «## Глава N» со списком «- Ключ: …»),
`секции` (один документ = одна запись из тел секций), `строки` (регэксп с именованными группами по строкам),
`плагин` (функция парсера проекта или движка для экзотики). Каждый формат либо даёт записи, либо молчит
(`None`) — тогда пробуется следующий. Ошибка структуры — `MarkupError` с файлом и строкой (FR-EX-3).
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Callable

from . import mdparse
from .catalog import flag
from .mdparse import MarkupError, parse_number

EMPTY = {"", "—", "-", "–", "нет"}


class ParseContext:
    """Что парсер знает о вызове: том, имя файла, переопределения колонок из манифеста, парсеры проекта."""

    def __init__(self, *, volume: int = 1, overrides: dict | None = None, project_root: Path | None = None,
                 library: Path | None = None, params: dict | None = None):
        self.volume = volume
        self.overrides = overrides or {}
        self.project_root = project_root
        self.library = library
        self.params = params or {}


# ------------------------------------------------------------------ конвертеры значений

_PLACE_RE = re.compile(r"т\s*(\d+)(?:\s*гл\s*(\d+))?", re.IGNORECASE)


def conv_place(text: str) -> dict:
    m = _PLACE_RE.search(text)
    if not m:
        return {}
    place: dict = {"vol": int(m.group(1))}
    if m.group(2):
        place["ch"] = int(m.group(2))
    return place


def conv_places(text: str) -> list[dict]:
    return [p for p in (conv_place(x) for x in text.split(";")) if p]


def conv_years(text: str) -> dict:
    """«до 1999» → {"year": {"before": 1999}}; «1990–1999» → from/to; «все» → {"all": True}."""
    m = re.match(r"до\s+(\d{4})", text.strip())
    if m:
        return {"year": {"before": int(m.group(1))}}
    m = re.match(r"(\d{4})\s*[–-]\s*(\d{4})", text.strip())
    if m:
        return {"year": {"from": int(m.group(1)), "to": int(m.group(2))}}
    return {"all": True}


def conv_scope(text: str) -> dict:
    """Фокал или «все» → applies_to."""
    v = text.strip()
    return {"focal": v} if v and v.lower() not in {"все", "всем", "любой"} else {"all": True}


CONVERTERS: dict[str, Callable[[str], Any]] = {
    "строка": lambda s: s.strip(),
    "число": parse_number,
    "целое": lambda s: (int(parse_number(s)) if parse_number(s) is not None else None),
    "глава": lambda s: (int(parse_number(s)) if parse_number(s) is not None else None),
    "список": lambda s: [x.strip() for x in s.split(";") if x.strip()],
    "список_запятая": lambda s: [x.strip() for x in s.split(",") if x.strip()],
    "место": conv_place,
    "места": conv_places,
    "годы": conv_years,
    "фокал": conv_scope,
    "действие": lambda s: "запрет" if "запрет" in s.lower() else "флаг",
    "да_нет": lambda s: s.strip().lower() in {"да", "вкл", "✓", "yes", "true"},
    "пусто_как_none": lambda s: (None if s.strip() in EMPTY else s.strip()),
}


PLACEHOLDER = "⚠ заполнить"  # заглушка стартового комплекта: машина её не читает (значение пустое)


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(PLACEHOLDER)


def convert(value: str, kind: str | None) -> Any:
    if is_placeholder(value):
        value = ""
    if not kind or kind == "строка":
        return value.strip()
    fn = CONVERTERS.get(kind)
    if fn is None:
        raise ValueError(f"неизвестный тип ячейки «{kind}» (допустимо: {', '.join(CONVERTERS)})")
    return fn(value)


# ------------------------------------------------------------------ колонки

def _synonyms(name: str, spec: Any, overrides: dict) -> list[str]:
    """Заголовки, под которыми ищется колонка: сопоставление из манифеста, синонимы типа и само имя поля
    (каркас стартового комплекта озаглавливает колонки именами полей — тип обязан их узнавать)."""
    syn: list[str] = []
    if name in overrides and overrides[name]:
        ov = overrides[name]
        syn += [ov] if isinstance(ov, str) else list(ov)
    if isinstance(spec, dict):
        syn += list(spec.get("синонимы") or [])
    elif isinstance(spec, str):
        syn += [spec]
    syn.append(name)
    return list(dict.fromkeys(s for s in syn if s))


def match_columns(headers: list[str], columns: dict, overrides: dict) -> dict[str, str] | None:
    """{поле: заголовок таблицы} по синонимам (подстрока без регистра). None — обязательной колонки нет."""
    low = [h.lower() for h in headers]
    found: dict[str, str] = {}
    for field, spec in columns.items():
        required = flag(spec.get("обязательна")) if isinstance(spec, dict) else False
        hit = None
        for s in _synonyms(field, spec, overrides):
            s = s.lower()
            hit = next((h for h, hl in zip(headers, low, strict=True) if hl == s), None) or \
                  next((h for h, hl in zip(headers, low, strict=True) if s in hl), None)
            if hit:
                break
        if hit is None and required:
            return None
        if hit:
            found[field] = hit
    return found


def _record(row: dict[str, str], mapping: dict[str, str], columns: dict, fmt: dict, ctx: ParseContext,
            path: Path) -> dict:
    rec: dict[str, Any] = {}
    for field, spec in columns.items():
        kind = spec.get("тип") if isinstance(spec, dict) else None
        default = spec.get("по_умолчанию") if isinstance(spec, dict) else None
        header = mapping.get(field)
        raw = row.get(header, "") if header else ""
        if is_placeholder(raw):
            raw = ""
        if raw.strip() in EMPTY and kind not in ("строка", None):
            rec[field] = default if default is not None else (None if kind in ("число", "целое", "глава") else convert("", kind))
        elif raw.strip() in EMPTY and kind in ("строка", None):
            rec[field] = default if default is not None else ""
        else:
            rec[field] = convert(raw, kind)
    for k, v in (fmt.get("постоянные") or {}).items():
        rec[k] = _template(v, ctx, path, rec)
    return rec


def _template(value: Any, ctx: ParseContext, path: Path, rec: dict | None = None) -> Any:
    if isinstance(value, str) and "{" in value:
        try:
            return value.format(файл=path.name, том=ctx.volume, **(rec or {}))
        except (KeyError, IndexError):
            return value
    return value


def _apply_mapping(rec: dict, fmt: dict, ctx: ParseContext, path: Path) -> dict:
    """`запись: {поле_схемы: поле_или_шаблон}` — переложить разобранные поля в поля схемы."""
    mapping = fmt.get("запись")
    if not mapping:
        return rec
    out: dict = {}
    for target, source in mapping.items():
        if isinstance(source, dict):
            out[target] = _nested(source, rec, ctx, path)
        elif isinstance(source, str) and source in rec:
            out[target] = rec[source]
        else:
            out[target] = _template(source, ctx, path, rec)
    return out


def _nested(source: dict, rec: dict, ctx: ParseContext, path: Path) -> dict:
    """Поле схемы-словарь из нескольких колонок: `{"*": [поля-словари для слияния], ключ: поле}`; пустые значения
    (None, «», []) в словарь не попадают."""
    out: dict = {}
    for f in source.get("*") or []:
        v = rec.get(f)
        if isinstance(v, dict):
            out.update(v)
    for k, src in source.items():
        if k == "*":
            continue
        v = rec[src] if isinstance(src, str) and src in rec else _template(src, ctx, path, rec)
        if v not in (None, "", []):
            out[k] = v
    return out


# ------------------------------------------------------------------ примитивы


def _tables(path: Path, fmt: dict) -> list[mdparse.Table]:
    section = fmt.get("секция")
    if section:
        sec = mdparse.find_section(mdparse.parse_sections(path), section)
        if sec is None:
            return []
        return mdparse.parse_tables(path, sec.body, start_line=sec.line + 1)
    return mdparse.parse_tables(path)


def fmt_table(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    columns: dict = fmt.get("колонки") or {}
    min_cols = int(fmt.get("минимум_колонок", 0) or 0)
    recognize = fmt.get("опознание")
    for table in _tables(path, fmt):
        if len(table.headers) < min_cols:
            continue
        if recognize and not re.search(recognize, " ".join(table.headers), re.IGNORECASE):
            continue
        mapping = match_columns(table.headers, columns, ctx.overrides)
        if mapping is None:
            continue
        records: list[dict] = []
        for i, row in enumerate(table.rows, start=1):
            rec = _record(row, mapping, columns, fmt, ctx, path)
            rec["_строка"] = table.line + 1 + i
            key_field = next((f for f, s in columns.items() if isinstance(s, dict) and s.get("роль") == "ключ"), None)
            if key_field and not rec.get(key_field):
                continue
            records.append(_apply_mapping(rec, fmt, ctx, path))
        return records
    return None


def fmt_wide_table(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Широкая таблица: первая колонка-ключ («Факт»), номер («#»), остальные — субъекты (FR-DT-1)."""
    key_syn = [s.lower() for s in _synonyms("ключ", fmt.get("ключ") or {"синонимы": ["факт", "событие"]}, ctx.overrides)]
    num_syn = [s.lower() for s in _synonyms("номер", fmt.get("номер") or {"синонимы": ["#", "№"]}, ctx.overrides)]
    min_cols = int(fmt.get("минимум_колонок", 4) or 4)
    cell_rules = knowledge_rules(fmt.get("ячейка_знания") or {})
    pm = cell_rules["partial"]
    exclude = {s.lower() for s in (fmt.get("исключить_колонки") or [])}
    id_prefix = str(fmt.get("префикс_id", "М-"))
    pseudo = set(fmt.get("псевдосубъекты") or [])
    for table in _tables(path, fmt):
        if len(table.headers) < min_cols:
            continue
        key_col = next((h for h in table.headers if h.lower() in key_syn), None)
        if key_col is None:
            continue
        num_col = next((h for h in table.headers if h.lower() in num_syn), None)
        subjects = [h for h in table.headers if h not in (key_col, num_col) and h.lower() not in exclude]
        facts: list[dict] = []
        for i, row in enumerate(table.rows, start=1):
            num = parse_number(row.get(num_col, "")) if num_col else None
            num = int(num) if num is not None else len({f["fact_id"] for f in facts}) + 1
            text = row.get(key_col, "")
            for subj in subjects:
                raw = row.get(subj, "").strip()
                if not raw:
                    continue
                partial = raw.startswith(pm) and raw.endswith(pm) and not raw.startswith(pm * 2)
                clean = raw.strip(pm).strip()
                from_ch, note = _knowledge_cell(clean, cell_rules, subj in pseudo)
                source = clean.split("/", 1)[1].strip() if "/" in clean else ""
                facts.append({
                    "fact_id": f"{id_prefix}{num:02d}", "fact": text, "subject": subj, "from_chapter": from_ch,
                    "source": source, "note": ("частично/неверно: " + clean) if partial else note,
                    "_строка": table.line + 1 + i,
                })
        if facts:
            return facts
    return None


def knowledge_rules(rules: dict) -> dict:
    """Правила `ячейка_знания` типа → {always: [префиксы → глава 0], empty: {маркеры «нет знания»}, chapter: регэксп
    номера главы, partial: маркер частичного знания}. Образец главы задаётся строкой вида «гл.N[ / источник]»: текст
    до N — префикс (точка и пробел после него необязательны), N — номер; «*курсив*» — маркер частичного знания."""
    out: dict = {"always": [], "empty": set(EMPTY), "chapter": re.compile(r"[Гг]л\.?\s*(\d+)"), "partial": "*"}
    for pat, val in (rules or {}).items():
        pat = str(pat)
        if val in (0, "0"):
            out["always"] += [w.strip().lower() for w in pat.split("|") if w.strip()]
        elif val is None or str(val).lower() in ("null", "none", "нет"):
            out["empty"] |= {w.strip() for w in pat.split("|") if w.strip()}
        elif str(val) == "N" and "N" in pat:
            prefix = re.split(r"\[.*?\]", pat.split("N", 1)[0])[0].strip().rstrip(".")
            out["chapter"] = re.compile(re.escape(prefix) + r"\.?\s*(\d+)", re.IGNORECASE) if prefix else re.compile(r"(\d+)")
        elif str(val) == "частичное_знание" and pat:
            out["partial"] = pat[0]
    return out


def _knowledge_cell(clean: str, rules: dict, pseudo: bool) -> tuple[int | None, str]:
    """Ячейка знания по правилам типа (`knowledge_rules`): «всегда|пролог» → 0, «—» → None, «гл.N …» → N;
    читатель-псевдосубъект — «расчётная разгадка ≈гл.N» → N, «улики с гл.N» без разгадки → None (знание не показано)."""
    kr = rules if "chapter" in rules else knowledge_rules(rules)
    low = clean.lower()
    if low in kr["empty"] or clean in kr["empty"]:
        return None, ""
    if any(low.startswith(w) for w in kr["always"]):
        return 0, ""
    if pseudo:
        m = re.search(r"разгадк[а-я]*", clean, re.IGNORECASE)
        if m:
            after = kr["chapter"].search(clean[m.end():])
            if after:
                return int(after.group(1)), ""
        if re.search(r"улик", clean, re.IGNORECASE):
            return None, clean
    m = kr["chapter"].search(clean)
    if m:
        return int(m.group(1)), ""
    return None, clean


def fmt_keyed_sections(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Секции «## Глава N — …» со списком «- Ключ: значение» / «- Ключ:» + подпункты."""
    head = re.compile(fmt.get("заголовок") or r"Глава\s+(\d+)")
    keys: dict = fmt.get("ключи") or {}
    records: list[dict] = []
    matched = False
    for sec in mdparse.parse_sections(path):
        m = head.match(sec.title)
        if not m:
            continue
        matched = True
        rec: dict[str, Any] = {"номер": int(m.group(1)) if m.groups() and m.group(1) and m.group(1).isdigit() else m.group(0),
                               "заголовок": sec.title, "_строка": sec.line}
        for field, spec in keys.items():
            spec = spec if isinstance(spec, dict) else {"ключ": spec}
            label = spec.get("ключ", field)
            labels = [label] + list(ctx.overrides.get(field, []) if isinstance(ctx.overrides.get(field), list) else
                                    ([ctx.overrides[field]] if ctx.overrides.get(field) else []))
            kind = spec.get("тип", "строка")
            value: Any = None
            for lb in labels:
                if kind == "список":
                    items = [i for i in mdparse.parse_list_items(sec.body, lb) if not is_placeholder(i)]
                    if items:
                        value = items
                        break
                else:
                    raw = mdparse.parse_kv(sec.body, lb)
                    if raw:
                        value = convert(raw, kind)
                        break
            if value is None:
                value = [] if kind == "список" else (None if kind in ("число", "целое", "глава") else "")
            rec[field] = value
        if keys and all(rec[f] in (None, "", []) for f in keys):
            continue  # секция-заглушка стартового комплекта (все ключи «⚠ заполнить») — записи нет
        if fmt.get("тело"):
            rec[fmt["тело"]] = sec.body
        for k, v in (fmt.get("постоянные") or {}).items():
            rec[k] = _template(v, ctx, path, rec)
        records.append(_apply_mapping(rec, fmt, ctx, path))
    return records if matched else None


WHOLE_DOCUMENT = "*"  # значение `секция:` поля — весь текст документа (чек-листы, сплошной текст)


def _field_section(sections: list[mdparse.Section], pattern: str) -> mdparse.Section | None:
    """Секция поля: сначала среди секций глубже заголовка документа (заголовок 1-го уровня — имя документа, его тело
    обычно пусто), и лишь если таких нет — среди всех."""
    inner = [s for s in sections if s.level >= 2]
    return mdparse.find_section(inner, pattern) or mdparse.find_section(sections, pattern)


def fmt_sections(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Один документ — одна запись: поля из тел секций по образцу заголовка (`"*"` — весь документ); имя — заголовок
    1-го уровня."""
    sections = mdparse.parse_sections(path)
    if not sections or all(s.level == 0 for s in sections):
        return None
    marker = fmt.get("опознание")
    if marker and not any(re.search(marker, s.title) for s in sections):
        return None
    rec: dict[str, Any] = {"_строка": 1}
    name_from = fmt.get("имя", "заголовок_1")
    if name_from == "заголовок_1":
        rec["name"] = next((s.title for s in sections if s.level == 1), path.stem)
    elif name_from == "имя_файла":
        rec["name"] = path.stem
    for field, spec in (fmt.get("поля") or {}).items():
        spec = spec if isinstance(spec, dict) else {"секция": spec}
        pat = spec.get("секция", field)
        if pat == WHOLE_DOCUMENT:
            rec[field] = path.read_text(encoding="utf-8").strip()
            rec.setdefault("_секции", {})[field] = 1
            continue
        pats = [pat] + ([ctx.overrides[field]] if ctx.overrides.get(field) else [])
        sec = next((s for p in pats for s in [_field_section(sections, p)] if s), None)
        if spec.get("тип") == "таблица_пар" and sec is not None:
            pairs: dict[str, str] = {}
            for t in mdparse.parse_tables(path, sec.body, start_line=sec.line + 1):
                if len(t.headers) >= 2:
                    for row in t.rows:
                        k, v = row[t.headers[0]], row[t.headers[1]]
                        if k:
                            pairs[k] = v
            rec[field] = pairs
        elif spec.get("тип") == "список" and sec is not None:
            rec[field] = [ln.strip()[2:].strip() for ln in sec.body.splitlines() if ln.strip().startswith("- ")]
        else:
            rec[field] = sec.body if sec else ({} if spec.get("тип") == "таблица_пар" else "")
        if sec is not None:
            rec.setdefault("_секции", {})[field] = sec.line
    for k, v in (fmt.get("постоянные") or {}).items():
        rec[k] = _template(v, ctx, path, rec)
    return [_apply_mapping(rec, fmt, ctx, path)]


def fmt_lines(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    rx = re.compile(fmt["регэксп"])
    kinds: dict = fmt.get("типы") or {}
    records: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = rx.match(line.strip())
        if not m:
            continue
        rec: dict[str, Any] = {k: convert(v or "", kinds.get(k)) for k, v in m.groupdict().items()}
        rec["_строка"] = i
        for k, v in (fmt.get("постоянные") or {}).items():
            rec[k] = _template(v, ctx, path, rec)
        records.append(_apply_mapping(rec, fmt, ctx, path))
    return records or None


# ------------------------------------------------------------------ плагины проекта (FR-DT-4)

_PLUGIN_CACHE: dict[str, Any] = {}


def _load_module_from(path: Path, name: str):
    key = f"{path}:{path.stat().st_mtime_ns}"
    if key in _PLUGIN_CACHE:
        return _PLUGIN_CACHE[key]
    spec = importlib.util.spec_from_file_location(f"konveyer_plugin_{name}", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[spec.name] = module  # type: ignore[union-attr]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    _PLUGIN_CACHE[key] = module
    return module


def resolve_plugin(func: str, ctx: ParseContext) -> Callable | None:
    """«модуль:функция» — модуль ищется в `типы/парсеры/` проекта, затем как импортируемый модуль движка
    (`konveyer.parsers.имя`). Нет плагина — формат молчит (None), а не падает: деградация П-5."""
    if ":" not in func:
        return None
    mod_name, fn_name = func.split(":", 1)
    module = None
    if ctx.project_root is not None:
        cand = ctx.project_root / "типы" / "парсеры" / f"{mod_name}.py"
        if cand.exists():
            module = _load_module_from(cand, mod_name)
    if module is None:
        for dotted in (mod_name, f"konveyer.parsers.{mod_name}"):
            try:
                module = importlib.import_module(dotted)
                break
            except ImportError:
                continue
    if module is None:
        return None
    return getattr(module, fn_name, None)


def fmt_plugin(path: Path, fmt: dict, ctx: ParseContext) -> Any:
    fn = resolve_plugin(str(fmt.get("функция", "")), ctx)
    if fn is None:
        return None
    kwargs = {}
    for name in ("volume", "ctx", "known_names", "library"):
        if name in getattr(fn, "__code__", None).co_varnames[: fn.__code__.co_argcount] if hasattr(fn, "__code__") else ():
            kwargs[name] = {"volume": ctx.volume, "ctx": ctx, "known_names": ctx.params.get("known_names", set()),
                            "library": ctx.library}[name]
    return fn(path, **kwargs)


PRIMITIVES: dict[str, Callable[[Path, dict, ParseContext], Any]] = {
    "таблица": fmt_table,
    "широкая_таблица": fmt_wide_table,
    "секции_с_ключами": fmt_keyed_sections,
    "секции": fmt_sections,
    "строки": fmt_lines,
    "плагин": fmt_plugin,
}


def parse_document(path: Path, formats: list[dict], ctx: ParseContext) -> tuple[Any, dict | None]:
    """Первый формат, давший результат: (записи, формат). Ни один не подошёл — (None, None)."""
    for fmt in formats:
        kind = fmt.get("вид")
        fn = PRIMITIVES.get(kind or "")
        if fn is None:
            raise ValueError(f"{path.name}: неизвестный вид формата «{kind}» (допустимо: {', '.join(PRIMITIVES)})")
        result = fn(path, fmt, ctx)
        if result is not None:
            return result, fmt
    return None, None


def describe_expected(formats: list[dict]) -> str:
    """Человекочитаемое «что ожидалось» для ошибки разбора (FR-EX-3)."""
    parts = []
    for fmt in formats:
        kind = fmt.get("вид")
        if kind == "таблица":
            cols = [f"«{'/'.join(v.get('синонимы', [k]) if isinstance(v, dict) else [k])}»" for k, v in (fmt.get("колонки") or {}).items()
                    if isinstance(v, dict) and v.get("обязательна")]
            parts.append("таблица с колонками " + ", ".join(cols) + (f" в секции «{fmt['секция']}»" if fmt.get("секция") else ""))
        elif kind == "широкая_таблица":
            parts.append("широкая таблица: первая колонка — ключ, остальные — субъекты")
        elif kind == "секции_с_ключами":
            parts.append(f"секции «{fmt.get('заголовок')}» со списком «- Ключ: …»")
        elif kind == "секции":
            names = [v if isinstance(v, str) else v.get("секция", k) for k, v in (fmt.get("поля") or {}).items()]
            parts.append("секции " + ", ".join("«весь документ»" if n == WHOLE_DOCUMENT else f"«{n}»" for n in names))
        elif kind == "строки":
            parts.append(f"строки по образцу {fmt.get('регэксп')}")
        elif kind == "плагин":
            parts.append(f"плагин-парсер {fmt.get('функция')}")
    return " либо ".join(parts) or "—"


def markup_error(path: Path, formats: list[dict], line: int = 1) -> MarkupError:
    return MarkupError(path, line, f"структура не распознана; ожидалось: {describe_expected(formats)}. "
                                   "Поправьте документ или сопоставьте колонки/секции в проект.yaml (блок «библиотека»)")
