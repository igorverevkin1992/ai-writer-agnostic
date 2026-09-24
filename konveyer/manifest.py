"""Манифест проекта `проект.yaml` (FR-MF-1…FR-MF-4): паспорт серии, модули, методики, карта библиотеки.

Карта библиотеки — единственное место, связывающее файлы канона с типами документов каталога; движок не знает
ни одного имени документа серии (П-1). Без манифеста (старая рабочая область, тесты) карта выводится машинным
слоем классификации по сигнатурам типов (`infer`) — и это же предложение видит автор при онбординге.
Служебные файлы библиотеки (черновики инструментов, ТЗ, тестовые промпты) объявляются в манифесте блоком
`служебные:` (маски имён) — движок не знает соглашений именования ни одной серии.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import catalog, guard

SCHEMA_VERSION = 1
FILENAME = "проект.yaml"
# маркер тома в имени документа: «Том2», «Том_2», «том2», «_Т2», «_T2» (латинское T), «Том 02»; «Фантом_2» — не маркер
VOLUME_MARK_RE = re.compile(r"(?:(?<![А-Яа-яЁёA-Za-z])[Тт]ом|_[ТтT])[\s_]*0*(\d+)(?!\d)", re.IGNORECASE)
ON = ("вкл", "да", "on", "true", "1", "yes")
OFF = ("выкл", "нет", "off", "false", "0", "no", "")

# переводы типовых сообщений схемы (FR-MF-3: валидатор объясняет ошибку по-русски)
_MESSAGES = {
    "int_parsing": "ожидается целое число", "int_type": "ожидается целое число", "int_from_float": "ожидается целое число",
    "string_type": "ожидается строка", "bool_type": "ожидается да/нет", "bool_parsing": "ожидается да/нет",
    "float_parsing": "ожидается число", "float_type": "ожидается число", "dict_type": "ожидается словарь «ключ: значение»",
    "model_type": "ожидается блок «ключ: значение»", "list_type": "ожидается список", "missing": "обязательное поле не задано",
    "extra_forbidden": "неизвестный ключ (опечатка?)", "none_required": "поле должно быть пустым",
    "literal_error": "недопустимое значение", "union_tag_invalid": "недопустимое значение",
}


def _message(err: dict) -> str:
    msg = _MESSAGES.get(err.get("type", ""))
    if msg:
        return msg
    text = str(err.get("msg", ""))
    for prefix in ("Value error, ", "Assertion failed, "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


class Passport(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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

    def names(self) -> set[str]:
        return {n for lvl in ("серия", "том", "акт", "глава") for n in self.for_level(lvl)}


class LibraryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    @property
    def is_mask(self) -> bool:
        return "*" in self.файл or "?" in self.файл


def normalize_switch(name: str, value: Any) -> str:
    """Значение переключателя модуля → «вкл»/«выкл»: YAML превращает on/true/yes в bool, 1 — в int, всё это допустимо."""
    if isinstance(value, bool):
        return "вкл" if value else "выкл"
    if isinstance(value, (int, float)):
        return "вкл" if value else "выкл"
    if value is None:
        return "выкл"
    v = str(value).strip().lower()
    if v in ON:
        return "вкл"
    if v in OFF:
        return "выкл"
    raise ValueError(f"модуль «{name}»: «{value}» — допустимо вкл/выкл (да/нет)")


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    версия_схемы: int = SCHEMA_VERSION
    проект: Passport = Passport()
    модули: dict[str, str] = Field(default_factory=dict)
    методики: Methodics = Methodics()
    библиотека: list[LibraryEntry] = Field(default_factory=list)
    служебные: list[str] = Field(default_factory=list)  # маски файлов библиотеки, не входящих в карту (ТЗ, инструменты)
    выведен: bool = False  # карта выведена классификацией, а не подтверждена автором

    @field_validator("модули", mode="before")
    @classmethod
    def _switches(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("ожидается словарь «модуль: вкл/выкл»")
        return {str(k): normalize_switch(str(k), val) for k, val in v.items()}

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

    def present_types(self) -> set[str]:
        """Типы, для которых в карте есть хотя бы одна запись (не выключенная)."""
        return {e.тип for e in self.библиотека if not e.выключен}

    def entry_for(self, rel: str) -> LibraryEntry | None:
        """Запись карты для документа: точное имя, маска (`31_Матрица*.md`) или папка (только прямые потомки —
        документ во вложенной папке считается вне карты, чтобы не потеряться молча)."""
        rel = rel.replace("\\", "/")
        for e in self.библиотека:
            f = e.файл.rstrip("/")
            if e.is_folder:
                if "/" in rel and rel.rsplit("/", 1)[0] == f:
                    return e
            elif e.is_mask:
                if fnmatch.fnmatchcase(rel, e.файл):
                    return e
            elif rel == f:
                return e
        return None

    def is_service(self, rel: str) -> bool:
        return is_excluded(rel, self.служебные)

    def docs(self, library: Path, тип: str, volume: int | None = None, types: dict | None = None) -> list[Path]:
        """Документы типа для тома `volume` (FR-EX-4): запись с `том: N` — только тому N; запись без тома и без маркера
        в имени — общесерийная. Для потомных типов (`по_тому`) такой документ относится к тому 1 (совместимость со
        старыми библиотеками); при плане в несколько томов валидатор требует указать `том:`."""
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


def is_excluded(rel: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """Путь относится к служебным: маска совпадает с ним или с любой из его родительских папок."""
    if not patterns:
        return False
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return any(fnmatch.fnmatchcase(c, p.rstrip("/")) for p in patterns for c in candidates)


def _expand(library: Path, e: LibraryEntry) -> list[Path]:
    target = library / e.файл.rstrip("/")
    if e.is_folder:
        return sorted(p for p in target.glob("*.md") if p.is_file()) if target.is_dir() else []
    if e.is_mask:
        return sorted(p for p in library.glob(e.файл) if p.is_file())
    return [target] if target.is_file() else []


def doc_volume(path: Path) -> int | None:
    """Том по маркеру в имени документа (`Том2`, `Том_2`, `_Т2`, `_T2`); None — маркера нет."""
    m = VOLUME_MARK_RE.search(path.stem)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ загрузка и запись


def path_of(root: Path) -> Path:
    return root / FILENAME


def _line_of_loc(text: str, loc: tuple) -> int | None:
    """Строка манифеста для пути ошибки схемы: («библиотека», 3, «том») — ключ внутри четвёртой записи карты,
    («проект», «томов_план») — первая строка с этим ключом."""
    if not text or not loc:
        return None
    lines = text.splitlines()
    start = 0
    keys = [x for x in loc if isinstance(x, str)]
    idx = next((x for x in loc if isinstance(x, int)), None)
    if idx is not None and keys and keys[0] == "библиотека":
        block = next((i for i, ln in enumerate(lines) if re.match(r"^библиотека\s*:", ln)), None)
        if block is not None:
            items = [i for i in range(block + 1, len(lines)) if re.match(r"^\s*-\s", lines[i])]
            if idx < len(items):
                start = items[idx]
                if len(keys) == 1:
                    return start + 1
        keys = keys[1:]
    key = keys[-1] if keys else None
    if key is None:
        return None
    for i in range(start, len(lines)):
        if re.match(rf"^\s*-?\s*{re.escape(key)}\s*:", lines[i]):
            return i + 1
    return None


def _validation_message(e: ValidationError, text: str) -> str:
    parts = []
    for err in e.errors():
        loc = tuple(err.get("loc", ()))
        n = _line_of_loc(text, loc)
        where = ".".join(str(x) for x in loc)
        parts.append(f"{FILENAME}:{n}: {where}: {_message(err)}" if n else f"{where}: {_message(err)}")
    return "; ".join(parts)


def load(root: Path) -> Manifest | None:
    """Манифест проекта; нет файла — None. Ошибки — ValueError по-русски с номером строки (FR-MF-3)."""
    path = path_of(root)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    data = catalog.load_yaml(path) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{FILENAME}: ожидался YAML-словарь с блоками проект/модули/методики/библиотека")
    version = data.get("версия_схемы")
    if isinstance(version, int) and version > SCHEMA_VERSION:
        raise ValueError(f"{FILENAME}: версия схемы {version} новее, чем у движка ({SCHEMA_VERSION}) — обновите движок")
    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"{FILENAME} не проходит проверку: " + _validation_message(e, text)) from None


def dump(manifest: Manifest) -> str:
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
    data.pop("выведен", None)
    if not data.get("служебные"):
        data.pop("служебные", None)
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


def _entry_line(text: str, e: LibraryEntry) -> int | None:
    """Строка записи карты: «файл: <имя>» (в кавычках или без), а не первое вхождение подстроки."""
    if not text:
        return None
    rx = re.compile(r"^\s*-?\s*файл\s*:\s*['\"]?" + re.escape(e.файл) + r"['\"]?\s*$")
    for i, line in enumerate(text.splitlines(), start=1):
        if rx.match(line):
            return i
    return _line_of(text, e.файл)


def type_fields(spec: catalog.TypeSpec) -> set[str]:
    """Поля, которые можно сопоставить в манифесте (`колонки`/`секции`/`синонимы`): колонки таблиц, ключи секций,
    поля документа-секций, ключ и номер широкой таблицы."""
    fields: set[str] = set()
    for ext in spec.extractions:
        for fmt in ext.get("форматы") or []:
            for key in ("колонки", "ключи", "поля"):
                fields |= set((fmt.get(key) or {}).keys())
            if fmt.get("вид") == "широкая_таблица":
                fields |= {"ключ", "номер"}
    return fields


def validate(manifest: Manifest, library: Path, types: dict[str, catalog.TypeSpec],
             modules: dict[str, catalog.ModuleSpec], text: str = "",
             methodics: set[str] | None = None) -> list[str]:
    """Ошибки манифеста по-русски с номером строки: неизвестный тип, отсутствующий файл, включённый модуль без
    требуемого типа, незнакомый модуль, том вне плана, дубли файлов, несогласованный том, сопоставление неизвестных
    полей, незнакомая методика (`methodics` — имена доступных методик; None — не проверять)."""
    errors: list[str] = []
    plan = manifest.проект.томов_план

    def where(needle: str) -> str:
        n = _line_of(text, needle) if text else None
        return f"{FILENAME}:{n}: " if n else f"{FILENAME}: "

    def at(e: LibraryEntry) -> str:
        n = _entry_line(text, e)
        return f"{FILENAME}:{n}: " if n else f"{FILENAME}: "

    if manifest.версия_схемы > SCHEMA_VERSION:
        errors.append(f"{where('версия_схемы')}версия схемы {manifest.версия_схемы} новее, чем у движка ({SCHEMA_VERSION}) — "
                      "обновите движок")
    seen: dict[str, LibraryEntry] = {}
    for e in manifest.библиотека:
        spec = types.get(e.тип)
        if spec is None:
            errors.append(f"{at(e)}«{e.файл}»: неизвестный тип «{e.тип}»; доступные: {', '.join(sorted(types))}")
        if not e.выключен and not _expand(library, e) and not (e.is_folder and (library / e.файл.rstrip("/")).is_dir()):
            errors.append(f"{at(e)}«{e.файл}»: файла нет в библиотеке ({library.name}/)")
        if e.множественность and e.множественность not in catalog.MULTIPLICITY:
            errors.append(f"{at(e)}«{e.файл}»: множественность «{e.множественность}» — "
                          f"допустимо {', '.join(catalog.MULTIPLICITY)}")
        if e.файл in seen and not e.выключен and not seen[e.файл].выключен:
            errors.append(f"{at(e)}«{e.файл}»: файл уже есть в карте (тип «{seen[e.файл].тип}») — документ разбирался бы дважды")
        seen.setdefault(e.файл, e)
        if e.том is not None and e.том > plan:
            errors.append(f"{at(e)}«{e.файл}»: том {e.том} больше плана ({plan} т.) — поправьте «том:» или «томов_план»")
        marked = doc_volume(Path(e.файл.rstrip("/"))) if not e.is_folder and not e.is_mask else None
        if e.том is not None and marked is not None and marked != e.том:
            errors.append(f"{at(e)}«{e.файл}»: в манифесте «том: {e.том}», а по имени файла — том {marked}; "
                          "оставьте одно из двух")
        if spec is not None and spec.per_volume and not e.выключен and e.том is None and marked is None and plan >= 2 \
                and not e.is_folder and not e.is_mask:
            errors.append(f"{at(e)}«{e.файл}»: тип «{e.тип}» потомный, а том не задан ни в манифесте, ни маркером в имени "
                          f"(Том2, _Т2) — при плане в {plan} т. укажите «том: N»")
        if spec is not None:
            allowed = type_fields(spec)
            for block in ("колонки", "секции", "синонимы"):
                unknown = sorted(k for k in (getattr(e, block) or {}) if k not in allowed)
                if unknown and allowed:
                    errors.append(f"{at(e)}«{e.файл}»: {block}: неизвестные поля типа «{e.тип}»: {', '.join(unknown)}; "
                                  f"доступные: {', '.join(sorted(allowed))}")
    for name in manifest.модули:
        if name not in modules:
            errors.append(f"{where(name + ':')}модуль «{name}» неизвестен; доступные: {', '.join(sorted(modules))}")
    for name, absent in manifest.missing_types(library, types, modules, None).items():
        errors.append(f"{where(name + ':')}модуль «{name}» включён, но в библиотеке нет ни одного документа типа: "
                      f"{', '.join(absent)} — добавьте документ или выключите модуль")
    if manifest.проект.текущий_том > plan:
        errors.append(f"{where('текущий_том')}текущий том {manifest.проект.текущий_том} больше плана ({plan} т.)")
    if methodics is not None:
        for lvl in ("серия", "том", "акт", "глава"):
            for name in manifest.методики.for_level(lvl):
                if name not in methodics:
                    errors.append(f"{where(lvl + ':')}методики.{lvl}: методика «{name}» неизвестна; доступные: "
                                  f"{', '.join(sorted(methodics)) or '—'}")
    return errors


# ------------------------------------------------------------------ вывод карты без манифеста


def infer(library: Path, types: dict[str, catalog.TypeSpec], threshold: float = 0.35,
          exclude: list[str] | tuple[str, ...] = ()) -> Manifest:
    """Карта библиотеки машинным слоем классификации (FR-ON-7): для каждого документа — тип с наибольшим
    весом сигнатур. Модули включаются по наличию документов их типов. `exclude` — маски служебных файлов."""
    from .onboarding import classify

    entries: list[LibraryEntry] = []
    folders: dict[str, tuple[str, float]] = {}
    if not library.is_dir():
        return Manifest(выведен=True, служебные=list(exclude))
    for path in sorted(library.rglob("*.md")):
        rel = path.relative_to(library).as_posix()
        if is_excluded(rel, exclude):
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
    return Manifest(проект=passport, модули=enabled, библиотека=sorted(entries, key=lambda e: e.файл),
                    служебные=list(exclude), выведен=True)


_EFFECTIVE_CACHE: dict[str, tuple[tuple, Manifest]] = {}


def _library_stamp(library: Path) -> tuple:
    if not library.is_dir():
        return ()
    return tuple(sorted((p.relative_to(library).as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
                        for p in library.rglob("*.md")))


AUTO_THRESHOLD = 0.5


def unmapped(manifest: Manifest, library: Path) -> list[str]:
    """Документы библиотеки, которых нет в карте манифеста (служебные по маскам `служебные:` — не считаются)."""
    out: list[str] = []
    if not library.is_dir():
        return out
    for path in sorted(library.rglob("*.md")):
        rel = path.relative_to(library).as_posix()
        if manifest.is_service(rel):
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
    inferred = infer(library, types, threshold=AUTO_THRESHOLD, exclude=manifest.служебные)
    extra = [e.model_copy(update={"авто": True}) for e in inferred.библиотека
             if manifest.entry_for(e.файл.rstrip("/")) is None and (
                 e.файл in missing or (e.is_folder and any(m.startswith(e.файл) for m in missing)))]
    if not extra:
        return manifest
    return manifest.model_copy(update={"библиотека": [*manifest.библиотека, *extra]})


def effective(root: Path, library: Path, types: dict[str, catalog.TypeSpec] | None = None) -> Manifest:
    """Манифест проекта: `проект.yaml` (+ авто-записи для документов вне карты), а без него — выведенная карта.
    Манифест прежней версии схемы сначала мигрируется с копией (FR-MF-4). Кэш по составу библиотеки и манифесту."""
    types = types or catalog.load_types(root)
    key = str(library.resolve()) + "|" + str(root.resolve())
    mpath = path_of(root)
    if mpath.exists() and schema_version(root) < SCHEMA_VERSION:
        migrate(root)
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


def schema_version(root: Path) -> int:
    """Версия схемы манифеста на диске (0 — ключа нет: манифест до появления версий)."""
    data = catalog.load_yaml(path_of(root)) or {}
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("версия_схемы", 0) or 0)
    except (TypeError, ValueError):
        return 0


def migrate(root: Path) -> tuple[bool, str]:
    """Поднимает манифест до текущей версии схемы, сохранив копию прежнего. (False, причина) — миграция не нужна.
    Содержимое канона не трогается (Д-23): меняется только манифест."""
    path = path_of(root)
    if not path.exists():
        return False, "манифеста нет"
    text = path.read_text(encoding="utf-8")
    data = catalog.load_yaml(path) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{FILENAME}: ожидался YAML-словарь с блоками проект/модули/методики/библиотека")
    version = schema_version(root)
    if version >= SCHEMA_VERSION:
        return False, f"схема уже версии {version}"
    backup = root / f"проект.v{version}.{datetime.now().strftime('%Y%m%d%H%M%S')}.yaml"
    guard.write_text(backup, text)
    data["версия_схемы"] = SCHEMA_VERSION
    data.setdefault("проект", {})
    data.setdefault("модули", {})
    data.setdefault("методики", {})
    data.setdefault("библиотека", [])
    guard.write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return True, f"манифест поднят до версии {SCHEMA_VERSION}; копия прежнего — {backup.name}"
