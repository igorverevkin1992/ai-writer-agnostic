"""Импорт материалов автора в сырьё (FR-ON-4…FR-ON-6, FR-ON-22): `konveyer импорт <путь>`.

`сырьё/оригиналы/<имя>` — копия исходника (никогда не меняется и не удаляется системой),
`сырьё/извлечено/<имя>.md` — извлечение, `сырьё/индекс.json` — записи `{файл, исходный_путь, хэш, формат,
извлечено_в, качество, дата_импорта, статус, тип?, документ_канона?}`. Повтор того же файла (тот же хэш) — без
дубликатов и без перезаписи (FR-ON-5). Кириллица, пробелы и вложенность в именах работают; слишком длинные имена
сокращаются с сохранением соответствия в индексе (FR-ON-6). Архивы .zip распаковываются во временную папку.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..paths import Workspace
from . import extract as extract_mod

INDEX = "индекс.json"
MAX_NAME = 80  # знаков в имени файла сырья (длинные пути Windows, FR-ON-6)
SKIP_DIRS = {".git", "__pycache__", ".DS_Store", "node_modules"}


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

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


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


def safe_name(rel: Path, taken: set[str]) -> str:
    """Имя копии в сырьё/оригиналы: путь → одно имя (папки через «__»), длинное — сокращается с хэшем хвоста."""
    parts = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", p).strip() for p in rel.parts]
    name = "__".join(p for p in parts if p)
    stem, suffix = (name.rsplit(".", 1) + [""])[:2] if "." in name else (name, "")
    suffix = f".{suffix}" if suffix else ""
    if len(name) > MAX_NAME:
        tail = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        stem = stem[: MAX_NAME - len(suffix) - 9] + "~" + tail
        name = stem + suffix
    base = name
    n = 2
    while name in taken:
        name = f"{stem}~{n}{suffix}"
        n += 1
    taken.add(name)
    return base if base == name else name


# ------------------------------------------------------------------ импорт


def _iter_files(src: Path) -> list[tuple[Path, Path]]:
    """[(абсолютный путь, относительный путь для имени)] — файлы папки/архива/одиночного файла."""
    if src.is_file():
        return [(src, Path(src.name))]
    out: list[tuple[Path, Path]] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(src).parts):
            continue
        out.append((p, p.relative_to(src)))
    return out


def import_path(ws: Workspace, source: Path, *, now: str | None = None) -> ImportReport:
    """Импорт файла, папки или .zip в сырьё проекта. Идемпотентен по хэшу (FR-ON-5)."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"источник не найден: {source}")
    report = ImportReport()
    entries = load_index(ws)
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    originals = raw_dir(ws) / "оригиналы"
    extracted = raw_dir(ws) / "извлечено"
    originals.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)
    taken = {e.файл for e in entries}
    by_hash = {e.хэш: e for e in entries}
    by_source = {e.исходный_путь: e for e in entries}

    tmp = None
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="konveyer_zip_"))
            with zipfile.ZipFile(source) as z:
                z.extractall(tmp)
            files = _iter_files(tmp)
            src_label = f"{source}!"
        else:
            files = _iter_files(source)
            src_label = ""
        for abs_path, rel in files:
            if abs_path.suffix.lower() == ".zip":
                continue  # вложенные архивы — отдельным импортом
            digest = sha256(abs_path)
            source_path = src_label + (rel.as_posix() if src_label else str(abs_path))
            if digest in by_hash:
                report.skipped.append(f"{rel.as_posix()} — уже импортирован как {by_hash[digest].файл}")
                continue
            previous = by_source.get(source_path)
            name = previous.файл if previous else safe_name(rel, taken)
            if previous:  # тот же источник, другое содержимое: новая версия рядом, прежняя остаётся (FR-ON-21)
                name = safe_name(Path(rel.stem + f"~{stamp[:10]}" + rel.suffix), taken)
            target = originals / name
            shutil.copyfile(abs_path, target)
            ext = extract_mod.extract(target)
            entry = RawEntry(файл=name, исходный_путь=source_path, хэш=digest, формат=abs_path.suffix.lower() or "(нет)",
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
            (report.changed if previous else report.added).append(entry)
            if previous:
                entry.причина = entry.причина or f"новая версия источника «{previous.файл}»"
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    # FR-ON-22: источник исчез — только пометка
    for e in entries:
        if not e.исходный_путь.endswith("!") and "!" not in e.исходный_путь:
            e.источник_исчез = not Path(e.исходный_путь).exists()
    report.index_path = save_index(ws, entries)
    return report
