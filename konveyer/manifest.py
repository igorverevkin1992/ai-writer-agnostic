"""Манифест проекта `проект.yaml` (FR-MF-1…FR-MF-4): паспорт серии, модули, методики, карта библиотеки.

Карта библиотеки — единственное место, связывающее файлы канона с типами документов каталога; движок не знает
ни одного имени документа серии (П-1). Без манифеста (старая рабочая область, тесты) карта выводится машинным
слоем классификации по сигнатурам типов (`infer`) — и это же предложение видит автор при онбординге.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import catalog, guard

SCHEMA_VERSION = 1
FILENAME = "проект.yaml"
VOLUME_MARK_RE = re.compile(r"(?:Том|_Т)\s*0*(\d+)(?!\d)")
ON = ("вкл", "да", "on", "true", "1")


class Passport(BaseModel):
    имя: str = "Серия"
    язык_прозы: str = "ru"
    томов_план: int = 1
    текущий_том: int = 1
    единица_объёма: str = "слова"
    профиль: str = ""

    @field_validator("текущий_том", "томов_план")
    @classmethod
    def _positive(cls, v: int) -> int:
        if int(v) < 1:
            raise ValueError("номер тома — целое число ≥ 1")
        return int(v)


class Methodics(BaseModel):
    серия: str | list[str] | None = None
    том: str | list[str] | None = None
    акт: str | list[str] | None = None
    глава: str | list[str] | None = None
    обязательные_шаги: dict[str, list[int]] = Field(default_factory=dict)

    def for_level(self, level: str) -> list[str]:
        v = getattr(self, level, None)
        if not v:
            return []
        return [v] if isinstance(v, str) else list(v)


class LibraryEntry(BaseModel):
    файл: str
    тип: str
    том: int | None = None
    множественность: str | None = None
    колонки: dict[str, Any] = Field(default_factory=dict)   # сопоставление колонок (FR-ON-13, FR-DT-5)
    секции: dict[str, Any] = Field(default_factory=dict)
    синонимы: dict[str, Any] = Field(default_factory=dict)
    выключен: bool = False
    источник: str = ""        # сырьё, из которого документ получен (FR-ON-18)
    уверенность: float | None = None
    авто: bool = False        # запись выведена классификацией (документа нет в проект.yaml) — доктор предупреждает

    @property
    def is_folder(self) -> bool:
        return self.файл.endswith("/") or self.множественность == "папка"


class Manifest(BaseModel):
    версия_схемы: int = SCHEMA_VERSION
    проект: Passport = Passport()
    модули: dict[str, str] = Field(default_factory=dict)
    методики: Methodics = Methodics()
    библиотека: list[LibraryEntry] = Field(default_factory=list)
    выведен: bool = False  # карта выведена классификацией, а не подтверждена автором

    # -------------------------------------------------------------- модули

    def module_enabled(self, name: str, modules: dict[str, catalog.ModuleSpec] | None = None) -> bool:
        if modules and name in modules and modules[name].base:
            return True
        v = self.модули.get(name)
        if v is None:
            return False
        return str(v).strip().lower() in ON

    def enabled_modules(self, modules: dict[str, catalog.ModuleSpec]) -> set[str]:
        return {m for m in modules if self.module_enabled(m, modules)}

    # -------------------------------------------------------------- карта

    def entries_of_type(self, тип: str) -> list[LibraryEntry]:
        return [e for e in self.библиотека if e.тип == тип and not e.выключен]

    def entry_for(self, rel: str) -> LibraryEntry | None:
        rel = rel.replace("\\", "/")
        for e in self.библиотека:
            f = e.файл.rstrip("/")
            if rel == f or (e.is_folder and rel.startswith(f + "/")):
                return e
        return None

    def docs(self, library: Path, тип: str, volume: int | None = None, types: dict | None = None) -> list[Path]:
        """Документы типа для тома `volume` (FR-EX-4): запись с `том: N` — только тому N; запись без тома —
        общесерийная (для потомных типов без маркера тома в имени — том 1, совместимость)."""
        spec = (types or {}).get(тип)
        per_volume = bool(spec and spec.per_volume)
        out: list[Path] = []
        for e in self.entries_of_type(тип):
            paths = _expand(library, e)
            for p in paths:
                vol = e.том if e.том is not None else doc_volume(p)
                if volume is not None and vol is not None and vol != volume:
                    continue
                if volume is not None and vol is None and per_volume and volume >= 2:
                    continue  # потомный документ без тома — том 1
                out.append(p)
        return sorted(dict.fromkeys(out))

    def missing_types(self, library: Path, types: dict[str, catalog.TypeSpec], modules: dict[str, catalog.ModuleSpec],
                      volume: int | None) -> dict[str, list[str]]:
        """Чего не хватает по включённым модулям: {модуль: [типы без документа]} (FR-ON-19 п. 3, FR-ON-20)."""
        out: dict[str, list[str]] = {}
        for m in modules.values():
            if not (m.base or self.module_enabled(m.name, modules)):
                continue
            absent = [t for t in m.requires_types if not self.docs(library, t, volume, types)]
            if absent:
                out[m.name] = absent
        return out


def _expand(library: Path, e: LibraryEntry) -> list[Path]:
    target = library / e.файл.rstrip("/")
    if e.is_folder:
        return sorted(p for p in target.glob("*.md") if p.is_file()) if target.is_dir() else []
    if "*" in e.файл:
        return sorted(p for p in library.glob(e.файл) if p.is_file())
    return [target] if target.is_file() else []


def doc_volume(path: Path) -> int | None:
    """Том по маркеру в имени документа (`Том2`, `_Т2`); None — маркера нет."""
    m = VOLUME_MARK_RE.search(path.stem)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ загрузка и запись


def path_of(root: Path) -> Path:
    return root / FILENAME


def load(root: Path) -> Manifest | None:
    path = path_of(root)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{FILENAME}: ожидался YAML-словарь с блоками проект/модули/методики/библиотека")
    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"{FILENAME} не проходит проверку: " + "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )) from None


def dump(manifest: Manifest) -> str:
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
    data.pop("выведен", None)
    for e in data.get("библиотека", []):
        for k in ("колонки", "секции", "синонимы"):
            if not e.get(k):
                e.pop(k, None)
        for k in ("выключен", "авто"):
            if not e.get(k):
                e.pop(k, None)
        if not e.get("источник"):
            e.pop("источник", None)
        if e.get("уверенность") is None:
            e.pop("уверенность", None)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def save(root: Path, manifest: Manifest) -> Path:
    path = path_of(root)
    guard.write_text(path, dump(manifest))
    return path


def set_volume(root: Path, volume: int) -> None:
    """Переключение текущего тома в манифесте (правится только строка `текущий_том`)."""
    path = path_of(root)
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"^(\s*текущий_том\s*:).*$", rf"\1 {int(volume)}", text, count=1, flags=re.M)
    if new == text:
        m = load(root) or Manifest()
        m.проект.текущий_том = int(volume)
        new = dump(m)
    guard.write_text(path, new)


# ------------------------------------------------------------------ валидация (FR-MF-2)


def _line_of(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return i
    return None


def validate(manifest: Manifest, library: Path, types: dict[str, catalog.TypeSpec],
             modules: dict[str, catalog.ModuleSpec], text: str = "") -> list[str]:
    """Ошибки манифеста по-русски с номером строки: неизвестный тип, отсутствующий файл, включённый модуль без
    требуемого типа, незнакомый модуль, том вне плана."""
    errors: list[str] = []

    def where(needle: str) -> str:
        n = _line_of(text, needle) if text else None
        return f"{FILENAME}:{n}: " if n else f"{FILENAME}: "

    for e in manifest.библиотека:
        if e.тип not in types:
            errors.append(f"{where(e.файл)}«{e.файл}»: неизвестный тип «{e.тип}»; доступные: {', '.join(sorted(types))}")
        if not e.выключен and not _expand(library, e) and not (e.is_folder and (library / e.файл.rstrip("/")).is_dir()):
            errors.append(f"{where(e.файл)}«{e.файл}»: файла нет в библиотеке ({library.name}/)")
        if e.множественность and e.множественность not in catalog.MULTIPLICITY:
            errors.append(f"{where(e.файл)}«{e.файл}»: множественность «{e.множественность}» — "
                          f"допустимо {', '.join(catalog.MULTIPLICITY)}")
    for name in manifest.модули:
        if name not in modules:
            errors.append(f"{where(name + ':')}модуль «{name}» неизвестен; доступные: {', '.join(sorted(modules))}")
    for name, absent in manifest.missing_types(library, types, modules, None).items():
        errors.append(f"{where(name + ':')}модуль «{name}» включён, но в библиотеке нет ни одного документа типа: "
                      f"{', '.join(absent)} — добавьте документ или выключите модуль")
    if manifest.проект.текущий_том > manifest.проект.томов_план:
        errors.append(f"{where('текущий_том')}текущий том {manifest.проект.текущий_том} больше плана "
                      f"({manifest.проект.томов_план} т.)")
    return errors


# ------------------------------------------------------------------ вывод карты без манифеста


def infer(library: Path, types: dict[str, catalog.TypeSpec], threshold: float = 0.35) -> Manifest:
    """Карта библиотеки машинным слоем классификации (FR-ON-7): для каждого документа — тип с наибольшим
    весом сигнатур. Модули включаются по наличию документов их типов."""
    from .onboarding import classify

    entries: list[LibraryEntry] = []
    folders: dict[str, tuple[str, float]] = {}
    if not library.is_dir():
        return Manifest(выведен=True)
    for path in sorted(library.rglob("*.md")):
        rel = path.relative_to(library).as_posix()
        if rel.startswith(("ИНСТРУМЕНТ_", "ТЗ_", "КОНВЕЙЕР_")) or "/Тест_" in "/" + rel:
            continue
        hyps = classify.classify_file(path, types)
        if not hyps or hyps[0].confidence < threshold:
            continue
        best = hyps[0]
        spec = types[best.type]
        if spec.multiplicity == "папка" and "/" in rel:
            folder = rel.rsplit("/", 1)[0] + "/"
            prev = folders.get(folder)
            if prev is None or prev[1] < best.confidence:
                folders[folder] = (best.type, best.confidence)
            continue
        entries.append(LibraryEntry(файл=rel, тип=best.type, том=doc_volume(path) if spec.per_volume else None,
                                    уверенность=round(best.confidence, 2)))
    for folder, (t, conf) in folders.items():
        entries.append(LibraryEntry(файл=folder, тип=t, множественность="папка", уверенность=round(conf, 2)))
    present = {e.тип for e in entries}
    modules = catalog.load_modules()
    enabled = {m.name: "вкл" for m in modules.values() if not m.base and all(t in present for t in m.requires_types)}
    volumes = [e.том for e in entries if e.том]
    passport = Passport(имя=library.parent.name if library.name == "Библиотека" else library.name,
                        томов_план=max(volumes) if volumes else 1)
    return Manifest(проект=passport, модули=enabled, библиотека=sorted(entries, key=lambda e: e.файл), выведен=True)


_EFFECTIVE_CACHE: dict[str, tuple[tuple, Manifest]] = {}


def _library_stamp(library: Path) -> tuple:
    if not library.is_dir():
        return ()
    return tuple(sorted((p.relative_to(library).as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
                        for p in library.rglob("*.md")))


AUTO_THRESHOLD = 0.5


def unmapped(manifest: Manifest, library: Path) -> list[str]:
    """Документы библиотеки, которых нет в карте манифеста (служебные — не считаются)."""
    out: list[str] = []
    if not library.is_dir():
        return out
    for path in sorted(library.rglob("*.md")):
        rel = path.relative_to(library).as_posix()
        if rel.startswith(("ИНСТРУМЕНТ_", "ТЗ_", "КОНВЕЙЕР_")) or "/Тест_" in "/" + rel or rel.startswith("Тест_"):
            continue
        if manifest.entry_for(rel) is None:
            out.append(rel)
    return out


def with_auto_entries(manifest: Manifest, library: Path, types: dict[str, catalog.TypeSpec]) -> Manifest:
    """Манифест + документы, положенные в библиотеку мимо манифеста: классифицируются и добавляются с пометкой
    `авто` (сценарий Г: автор правит библиотеку руками); доктор перечисляет их и предлагает закрепить."""
    missing = unmapped(manifest, library)
    if not missing:
        return manifest
    inferred = infer(library, types, threshold=AUTO_THRESHOLD)
    extra = [e.model_copy(update={"авто": True}) for e in inferred.библиотека
             if manifest.entry_for(e.файл.rstrip("/")) is None and (
                 e.файл in missing or (e.is_folder and any(m.startswith(e.файл) for m in missing)))]
    if not extra:
        return manifest
    return manifest.model_copy(update={"библиотека": [*manifest.библиотека, *extra]})


def effective(root: Path, library: Path, types: dict[str, catalog.TypeSpec] | None = None) -> Manifest:
    """Манифест проекта: `проект.yaml` (+ авто-записи для документов вне карты), а без него — выведенная карта.
    Кэш по составу библиотеки и манифесту."""
    types = types or catalog.load_types(root)
    key = str(library.resolve()) + "|" + str(root.resolve())
    mpath = path_of(root)
    mstamp = (mpath.stat().st_mtime_ns, mpath.stat().st_size) if mpath.exists() else None
    stamp = (_library_stamp(library), mstamp)
    cached = _EFFECTIVE_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    m = load(root)
    result = with_auto_entries(m, library, types) if m is not None else infer(library, types)
    _EFFECTIVE_CACHE[key] = (stamp, result)
    return result


# ------------------------------------------------------------------ миграция схемы (FR-MF-4, NFR-11)


def migrate(root: Path) -> tuple[bool, str]:
    """Поднимает манифест до текущей версии схемы, сохранив копию прежнего. (False, причина) — миграция не нужна."""
    path = path_of(root)
    if not path.exists():
        return False, "манифеста нет"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = int(data.get("версия_схемы", 0) or 0)
    if version >= SCHEMA_VERSION:
        return False, f"схема уже версии {version}"
    backup = root / f"проект.v{version}.{datetime.now().strftime('%Y%m%d%H%M%S')}.yaml"
    guard.write_text(backup, path.read_text(encoding="utf-8"))
    data["версия_схемы"] = SCHEMA_VERSION
    data.setdefault("проект", {})
    data.setdefault("модули", {})
    data.setdefault("методики", {})
    data.setdefault("библиотека", [])
    guard.write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return True, f"манифест поднят до версии {SCHEMA_VERSION}; копия прежнего — {backup.name}"
