"""Извлечение материалов автора в «структурированный документ» — Markdown (FR-ON-1…FR-ON-3).

Поддерживаемые форматы: .md, .txt, .docx, .rtf, .pdf, .csv, .tsv, .xlsx, .html, .json, .yaml. Остальные
регистрируются как сырьё без извлечения. Каждое извлечение получает оценку качества: доля распознанных таблиц,
число подозрительных мест (склеенные строки, потерянные колонки, колонтитулы, переносы) и вердикт
«чисто / с потерями / плохо». OCR в систему не входит (Д-9): PDF без текстового слоя отклоняется.
Необязательные библиотеки (python-docx, pypdf, openpyxl) подключаются лениво; без них формат — «нет извлечения»
с понятной причиной.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import yaml

SUPPORTED = {".md", ".txt", ".docx", ".rtf", ".pdf", ".csv", ".tsv", ".xlsx", ".html", ".htm", ".json", ".yaml", ".yml"}
TEXT_LIKE = {".md", ".txt", ".markdown"}


@dataclass
class Quality:
    tables_total: int = 0
    tables_ok: int = 0
    suspicious: list[str] = field(default_factory=list)   # что именно подозрительно (файл:строка — описание)

    @property
    def verdict(self) -> str:
        lost = self.tables_total - self.tables_ok
        if lost > 0 and lost >= max(1, self.tables_total // 2) or len(self.suspicious) > 20:
            return "плохо"
        if lost or self.suspicious:
            return "с потерями"
        return "чисто"

    def as_dict(self) -> dict:
        return {"таблиц": self.tables_total, "таблиц_распознано": self.tables_ok,
                "подозрительных": len(self.suspicious), "оценка": self.verdict,
                "подозрительные_места": self.suspicious[:50]}


@dataclass
class Extraction:
    markdown: str | None            # None — извлечения нет
    quality: Quality
    reason: str = ""                # почему извлечения нет / что рекомендовать
    fmt: str = ""


class ExtractError(ValueError):
    pass


# ------------------------------------------------------------------ общие помощники


def md_table(rows: list[list[str]]) -> str:
    """Строки ячеек → таблица Markdown; пустая шапка недопустима (первая строка — заголовки)."""
    rows = [[_cell(c) for c in r] for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines) + "\n"


def _cell(value: object) -> str:
    s = "" if value is None else str(value)
    return s.replace("\r\n", " ").replace("\n", " ").replace("|", "\\|").strip()


def _dehyphenate(text: str, quality: Quality, label: str) -> str:
    """Переносы слов «сло-\\nво» в текстовом слое PDF — склейка с пометкой в подозрительных."""
    n = len(re.findall(r"(?<=[а-яa-z])-\n(?=[а-яa-z])", text))
    if n:
        quality.suspicious.append(f"{label}: переносов слов склеено: {n}")
    return re.sub(r"(?<=[а-яa-z])-\n(?=[а-яa-z])", "", text)


def _strip_running_heads(lines: list[str], quality: Quality, label: str) -> list[str]:
    """Колонтитулы: строка, повторяющаяся ≥ 3 раз (номера страниц, название книги) — убирается."""
    counts: dict[str, int] = {}
    for ln in lines:
        s = ln.strip()
        if s and len(s) < 80 and not s.startswith("#"):
            counts[s] = counts.get(s, 0) + 1
    heads = {s for s, c in counts.items() if c >= 3 and (re.fullmatch(r"\d+", s) or c >= 4)}
    if heads:
        quality.suspicious.append(f"{label}: колонтитулов убрано: {len(heads)}")
        return [ln for ln in lines if ln.strip() not in heads]
    return lines


# ------------------------------------------------------------------ форматы


def _from_text(path: Path) -> Extraction:
    raw = path.read_bytes()
    q = Quality()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            if enc == "cp1251":
                q.suspicious.append(f"{path.name}: файл был не в UTF-8 (cp1251) — перекодирован")
            break
        except UnicodeDecodeError:
            continue
    else:
        return Extraction(None, q, "не удалось определить кодировку — пересохраните в UTF-8", path.suffix)
    text = text.replace("\r\n", "\n")
    # таблицы Markdown: считаем и проверяем ширину строк
    for block in re.findall(r"(?:^\|.*\|\s*$\n?)+", text, re.M):
        q.tables_total += 1
        widths = {ln.count("|") for ln in block.strip().splitlines()}
        if len(widths) == 1:
            q.tables_ok += 1
        else:
            q.suspicious.append(f"{path.name}: таблица с разным числом колонок в строках")
    return Extraction(text if text.endswith("\n") else text + "\n", q, "", path.suffix)


def _from_docx(path: Path) -> Extraction:
    try:
        import docx  # type: ignore
        from docx.oxml.ns import qn  # type: ignore
    except ImportError:
        return Extraction(None, Quality(), "нужна библиотека python-docx: pip install 'konveyer[docx]'", ".docx")
    q = Quality()
    d = docx.Document(str(path))
    out: list[str] = []
    body = d.element.body
    tables = {t._tbl: t for t in d.tables}
    paras = {p._p: p for p in d.paragraphs}
    list_counter = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            p = paras.get(child)
            if p is None:
                continue
            text = _docx_runs(p)
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"(?:Heading|Заголовок)\s*(\d)", style, re.I)
            if style.lower() == "title" or style == "Название":
                out.append(f"# {text}\n")
            elif m:
                out.append("#" * min(6, int(m.group(1))) + f" {text}\n")
            elif "List" in style or "Список" in style or child.find(".//" + qn("w:numPr")) is not None:
                list_counter += 1
                out.append(f"- {text}")
            elif text.strip():
                out.append(text + "\n")
            else:
                out.append("")
        elif child.tag == qn("w:tbl"):
            t = tables.get(child)
            if t is None:
                continue
            q.tables_total += 1
            rows: list[list[str]] = []
            widths = set()
            for r in t.rows:
                cells = [c.text for c in r.cells]
                # объединённые ячейки повторяются подряд — схлопываем дубли
                dedup: list[str] = []
                for c in cells:
                    if not dedup or dedup[-1] != c:
                        dedup.append(c)
                    else:
                        continue
                widths.add(len(dedup))
                rows.append(dedup)
            if len(widths) > 1:
                q.suspicious.append(f"{path.name}: таблица с объединёнными ячейками — колонки могли потеряться")
            else:
                q.tables_ok += 1
            out.append("\n" + md_table(rows))
    # комментарии рецензентов — сносками в конце
    comments = _docx_comments(path)
    if comments:
        out.append("\n## Комментарии из документа\n")
        out += [f"- {c}" for c in comments]
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return Extraction(text, q, "", ".docx")


def _docx_runs(p) -> str:
    parts: list[str] = []
    for r in p.runs:
        t = r.text
        if not t:
            continue
        if r.bold and t.strip():
            t = f"**{t.strip()}**" if not t.startswith(" ") else f" **{t.strip()}**"
        elif r.italic and t.strip():
            t = f"*{t.strip()}*"
        parts.append(t)
    return "".join(parts).strip() or p.text.strip()


def _docx_comments(path: Path) -> list[str]:
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            if "word/comments.xml" not in z.namelist():
                return []
            xml = z.read("word/comments.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        return []
    out = []
    for m in re.finditer(r"<w:comment\b.*?</w:comment>", xml, re.S):
        text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", m.group(0), re.S)).strip()
        if text:
            out.append(html.unescape(text))
    return out


def _from_pdf(path: Path) -> Extraction:
    try:
        from pypdf import PdfReader  # type: ignore
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — pypdf или его криптобиблиотека могут быть сломаны (в т.ч. паника расширения)
        return Extraction(None, Quality(), "библиотека pypdf недоступна или сломана (pip install pypdf); либо конвертируйте PDF в .docx/.md", ".pdf")
    q = Quality()
    try:
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:  # noqa: BLE001
        return Extraction(None, q, f"PDF не прочитан ({type(e).__name__}); конвертируйте в .docx/.md и повторите импорт", ".pdf")
    text = "\n".join(pages)
    if len(re.sub(r"\s+", "", text)) < 20 * max(1, len(pages)) // 2:
        return Extraction(None, q, "PDF без текстового слоя (скан): распознайте его самостоятельно (OCR в систему не входит, Д-9) "
                                    "и импортируйте .docx/.md", ".pdf")
    text = _dehyphenate(text, q, path.name)
    lines = _strip_running_heads(text.splitlines(), q, path.name)
    glued = sum(1 for ln in lines if len(ln) > 400)
    if glued:
        q.suspicious.append(f"{path.name}: склеенных строк (длиннее 400 знаков): {glued}")
    if any("|" in ln or "\t" in ln for ln in lines):
        q.tables_total += 1
        q.suspicious.append(f"{path.name}: похоже на таблицу, но из PDF таблицы не восстанавливаются — проверьте")
    return Extraction("\n".join(lines).strip() + "\n", q, "", ".pdf")


def _from_csv(path: Path, delimiter: str) -> Extraction:
    q = Quality()
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
        q.suspicious.append(f"{path.name}: кодировка не UTF-8 — перекодирован из cp1251")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    q.tables_total = 1
    widths = {len(r) for r in rows if r}
    if len(widths) <= 1:
        q.tables_ok = 1
    else:
        q.suspicious.append(f"{path.name}: разное число колонок в строках ({min(widths)}–{max(widths)})")
    return Extraction(f"# {path.stem}\n\n" + md_table(rows), q, "", path.suffix)


def _from_xlsx(path: Path) -> Extraction:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return Extraction(None, Quality(), "нужна библиотека openpyxl (pip install openpyxl) или сохраните лист как .csv", ".xlsx")
    q = Quality()
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    out = [f"# {path.stem}\n"]
    for ws in wb.worksheets:
        rows = [[("" if v is None else str(v)) for v in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            continue
        # обрезаем пустые хвостовые колонки
        width = max(len([c for c in r if c.strip()]) and max(i + 1 for i, c in enumerate(r) if c.strip()) for r in rows)
        rows = [r[:width] for r in rows]
        q.tables_total += 1
        if any(ws.merged_cells.ranges) if hasattr(ws, "merged_cells") else False:
            q.suspicious.append(f"{path.name}/{ws.title}: объединённые ячейки — колонки могли потеряться")
        else:
            q.tables_ok += 1
        out.append(f"\n## {ws.title}\n\n" + md_table(rows))
    return Extraction("\n".join(out).strip() + "\n", q, "", ".xlsx")


class _HTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.stack: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag == "table":
            self.table, self.tables = [], self.tables + 1
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "li":
            self.out.append("- ")
        elif tag in ("p", "div", "br"):
            self.out.append("\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n" + "#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.out.append("\n" + md_table(self.table))
            self.table = None
        elif tag in ("p", "li", "div") or re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n")
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1] in ("script", "style"):
            return
        if self.cell is not None:
            self.cell.append(data.strip())
        else:
            self.out.append(data)


def _from_html(path: Path) -> Extraction:
    q = Quality()
    p = _HTML()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    q.tables_total = q.tables_ok = p.tables
    return Extraction(text, q, "", path.suffix)


def _from_structured(path: Path) -> Extraction:
    """JSON/YAML: список словарей → таблица; словарь → секции «ключ: значение»; иначе — код."""
    q = Quality()
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as e:
        return Extraction(None, q, f"файл не разобран как {path.suffix[1:].upper()}: {e}", path.suffix)
    out = [f"# {path.stem}\n"]

    def emit(value, level: int) -> None:
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            keys = list(dict.fromkeys(k for x in value for k in x))
            q.tables_total += 1
            q.tables_ok += 1
            out.append(md_table([keys] + [[_scalar(x.get(k)) for k in keys] for x in value]))
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    out.append("\n" + "#" * min(6, level) + f" {k}\n")
                    emit(v, level + 1)
                else:
                    out.append(f"- {k}: {_scalar(v)}")
        elif isinstance(value, list):
            out.extend(f"- {_scalar(v)}" for v in value)
        else:
            out.append(_scalar(value))

    emit(data, 2)
    return Extraction("\n".join(out).strip() + "\n", q, "", path.suffix)


def _scalar(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def _drop_rtf_groups(raw: str, names: tuple[str, ...]) -> str:
    """Убирает служебные группы RTF ({\\fonttbl …}) с учётом вложенных скобок."""
    out = []
    i = 0
    while i < len(raw):
        if raw.startswith("{\\", i) and any(raw.startswith("{\\" + n, i) or raw.startswith("{\\*\\" + n, i) for n in names):
            depth = 0
            while i < len(raw):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            continue
        out.append(raw[i])
        i += 1
    return "".join(out)


def _from_rtf(path: Path) -> Extraction:
    """Упрощённый разбор RTF: текст без управляющих слов; таблицы не восстанавливаются."""
    q = Quality()
    raw = path.read_text(encoding="latin-1", errors="replace")

    def _hex(m):
        try:
            return bytes([int(m.group(1), 16)]).decode("cp1251")
        except (ValueError, UnicodeDecodeError):
            return ""

    raw = _drop_rtf_groups(raw, ("fonttbl", "colortbl", "stylesheet", "info", "listtable", "listoverridetable", "generator"))
    text = re.sub(r"\\'([0-9a-fA-F]{2})", _hex, raw)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return Extraction(None, q, "RTF без текста или в неподдерживаемой кодировке — конвертируйте в .docx", ".rtf")
    if "\\trowd" in raw:
        q.tables_total += 1
        q.suspicious.append(f"{path.name}: таблицы RTF не восстанавливаются — конвертируйте в .docx")
    return Extraction(text + "\n", q, "", ".rtf")


# ------------------------------------------------------------------ вход


def extract(path: Path) -> Extraction:
    """Извлечение по расширению; неподдерживаемый формат — сырьё без извлечения (FR-ON-1)."""
    suffix = path.suffix.lower()
    if suffix in TEXT_LIKE:
        return _from_text(path)
    if suffix == ".docx":
        return _from_docx(path)
    if suffix == ".pdf":
        return _from_pdf(path)
    if suffix == ".csv":
        return _from_csv(path, ",")
    if suffix == ".tsv":
        return _from_csv(path, "\t")
    if suffix == ".xlsx":
        return _from_xlsx(path)
    if suffix in (".html", ".htm"):
        return _from_html(path)
    if suffix in (".json", ".yaml", ".yml"):
        return _from_structured(path)
    if suffix == ".rtf":
        return _from_rtf(path)
    return Extraction(None, Quality(), f"формат {suffix or '(без расширения)'} не поддерживается: сохраните как .md/.docx/.xlsx", suffix)
