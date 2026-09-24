"""Импорт материалов автора в сырьё (FR-ON-4…FR-ON-6, FR-ON-22): `konveyer импорт <путь>`.

`сырьё/оригиналы/<имя>` — копия исходника (никогда не меняется и не удаляется системой),
`сырьё/извлечено/<имя>.md` — извлечение, `сырьё/индекс.json` — записи `{файл, исходный_путь, хэш, формат,
извлечено_в, качество, дата_импорта, статус, тип?, документ_канона?}`. Повтор того же файла (тот же хэш) — без
дубликатов и без перезаписи (FR-ON-5). Кириллица, пробелы и вложенность в именах работают; слишком длинные имена
сокращаются с сохранением соответствия в индексе (FR-ON-6). Архивы .zip (в том числе вложенные в папку или в другой
архив) распаковываются во временную папку; имена из архивов Windows (cp866) перекодируются. Индекс сохраняется
даже при сбое на одном из файлов: у каждого файла либо извлечение, либо явная причина отказа (5.8, приёмка 1).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..paths import Workspace
from . import extract as extract_mod

INDEX = "индекс.json"
MAX_NAME = 80  # знаков в имени файла сырья (длинные пути Windows, FR-ON-6)
MAX_TARGET_PATH = 240  # полный путь копии в сырьё/оригиналы: лимит Windows 260 с запасом на «.md» извлечения
SKIP_DIRS = {".git", "__pycache__", ".DS_Store", "node_modules"}
ARCHIVE_SEP = "!"       # исходный_путь файла из архива: «архив.zip!папка/файл.md»
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class RawEntry:
    файл: str                       # имя в сырьё/оригиналы/
    исходный_путь: str
    хэш: str
    формат: str
    извлечено_в: str | None = None  # сырьё/извлечено/<имя>.md
    качество: dict = field(default_factory=dict)
    дата_импорта: str = ""
    статус: str = "сырьё"           # сырьё | в_каноне | отклонено | заменён (новой версией источника)
    тип: str | None = None
    документ_канона: str | None = None
    причина: str = ""               # почему извлечения нет / отказ
    источник_исчез: bool = False    # FR-ON-22
    хэш_извлечения: str | None = None  # для трёхстороннего сравнения при повторном импорте (FR-ON-21)
    документы_канона: list[str] = field(default_factory=list)  # все части при разбиении (FR-ON-9); документ_канона — первая

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @property
    def from_archive(self) -> bool:
        return ARCHIVE_SEP in self.исходный_путь


@dataclass
class ImportReport:
    added: list[RawEntry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # уже импортированы (тот же хэш)
    changed: list[RawEntry] = field(default_factory=list)  # тот же исходный путь, новый хэш (повторный импорт)
    rejected: list[RawEntry] = field(default_factory=list) # без извлечения
    index_path: Path | None = None


# ------------------------------------------------------------------ индекс


def raw_dir(ws: Workspace) -> Path:
    return ws.root / "сырьё"


def index_path(ws: Workspace) -> Path:
    return raw_dir(ws) / INDEX


def load_index(ws: Workspace) -> list[RawEntry]:
    p = index_path(ws)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [RawEntry(**{k: v for k, v in d.items() if k in RawEntry.__dataclass_fields__}) for d in data]


def save_index(ws: Workspace, entries: list[RawEntry]) -> Path:
    p = index_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([e.as_dict() for e in entries], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def entry_by_name(entries: list[RawEntry], name: str) -> RawEntry | None:
    return next((e for e in entries if e.файл == name), None)


# ------------------------------------------------------------------ имена


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(rel: Path, taken: set[str], max_len: int = MAX_NAME) -> str:
    """Имя копии в сырьё/оригиналы: путь → одно имя (папки через «__»), длинное — сокращается с хэшем хвоста.
    Имя приводится к NFC (macOS отдаёт NFD — иначе одно и то же имя даёт разные записи), зарезервированные имена
    Windows (CON, PRN…) и точки/пробелы на конце экранируются (FR-ON-6)."""
    parts = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", unicodedata.normalize("NFC", p)).strip(" .") for p in rel.parts]
    name = "__".join(p for p in parts if p) or "файл"
    stem, suffix = (name.rsplit(".", 1) + [""])[:2] if "." in name else (name, "")
    suffix = f".{suffix}" if suffix else ""
    if stem.lower() in WINDOWS_RESERVED:
        stem = f"{stem}_"
        name = stem + suffix
    max_len = max(24, min(max_len, MAX_NAME))
    if len(name) > max_len:
        tail = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        stem = stem[: max_len - len(suffix) - 9] + "~" + tail
        name = stem + suffix
    base = name
    n = 2
    while name in taken:
        name = f"{stem}~{n}{suffix}"
        n += 1
    taken.add(name)
    return base if base == name else name


# ------------------------------------------------------------------ импорт


@dataclass
class _Found:
    """Файл к импорту: где лежит сейчас, относительный путь для имени, исходный путь для индекса, причина отказа."""
    path: Path | None
    rel: Path
    source: str
    reason: str = ""


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Имя записи архива: без бита UTF-8 (0x800) zipfile читает его как cp437 — архивы Проводника Windows и WinRAR
    с кириллицей на самом деле в cp866 (реже cp1251); перекодируем."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("cp866", "cp1251"):
        try:
            decoded = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if not re.search(r"[\x00-\x1f\x7f-\x9f]", decoded):
            return decoded
    return name


def _unpack_zip(archive: Path, into: Path) -> list[tuple[Path, Path]]:
    """Распаковка архива во временную папку с перекодировкой имён и защитой от выхода за её пределы:
    [(распакованный файл, относительный путь внутри архива)]."""
    out: list[tuple[Path, Path]] = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = Path(*[p for p in Path(_decode_zip_name(info).replace("\\", "/")).parts if p not in ("..", "/", "")])
            if not rel.parts:
                continue
            target = into / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append((target, rel))
    return out


def _iter_files(src: Path) -> list[tuple[Path, Path]]:
    """[(абсолютный путь, относительный путь для имени)] — файлы папки/архива/одиночного файла."""
    if src.is_file():
        return [(src, Path(src.name))]
    out: list[tuple[Path, Path]] = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_symlink() and not p.exists():
            out.append((p, rel))  # битая ссылка: регистрируется с причиной, а не пропускается молча
            continue
        if not p.is_file():
            continue
        out.append((p, rel))
    return out


def _collect(source: Path, tmp_root: Path, label: str, rel_base: Path | None, depth: int) -> list[_Found]:
    """Файлы источника (папка/файл/архив) с рекурсивной распаковкой вложенных архивов (FR-ON-1)."""
    found: list[_Found] = []
    for abs_path, rel in _iter_files(source):
        rel = (rel_base / rel) if rel_base else rel
        src_label = f"{label}{rel.as_posix()}" if label else str(abs_path)
        if abs_path.is_symlink() and not abs_path.exists():
            found.append(_Found(None, rel, src_label, "битая символическая ссылка — файла по ссылке нет"))
            continue
        if abs_path.suffix.lower() == ".zip":
            if depth >= 3:
                found.append(_Found(None, rel, src_label, "архив вложен слишком глубоко — распакуйте и импортируйте папку"))
                continue
            inner = Path(tempfile.mkdtemp(prefix="konveyer_zip_", dir=tmp_root))
            try:
                _unpack_zip(abs_path, inner)
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                found.append(_Found(None, rel, src_label, f"архив не распакован ({type(e).__name__}) — распакуйте его вручную"))
                continue
            # сам источник — архив: имена без префикса; архив внутри папки/архива — с префиксом его пути
            base = None if (depth == 0 and source.is_file()) else rel.with_suffix("")
            found += _collect(inner, tmp_root, f"{src_label}{ARCHIVE_SEP}", base, depth + 1)
            continue
        found.append(_Found(abs_path, rel, src_label))
    return found


def _import_one(ws: Workspace, f: _Found, entries: list[RawEntry], report: ImportReport, taken: set[str],
                by_hash: dict[str, RawEntry], by_source: dict[str, RawEntry], stamp: str) -> None:
    originals = raw_dir(ws) / "оригиналы"
    extracted = raw_dir(ws) / "извлечено"
    if f.path is None:
        if f.source in by_source:
            report.skipped.append(f"{f.rel.as_posix()} — уже зарегистрирован как {by_source[f.source].файл} (без извлечения)")
            return
        entry = RawEntry(файл=safe_name(f.rel, taken), исходный_путь=f.source, хэш="", формат=f.rel.suffix.lower() or "(нет)",
                         дата_импорта=stamp, причина=f.reason)
        entries.append(entry)
        by_source[f.source] = entry
        report.rejected.append(entry)
        report.added.append(entry)
        return
    digest = sha256(f.path)
    if digest in by_hash:
        report.skipped.append(f"{f.rel.as_posix()} — уже импортирован как {by_hash[digest].файл}")
        return
    previous = by_source.get(f.source)
    max_len = MAX_TARGET_PATH - len(str(originals)) - 1
    if previous:  # тот же источник, другое содержимое: новая версия рядом, прежняя остаётся (FR-ON-21)
        name = safe_name(Path(f.rel.stem + f"~{stamp[:10]}" + f.rel.suffix), taken, max_len)
    else:
        name = safe_name(f.rel, taken, max_len)
    target = originals / name
    shutil.copyfile(f.path, target)
    try:
        ext = extract_mod.extract(target)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — оригинал уже скопирован: запись с причиной, а не сирота без индекса
        ext = extract_mod.Extraction(None, extract_mod.Quality(),
                                     f"извлечение прервано ({type(e).__name__}: {str(e)[:100]}) — конвертируйте в .md/.docx", f.rel.suffix)
    entry = RawEntry(файл=name, исходный_путь=f.source, хэш=digest, формат=f.rel.suffix.lower() or "(нет)",
                     дата_импорта=stamp, качество=ext.quality.as_dict())
    if ext.markdown is not None:
        md_name = name if name.lower().endswith(".md") else name + ".md"
        (extracted / md_name).write_text(ext.markdown, encoding="utf-8")
        entry.извлечено_в = f"сырьё/извлечено/{md_name}"
        entry.хэш_извлечения = hashlib.sha256(ext.markdown.encode("utf-8")).hexdigest()
    else:
        entry.причина = ext.reason
        report.rejected.append(entry)
    entries.append(entry)
    by_hash[digest] = entry
    by_source[f.source] = entry
    (report.changed if previous else report.added).append(entry)
    if previous:
        entry.причина = entry.причина or f"новая версия источника «{previous.файл}»"
        if previous.статус == "сырьё":
            # прежняя версия ещё не применялась — её вытесняет новая, иначе в канон ушли бы два документа (FR-ON-5)
            previous.статус = "заменён"
            previous.причина = f"заменён новой версией {name} (не был применён)"


def import_path(ws: Workspace, source: Path, *, now: str | None = None) -> ImportReport:
    """Импорт файла, папки или .zip в сырьё проекта. Идемпотентен по хэшу (FR-ON-5). Индекс сохраняется и при сбое
    на одном из файлов (уже скопированные оригиналы не остаются сиротами)."""
    source = Path(source).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"источник не найден: {source}")
    source = source.resolve()  # относительный путь в индексе зависел бы от текущей папки (FR-ON-21/22)
    report = ImportReport()
    entries = load_index(ws)
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    (raw_dir(ws) / "оригиналы").mkdir(parents=True, exist_ok=True)
    (raw_dir(ws) / "извлечено").mkdir(parents=True, exist_ok=True)
    taken = {e.файл for e in entries}
    by_hash = {e.хэш: e for e in entries if e.хэш}
    by_source = {e.исходный_путь: e for e in entries}

    tmp = Path(tempfile.mkdtemp(prefix="konveyer_import_"))
    try:
        files = _collect(source, tmp, "", None, 0)
        for f in files:
            _import_one(ws, f, entries, report, taken, by_hash, by_source, stamp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # FR-ON-22: источник исчез — только пометка; файлы из архивов проверяются по самому архиву
        for e in entries:
            outer = e.исходный_путь.split(ARCHIVE_SEP, 1)[0]
            e.источник_исчез = bool(outer) and not Path(outer).exists()
        report.index_path = save_index(ws, entries)
    return report
