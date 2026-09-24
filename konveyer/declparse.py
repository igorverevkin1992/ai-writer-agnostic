"""Декларативный разбор документов канона по спецификации типа (FR-DT-4, Д-5).

Примитивы: `таблица` (pipe-таблица с сопоставлением колонок и синонимами), `широкая_таблица` (первая колонка —
ключ, остальные — динамические субъекты), `секции_с_ключами` (секции «## Глава N» со списком «- Ключ: …»),
`секции` (один документ = одна запись из тел секций), `строки` (регэксп с именованными группами по строкам),
`плагин` (функция парсера проекта или движка для экзотики). Каждый формат либо даёт записи, либо молчит
(`None`) — тогда пробуется следующий. Ошибка структуры — `MarkupError` с файлом и строкой (FR-EX-3).

Контракт плагина (`вид: плагин`, `функция: "модуль:функция"`): модуль — файл `типы/парсеры/<модуль>.py` проекта
или модуль движка `konveyer.<…>`; функция принимает путь документа и, по имени параметра, `volume` (том),
`ctx` (ParseContext), `known_names` (известные имена), `library` (папка библиотеки); возвращает список записей
(словарей или моделей схемы), словарь `{ключ: модель}` либо `None` («формат не мой»).
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any, Callable

from . import mdparse, names
from .catalog import flag
from .mdparse import MarkupError, parse_number

EMPTY = {"", "—", "-", "–", "нет"}
ENGINE_PLUGIN_PREFIX = "konveyer."


class ParseContext:
    """Что парсер знает о вызове: том, имя файла, переопределения колонок/секций из манифеста, парсеры проекта,
    имя текущего извлечения (для переопределения `секции: {имя_извлечения: образец}`)."""

    def __init__(self, *, volume: int = 1, overrides: dict | None = None, project_root: Path | None = None,
                 library: Path | None = None, params: dict | None = None, extraction: str = "",
                 sections: dict | None = None):
        self.volume = volume
        self.overrides = overrides or {}
        self.sections = sections if sections is not None else self.overrides
        self.project_root = project_root
        self.library = library
        self.params = params or {}
        self.extraction = extraction


# ------------------------------------------------------------------ конвертеры значений

# «т1 гл5», «т.1 гл.5», «том 1, гл. 5», «Т.2», «т.1, глава 3»
_PLACE_RE = re.compile(r"т(?:ом)?\.?\s*(\d+)(?:[\s,;]*гл(?:ав[аы])?\.?\s*(\d+))?", re.IGNORECASE)
_VOL_ONLY_RE = re.compile(r"(?<![а-яё\w])т(?:ом[а-я]*)?\.?\s*\d+", re.IGNORECASE)


def conv_place(text: str) -> dict:
    """«т.1 гл.5» → {vol: 1, ch: 5}; пусто/«—» → {}; непонятная запись — ValueError (ошибка с файлом и строкой)."""
    if text.strip() in EMPTY:
        return {}
    m = _PLACE_RE.search(text)
    if not m:
        raise ValueError(f"место «{text.strip()}» не разобрано; ожидается «т.N гл.M» (том и глава)")
    place: dict = {"vol": int(m.group(1))}
    if m.group(2):
        place["ch"] = int(m.group(2))
    return place


def conv_places(text: str) -> list[dict]:
    return [p for p in (conv_place(x) for x in text.split(";")) if p]


def conv_years(text: str) -> dict:
    """«до 1999» → {"year": {"before": 1999}}; «1990–1999» → from/to; «после 1999» / «с 2000» → from;
    «1990-е» → десятилетие; «все»/пусто → {"all": True}. Иное непустое значение — ValueError."""
    v = text.strip()
    low = v.lower()
    if low in EMPTY or low in {"все", "всегда", "любые", "любой"}:
        return {"all": True}
    m = re.fullmatch(r"до\s+(\d{4})(?:\s*г\.?)?", low)
    if m:
        return {"year": {"before": int(m.group(1))}}
    m = re.fullmatch(r"(\d{4})\s*[–-]\s*(\d{4})(?:\s*гг?\.?)?", low)
    if m:
        return {"year": {"from": int(m.group(1)), "to": int(m.group(2))}}
    m = re.fullmatch(r"после\s+(\d{4})(?:\s*г\.?)?", low)
    if m:
        return {"year": {"from": int(m.group(1)) + 1}}
    m = re.fullmatch(r"(?:с|от)\s+(\d{4})(?:\s*г\.?)?", low)
    if m:
        return {"year": {"from": int(m.group(1))}}
    m = re.fullmatch(r"(\d{3})0\s*-?\s*[ех]", low)
    if m:
        start = int(m.group(1)) * 10
        return {"year": {"from": start, "to": start + 9}}
    m = re.fullmatch(r"(\d{4})(?:\s*г\.?)?", low)
    if m:
        return {"year": {"from": int(m.group(1)), "to": int(m.group(1))}}
    raise ValueError(f"годы «{v}» не разобраны; ожидается «до ГГГГ», «после ГГГГ», «с ГГГГ», «ГГГГ–ГГГГ», "
                     "«ГГГ0-е» или «все»")


def conv_scope(text: str) -> dict:
    """Фокал или «все» → applies_to."""
    v = text.strip()
    return {"focal": v} if v and v.lower() not in {"все", "всем", "любой"} else {"all": True}


def conv_number(text: str) -> float | None:
    """Число; непустое нечисловое значение («много», «один») — ValueError (FR-EX-3)."""
    if text.strip() in EMPTY:
        return None
    n = parse_number(text)
    if n is None:
        raise ValueError(f"«{text.strip()}» — не число")
    return n


def conv_int(text: str) -> int | None:
    n = conv_number(text)
    return int(n) if n is not None else None


def conv_chapter(text: str) -> int | None:
    """Номер главы: «гл. 5», «5», «т.2 гл.3» → 3; одна ссылка на том («т.2») — главы нет (None)."""
    if text.strip() in EMPTY:
        return None
    m = names.CH_RE.search(text)
    if m:
        return int(m.group(1))
    if _VOL_ONLY_RE.search(text):
        return None
    return conv_int(text)


CONVERTERS: dict[str, Callable[[str], Any]] = {
    "строка": lambda s: s.strip(),
    "число": conv_number,
    "целое": conv_int,
    "глава": conv_chapter,
    "список": lambda s: mdparse.split_list(s, ";"),
    "список_запятая": lambda s: mdparse.split_list(s, ";,"),
    "место": conv_place,
    "места": conv_places,
    "годы": conv_years,
    "фокал": conv_scope,
    "действие": lambda s: "запрет" if "запрет" in s.lower() else "флаг",
    "да_нет": lambda s: s.strip().lower() in {"да", "вкл", "✓", "yes", "true"},
    "пусто_как_none": lambda s: (None if s.strip() in EMPTY else s.strip()),
}
NUMERIC_KINDS = ("число", "целое", "глава")


PLACEHOLDER = "⚠ заполнить"  # заглушка стартового комплекта: в выгрузки не попадает (значение пустое, запись из одних заглушек — нет)


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(PLACEHOLDER)


def is_blank(value: Any) -> bool:
    """Пустая ячейка или заглушка стартового комплекта («—» — осознанное значение, не пустота)."""
    return isinstance(value, str) and (not value.strip() or is_placeholder(value))


_ITEM_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def strip_placeholders(text: str) -> str:
    """Текст без строк-заглушек каркаса: пункт «- ⚠ заполнить» или абзац «⚠ заполнить» в выгрузку не попадает
    (§1.3: машина не сочиняет и не хранит факты-заглушки)."""
    kept = [ln for ln in text.splitlines() if not is_placeholder(_ITEM_MARK_RE.sub("", ln))]
    return "\n".join(kept).strip()


def convert(value: str, kind: str | None) -> Any:
    if is_placeholder(value):
        value = ""
    if not kind or kind == "строка":
        return value.strip()
    fn = CONVERTERS.get(kind)
    if fn is None:
        raise ValueError(f"неизвестный тип ячейки «{kind}» (допустимо: {', '.join(CONVERTERS)})")
    return fn(value)


def _convert_at(value: str, kind: str | None, path: Path, line: int, field: str) -> Any:
    """Конвертация с адресом: ошибка значения → MarkupError с файлом, строкой и полем (FR-EX-3)."""
    try:
        return convert(value, kind)
    except ValueError as e:
        raise MarkupError(path, line, f"поле «{field}»: {e}") from None


# ------------------------------------------------------------------ колонки

def _override_list(value: Any) -> list[str]:
    if not value:
        return []
    return [str(value)] if isinstance(value, str) else [str(v) for v in value]


def _spec_synonyms(name: str, spec: Any) -> list[str]:
    """Синонимы колонки по порядку предпочтения; каноническое имя поля — последний синоним (заголовки каркаса
    стартового комплекта — это имена полей каталога)."""
    if isinstance(spec, dict):
        syn = [str(s) for s in (spec.get("синонимы") or [])]
    elif isinstance(spec, str):
        syn = [spec]
    else:
        syn = []
    return list(dict.fromkeys([*syn, name]))


def _synonyms(name: str, spec: Any, overrides: dict) -> list[str]:
    return _override_list(overrides.get(name)) + _spec_synonyms(name, spec)


def _hit_level(header: str, synonym: str) -> int | None:
    """Строгость совпадения заголовка с синонимом: 0 — точное, 1 — с начала слова («том» ↔ «Том 2»),
    2 — подстрока внутри слова (только для синонимов длиннее трёх букв: «том» не находит «Автомат»)."""
    if header == synonym:
        return 0
    if re.search(rf"(?<![\w]){re.escape(synonym)}", header):
        return 1
    if len(synonym) > 3 and synonym in header:
        return 2
    return None


def _header_hits(headers: list[str], synonyms: list[str]) -> list[str]:
    """Заголовки, подходящие под синонимы: сначала точные совпадения, затем с начала слова, затем подстрока;
    на одном уровне строгости синонимы — по порядку предпочтения (первый подошедший решает). Возвращает
    заголовки, подошедшие под выбранный синоним (в порядке таблицы); их больше одного — колонка неоднозначна."""
    folded = [(h, mdparse.fold(h)) for h in headers]
    syns = [mdparse.fold(s) for s in synonyms if s.strip()]
    for level in (0, 1, 2):
        for s in syns:
            hits = [h for h, hf in folded if _hit_level(hf, s) == level]
            if hits:
                return hits
    return []


def match_columns(headers: list[str], columns: dict, overrides: dict) -> dict[str, str] | None:
    """{поле: заголовок таблицы} по синонимам без регистра и «ё/е»: сначала точное совпадение, затем совпадение
    с начала слова («том» ↔ «Том 2», но не «Автомат»), затем подстрока; переопределения манифеста — прежде
    синонимов типа. None — обязательной колонки нет. Два разных заголовка на одном уровне — ValueError
    «неоднозначная колонка» (уточняется в проект.yaml)."""
    hits: dict[str, list[str]] = {}
    for field, spec in columns.items():
        required = flag(spec.get("обязательна")) if isinstance(spec, dict) else False
        hits[field] = _header_hits(headers, _override_list(overrides.get(field))) or \
            _header_hits(headers, _spec_synonyms(field, spec))
        if not hits[field] and required:
            return None
    # заголовок, однозначно занятый одним полем, не претендует на другие («получает» / «чего НЕ получает»)
    found: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for field, hs in hits.items():
            if field in found or len(hs) != 1:
                continue
            found[field] = hs[0]
            changed = True
            for other, ohs in hits.items():
                if other != field and len(ohs) > 1 and hs[0] in ohs:
                    ohs.remove(hs[0])
    ambiguous = [f"«{field}»: подходят {', '.join(f'«{h}»' for h in hs)}" for field, hs in hits.items()
                 if field not in found and len(hs) > 1]
    if ambiguous:
        raise ValueError("неоднозначная колонка — " + "; ".join(ambiguous)
                         + ". Уточните сопоставление в проект.yaml (блок «библиотека», колонки)")
    return found


def _record(row: dict[str, str], mapping: dict[str, str], columns: dict, fmt: dict, ctx: ParseContext,
            path: Path, line: int) -> dict | None:
    """Запись из строки таблицы. None — строка-заглушка стартового комплекта: обязательная колонка (кроме ключа)
    ещё «⚠ заполнить» или все обязательные колонки пусты."""
    rec: dict[str, Any] = {}
    blank_required = True
    has_required = False
    placeholder_required = False
    for field, spec in columns.items():
        kind = spec.get("тип") if isinstance(spec, dict) else None
        default = spec.get("по_умолчанию") if isinstance(spec, dict) else None
        required = flag(spec.get("обязательна")) if isinstance(spec, dict) else False
        is_key = isinstance(spec, dict) and spec.get("роль") == "ключ"
        header = mapping.get(field)
        raw = row.get(header, "") if header else ""
        if required and not is_key:
            has_required = True
            placeholder_required = placeholder_required or is_placeholder(raw)
            if not is_blank(raw):
                blank_required = False
        if is_placeholder(raw):
            raw = ""
        if raw.strip() in EMPTY and kind not in ("строка", None):
            rec[field] = default if default is not None else (None if kind in NUMERIC_KINDS else convert("", kind))
        elif raw.strip() in EMPTY and kind in ("строка", None):
            rec[field] = default if default is not None else ""
        else:
            rec[field] = _convert_at(raw, kind, path, line, field)
    if has_required and (blank_required or placeholder_required):
        return None
    for k, v in (fmt.get("постоянные") or {}).items():
        rec[k] = _template(v, ctx, path, rec)
    return rec


def _template(value: Any, ctx: ParseContext, path: Path, rec: dict | None = None) -> Any:
    """`"{файл} (нормы)"`, `"{том}"`, `"{поле}"` — подстановка из контекста и записи; поле записи с именем
    «файл»/«том» не конфликтует с контекстом (контекст в приоритете)."""
    if isinstance(value, str) and "{" in value:
        try:
            return value.format(**{**(rec or {}), "файл": path.name, "том": ctx.volume})
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return value
    return value


def _apply_mapping(rec: dict, fmt: dict, ctx: ParseContext, path: Path) -> dict:
    """`запись: {поле_схемы: поле_или_шаблон}` — переложить разобранные поля в поля схемы."""
    mapping = fmt.get("запись")
    if not mapping:
        return rec
    out: dict = {k: v for k, v in rec.items() if k.startswith("_")}  # «_строка», «_секции» — адрес записи в документе
    for target, source in mapping.items():
        if isinstance(source, dict):
            out[target] = _nested(source, rec, ctx, path)
        elif isinstance(source, str) and source in rec:
            out[target] = rec[source]
        elif isinstance(source, str) and source.startswith("_"):
            continue  # служебный ключ не заполнен (например, ни одной секции) — умолчание схемы
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


def _section_patterns(base: Any, ctx: ParseContext, key: str, path: Path) -> list[str]:
    """Образцы секции: из спецификации типа плюс переопределения манифеста (`секции: {ключ: образец|[…]}`)."""
    pats = _override_list(base)
    for ov_key in (key, "секция"):
        ov = ctx.sections.get(ov_key, ctx.overrides.get(ov_key))
        if ov is None or not ov_key:
            continue
        if not isinstance(ov, (str, list)) or (isinstance(ov, list) and not all(isinstance(x, str) for x in ov)):
            raise MarkupError(path, 1, f"проект.yaml: переопределение секции «{ov_key}» должно быть строкой или "
                                       f"списком строк, а не {type(ov).__name__}")
        pats = _override_list(ov) + pats
    return list(dict.fromkeys(pats))


def _tables(path: Path, fmt: dict, ctx: ParseContext) -> list[mdparse.Table]:
    """Таблицы документа; при `секция:` — из всех секций, подходящих под образец (типа или манифеста)."""
    pats = _section_patterns(fmt.get("секция"), ctx, ctx.extraction, path)
    if not pats:
        return mdparse.parse_tables(path)
    sections = mdparse.parse_sections(path)
    seen: list[mdparse.Section] = []
    for p in pats:
        for sec in mdparse.find_sections(sections, p, min_level=1):
            if sec not in seen:
                seen.append(sec)
    tables: list[mdparse.Table] = []
    for sec in sorted(seen, key=lambda s: s.line):
        tables.extend(mdparse.parse_tables(path, sec.raw_body, start_line=sec.line + 1))
    return tables


def fmt_table(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Записи из всех таблиц документа (или его секций), у которых сошлись колонки; ни одной — None."""
    columns: dict = fmt.get("колонки") or {}
    min_cols = int(fmt.get("минимум_колонок", 0) or 0)
    recognize = fmt.get("опознание")
    key_field = next((f for f, s in columns.items() if isinstance(s, dict) and s.get("роль") == "ключ"), None)
    records: list[dict] = []
    matched = False
    for table in _tables(path, fmt, ctx):
        if len(table.headers) < min_cols:
            continue
        if recognize and not re.search(recognize, " ".join(table.headers), re.IGNORECASE):
            continue
        try:
            mapping = match_columns(table.headers, columns, ctx.overrides)
        except ValueError as e:
            raise MarkupError(path, table.line, str(e)) from None
        if mapping is None:
            continue
        matched = True
        for i, row in enumerate(table.rows, start=1):
            line = table.line + 1 + i
            rec = _record(row, mapping, columns, fmt, ctx, path, line)
            if rec is None:
                continue
            rec["_строка"] = line
            if key_field and not rec.get(key_field):
                continue
            records.append(_apply_mapping(rec, fmt, ctx, path))
    return records if matched else None


# ---- широкая таблица: правила ячейки знания

DEFAULT_CELL_RULES: dict[str, Any] = {"всегда|с начала|пролог": 0, "—|-|нет": None, "гл.N[ / источник]": "N"}
_REGEX_MARKERS = ("\\", "(", "^", "$", "?", "+", "{")


def _compile_cell_rule(pattern: str, anchored: bool) -> re.Pattern:
    """Образец правила ячейки: явный регэксп (есть `\\`, `(`, `^`…) — как есть (поиск по ячейке); иначе простая
    запись: `|` — варианты, `.` необязательна, пробелы свободны, `N` — число, хвост в `[…]` — пояснение.
    `anchored` — образец должен стоять в начале ячейки («всегда (с пролога)», «— (см. факт 3)»); иначе ищется
    с начала любого слова («с гл. 5» подходит под «гл.N»)."""
    if any(ch in pattern for ch in _REGEX_MARKERS):
        return re.compile(pattern, re.IGNORECASE)
    plain = re.sub(r"\[.*\]\s*$", "", pattern)
    alts = []
    for alt in plain.split("|"):
        alt = alt.strip()
        if not alt:
            continue
        parts = []
        for ch in alt:
            if ch == "N":
                parts.append(r"\s*(\d+)")
            elif ch == ".":
                parts.append(r"\.?\s*")
            elif ch.isspace():
                parts.append(r"\s*")
            else:
                parts.append(re.escape(ch))
        alts.append("".join(parts))
    head = r"^\s*" if anchored else r"(?<![\w])"
    return re.compile(head + "(?:" + "|".join(alts) + ")", re.IGNORECASE)


def compile_cell_rules(rules: dict | None) -> list[tuple[re.Pattern, Any]]:
    """Правила `ячейка_знания` типа: {образец: значение}. Значение: число — глава-константа («всегда» → 0);
    null — субъект не знает; "N" — глава из числа в ячейке (ищется по всей ячейке); "пометка" — не знает, текст
    ячейки в пометку. Образцы констант и «не знает» должны стоять в начале ячейки."""
    out: list[tuple[re.Pattern, Any]] = []
    for pat, val in (rules or {}).items():
        if val == "частичное_знание":
            continue
        out.append((_compile_cell_rule(str(pat), anchored=str(val).strip() != "N"), val))
    return out


def partial_marker(rules: dict | None) -> str:
    """Маркер частичного знания из правила `"*курсив*": частичное_знание` — обрамляющий символ (по умолчанию «*»)."""
    for pat, val in (rules or {}).items():
        if val == "частичное_знание" and str(pat):
            return str(pat)[0]
    return "*"


def _knowledge_cell(clean: str, rules: list[tuple[re.Pattern, Any]]) -> tuple[int | None, str]:
    """Ячейка знания по правилам типа: (глава, пометка). Ни одно правило не подошло — субъект не знает, текст
    ячейки уходит в пометку (например «улики с гл. 5» — знание не показано)."""
    low = clean.strip().lower()
    if low in EMPTY:
        return None, ""
    for rx, val in rules:
        m = rx.search(clean)
        if not m:
            continue
        if val is None:
            return None, ("" if not clean[m.end():].strip() else clean)  # «— (см. факт 3)»: пояснение остаётся
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val), ""
        sval = str(val).strip()
        if sval == "N":
            num = next((g for g in m.groups() if g and g.isdigit()), None)
            if num is not None:
                return int(num), ""
            continue
        if sval == "пометка":
            return None, clean
        if sval.isdigit():
            return int(sval), ""
        return None, clean
    return None, clean


def fmt_wide_table(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Широкая таблица: первая колонка-ключ («Факт»), номер («#»), остальные — субъекты (FR-DT-1). Строки и ячейки
    с заглушкой «⚠ заполнить» пропускаются; ячейки читаются по правилам `ячейка_знания` формата и
    `ячейка_знания_псевдосубъекта` (в формате или на верхнем уровне типа — так профиль переопределяет их без
    копирования извлечения) для субъектов из `псевдосубъекты` типа (например «Читатель»)."""
    key_syn = [mdparse.fold(s) for s in _synonyms("ключ", fmt.get("ключ") or {"синонимы": ["факт", "событие"]}, ctx.overrides)]
    num_syn = [mdparse.fold(s) for s in _synonyms("номер", fmt.get("номер") or {"синонимы": ["#", "№"]}, ctx.overrides)]
    min_cols = int(fmt.get("минимум_колонок", 4) or 4)
    rules = compile_cell_rules(fmt.get("ячейка_знания") or DEFAULT_CELL_RULES)
    type_raw: dict = ctx.params.get("тип") or {}
    pseudo_rules = compile_cell_rules(fmt.get("ячейка_знания_псевдосубъекта")
                                      or type_raw.get("ячейка_знания_псевдосубъекта")) + rules
    marker = partial_marker(fmt.get("ячейка_знания"))
    exclude = {mdparse.fold(s) for s in (fmt.get("исключить_колонки") or [])}
    id_prefix = str(fmt.get("префикс_id", "М-"))
    pseudo = set(fmt.get("псевдосубъекты") or []) | set(ctx.params.get("pseudo") or [])
    facts: list[dict] = []
    matched = False
    for table in _tables(path, fmt, ctx):
        if len(table.headers) < min_cols:
            continue
        key_col = next((h for h in table.headers if mdparse.fold(h) in key_syn), None)
        if key_col is None:
            continue
        matched = True
        num_col = next((h for h in table.headers if mdparse.fold(h) in num_syn), None)
        subjects = [h for h in table.headers if h not in (key_col, num_col) and mdparse.fold(h) not in exclude]
        for i, row in enumerate(table.rows, start=1):
            line = table.line + 1 + i
            text = row.get(key_col, "").strip()
            if is_blank(text):
                continue
            num = parse_number(row.get(num_col, "")) if num_col else None
            num = int(num) if num is not None else len({f["fact_id"] for f in facts}) + 1
            for subj in subjects:
                raw = row.get(subj, "").strip()
                if is_blank(raw):
                    continue
                partial = raw.startswith(marker) and raw.endswith(marker) and not raw.startswith(marker * 2)
                clean = raw.strip(marker).strip()
                from_ch, note = _knowledge_cell(clean, pseudo_rules if subj in pseudo else rules)
                source = clean.split("/", 1)[1].strip() if "/" in clean else ""
                facts.append({
                    "fact_id": f"{id_prefix}{num:02d}", "fact": text, "subject": subj, "from_chapter": from_ch,
                    "source": source, "note": ("частично/неверно: " + clean) if partial else note,
                    "_строка": line,
                })
    return facts if matched else None


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
            labels = [label] + _override_list(ctx.overrides.get(field))
            kind = spec.get("тип", "строка")
            value: Any = None
            for lb in labels:
                if kind in ("список", "список_запятая"):
                    seps = ";," if kind == "список_запятая" else ";"
                    items = [i for i in mdparse.parse_list_items(sec.body, lb, seps) if not is_placeholder(i)]
                    if items:
                        value = items
                        break
                else:
                    raw = mdparse.parse_kv(sec.body, lb)
                    if raw:
                        value = _convert_at(raw, kind, path, sec.line, field)
                        break
            if value is None:
                value = [] if kind in ("список", "список_запятая") else (None if kind in NUMERIC_KINDS else "")
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


def fmt_sections(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    """Один документ — одна запись: поля из тел секций по образцу заголовка (вместе с подсекциями; `"*"` — весь
    документ); имя — заголовок 1-го уровня (тогда поля ищутся только среди секций уровня ≥ 2). Документ-каркас,
    где все поля пусты или «⚠ заполнить», записи не даёт."""
    sections = mdparse.parse_sections(path)
    if not sections or all(s.level == 0 for s in sections):
        return None
    marker = fmt.get("опознание")
    if marker and not any(re.search(marker, s.title) for s in sections):
        return None
    name_from = fmt.get("имя", "заголовок_1")
    top = next((s for s in sections if s.level == 1), None)
    rec: dict[str, Any] = {"_строка": top.line if top else 1, "_секции": {}}
    min_level = 1
    if name_from == "заголовок_1":
        rec["name"] = top.title if top else path.stem
        min_level = 2
    elif name_from == "имя_файла":
        rec["name"] = path.stem
    filled = False
    for field, spec in (fmt.get("поля") or {}).items():
        spec = spec if isinstance(spec, dict) else {"секция": spec}
        pat = spec.get("секция", field)
        kind = spec.get("тип")
        if pat == WHOLE_DOCUMENT:
            rec[field] = strip_placeholders(mdparse.read_text(path))
            rec["_секции"][field] = 1
            if rec[field]:
                filled = True
            continue
        pats = _section_patterns(pat, ctx, field, path)
        sec = next((s for p in pats for s in [mdparse.find_section(sections, p, min_level)] if s), None)
        body = mdparse.nested_body(sections, sec) if sec is not None else ""
        if kind == "таблица_пар" and sec is not None:
            pairs: dict[str, str] = {}
            for t in mdparse.parse_tables(path, body, start_line=sec.line + 1):
                if len(t.headers) >= 2:
                    for row in t.rows:
                        k, v = row[t.headers[0]], row[t.headers[1]]
                        if k and not is_blank(k) and k not in EMPTY:
                            pairs[k] = "" if is_placeholder(v) else v
            rec[field] = pairs
        elif kind == "список" and sec is not None:
            rec[field] = [ln.strip()[2:].strip() for ln in body.splitlines()
                          if ln.strip().startswith("- ") and not is_placeholder(ln.strip()[2:])]
        else:
            text = strip_placeholders(body) if sec is not None else ""
            rec[field] = text if sec is not None or kind != "таблица_пар" else {}
        if rec[field] not in ("", {}, []):
            filled = True
        if sec is not None:
            rec.setdefault("_секции", {})[field] = sec.line
    if not filled and fmt.get("поля") and PLACEHOLDER in mdparse.read_text(path):
        return []  # каркас стартового комплекта: карточки нет
    for k, v in (fmt.get("постоянные") or {}).items():
        rec[k] = _template(v, ctx, path, rec)
    return [_apply_mapping(rec, fmt, ctx, path)]


def fmt_lines(path: Path, fmt: dict, ctx: ParseContext) -> list[dict] | None:
    rx = re.compile(fmt["регэксп"])
    kinds: dict = fmt.get("типы") or {}
    records: list[dict] = []
    for i, line in enumerate(mdparse.read_text(path).splitlines(), start=1):
        m = rx.match(line.strip())
        if not m:
            continue
        rec: dict[str, Any] = {k: _convert_at(v or "", kinds.get(k), path, i, k) for k, v in m.groupdict().items()}
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
    """«модуль:функция» — модуль ищется только в `типы/парсеры/<модуль>.py` проекта или среди модулей движка
    (`konveyer.…`); произвольные модули Python по имени из YAML не импортируются (§10). Нет плагина — формат
    молчит (None), а не падает: деградация П-5."""
    if ":" not in func:
        return None
    mod_name, fn_name = func.split(":", 1)
    mod_name, fn_name = mod_name.strip(), fn_name.strip()
    module = None
    if ctx.project_root is not None and re.fullmatch(r"[\w\-]+", mod_name):
        cand = ctx.project_root / "типы" / "парсеры" / f"{mod_name}.py"
        if cand.exists():
            module = _load_module_from(cand, mod_name)
    if module is None and mod_name.startswith(ENGINE_PLUGIN_PREFIX):
        try:
            module = importlib.import_module(mod_name)
        except ImportError:
            module = None
    if module is None:
        return None
    fn = getattr(module, fn_name, None)
    return fn if callable(fn) else None


def fmt_plugin(path: Path, fmt: dict, ctx: ParseContext) -> Any:
    fn = resolve_plugin(str(fmt.get("функция", "")), ctx)
    if fn is None:
        return None
    available = {"volume": ctx.volume, "ctx": ctx, "known_names": ctx.params.get("known_names", set()),
                 "library": ctx.library}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {name: value for name, value in available.items() if name in params}
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
                    if isinstance(v, dict) and flag(v.get("обязательна"))]
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
