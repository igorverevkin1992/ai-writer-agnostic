"""Разбор Markdown библиотеки по соглашениям разметки (Д-1).

Канон не переформатируется под парсер — парсер пишется под канон.
Форматы объявляются каталогом типов (`konveyer/типы/*.yaml`, FR-DT-4); сгенерированная сводка — «Соглашения типов»
(`konveyer типы --документация`).
При расхождении структуры парсер обязан указать файл и строку (FR-X1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class MarkupError(ValueError):
    """Расхождение структуры MD с соглашениями Д-1 (FR-X1)."""

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


def parse_sections(path: Path) -> list[Section]:
    """Режет файл на секции по заголовкам #..######."""
    sections: list[Section] = []
    current = Section(title="", level=0, line=0)
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            sections.append(current)
            current = Section(title=m.group(2).strip(), level=len(m.group(1)), line=i)
        else:
            current.body_lines.append(line)
    sections.append(current)
    return sections


def find_section(sections: list[Section], pattern: str) -> Section | None:
    rx = re.compile(pattern)
    for s in sections:
        if rx.search(s.title):
            return s
    return None


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_tables(path: Path, text: str | None = None, start_line: int = 1) -> list[Table]:
    """Извлекает pipe-таблицы. Ошибка структуры → MarkupError с файлом/строкой."""
    lines = (text if text is not None else path.read_text(encoding="utf-8")).splitlines()
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            headers = _split_row(line)
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
        tables = parse_tables(path, sec.body, start_line=sec.line + 1)
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


def parse_number(value: str) -> float | None:
    value = value.strip().replace(",", ".").replace("%", "")
    if value in {"", "—", "-", "–"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group()) if m else None


def parse_list_items(body: str, key: str) -> list[str]:
    """Список из блока вида `- Ключ:` с подпунктами `  - элемент`."""
    items: list[str] = []
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^[-*]\s+{re.escape(key)}\s*:\s*(.*)$", line.strip())
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            items.extend(x.strip() for x in inline.split(";") if x.strip())
        for sub in lines[i + 1 :]:
            sm = re.match(r"^\s+[-*]\s+(.*)$", sub)
            if sm:
                items.append(sm.group(1).strip())
            elif sub.strip():
                break
        break
    return items


def parse_kv(body: str, key: str) -> str:
    for line in body.splitlines():
        m = re.match(rf"^[-*]\s+{re.escape(key)}\s*:\s*(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return ""
