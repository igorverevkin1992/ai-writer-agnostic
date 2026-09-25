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
    notes: list[str] = field(default_factory=list)        # информационные заметки без потерь (перекодировка и т. п.)

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
                "подозрительные_места": self.suspicious[:50], "заметки": self.notes[:20]}


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
                q.notes.append(f"{path.name}: файл был не в UTF-8 (cp1251) — перекодирован без потерь")
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
        return Extraction(None, Quality(), "нужна библиотека python-docx: pip install 'konveyer[docx]' (или 'konveyer[onboarding]')", ".docx")
    q = Quality()
    d = docx.Document(str(path))
    out: list[str] = []
    body = d.element.body
    tables = {t._tbl: t for t in d.tables}
    paras = {p._p: p for p in d.paragraphs}
    numbering = _docx_numbering(d)
    counters: dict[tuple[str, str], int] = {}
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            p = paras.get(child)
            if p is None:
                continue
            text = _docx_runs(p)
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"(?:Heading|Заголовок)\s*(\d)", style, re.I)
            num_pr = child.find(".//" + qn("w:numPr"))
            if style.lower() == "title" or style == "Название":
                out.append(f"# {text}\n")
            elif m:
                out.append("#" * min(6, int(m.group(1))) + f" {text}\n")
            elif "List" in style or "Список" in style or num_pr is not None:
                # нумерованный список сохраняет порядок частей (FR-ON-2): «1.», маркированный — «-»
                marker = _docx_list_marker(num_pr, style, numbering, counters)
                out.append(f"{marker} {text}")
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
            merged = False
            for r in t.rows:
                cells: list[str] = []
                prev_tc = None
                for c in r.cells:
                    # объединённая по горизонтали ячейка — один и тот же `_tc` на несколько колонок сетки:
                    # значение пишется один раз, остальные колонки остаются пустыми — ширина строки не плывёт
                    if prev_tc is not None and c._tc is prev_tc:
                        cells.append("")
                        merged = True
                    else:
                        cells.append(c.text)
                    prev_tc = c._tc
                rows.append(cells)
            q.tables_ok += 1
            if merged:
                q.suspicious.append(f"{path.name}: таблица с объединёнными ячейками — проверьте заголовки колонок")
            out.append("\n" + md_table(rows))
    # сноски и комментарии рецензентов — в конце документа
    footnotes = _docx_footnotes(path)
    if footnotes:
        out.append("\n## Сноски\n")
        out += [f"[^{n}]: {t}" for n, t in footnotes]
    comments = _docx_comments(path)
    if comments:
        out.append("\n## Комментарии из документа\n")
        out += [f"- {c}" for c in comments]
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return Extraction(text, q, "", ".docx")


def _docx_numbering(d) -> dict[tuple[str, str], str]:
    """{(numId, ilvl): numFmt} из word/numbering.xml — чтобы отличить нумерованный список от маркированного."""
    from docx.oxml.ns import qn  # type: ignore

    out: dict[tuple[str, str], str] = {}
    try:
        el = d.part.numbering_part.element
    except (AttributeError, KeyError, NotImplementedError):
        return out
    abstract: dict[str, dict[str, str]] = {}
    for a in el.findall(qn("w:abstractNum")):
        levels = {}
        for lvl in a.findall(qn("w:lvl")):
            fmt = lvl.find(qn("w:numFmt"))
            levels[lvl.get(qn("w:ilvl"), "0")] = fmt.get(qn("w:val"), "") if fmt is not None else ""
        abstract[a.get(qn("w:abstractNumId"), "")] = levels
    for num in el.findall(qn("w:num")):
        ref = num.find(qn("w:abstractNumId"))
        levels = abstract.get(ref.get(qn("w:val"), "") if ref is not None else "", {})
        for ilvl, fmt in levels.items():
            out[(num.get(qn("w:numId"), ""), ilvl)] = fmt
    return out


def _docx_list_marker(num_pr, style: str, numbering: dict[tuple[str, str], str], counters: dict[tuple[str, str], int]) -> str:
    from docx.oxml.ns import qn  # type: ignore

    key = ("", "0")
    fmt = ""
    if num_pr is not None:
        num_id = num_pr.find(qn("w:numId"))
        ilvl = num_pr.find(qn("w:ilvl"))
        key = (num_id.get(qn("w:val"), "") if num_id is not None else "", ilvl.get(qn("w:val"), "0") if ilvl is not None else "0")
        fmt = numbering.get(key, "")
    numbered = (fmt and fmt != "bullet") or (not fmt and re.search(r"number|нумер", style, re.I) is not None)
    if not numbered:
        return "-"
    counters[key] = counters.get(key, 0) + 1
    return f"{counters[key]}."


def _docx_runs(p) -> str:
    """Текст абзаца по всем `w:r`, включая runs внутри гиперссылок (их нет в `p.runs`); ссылки на сноски — `[^n]`."""
    from docx.oxml.ns import qn  # type: ignore
    from docx.text.run import Run  # type: ignore

    parts: list[str] = []
    for el in p._p.iter():
        if el.tag == qn("w:r"):
            r = Run(el, p)
            t = r.text
            ref = el.find(qn("w:footnoteReference"))
            if ref is not None:
                t += f"[^{ref.get(qn('w:id'), '')}]"
            if not t:
                continue
            if r.bold and t.strip():
                t = f"**{t.strip()}**" if not t.startswith(" ") else f" **{t.strip()}**"
            elif r.italic and t.strip():
                t = f"*{t.strip()}*"
            parts.append(t)
    return "".join(parts).strip() or p.text.strip()


def _docx_part_xml(path: Path, member: str) -> str:
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            if member not in z.namelist():
                return ""
            return z.read(member).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""


def _docx_comments(path: Path) -> list[str]:
    xml = _docx_part_xml(path, "word/comments.xml")
    out = []
    for m in re.finditer(r"<w:comment\b.*?</w:comment>", xml, re.S):
        text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", m.group(0), re.S)).strip()
        if text:
            out.append(html.unescape(text))
    return out


def _docx_footnotes(path: Path) -> list[tuple[str, str]]:
    """[(id, текст)] из word/footnotes.xml без служебных разделителей (id −1 и 0)."""
    xml = _docx_part_xml(path, "word/footnotes.xml")
    out = []
    for m in re.finditer(r"<w:footnote\b([^>]*)>(.*?)</w:footnote>", xml, re.S):
        fid = re.search(r'w:id="(-?\d+)"', m.group(1))
        if fid is None or int(fid.group(1)) <= 0:
            continue
        text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", m.group(2), re.S)).strip()
        if text:
            out.append((fid.group(1), html.unescape(text)))
    return out


def _from_pdf(path: Path) -> Extraction:
    try:
        from pypdf import PdfReader  # type: ignore
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — pypdf или его криптобиблиотека могут быть сломаны (в т.ч. паника расширения)
        return Extraction(None, Quality(), "библиотека pypdf недоступна или сломана (pip install 'konveyer[pdf]'); либо конвертируйте PDF в .docx/.md", ".pdf")
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


CSV_DELIMITERS = ",;\t|"


def _sniff_delimiter(text: str, default: str) -> str:
    """Разделитель CSV: Excel в русской локали пишет «;», выгрузки — табуляцию; по расширению — только запасной вариант."""
    head = text[:4096]
    try:
        return csv.Sniffer().sniff(head, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        first = head.splitlines()[0] if head.splitlines() else ""
        counts = {d: first.count(d) for d in CSV_DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] > counts.get(default, 0) else default


def _from_csv(path: Path, delimiter: str) -> Extraction:
    q = Quality()
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
        q.notes.append(f"{path.name}: кодировка не UTF-8 — перекодирован из cp1251")
    delimiter = _sniff_delimiter(text, delimiter)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    q.tables_total = 1
    widths = {len(r) for r in rows if r}
    if len(widths) <= 1:
        q.tables_ok = 1
    else:
        q.suspicious.append(f"{path.name}: разное число колонок в строках ({min(widths)}–{max(widths)})")
    if rows and len(rows[0]) == 1 and any(d in rows[0][0] for d in CSV_DELIMITERS if d != delimiter):
        q.tables_ok = 0
        q.suspicious.append(f"{path.name}: одна колонка, но в шапке есть разделители — разделитель не распознан")
    return Extraction(f"# {path.stem}\n\n" + md_table(rows), q, "", path.suffix)


def _from_xlsx(path: Path) -> Extraction:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return Extraction(None, Quality(), "нужна библиотека openpyxl (pip install 'konveyer[xlsx]') или сохраните лист как .csv", ".xlsx")
    q = Quality()
    # не read_only: у ReadOnlyWorksheet нет merged_cells, а книга должна закрываться (иначе оригинал заперт на Windows)
    wb = openpyxl.load_workbook(str(path), data_only=True)
    try:
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
            q.tables_ok += 1
            if ws.merged_cells.ranges:
                q.suspicious.append(f"{path.name}/{ws.title}: объединённые ячейки ({len(ws.merged_cells.ranges)}) — "
                                    "значение стоит в первой ячейке диапазона, проверьте заголовки")
            out.append(f"\n## {ws.title}\n\n" + md_table(rows))
    finally:
        wb.close()
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
    is_json = path.suffix.lower() == ".json"
    parsers = [json.loads, yaml.safe_load] if is_json else [yaml.safe_load, json.loads]
    data = None
    first_error: Exception | None = None
    for parse in parsers:  # JSON с табуляцией не проходит как YAML и наоборот — пробуем оба разбора
        try:
            data = parse(text)
            first_error = None
            break
        except (ValueError, yaml.YAMLError) as e:
            first_error = first_error or e
    if first_error is not None:
        return Extraction(None, q, f"файл не разобран как {path.suffix[1:].upper()}: {first_error}", path.suffix)
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


def _by_suffix(path: Path, suffix: str) -> Extraction | None:
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
    return None


def extract(path: Path) -> Extraction:
    """Извлечение по расширению; неподдерживаемый формат — сырьё без извлечения (FR-ON-1).
    Повреждённый файл (битый .docx/.xlsx, обрезанный архив) — тоже «без извлечения» с причиной, а не сбой импорта:
    каждый файл получает запись в индексе либо с извлечением, либо с явной причиной отказа (5.8, приёмка 1)."""
    suffix = path.suffix.lower()
    if suffix in SUPPORTED and path.stat().st_size == 0:
        return Extraction(None, Quality(), "файл пуст (0 байт) — извлекать нечего", suffix)
    try:
        result = _by_suffix(path, suffix)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — библиотека формата упала на повреждённом файле: причина, не сбой импорта
        detail = str(e).replace(str(path), path.name).replace(str(path.parent), "…")[:120]  # без абсолютных путей (FR-SC-9)
        return Extraction(None, Quality(), f"файл не прочитан ({type(e).__name__}: {detail}) — "
                                           "конвертируйте его в .md/.docx и повторите импорт", suffix)
    if result is None:
        return Extraction(None, Quality(), f"формат {suffix or '(без расширения)'} не поддерживается: сохраните как .md/.docx/.xlsx", suffix)
    return result
