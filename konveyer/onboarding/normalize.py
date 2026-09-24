"""Нормализация подтверждённого документа в канонический Markdown типа (FR-ON-15, FR-ON-18).

Меняется только раскладка, не содержание: абзацы автора переносятся дословно; заголовки таблицы-реестра (той, чьи
колонки лучше всего совпали с колонками формата, а не первой по позиции) приводятся к объявленным колонкам типа
(по сопоставлению колонок, FR-ON-13), недостающие объявленные колонки добавляются с «—», недостающая ключевая
колонка (id) заполняется порядковыми идентификаторами с префиксом типа; обязательные секции типа создаются пустыми
с подсказкой; в шапке — ссылка на источник в сырьё; вопросы автору ставятся в документ пометкой
«⚠ решение автора» рядом с местом, к которому относятся (FR-ON-14).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import catalog, declparse

TABLE_BLOCK_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M)
SOURCE_MARK = "<!-- источник: {source} -->"
SECTION_HINT = "⚠ заполнить"
DECISION_MARK = "⚠ решение автора"
QUESTION_RE = re.compile(r"^(?P<file>[^:\n]+?)(?::(?P<line>\d+))?: (?P<text>.+)$")


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


def _lenient(columns: dict) -> dict:
    return {f: ({**c, "обязательна": False} if isinstance(c, dict) else c) for f, c in columns.items()}


def find_registry_table(text: str, columns: dict, mapping: dict[str, str] | None = None) -> tuple[re.Match | None, dict[str, str]]:
    """Таблица-реестр документа: та, чьи заголовки совпали с наибольшим числом колонок формата (как это делает
    экспорт, перебирая все таблицы), а не первая по позиции — перед реестром может стоять легенда или шапка.
    Ни одна не совпала — (None, {}): таблицы не трогаются, вопрос уходит автору (FR-ON-23)."""
    best: re.Match | None = None
    best_found: dict[str, str] = {}
    best_score = 0
    lenient = _lenient(columns)
    for m in TABLE_BLOCK_RE.finditer(text):
        lines = m.group(0).rstrip("\n").splitlines()
        if len(lines) < 2 or not _is_sep(_split_row(lines[1])):
            continue
        headers = _split_row(lines[0])
        found = declparse.match_columns(headers, lenient, {}) or {}
        for fld, h in (mapping or {}).items():
            if h in headers:
                found[fld] = h
        if len(found) > best_score:
            best, best_found, best_score = m, found, len(found)
    return best, best_found


def normalize_tables(text: str, spec: catalog.TypeSpec, mapping: dict[str, str] | None, result: Normalized) -> str:
    """Таблица-реестр документа: заголовки → имена колонок типа, недостающие колонки — «—», ключ — сгенерирован."""
    fmt, _ = _first_table_format(spec)
    if fmt is None:
        return text
    columns: dict = fmt["колонки"]
    m, found = find_registry_table(text, columns, mapping)
    if m is None:
        if TABLE_BLOCK_RE.search(text):
            result.notes.append(f"ни одна таблица не похожа на реестр типа «{spec.name}» — таблицы оставлены как есть")
        return text
    block = m.group(0)
    lines = block.rstrip("\n").splitlines()
    headers = _split_row(lines[0])
    body = [_split_row(ln) for ln in lines[2:]]
    mapping = dict(mapping or {})
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


def decisions_pending(text: str) -> list[str]:
    """Пометки «⚠ решение автора», ещё не снятые автором."""
    return re.findall(rf"<!-- {DECISION_MARK}: (.*?) -->", text)


def source_of(text: str) -> str | None:
    m = re.search(r"^<!-- источник: (.*) -->$", text, re.M)
    return m.group(1) if m else None


def mark_questions(out: str, original: str, questions: list[str], result: Normalized) -> str:
    """Пометки «⚠ решение автора» (FR-ON-14): вопрос со строкой ставится HTML-комментарием после этой строки
    (для строки таблицы — после её таблицы, чтобы не разорвать блок); вопрос без строки — в конец документа.
    Содержимое автора не меняется — добавляются только комментарии."""
    if not questions:
        return out
    src_lines = original.replace("\r\n", "\n").splitlines()
    out_lines = out.rstrip("\n").splitlines()
    inserts: dict[int, list[str]] = {}
    tail: list[str] = []
    for q in questions:
        m = QUESTION_RE.match(q.strip())
        text = m.group("text") if m else q.strip()
        line_no = int(m.group("line")) if m and m.group("line") else 0
        mark = f"<!-- {DECISION_MARK}: {text} -->"
        idx = _locate(src_lines, out_lines, line_no)
        if idx is None:
            tail.append(mark)
        else:
            inserts.setdefault(idx, []).append(mark)
    merged: list[str] = []
    for i, ln in enumerate(out_lines):
        merged.append(ln)
        for mark in inserts.get(i, []):
            merged.append(mark)
    if tail:
        merged += ["", *tail]
    result.notes += [f"{DECISION_MARK}: {len(questions)}"]
    return "\n".join(merged) + "\n"


def _locate(src_lines: list[str], out_lines: list[str], line_no: int) -> int | None:
    """Индекс строки результата, после которой ставится пометка к строке `line_no` исходного извлечения."""
    if not (1 <= line_no <= len(src_lines)):
        return None
    src = src_lines[line_no - 1].strip()
    if src.startswith("|"):
        cells = [c for c in _split_row(src) if c]
        for i, ln in enumerate(out_lines):
            if ln.strip().startswith("|") and all(c in ln for c in cells):
                j = i
                while j + 1 < len(out_lines) and out_lines[j + 1].strip().startswith("|"):
                    j += 1
                return j
        return None
    for i, ln in enumerate(out_lines):
        if ln.strip() == src and src:
            return i
    return None


def normalize(text: str, spec: catalog.TypeSpec, *, source: str = "", mapping: dict[str, str] | None = None,
              title: str | None = None, questions: list[str] | None = None) -> Normalized:
    """Канонический Markdown типа из извлечения: текст дословно, раскладка — по типу, вопросы автору — пометками."""
    result = Normalized(text=text)
    out = text.replace("\r\n", "\n")
    if not re.match(r"^#\s+", out) and title:
        out = f"# {title}\n\n" + out
    out = normalize_tables(out, spec, mapping, result)
    out = ensure_sections(out, spec, result)
    if source:
        out = with_source(out, source)
    out = mark_questions(out, text, questions or [], result)
    result.text = out.rstrip("\n") + "\n"
    return result


def paragraphs(text: str) -> list[str]:
    """Абзацы авторского текста (без таблиц, заголовков и служебных пометок) — для проверки дословности."""
    body = strip_source(text)
    body = TABLE_BLOCK_RE.sub("", body)
    body = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.M)
    body = re.sub(rf"^<!-- {DECISION_MARK}: .* -->$", "", body, flags=re.M)
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and p.strip() != SECTION_HINT]


def split_by_sections(path: Path, parts: list[dict]) -> tuple[list[tuple[str, str, str]], str]:
    """Разбиение файла по заголовкам 2-го уровня (FR-ON-9): ([(заголовок, тип, текст части)], остаток).
    Остаток — вступление до первого раздела и разделы, не получившие типа: он не пропадает молча, а идёт
    отдельным документом или сырьём (П-7)."""
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    wanted = {p["heading"]: p["type"] for p in parts}
    out: list[tuple[str, str, str]] = []
    rest: list[str] = []
    chunks = re.split(r"(?m)^(?=##\s+)", text)
    for chunk in chunks:
        m = re.match(r"^##\s+(.*)$", chunk, re.M)
        if not m or m.group(1).strip() not in wanted:
            rest.append(chunk)
            continue
        heading = m.group(1).strip()
        body = chunk[m.end():].strip("\n")
        out.append((heading, wanted[heading], f"# {heading}\n\n{body}\n"))
    remainder = "".join(rest)
    remainder_body = re.sub(r"^<!-- источник: .* -->$", "", remainder, flags=re.M)
    remainder_body = re.sub(r"^#\s+.*$", "", remainder_body, count=1, flags=re.M)
    return out, (remainder if remainder_body.strip() else "")
