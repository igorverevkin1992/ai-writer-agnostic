"""Нормализация подтверждённого документа в канонический Markdown типа (FR-ON-15, FR-ON-18).

Меняется только раскладка, не содержание: абзацы автора переносятся дословно; заголовки таблиц приводятся к
объявленным колонкам типа (по сопоставлению колонок, FR-ON-13), недостающие объявленные колонки добавляются
с «—», недостающая ключевая колонка (id) заполняется порядковыми идентификаторами с префиксом типа;
обязательные секции типа создаются пустыми с подсказкой; в шапке — ссылка на источник в сырьё.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import catalog, declparse

TABLE_BLOCK_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M)
SOURCE_MARK = "<!-- источник: {source} -->"
SECTION_HINT = "⚠ заполнить"


@dataclass
class Normalized:
    text: str
    renamed: dict[str, str] = field(default_factory=dict)  # заголовок файла → колонка типа
    added_columns: list[str] = field(default_factory=list)
    generated_ids: int = 0
    added_sections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells)


def _first_table_format(spec: catalog.TypeSpec) -> tuple[dict | None, str]:
    for ext in spec.extractions:
        for fmt in ext.get("форматы", []):
            if fmt.get("вид") == "таблица" and fmt.get("колонки"):
                return fmt, str(ext.get("имя", ""))
    return None, ""


def _id_prefix(spec: catalog.TypeSpec, fmt: dict) -> str:
    for f in (fmt, *[ff for ext in spec.extractions for ff in ext.get("форматы", [])]):
        if f.get("префикс_id"):
            return str(f["префикс_id"])
    return spec.name[:1].upper() + "-"


def normalize_tables(text: str, spec: catalog.TypeSpec, mapping: dict[str, str] | None, result: Normalized) -> str:
    """Первая таблица документа: заголовки → имена колонок типа, недостающие колонки — «—», ключ — сгенерирован."""
    fmt, _ = _first_table_format(spec)
    if fmt is None:
        return text
    columns: dict = fmt["колонки"]
    m = TABLE_BLOCK_RE.search(text)
    if not m:
        return text
    block = m.group(0)
    lines = block.rstrip("\n").splitlines()
    if len(lines) < 2:
        return text
    headers = _split_row(lines[0])
    if not _is_sep(_split_row(lines[1])):
        return text
    body = [_split_row(ln) for ln in lines[2:]]
    mapping = dict(mapping or {})
    found = declparse.match_columns(headers, columns, {}) or {}
    for fld, h in found.items():
        mapping.setdefault(fld, h)
    header_to_field = {h: f for f, h in mapping.items() if h in headers}
    new_headers = [header_to_field.get(h, h) for h in headers]
    for h, f in header_to_field.items():
        if h != f:
            result.renamed[h] = f
    key_field = next((f for f, s in columns.items() if isinstance(s, dict) and s.get("роль") == "ключ"), None)
    for fld, cspec in columns.items():
        if fld in new_headers:
            continue
        required = isinstance(cspec, dict) and cspec.get("обязательна")
        if fld == key_field:
            prefix = _id_prefix(spec, fmt)
            new_headers.insert(0, fld)
            body = [[f"{prefix}{i:03d}"] + row for i, row in enumerate(body, start=1)]
            result.generated_ids = len(body)
        elif required:
            new_headers.append(fld)
            body = [row + ["—"] for row in body]
            result.added_columns.append(fld)
    width = len(new_headers)
    body = [(row + ["—"] * (width - len(row)))[:width] for row in body]
    out = ["| " + " | ".join(new_headers) + " |", "|" + "---|" * width] + ["| " + " | ".join(r) + " |" for r in body]
    return text[: m.start()] + "\n".join(out) + "\n" + text[m.end():]


def ensure_sections(text: str, spec: catalog.TypeSpec, result: Normalized) -> str:
    """Обязательные секции типа (`обязательные_секции`) — если нет, добавляются пустыми с подсказкой."""
    if not spec.required_sections:
        return text
    headings = [m.group(1).lower() for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)]
    tail: list[str] = []
    for sec in spec.required_sections:
        pattern = str(sec)
        if any(re.search(pattern, h) for h in headings):
            continue
        title = re.sub(r"[\[\]()|^$.*+?\\]", "", pattern.split("|")[0]).strip().capitalize() or pattern
        tail += ["", f"## {title}", "", SECTION_HINT, ""]
        result.added_sections.append(title)
    return text.rstrip("\n") + "\n" + "\n".join(tail) if tail else text


def with_source(text: str, source: str) -> str:
    """Ссылка на источник после заголовка 1-го уровня (или в начале) — FR-ON-18."""
    mark = SOURCE_MARK.format(source=source)
    if mark in text:
        return text
    m = re.match(r"^(#\s+.*\n)", text)
    if m:
        return text[: m.end()] + "\n" + mark + "\n" + text[m.end():]
    return mark + "\n\n" + text


def strip_source(text: str) -> str:
    return re.sub(r"^<!-- источник: .* -->\n\n?", "", text, flags=re.M)


def source_of(text: str) -> str | None:
    m = re.search(r"^<!-- источник: (.*) -->$", text, re.M)
    return m.group(1) if m else None


def normalize(text: str, spec: catalog.TypeSpec, *, source: str = "", mapping: dict[str, str] | None = None,
              title: str | None = None) -> Normalized:
    """Канонический Markdown типа из извлечения: текст дословно, раскладка — по типу."""
    result = Normalized(text=text)
    out = text.replace("\r\n", "\n")
    if not re.match(r"^#\s+", out) and title:
        out = f"# {title}\n\n" + out
    out = normalize_tables(out, spec, mapping, result)
    out = ensure_sections(out, spec, result)
    if source:
        out = with_source(out, source)
    result.text = out.rstrip("\n") + "\n"
    return result


def paragraphs(text: str) -> list[str]:
    """Абзацы авторского текста (без таблиц, заголовков и служебных пометок) — для проверки дословности."""
    body = strip_source(text)
    body = TABLE_BLOCK_RE.sub("", body)
    body = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.M)
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and p.strip() != SECTION_HINT]


def split_by_sections(path: Path, parts: list[dict]) -> list[tuple[str, str, str]]:
    """Разбиение файла по заголовкам 2-го уровня: [(заголовок, тип, текст части)] (FR-ON-9)."""
    text = path.read_text(encoding="utf-8")
    wanted = {p["heading"]: p["type"] for p in parts}
    out: list[tuple[str, str, str]] = []
    chunks = re.split(r"(?m)^(?=##\s+)", text)
    for chunk in chunks:
        m = re.match(r"^##\s+(.*)$", chunk, re.M)
        if not m or m.group(1).strip() not in wanted:
            continue
        heading = m.group(1).strip()
        body = chunk[m.end():].strip("\n")
        out.append((heading, wanted[heading], f"# {heading}\n\n{body}\n"))
    return out
