"""Каталог типов документов и реестр модулей (FR-DT-1…FR-DT-5, FR-MD-1…FR-MD-3).

Тип документа объявляется декларативно — `типы/<тип>.yaml` в движке; проект может добавить свои типы или
переопределить движковые в `типы/` проекта (тот же формат). Модуль — `модули/<модуль>.yaml`: требуемые типы,
секции окна, чек-листы Э2, коды линтера, команды. Спецификации — единственное место, где описан формат канона:
парсеры (`declparse`) читают их, а не «знают» формат (П-1: в коде движка ни одного имени документа серии).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

MULTIPLICITY = ("один", "папка", "по_тому", "несколько")


@dataclass(frozen=True)
class TypeSpec:
    name: str
    purpose: str = ""
    multiplicity: str = "один"          # один | папка | по_тому | несколько
    default_name: str = ""              # имя файла библиотеки по умолчанию («NN_Имя_Том{том}.md»)
    required_for_tact: bool = False     # минимальный комплект первого такта (FR-LC-2)
    feeds: tuple[str, ...] = ()         # какие модули питает
    extractions: tuple[dict, ...] = ()  # что читает машина: [{имя, выгрузка, схема, результат, форматы: [...]}]
    signatures: dict = field(default_factory=dict)   # сигнатуры классификации (FR-ON-7)
    window: dict = field(default_factory=dict)       # что показывать Писателю / что запрещено (FR-DT-3)
    lint_codes: tuple[str, ...] = ()
    required_sections: tuple[str, ...] = ()          # обязательные секции нормализованного документа (FR-ON-15)
    skeleton: str = ""                               # каркас пустого документа (стартовый комплект)
    raw: dict = field(default_factory=dict)
    source: str = ""                                 # откуда загружен (движок | проект)

    @property
    def exports(self) -> list[str]:
        return sorted({e["выгрузка"] for e in self.extractions if e.get("выгрузка")})

    @property
    def per_volume(self) -> bool:
        return self.multiplicity == "по_тому"


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    description: str = ""
    requires_types: tuple[str, ...] = ()
    optional_types: tuple[str, ...] = ()
    window_sections: tuple[str, ...] = ()
    e2_checks: tuple[str, ...] = ()
    lint_codes: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    base: bool = False                  # базовый модуль: включён всегда
    raw: dict = field(default_factory=dict)


def _engine_dir(name: str) -> Path:
    return Path(str(resources.files("konveyer").joinpath(name)))


def _load_yaml_dir(folder: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: спецификация должна быть YAML-словарём")
        out[path.stem] = data
    return out


def _type_from(name: str, data: dict, source: str) -> TypeSpec:
    mult = str(data.get("множественность", "один"))
    if mult not in MULTIPLICITY:
        raise ValueError(f"тип «{name}»: множественность «{mult}» — допустимо {', '.join(MULTIPLICITY)}")
    extractions = tuple(dict(e) for e in (data.get("извлечения") or []))
    for e in extractions:
        if "имя" not in e:
            raise ValueError(f"тип «{name}»: у каждого извлечения нужно поле «имя»")
    return TypeSpec(
        name=str(data.get("тип", name)),
        purpose=str(data.get("назначение", "")),
        multiplicity=mult,
        default_name=str(data.get("имя_по_умолчанию", "")),
        required_for_tact=bool(data.get("обязателен_для_такта", False)),
        feeds=tuple(data.get("питает_модули") or ()),
        extractions=extractions,
        signatures=dict(data.get("сигнатуры") or {}),
        window=dict(data.get("окно") or {}),
        lint_codes=tuple(data.get("проверки_линтера") or ()),
        required_sections=tuple(data.get("обязательные_секции") or ()),
        skeleton=str(data.get("каркас", "")),
        raw=data,
        source=source,
    )


def _module_from(name: str, data: dict) -> ModuleSpec:
    return ModuleSpec(
        name=str(data.get("модуль", name)),
        description=str(data.get("описание", "")),
        requires_types=tuple(data.get("требует_типы") or ()),
        optional_types=tuple(data.get("желательные_типы") or ()),
        window_sections=tuple(data.get("окно") or ()),
        e2_checks=tuple(data.get("э2") or ()),
        lint_codes=tuple(data.get("линтер") or ()),
        commands=tuple(data.get("команды") or ()),
        base=bool(data.get("базовый", False)),
        raw=data,
    )


@functools.lru_cache(maxsize=32)
def _cached_types(project_root: str | None, stamp: tuple) -> dict[str, TypeSpec]:
    engine = _load_yaml_dir(_engine_dir("типы"))
    types = {n: _type_from(n, d, "движок") for n, d in engine.items()}
    if project_root:
        for n, d in _load_yaml_dir(Path(project_root) / "типы").items():
            # переопределение проекта дополняет тип движка по верхним ключам; «заменить: да» — тип целиком свой
            base = engine.get(n) if not d.get("заменить") else None
            merged = {**base, **{k: v for k, v in d.items() if k != "заменить"}} if base else d
            types[n] = _type_from(n, merged, "проект")
    return types


def _stamp(folder: Path) -> tuple:
    if not folder.is_dir():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in folder.glob("*.yaml")))


def load_types(project_root: Path | None = None) -> dict[str, TypeSpec]:
    """Каталог типов: движок + переопределения проекта (`типы/` проекта). Кэш сбрасывается по mtime."""
    root = str(project_root) if project_root else None
    return _cached_types(root, _stamp(Path(project_root) / "типы") if project_root else ())


@functools.lru_cache(maxsize=32)
def _cached_modules(project_root: str | None, stamp: tuple) -> dict[str, ModuleSpec]:
    mods = {n: _module_from(n, d) for n, d in _load_yaml_dir(_engine_dir("модули")).items()}
    if project_root:
        for n, d in _load_yaml_dir(Path(project_root) / "модули").items():
            mods[n] = _module_from(n, d)
    return mods


def load_modules(project_root: Path | None = None) -> dict[str, ModuleSpec]:
    root = str(project_root) if project_root else None
    return _cached_modules(root, _stamp(Path(project_root) / "модули") if project_root else ())


def types_for_export(types: dict[str, TypeSpec], export_name: str) -> list[TypeSpec]:
    return [t for t in types.values() if export_name in t.exports]


def type_for_default_name(types: dict[str, TypeSpec], filename: str) -> TypeSpec | None:
    for t in types.values():
        if t.default_name and t.default_name.split("{")[0] and filename.startswith(t.default_name.split("{")[0]):
            return t
    return None


def documentation(types: dict[str, TypeSpec], modules: dict[str, ModuleSpec]) -> str:
    """«Соглашения типов документов» — генерируется из каталога, чтобы не расходиться с кодом (NFR-10)."""
    lines = ["# Соглашения типов документов", "",
             "Документ сгенерирован из каталога типов движка (`konveyer типы --документация`). "
             "Здесь описано, что именно машина читает из документа каждого типа, что попадает в окно Писателя "
             "и какие проверки линтера тип включает. Канон не переформатируется под парсер: имена колонок и "
             "секций сопоставляются в манифесте проекта (`проект.yaml`, блок `библиотека`).", ""]
    for name in sorted(types):
        t = types[name]
        lines += [f"## {t.name}", "", f"- Назначение: {t.purpose or '—'}",
                  f"- Множественность: {t.multiplicity}" + (f"; имя по умолчанию `{t.default_name}`" if t.default_name else ""),
                  f"- Питает модули: {', '.join(t.feeds) or '—'}",
                  f"- Обязателен для первого такта: {'да' if t.required_for_tact else 'нет'}"]
        for e in t.extractions:
            lines.append(f"- Извлечение «{e['имя']}» → `{e.get('выгрузка', '—')}`:")
            for f in e.get("форматы", []):
                kind = f.get("вид", "?")
                if kind == "таблица":
                    cols = ", ".join(f"{k} ({'/'.join(v.get('синонимы', [k])) if isinstance(v, dict) else v})"
                                     for k, v in (f.get("колонки") or {}).items())
                    lines.append("  - таблица" + (f" в секции «{f['секция']}»" if f.get("секция") else "") + f": колонки {cols}")
                elif kind == "плагин":
                    lines.append(f"  - плагин-парсер проекта `{f.get('функция')}`")
                else:
                    lines.append(f"  - {kind}: {f.get('описание', '')}".rstrip(": "))
        if t.window:
            show = t.window.get("показывать") or []
            hide = t.window.get("запрещено") or []
            lines.append(f"- В окно Писателя: {', '.join(show) or '—'}; запрещено показывать: {', '.join(hide) or '—'}")
        if t.lint_codes:
            lines.append(f"- Проверки линтера: {', '.join(t.lint_codes)}")
        sig = t.signatures
        if sig:
            parts = [f"{k}: {v}" for k, v in sig.items() if k != "вес"]
            lines.append(f"- Сигнатуры классификации: {'; '.join(parts)}")
        lines.append("")
    lines += ["# Модули", ""]
    for name in sorted(modules):
        m = modules[name]
        lines += [f"## {m.name}" + (" (базовый)" if m.base else ""), "", f"- {m.description or '—'}",
                  f"- Требует типы: {', '.join(m.requires_types) or '—'}",
                  f"- Секции окна: {', '.join(m.window_sections) or '—'}",
                  f"- Чек-листы Э2: {', '.join(m.e2_checks) or '—'}",
                  f"- Коды линтера: {', '.join(m.lint_codes) or '—'}", ""]
    return "\n".join(lines) + "\n"


def all_lint_codes(modules: dict[str, ModuleSpec]) -> set[str]:
    return {c for m in modules.values() for c in m.lint_codes}


def enabled_lint_codes(modules: dict[str, ModuleSpec], enabled: set[str]) -> set[str]:
    """Коды линтера включённых модулей (базовые — всегда)."""
    return {c for m in modules.values() if m.base or m.name in enabled for c in m.lint_codes}


def any_value(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return default
