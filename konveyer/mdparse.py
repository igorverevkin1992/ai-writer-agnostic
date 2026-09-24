"""Разбор Markdown библиотеки: секции по заголовкам, pipe-таблицы, списки «- Ключ: значение».

Канон не переформатируется под парсер — парсер настраивается под канон (FR-DT-5, Д-6); что именно читается из
документа каждого типа, объявляет каталог типов (`типы/*.yaml`, FR-DT-4). При расхождении структуры парсер
обязан назвать файл и строку (FR-EX-3). Файлы читаются в UTF-8 с необязательной меткой порядка байт (NFR-2:
Блокнот Windows пишет BOM по умолчанию).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ENCODING = "utf-8-sig"


def read_text(path: Path) -> str:
    """Текст документа канона: UTF-8, BOM снимается."""
    return path.read_text(encoding=ENCODING)


def fold(text: str) -> str:
    """Форма для сравнения ключей и заголовков: без регистра, «ё» = «е» (FR-DT-5)."""
    return text.strip().lower().replace("ё", "е")


class MarkupError(ValueError):
    """Расхождение структуры документа с объявленным форматом (FR-EX-3): файл, строка, что ожидалось."""

    def __init__(self, path: Path, line: int, message: str):
        super().__init__(f"{path}:{line}: {message}")
        self.path = path
        self.line = line


@dataclass
class Table:
    headers: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)
    line: int = 0


@dataclass
class Section:
    title: str
    level: int
    line: int
    body_lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines).strip()

    @property
    def raw_body(self) -> str:
        """Тело без обрезки пустых строк: строка k тела — строка `line + k` файла."""
        return "\n".join(self.body_lines)


def parse_sections(path: Path) -> list[Section]:
    """Режет файл на секции по заголовкам #..######."""
    sections: list[Section] = []
    current = Section(title="", level=0, line=0)
    for i, line in enumerate(read_text(path).splitlines(), start=1):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            sections.append(current)
            current = Section(title=m.group(2).strip(), level=len(m.group(1)), line=i)
        else:
            current.body_lines.append(line)
    sections.append(current)
    return sections


def nested_body(sections: list[Section], section: Section) -> str:
    """Тело секции вместе с вложенными подсекциями (заголовки глубже уровня секции — до следующего заголовка
    того же или более высокого уровня); нумерация строк — как у `raw_body`."""
    idx = sections.index(section)
    lines = list(section.body_lines)
    for s in sections[idx + 1:]:
        if s.level <= section.level:
            break
        lines.append("#" * s.level + " " + s.title)
        lines.extend(s.body_lines)
    return "\n".join(lines)


def find_section(sections: list[Section], pattern: str, min_level: int = 0) -> Section | None:
    rx = re.compile(pattern)
    for s in sections:
        if s.level >= min_level and rx.search(s.title):
            return s
    return None


def find_sections(sections: list[Section], pattern: str, min_level: int = 0) -> list[Section]:
    """Все секции, чей заголовок подходит под образец."""
    rx = re.compile(pattern)
    return [s for s in sections if s.level >= min_level and rx.search(s.title)]


_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _split_row(line: str) -> list[str]:
    """Ячейки строки таблицы: один ведущий и один замыкающий «|» снимаются (пустая последняя ячейка «||»
    сохраняется), «\\|» — экранированная черта внутри ячейки."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in _CELL_SPLIT_RE.split(s)]


def parse_tables(path: Path, text: str | None = None, start_line: int = 1) -> list[Table]:
    """Извлекает pipe-таблицы. Ошибка структуры → MarkupError с файлом/строкой."""
    lines = (text if text is not None else read_text(path)).splitlines()
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            headers = _split_row(line)
            if len(set(headers)) != len(headers):
                dup = sorted({h for h in headers if headers.count(h) > 1})
                raise MarkupError(path, start_line + i,
                                  f"в заголовке таблицы повторяются колонки: {', '.join(f'«{h}»' for h in dup)}")
            table = Table(headers=headers, line=start_line + i)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i])
                if len(cells) != len(headers):
                    raise MarkupError(
                        path, start_line + i,
                        f"в таблице {len(headers)} колонок, в строке — {len(cells)}",
                    )
                table.rows.append(dict(zip(headers, cells, strict=True)))
                i += 1
            tables.append(table)
        else:
            i += 1
    return tables


def require_table(path: Path, columns: list[str], section_pattern: str | None = None) -> Table:
    """Первая таблица (файла или секции), содержащая все нужные колонки."""
    if section_pattern:
        sec = find_section(parse_sections(path), section_pattern)
        if sec is None:
            raise MarkupError(path, 1, f"не найдена секция по образцу «{section_pattern}»")
        tables = parse_tables(path, sec.raw_body, start_line=sec.line + 1)
    else:
        tables = parse_tables(path)
    for t in tables:
        if all(any(col in h for h in t.headers) for col in columns):
            return t
    raise MarkupError(path, 1, f"не найдена таблица с колонками {columns}")


def cell(row: dict[str, str], name: str) -> str:
    """Значение колонки по подстроке имени (заголовки канона могут уточняться)."""
    for k, v in row.items():
        if name in k:
            return v
    return ""


_THOUSANDS_RE = re.compile(r"(?<=\d)[   ](?=\d{3}\b)")


def parse_number(value: str) -> float | None:
    """Первое число в строке: «12», «0,45», «30%», «1 000» (пробел — разделитель тысяч), «−3». Запятая —
    десятичный разделитель (русская запись). Нет числа — None."""
    value = _THOUSANDS_RE.sub("", value.strip()).replace(",", ".").replace("%", "").replace("−", "-")
    if value in {"", "—", "-", "–"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group()) if m else None


def split_list(text: str, separators: str = ";") -> list[str]:
    """«а; б (в; г), д» → [«а», «б (в; г)», «д»] при separators=";,": разделители внутри скобок не делят."""
    items, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in separators and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [it.strip() for it in items if it.strip()]


def _key_re(key: str) -> re.Pattern:
    """«- Ключ: …» без учёта регистра и «ё/е»: «Объём» находит и «Объем», «НЕ знает» — «Не знает»."""
    escaped = "".join("[её]" if ch in "её" else re.escape(ch) for ch in key.strip())
    return re.compile(rf"^[-*]\s+{escaped}\s*:\s*(.*)$", re.IGNORECASE)


def parse_list_items(body: str, key: str, separators: str = ";") -> list[str]:
    """Список из блока вида `- Ключ:` с подпунктами `  - элемент`; элементы в строке ключа делятся
    по `separators`."""
    items: list[str] = []
    lines = body.splitlines()
    rx = _key_re(key)
    for i, line in enumerate(lines):
        m = rx.match(line.strip())
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            items.extend(split_list(inline, separators))
        for sub in lines[i + 1 :]:
            sm = re.match(r"^\s+[-*]\s+(.*)$", sub)
            if sm:
                items.append(sm.group(1).strip())
            elif sub.strip():
                break
        break
    return items


def parse_kv(body: str, key: str) -> str:
    rx = _key_re(key)
    for line in body.splitlines():
        m = rx.match(line.strip())
        if m:
            return m.group(1).strip()
    return ""
