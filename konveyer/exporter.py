"""Экспорт: библиотека канона → JSON-выгрузки (FR-EX-1…FR-EX-5, П-2, П-6).

Экспорт читает манифест проекта и спецификации типов каталога (`catalog`, `manifest`, `declparse`): какой файл
какого типа, что из него читает машина. Ни одного имени документа серии в коде нет (П-1). Все документы
разбираются ДО первой записи (атомарность, FR-SC-5); ошибки разбора собираются разом (FR-EX-3); файл выгрузки
перезаписывается только при изменении содержимого (FR-EX-2); корпус прозы пересчитывается только для изменившихся
текстов. Набор файлов выгрузок задаёт каталог типов (`даёт_выгрузку` / `выгрузка` извлечений) — новый тип
проекта с новой выгрузкой не требует правки кода (FR-DT-4).

Заголовок выгрузок (FR-EX-5) — один на все файлы, в `выгрузки/индекс.json`: `{версия_схемы, дата,
отпечаток_канона, том, files}`. Сами `*.json` — голые списки/словари (так их читают все потребители). Поле `дата`
детерминировано (П-6): это дата коммита HEAD библиотеки, а не время экспорта; библиотека без git — пустая строка.
Отпечаток канона учитывает документы библиотеки и конфигурацию проекта (манифест, типы, модули, язык), потому что
она меняет выгрузки и набор проверок.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from . import catalog, declparse, gitops, guard, lang as lang_mod, manifest as manifest_mod, mdparse, names, textutils
from .mdparse import MarkupError
from .schemas import (
    Act, Arc, Brief, Checklist, ChronicleEvent, ChronologyEvent, ContinuityEvent, Decision, DocumentSpec, Dose,
    SCOPE_NARRATOR, Dossier, InfoBan, MatrixFact, MethodNote, NarrationRules, Norm, Plant, StopRule, StoryCircle,
    VolumePlan, WorldEntry,
)

SCHEMA_VERSION = 1
INDEX = "индекс.json"
CORPUS_DIR = "корпус"
CORPUS_INDEX = ".index.json"  # кэш корпуса: имя главы → хэш источника и хэш результата (без времени: П-6)

SCHEMAS: dict[str, type[BaseModel]] = {
    "Norm": Norm, "StopRule": StopRule, "MatrixFact": MatrixFact, "Plant": Plant, "ContinuityEvent": ContinuityEvent,
    "Brief": Brief, "Dossier": Dossier, "InfoBan": InfoBan, "StoryCircle": StoryCircle, "Act": Act, "Arc": Arc,
    "Dose": Dose, "DocumentSpec": DocumentSpec, "ChronicleEvent": ChronicleEvent, "ChronologyEvent": ChronologyEvent,
    "NarrationRules": NarrationRules, "MethodNote": MethodNote, "WorldEntry": WorldEntry, "VolumePlan": VolumePlan,
    "Decision": Decision, "Checklist": Checklist,
}


class FreeRecord(BaseModel):
    """Схема «словарь»: запись типа проекта без своей модели — любые поля как есть (`схема: словарь`)."""

    model_config = {"extra": "allow"}


FREE_SCHEMAS = {"словарь", "запись", ""}

# файлы выгрузок, которые пишутся всегда (пустые списки/словари — деградация, а не отсутствие файла);
# к ним добавляются выгрузки всех типов каталога (движка и проекта)
EXPORT_FILES = [
    "norms.json", "stoplists.json", "matrix.json", "plants.json", "continuity.json", "briefs.json", "dossiers.json",
    "infobans.json", "parts.json", "circles.json", "acts.json", "arcs.json", "doses.json", "documents.json",
    "chronicle.json", "chronology.json", "narration.json", "method.json", "world.json", "objects.json", "places.json",
    "volumes.json", "decisions.json", "checklists.json",
]
DICT_EXPORTS = {"norms.json"}
# порядок типов при разборе: сначала источники известных имён (участники сцен, знающие тайну)
TYPE_ORDER = ["повествование", "эпистемика", "персонажи", "стиль", "язык", "план_глав"]
# папки конфигурации проекта, входящие в отпечаток канона (меняют выгрузки и набор проверок)
CONFIG_DIRS = ("типы", "модули", "языки", "методики")


class ExportErrors(MarkupError):
    """Несколько ошибок разбора разом (FR-EX-3): каждая — файл, строка, что ожидалось; в тексте перечислены все."""

    def __init__(self, errors: list[MarkupError]):
        self.errors = errors
        first = errors[0]
        lines = [str(e) for e in errors]
        message = lines[0].split(": ", 1)[-1] if len(errors) == 1 else \
            f"ошибок разбора: {len(errors)}\n  " + "\n  ".join(lines)
        super().__init__(first.path, first.line, message)
        self.path = first.path
        self.line = first.line


# ------------------------------------------------------------------ документы по типам

def project_root_of(library: Path, root: Path | None = None) -> Path:
    return root if root is not None else library.parent


def docs_of_type(library: Path, тип: str, volume: int | None = None, root: Path | None = None) -> list[Path]:
    """Документы канона типа `тип` для тома `volume` по манифесту проекта (без манифеста — по классификации)."""
    root = project_root_of(library, root)
    types = catalog.load_types(root)
    man = manifest_mod.effective(root, library, types)
    return man.docs(library, тип, volume, types)


def doc_volume(path: Path) -> int | None:
    return manifest_mod.doc_volume(path)


def _prose_exclusions(spec: catalog.TypeSpec | None) -> tuple[str, ...]:
    """Маркеры имён файлов, которые не считаются принятыми главами («МАКЕТ»); сравнение без регистра."""
    raw = spec.raw.get("исключить", ["МАКЕТ"]) if spec else ["МАКЕТ"]
    return tuple(str(x).upper() for x in raw)


def prose_files(library: Path, volume: int | None = None, root: Path | None = None) -> list[tuple[int, Path]]:
    """Принятые главы тома по документам типа «проза»: [(номер главы, путь)] по возрастанию; макеты не берутся."""
    root = project_root_of(library, root)
    spec = catalog.load_types(root).get("проза")
    rx = re.compile(spec.raw.get("регэксп_главы", r"Том0*(\d+)_Глава0*(\d+)") if spec else r"Том0*(\d+)_Глава0*(\d+)")
    exclude = _prose_exclusions(spec)
    out: list[tuple[int, Path]] = []
    for p in docs_of_type(library, "проза", None, root):
        m = rx.search(p.stem)
        if not m or any(x in p.stem.upper() for x in exclude):
            continue
        if volume is not None and int(m.group(1)) != volume:
            continue
        out.append((int(m.group(2)), p))
    return sorted(out)


def prose_name(library: Path, volume: int, chapter: int, root: Path | None = None) -> str:
    root = project_root_of(library, root)
    spec = catalog.load_types(root).get("проза")
    pattern = spec.raw.get("имя_главы", "Том{том}_Глава{глава:02d}.md") if spec else "Том{том}_Глава{глава:02d}.md"
    return pattern.format(том=volume, глава=chapter)


def prose_folder(library: Path, root: Path | None = None) -> Path:
    root = project_root_of(library, root)
    types = catalog.load_types(root)
    man = manifest_mod.effective(root, library, types)
    entries = man.entries_of_type("проза")
    if entries:
        return library / entries[0].файл.rstrip("/")
    spec = types.get("проза")
    return library / (spec.default_name.rstrip("/") if spec and spec.default_name else "Проза")


def missing_volume_docs(library: Path, volume: int, root: Path | None = None, *, only_required: bool = False) -> list[str]:
    """Чего не хватает, чтобы вести том `volume` (FR-LC-2): типы, обязательные для такта, и потомные типы
    включённых модулей без документа тома. `only_required` — только то, без чего такт невозможен
    (документы модулей — предупреждение, не отказ)."""
    root = project_root_of(library, root)
    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    man = manifest_mod.effective(root, library, types)
    missing: list[str] = []
    for t in types.values():
        if not t.required_for_tact:
            continue
        if not man.docs(library, t.name, volume, types):
            missing.append(f"{t.name} ({t.default_name.format(том=volume) if t.default_name else 'документ'})")
    if only_required:
        return missing
    for mod, absent in man.missing_types(library, types, modules, volume).items():
        for t in absent:
            spec = types.get(t)
            label = f"{t} ({spec.default_name.format(том=volume)})" if spec and spec.default_name else t
            if label not in missing and spec and spec.per_volume:
                missing.append(label + f" — модуль «{mod}»")
    return missing


# ------------------------------------------------------------------ сбор (разбор всего до записи)


def _schema_model(schema_name: str, ctx: declparse.ParseContext | None) -> type[BaseModel]:
    """Модель схемы по имени: движковая (`Norm`, `Brief`…), свободная (`словарь`) или модель плагина проекта
    (`модуль:Класс` из `типы/парсеры/`)."""
    model = SCHEMAS.get(schema_name)
    if model is not None:
        return model
    if schema_name.strip() in FREE_SCHEMAS:
        return FreeRecord
    if ":" in schema_name and ctx is not None:
        cand = declparse.resolve_plugin(schema_name, ctx)
        if isinstance(cand, type) and issubclass(cand, BaseModel):
            return cand
    raise ValueError(f"неизвестная схема выгрузки «{schema_name}» в каталоге типов; доступные: "
                     f"{', '.join(sorted(SCHEMAS))}, словарь, модуль:Класс (типы/парсеры/ проекта)")


def _acts_of(data: dict, path: Path, line: int, errors: list[MarkupError]) -> list[Act]:
    """Строка таблицы актов → Act; акт без разобранного диапазона глав — ошибка с файлом и строкой
    (кроме строк-каркасов, где главы не заполнены)."""
    from .dramaturgy_doc import acts_from_rows

    chapters = str(data.get("chapters_text") or "").strip()
    if not chapters:
        return []  # каркас: акт объявлен, главы не заполнены («⚠ заполнить» → пусто)
    try:
        acts = acts_from_rows([data])
    except (ValueError, ValidationError) as e:
        errors.append(MarkupError(path, line, f"акт: {e}"))
        return []
    if not acts:
        errors.append(MarkupError(path, line, f"акт «{data.get('act')}»: не разобран диапазон глав «{chapters}» "
                                              f"(ожидается «1–4» или «5»)"))
    for a in acts:
        a.line = a.line or line  # строка таблицы актов — для находок линтера
    return acts


def _validate(records: list[dict], schema_name: str, path: Path, errors: list[MarkupError],
              ctx: declparse.ParseContext | None = None, key_field: str = "") -> list[tuple[dict, BaseModel]]:
    """Записи → (исходная запись, модель схемы); запись, не прошедшая схему, — ошибка с файлом и строкой;
    поле, которого в схеме нет (опечатка в `запись:` типа проекта), — тоже ошибка, а не молчаливая потеря.
    `key_field` — поле-ключ словарной выгрузки (`результат: словарь:id`): оно в схему не входит."""
    model = _schema_model(schema_name, ctx)
    out: list[tuple[dict, BaseModel]] = []
    known_fields = set(model.model_fields) | {getattr(f, "alias", None) for f in model.model_fields.values()}
    if key_field:
        known_fields.add(key_field)
    strict = model.model_config.get("extra") != "allow"
    for rec in records:
        line = int(rec.get("_строка") or 0) or 1
        data = {k: v for k, v in rec.items() if not k.startswith("_")}
        if strict:
            unknown = sorted(set(data) - known_fields)
            if unknown:
                errors.append(MarkupError(path, line, f"неизвестные поля схемы {schema_name}: {', '.join(unknown)}; "
                                                      f"допустимые: {', '.join(sorted(model.model_fields))}"))
                continue
        if "line" in model.model_fields and not data.get("line") and rec.get("_строка"):
            data["line"] = int(rec["_строка"])  # строка записи — для находок линтера (FR-LT-1)
        if model is Act:
            out.extend((rec, a) for a in _acts_of(data, path, line, errors))
            continue
        try:
            out.append((rec, model.model_validate(data)))
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:3])
            errors.append(MarkupError(path, line, f"запись не соответствует схеме {schema_name}: {msg}"))
    return out


def _assemble_values(values: list[Any], spec: dict, path: Path) -> list[BaseModel]:
    """`сборка: {вид: стоп_правило, …}` — плоский список значений → одна запись схемы."""
    build = spec.get("сборка") or {}
    items = [str(v).strip() for v in values if str(v).strip()]
    if not items:
        return []
    if build.get("вид") == "стоп_правило":
        return [StopRule(scope=str(build.get("scope", SCOPE_NARRATOR)), rule_id=str(build.get("rule_id", "правило")),
                         items=items, applies_to={"all": True}, action=str(build.get("action", "флаг")),
                         kind=str(build.get("kind", "лексика")))]
    raise ValueError(f"{path.name}: неизвестная сборка «{build.get('вид')}»")


def _overrides(entry: manifest_mod.LibraryEntry | None) -> dict:
    if entry is None:
        return {}
    out: dict = {}
    out.update(entry.колонки or {})
    out.update(entry.секции or {})
    out.update(entry.синонимы or {})
    return out


def export_names(types: dict[str, catalog.TypeSpec]) -> tuple[list[str], set[str]]:
    """(все файлы выгрузок, какие из них — словари) по каталогу типов плюс базовый список EXPORT_FILES."""
    files = list(EXPORT_FILES)
    dicts = set(DICT_EXPORTS)
    for t in types.values():
        for e in t.extractions:
            export = e.get("выгрузка")
            if not export:
                continue
            if export not in files:
                files.append(export)
            if str(e.get("результат", "")).startswith("словарь:"):
                dicts.add(export)
    return files, dicts


class Collected:
    """Все разобранные данные тома до записи: {файл выгрузки: список моделей | словарь}. `errors` — ошибки разбора
    (экспорт невозможен); `warnings` — ошибки в документах, которые питают только выключенные модули (П-5: данные
    никому не нужны, экспорт идёт, о проблеме говорит «доктор»)."""

    def __init__(self, types: dict[str, catalog.TypeSpec] | None = None) -> None:
        files, self.dict_exports = export_names(types or {})
        self.data: dict[str, Any] = {name: ({} if name in self.dict_exports else []) for name in files}
        self.errors: list[MarkupError] = []
        self.warnings: list[MarkupError] = []
        self.known_names: set[str] = set()
        self.pseudo: set[str] = set()

    def add(self, export: str, items: list[BaseModel] | dict) -> None:
        if export in self.dict_exports:
            self.data.setdefault(export, {}).update(items)  # type: ignore[arg-type]
        else:
            self.data.setdefault(export, []).extend(items)  # type: ignore[arg-type]


def _type_sequence(types: dict[str, catalog.TypeSpec]) -> list[catalog.TypeSpec]:
    first = [types[n] for n in TYPE_ORDER if n in types]
    rest = [t for n, t in sorted(types.items()) if n not in TYPE_ORDER]
    return first + rest


def _feeds_only_disabled(spec: catalog.TypeSpec, modules: dict[str, catalog.ModuleSpec], enabled: set[str]) -> bool:
    """Тип питает только выключенные (небазовые) модули — его данные никому не нужны; обязательные для такта
    типы под это правило не подпадают."""
    if spec.required_for_tact:
        return False
    owners = [m for m in spec.feeds if m in modules and not modules[m].base]
    return bool(owners) and not any(m in enabled for m in owners)


def collect(library: Path, volume: int = 1, root: Path | None = None, *, require_docs: bool = True) -> Collected:
    """Разбор всех документов тома по манифесту и каталогу типов. Ошибки собираются, ничего не пишется.
    `require_docs=False` — отсутствие обязательных для такта документов не ошибка (онбординг, FR-LC-1)."""
    root = project_root_of(library, root)
    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    man = manifest_mod.effective(root, library, types)
    enabled = man.enabled_modules(modules)
    col = Collected(types)
    mpath = manifest_mod.path_of(root)
    if mpath.exists() and not man.выведен:
        # карта с неверными записями разбирается не так, как думает автор (FR-MF-2): ошибка с файлом и строкой манифеста
        for line, msg in manifest_mod.entry_problems(man, library, types, mpath.read_text(encoding="utf-8"), strict=False):
            col.errors.append(MarkupError(mpath, line or 0, msg))
    for spec in _type_sequence(types):
        col.pseudo |= set(spec.raw.get("псевдосубъекты") or [])
        if not spec.extractions:
            continue
        soft = _feeds_only_disabled(spec, modules, enabled)
        sink = col.warnings if soft else col.errors
        docs = man.docs(library, spec.name, volume, types)
        if not docs and spec.required_for_tact and require_docs:
            expected = spec.default_name.format(том=volume) if spec.default_name else f"документ типа «{spec.name}»"
            col.errors.append(MarkupError(library / expected, 0,
                                          f"для тома {volume} нет документа типа «{spec.name}» (ожидается {expected}); "
                                          f"такт без него невозможен (FR-LC-2)"))
            continue
        for doc in docs:
            entry = man.entry_for(doc.relative_to(library).as_posix())
            doc_vol = entry.том if entry and entry.том else (doc_volume(doc) or volume)
            ctx = declparse.ParseContext(volume=doc_vol, overrides=_overrides(entry), project_root=root, library=library,
                                         sections=dict(entry.секции or {}) if entry else {},
                                         params={"known_names": col.known_names, "exports": col.data, "pseudo": col.pseudo,
                                                 "тип": spec.raw})
            for ext in spec.extractions:
                ctx.extraction = str(ext.get("имя", ""))
                formats = list(ext.get("форматы") or [])
                doc_errors: list[MarkupError] = []
                try:
                    records, fmt = declparse.parse_document(doc, formats, ctx)
                except MarkupError as e:
                    sink.append(e)
                    continue
                except UnicodeDecodeError:
                    sink.append(MarkupError(doc, 1, "файл не в UTF-8 (NFR-2) — пересохраните его в UTF-8"))
                    continue
                except (ValueError, KeyError, TypeError, AttributeError, re.error) as e:
                    sink.append(MarkupError(doc, 1, f"разбор «{ext['имя']}»: {type(e).__name__}: {e}"))
                    continue
                if records is None:
                    if not catalog.flag(ext.get("необязательно")):
                        sink.append(declparse.markup_error(doc, formats))
                    continue
                export = ext.get("выгрузка")
                if not export:
                    continue
                result = str(ext.get("результат", "список"))
                key_field = result.split(":", 1)[1] if result.startswith("словарь:") else ""
                if result.startswith("значения:"):
                    field = result.split(":", 1)[1]
                    values = [r.get(field) for r in records] if isinstance(records, list) else []
                    col.add(export, _assemble_values(values, ext, doc))
                    continue
                if isinstance(records, list) and records and isinstance(records[0], BaseModel):
                    pairs = [(m.model_dump(), m) for m in records]  # плагин вернул модели
                elif isinstance(records, dict):
                    pairs = [({"_ключ": k, **(v.model_dump() if isinstance(v, BaseModel) else {})}, v) for k, v in records.items()]
                else:
                    try:
                        pairs = _validate(list(records) if isinstance(records, list) else [], str(ext.get("схема", "")),
                                          doc, doc_errors, ctx, key_field)
                    except ValueError as e:
                        sink.append(MarkupError(doc, 1, str(e)))
                        continue
                sink.extend(doc_errors)
                for _, m in pairs:
                    _stamp_file(m, doc.relative_to(library).as_posix())
                if key_field:
                    key = key_field
                    keyed: dict[str, Any] = {}
                    for rec, m in pairs:
                        k = str(rec.get(key, rec.get("_ключ", getattr(m, key, ""))))
                        if k in keyed or k in col.data.get(export, {}):
                            sink.append(MarkupError(doc, int(rec.get("_строка") or 0) or 1,
                                                    f"«{k}» встречается повторно (ключ «{key}» должен быть уникален)"))
                            continue
                        keyed[k] = m
                    col.add(export, keyed)
                else:
                    col.add(export, [m for _, m in pairs])
        _after_type(spec.name, col, root)
    _postprocess(col, volume, library, root, types)
    return col


def _stamp_file(model: BaseModel, rel: str) -> None:
    if hasattr(model, "file") and not getattr(model, "file"):
        try:
            object.__setattr__(model, "file", rel)
        except (AttributeError, ValueError):
            pass


def _after_type(name: str, col: Collected, root: Path | None) -> None:
    """Известные имена накапливаются по мере разбора: субъекты эпистемики, карточки, фокалы."""
    if name == "эпистемика":
        col.known_names |= {f.subject for f in col.data["matrix.json"] if f.subject and f.subject not in col.pseudo}
    elif name == "персонажи":
        col.known_names |= {d.name for d in col.data["dossiers.json"] if d.name}
    elif name == "повествование":
        for n in col.data["narration.json"]:
            n.focal_names = n.focal_names or _focal_names(n.focals_text, lang_mod.for_project(root).not_names)
            col.known_names |= set(n.focal_names)


_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|?$")


def _focal_names(text: str, not_names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Имена из таблицы/списка фокалов документа повествования: заглавные слова ячеек данных. Строка заголовка
    таблицы и разделитель не читаются (слова заголовка — не имена), ячейки-предложения («Имя — никогда не
    фокален») — оговорка, не список; слова, объявленные языковым слоем не именами (`не_имена`), отбрасываются."""
    out: set[str] = set()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    for i, line in enumerate(lines):
        if _TABLE_SEP_RE.match(line) or (i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1])):
            continue
        for cell_text in mdparse._split_row(line):
            if re.match(r"^\s*[А-ЯЁ][а-яё]+\s+[—–-]\s", cell_text):
                continue
            if set(cell_text.strip()) <= set(":- "):
                continue
            out.update(re.findall(r"\b([А-ЯЁ][а-яё]{2,})\b", cell_text))
    return sorted(out - set(not_names))


# ------------------------------------------------------------------ постобработка (общая, без серии)


def _post_rules(types: dict[str, catalog.TypeSpec], type_name: str) -> dict:
    """Блок `постобработка:` спецификации типа: образцы разметки канона (год рождения, возраст, «глазами»,
    маркеры видимости) живут в типах, а не в коде (FR-DT-4, П-1)."""
    spec = types.get(type_name)
    rules = spec.raw.get("постобработка") if spec else None
    return dict(rules) if isinstance(rules, dict) else {}


def _patterns(value: Any) -> list[re.Pattern]:
    items = [value] if isinstance(value, str) else list(value or [])
    return [re.compile(str(p)) for p in items if str(p).strip()]


def _postprocess(col: Collected, volume: int, library: Path, root: Path, types: dict[str, catalog.TypeSpec]) -> None:
    d = col.data
    known = col.known_names
    language = lang_mod.for_project(root)
    # хроника: месяц события
    for e in d["chronicle.json"]:
        e.month = _month(e.date, language)
    # хронология: тома и главы видимости, годы разделов, маркеры видимости из спецификации типа
    vis_rules = _post_rules(types, "хронология").get("видимость") or {}
    hidden_words = [str(w).lower() for w in (vis_rules.get("скрыт") or [])]
    background_words = [str(w).lower() for w in (vis_rules.get("фон") or [])]
    for e in d["chronology.json"]:
        vis = e.visibility or ""
        e.volumes = sorted(set(names.volumes_listed(vis)))
        e.chapters = sorted(set(names.chapters_listed(vis)))
        if e.year is None:
            ym = re.search(r"(1[6-9]\d\d|20\d\d)", e.event_id + " " + e.date)
            e.year = int(ym.group(1)) if ym else None
        low = vis.lower()
        e.hidden = e.hidden or any(w in low for w in hidden_words)
        e.background = e.background or any(w in low for w in background_words)
    # брифы: год из даты, фокал («глазами Имя» → Имя), участники из сцен/битов, дубли глав
    eyes_rules = _patterns(_post_rules(types, "план_глав").get("фокал_образец"))
    briefs: dict[tuple[int, int], Brief] = {}
    for b in sorted(d["briefs.json"], key=lambda b: (b.volume, b.chapter, -len(b.beats))):
        if isinstance(b.beats, str):
            b.beats = [b.beats] if b.beats else []
        if b.year is None and b.date:
            ym = re.search(r"(1[6-9]\d\d|20\d\d)", b.date)
            b.year = int(ym.group(1)) if ym else None
        for rx in eyes_rules:
            m = rx.search(b.focal or "")
            if m:
                b.focal = (m.groupdict().get("имя") or m.group(m.lastindex or 0)).strip()
                break
        b.focal = names.normalize_name(b.focal, known) if known and b.focal else b.focal
        if not b.participants and known:
            text = " · ".join([*b.scenes, *b.beats])
            b.participants = [n for n in names.find_acting_names(text, known, col.pseudo) if n != b.focal]
        b.participants = [p for p in dict.fromkeys(b.participants) if p and p != b.focal]
        b.plants = [p.strip() for x in b.plants for p in str(x).split(";") if p.strip()]
        key = (b.volume, b.chapter)
        if key not in briefs:
            briefs[key] = b
    d["briefs.json"] = sorted(briefs.values(), key=lambda b: (b.volume, b.chapter))
    # информрежим: «кто знает» → known_by по известным именам; маркеры — список
    matrix = d["matrix.json"]
    for ban in d["infobans.json"]:
        if ban.known_text and not ban.known_by:
            ban.known_by = _parse_known_by(ban.known_text, known)
        if ban.until_chapter is None and ban.secret:
            reader = next((f.from_chapter for f in matrix if f.fact_id == ban.ban_id and f.subject in col.pseudo), None)
            if reader is not None:
                ban.until_chapter = reader
    # досье: год рождения и возраст по томам — по образцам типа «персонажи»; ссылки [[Имя]] — разметка Markdown
    doss_rules = _post_rules(types, "персонажи")
    born_rules = _patterns(doss_rules.get("год_рождения"))
    age_rules = _patterns(doss_rules.get("возраст_по_томам"))
    for doss in d["dossiers.json"]:
        body = "\n".join([doss.profile, doss.physique, doss.status, doss.arc])
        for rx in born_rules:
            bm = rx.search(body)
            if bm:
                doss.born_year = int(bm.groupdict().get("год") or bm.group(1))
                break
        for rx in age_rules:
            for am in rx.finditer(body):
                g = am.groupdict()
                age, vol = g.get("возраст"), g.get("том")
                if age and vol:
                    doss.ages[f"т.{vol}"] = int(age)
        doss.refs = sorted({m.group(1).strip() for m in re.finditer(r"\[\[([^\]]+)\]\]", body + "\n".join(doss.relations.values()))})
    # акты → части (совместимость: parts.json — список словарей актов)
    acts = sorted({a.act: a for a in d["acts.json"]}.values(), key=lambda a: a.act)
    d["acts.json"] = acts
    d["parts.json"] = [{"part": a.act, "title": a.title, "period": a.parts, "from_chapter": a.from_chapter,
                        "to_chapter": a.to_chapter} for a in acts]
    # нормы без числового значения (каркас стартового комплекта, «—») — нормы нет: метрика не считается (FR-MT-3);
    # о незаполненной норме объёма скажет `доктор` (FR-LC-2а)
    for norm_id, n in list(d["norms.json"].items()):
        if n.min is None and n.max is None and n.brak is None:
            del d["norms.json"][norm_id]
    from . import metrics as metrics_mod

    # лексемная норма с неразобранной единицей («слово1, слово2 на 1000») — своя ошибка, точнее общей «нет метрики»
    bad_lexeme = {nid for nid, n in d["norms.json"].items()
                  if nid.startswith(metrics_mod.LEXEME_PREFIX) and metrics_mod.lexeme_norm(nid, n) is None}
    for norm_id in sorted(bad_lexeme):
        src = d["norms.json"][norm_id].source.split(" (")[0]
        col.errors.append(MarkupError(library / src, 1, f"норма «{norm_id}»: единица должна перечислять слова и базу "
                                                        "(«слово1, слово2 на 1000»)"))
    for norm_id in metrics_mod.unknown_norms(d["norms.json"]):
        if norm_id in bad_lexeme:
            continue
        src = d["norms.json"][norm_id].source.split(" (")[0]
        col.errors.append(MarkupError(library / src, 1, f"норма «{norm_id}» не соответствует ни одной метрике реестра Э1; "
                                      f"доступные: {', '.join(metrics_mod.available())}"))
    # норма принята, но проверяться не будет или будет проверяться неверно (брак без стороны, нет параметра) — тоже ошибка
    for norm_id, problem in metrics_mod.norm_problems(d["norms.json"]):
        src = d["norms.json"][norm_id].source.split(" (")[0]
        col.errors.append(MarkupError(library / src, 1, f"норма «{norm_id}»: {problem}"))


def _month(date: str, language: lang_mod.Language | None = None) -> int | None:
    """Месяц даты: «12.06.1995» / «12.06» → 6; «1995.06.12» / «1995-06-12» → 6; «май 1996» → 5 (названия месяцев —
    из языкового слоя)."""
    m = re.search(r"\b\d{4}[.\-/](\d{2})(?:[.\-/]\d{1,2})?\b", date)
    if m:
        return int(m.group(1))
    m = re.search(r"\b\d{1,2}\.(\d{2})\b", date)
    if m:
        return int(m.group(1))
    return (language or lang_mod.get()).month_of(date)


def _parse_known_by(text: str, known: set[str]) -> dict[str, int]:
    """«Имя, Имя2; Имя3 — с гл. 7; никто; Имя4 узнает в томе 3» → {имя: глава}; имя без главы — 0 (всегда)."""
    out: dict[str, int] = {}
    for chunk in re.split(r"[;,]", text):
        chunk = chunk.strip()
        if not chunk or re.search(r"\b(никто|не узнает|не узнаёт)\b", chunk, re.IGNORECASE):
            continue
        if re.search(r"в томе\s*\d+", chunk, re.IGNORECASE):
            continue
        name = names.normalize_name(re.sub(r"^(только|больше)\s+", "", chunk, flags=re.IGNORECASE), known)
        if name not in known:
            continue
        chm = names.CH_RE.search(chunk)
        out[name] = int(chm.group(1)) if chm else 0
    return out


def known_names_of(col_or_exports: Collected | Path, root: Path | None = None) -> set[str]:
    """Известные имена проекта: субъекты эпистемики (без псевдосубъектов), карточки персонажей, фокалы.
    `root` — корень проекта: псевдосубъекты берутся с учётом типов проекта."""
    if isinstance(col_or_exports, Collected):
        return set(col_or_exports.known_names)
    exports_dir = col_or_exports
    out: set[str] = set()
    pseudo = pseudo_subjects(root)
    try:
        out |= {f.subject for f in load_matrix(exports_dir) if f.subject not in pseudo}
    except FileNotFoundError:
        pass
    try:
        out |= {d.name for d in load_dossiers(exports_dir)}
    except FileNotFoundError:
        pass
    for n in load_narration(exports_dir):
        out |= set(n.focal_names)
    return {n for n in out if n}


def pseudo_subjects(root: Path | None = None) -> set[str]:
    """Субъекты эпистемики, не являющиеся персонажами (например «Читатель») — объявлены типом (верхний ключ
    `псевдосубъекты`) движка или проекта; `root` — корень проекта (без него типы проекта не видны)."""
    types = catalog.load_types(root)
    out: set[str] = set()
    for t in types.values():
        out |= set(t.raw.get("псевдосубъекты") or [])
    return out


# ------------------------------------------------------------------ запись


def _render(model: BaseModel | list | dict) -> str:
    if isinstance(model, BaseModel):
        data = model.model_dump(by_alias=True)
    elif isinstance(model, list):
        data = [m.model_dump(by_alias=True) if isinstance(m, BaseModel) else m for m in model]
    else:
        data = {k: (v.model_dump() if isinstance(v, BaseModel) else v) for k, v in model.items()}
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_if_changed(path: Path, text: str, known_hash: str | None = None) -> str:
    """Инкрементальная запись (FR-EX-2): файл перезаписывается, только если содержимое изменилось."""
    digest = _sha(text)
    if known_hash is not None and known_hash != digest:
        guard.write_text(path, text)
        return digest
    try:
        if path.read_text(encoding="utf-8") == text:
            return digest
    except (OSError, UnicodeDecodeError):
        pass
    guard.write_text(path, text)
    return digest


def _read_index(exports_dir: Path) -> dict:
    try:
        data = json.loads((exports_dir / INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def exports_schema_version(exports_dir: Path) -> int | None:
    """Версия схемы выгрузок по `индекс.json`; None — выгрузок нет (или индекс повреждён)."""
    v = _read_index(exports_dir).get("версия_схемы")
    return int(v) if isinstance(v, int) else None


def load_manifest(exports_dir: Path) -> dict[str, str]:
    """{файл: sha256} прошлого экспорта; пусто, если индекса нет, он повреждён или выгрузки другой версии схемы
    (тогда экспорт полный, без инкрементальности — NFR-11)."""
    data = _read_index(exports_dir)
    if data.get("версия_схемы") != SCHEMA_VERSION:
        return {}
    files = data.get("files", {})
    return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(files, dict) else {}


def _hash_tree(h: Any, base: Path, files: list[Path]) -> None:
    for p in sorted(files):
        h.update(p.relative_to(base).as_posix().encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")


def canon_fingerprint(library: Path, root: Path | None = None) -> str:
    """Отпечаток канона: sha256 по именам и содержимому всех документов библиотеки и конфигурации проекта —
    манифеста, типов, модулей, языков и методик проекта (FR-EX-5, FR-LT-5): они меняют выгрузки и набор проверок,
    поэтому кэш линтера и регрессия по одному отпечатку документов устаревали бы."""
    h = hashlib.sha256()
    if library.is_dir():
        _hash_tree(h, library, list(library.rglob("*.md")))
    root = root if root is not None else (library.parent if library.is_dir() else None)
    if root is not None and root.is_dir():
        files: list[Path] = []
        mpath = manifest_mod.path_of(root)
        if mpath.is_file():
            files.append(mpath)
        for name in CONFIG_DIRS:
            folder = root / name
            if folder.is_dir():
                files += [p for p in folder.rglob("*") if p.is_file() and p.suffix in (".yaml", ".yml", ".py")
                          and "__pycache__" not in p.parts]
        h.update("\0конфигурация\0".encode("utf-8"))
        _hash_tree(h, root, files)
    return h.hexdigest()


def canon_date(library: Path) -> str:
    """Дата выгрузок (FR-EX-5), детерминированная (П-6): дата коммита HEAD библиотеки; без git — пустая строка."""
    try:
        if not gitops.is_repo(library) or not gitops.has_commits(library):
            return ""
        return gitops.head_date(library)
    except (RuntimeError, OSError):
        return ""


# ------------------------------------------------------------------ корпус прозы


def _load_corpus_index(corpus_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((corpus_dir / CORPUS_INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _corpus_plan(library: Path, exports_dir: Path, root: Path | None) -> tuple[list[tuple[Path, str | None, dict]], dict[str, dict], list[MarkupError]]:
    """План корпуса: (файл корпуса, текст или None если не изменился, запись индекса) по принятым главам
    (макеты и прочие исключения типа «проза» в корпус не входят: они не эталон стиля). Файл не в UTF-8 —
    ошибка с именем файла (третий элемент), а не трейсбек."""
    corpus_dir = exports_dir / CORPUS_DIR
    # выгрузки другой версии схемы — кэш корпуса не доверяется, тексты пересобираются целиком
    index = _load_corpus_index(corpus_dir) if exports_schema_version(exports_dir) == SCHEMA_VERSION else {}
    plan: list[tuple[Path, str | None, dict]] = []
    errors: list[MarkupError] = []
    for _, path in prose_files(library, None, root):
        out = corpus_dir / (path.stem + ".txt")
        src_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = index.get(out.name)
        if (isinstance(entry, dict) and entry.get("источник") == src_hash and isinstance(entry.get("hash"), str)
                and out.exists()):
            plan.append((out, None, entry))
            continue
        try:
            raw = mdparse.read_text(path)
        except UnicodeDecodeError:
            errors.append(MarkupError(path, 1, "файл не в UTF-8 (NFR-2) — пересохраните его в UTF-8"))
            continue
        tokens = textutils.normalize(textutils.narrator_text(raw))
        text = " ".join(tokens) + "\n"
        plan.append((out, text, {"источник": src_hash, "hash": _sha(text)}))
    return plan, index, errors


def _write_corpus(plan, exports_dir: Path, old_index: dict[str, dict]) -> dict[str, str]:
    corpus_dir = exports_dir / CORPUS_DIR
    hashes: dict[str, str] = {}
    new_index: dict[str, dict] = {}
    for out, text, entry in plan:
        if text is not None:
            _write_if_changed(out, text)
        hashes[out.name] = entry["hash"]
        new_index[out.name] = entry
    if corpus_dir.exists():
        for stale in corpus_dir.glob("*.txt"):
            if stale.name not in hashes:
                guard.remove(stale)
    if new_index != old_index:
        guard.write_text(corpus_dir / CORPUS_INDEX, json.dumps(new_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return hashes


def export_corpus(library: Path, exports_dir: Path, root: Path | None = None) -> dict[str, str]:
    plan, old_index, errors = _corpus_plan(library, exports_dir, root)
    if errors:
        raise ExportErrors(errors)
    return _write_corpus(plan, exports_dir, old_index)


def find_corpus_file(corpus_dir: Path, chapter: int, volume: int | None = None) -> Path | None:
    """Файл корпуса главы по номеру (и тому): границы числа обязательны («Глава1» ≠ «Глава10»)."""
    if not corpus_dir.exists():
        return None
    ch_rx = re.compile(rf"Глава0*{chapter}(?!\d)")
    vol_rx = re.compile(rf"Том0*{volume}(?!\d)") if volume is not None else None
    for f in sorted(corpus_dir.glob("*.txt")):
        if ch_rx.search(f.stem) and (vol_rx is None or vol_rx.search(f.stem)):
            return f
    return None


# ------------------------------------------------------------------ запуск


def run_export(library: Path, exports_dir: Path, logs_dir: Path, volume: int = 1, root: Path | None = None,
               *, require_docs: bool = True) -> dict[str, str]:
    """Перегенерирует все выгрузки тома `volume` (FR-EX-1): сначала разбирается ВЕСЬ канон (включая план корпуса),
    и только затем пишутся файлы (FR-SC-5). Возвращает {файл: sha256}."""
    root = project_root_of(library, root)
    col = collect(library, volume, root, require_docs=require_docs)
    corpus_plan, old_index, corpus_errors = _corpus_plan(library, exports_dir, root)
    if col.errors or corpus_errors:
        raise ExportErrors(col.errors + corpus_errors)
    known = load_manifest(exports_dir)
    hashes: dict[str, str] = {}
    for name in sorted(col.data):
        hashes[name] = _write_if_changed(exports_dir / name, _render(col.data[name]), known.get(name))
    hashes.update(_write_corpus(corpus_plan, exports_dir, old_index))
    index = {"версия_схемы": SCHEMA_VERSION, "дата": canon_date(library), "том": volume,
             "отпечаток_канона": canon_fingerprint(library, root), "files": hashes,
             "предупреждения": sorted(relative_message(w, library) for w in col.warnings)}
    _write_if_changed(exports_dir / INDEX, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if hashes != known:  # повторный экспорт без изменений — ноль записей, в том числе в журнале (NFR-6)
        guard.append_text(logs_dir / "экспорт.jsonl", json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "том": volume, "отпечаток": index["отпечаток_канона"],
             "hashes": hashes, "предупреждений": len(col.warnings)},
            ensure_ascii=False, sort_keys=True) + "\n")
    return hashes


def relative_message(err: MarkupError, library: Path) -> str:
    """«файл:строка: текст» относительно библиотеки (в индексе и отчётах нет абсолютных путей, FR-SC-9)."""
    try:
        rel = Path(err.path).relative_to(library).as_posix()
    except (ValueError, TypeError):
        rel = Path(err.path).name
    text = str(err).split(": ", 1)[-1] if str(err).startswith(str(err.path)) else str(err)
    return f"{rel}:{err.line}: {text}"


def export_warnings(exports_dir: Path) -> list[str]:
    """Предупреждения последнего экспорта (документы выключенных модулей с ошибками разметки) — для «доктора»."""
    try:
        data = json.loads((exports_dir / INDEX).read_text(encoding="utf-8"))
        items = data.get("предупреждения") if isinstance(data, dict) else None
        return [str(x) for x in items] if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


# ------------------------------------------------------------------ чтение


def load_export(exports_dir: Path, name: str):
    path = exports_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Выгрузка {name} не найдена. Выполните `konveyer экспорт` (экспорт обязателен перед сборкой окна).")
    version = exports_schema_version(exports_dir)
    if version is not None and version != SCHEMA_VERSION:
        raise FileNotFoundError(f"Выгрузки собраны движком со схемой версии {version}, а нужна {SCHEMA_VERSION}. "
                                "Выполните `konveyer экспорт` — выгрузки пересоберутся целиком (NFR-11).")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_list(exports_dir: Path, name: str, model: type[BaseModel], optional: bool = True) -> list:
    try:
        return [model.model_validate(r) for r in load_export(exports_dir, name)]
    except FileNotFoundError:
        if optional:
            return []
        raise


def load_norms(exports_dir: Path) -> dict[str, Norm]:
    return {k: Norm.model_validate(v) for k, v in load_export(exports_dir, "norms.json").items()}


def load_stoplists(exports_dir: Path) -> list[StopRule]:
    return _load_list(exports_dir, "stoplists.json", StopRule, optional=False)


def load_matrix(exports_dir: Path) -> list[MatrixFact]:
    return _load_list(exports_dir, "matrix.json", MatrixFact, optional=False)


def load_plants(exports_dir: Path) -> list[Plant]:
    return _load_list(exports_dir, "plants.json", Plant, optional=False)


def load_briefs(exports_dir: Path) -> list[Brief]:
    return _load_list(exports_dir, "briefs.json", Brief, optional=False)


def load_brief(exports_dir: Path, chapter: int) -> Brief:
    for b in load_briefs(exports_dir):
        if b.chapter == chapter:
            return b
    raise FileNotFoundError(f"В плане глав (briefs.json) нет главы {chapter}.")


def load_continuity(exports_dir: Path) -> list[ContinuityEvent]:
    return _load_list(exports_dir, "continuity.json", ContinuityEvent, optional=False)


def load_dossiers(exports_dir: Path) -> list[Dossier]:
    return _load_list(exports_dir, "dossiers.json", Dossier, optional=False)


def load_infobans(exports_dir: Path) -> list[InfoBan]:
    return _load_list(exports_dir, "infobans.json", InfoBan, optional=False)


def load_parts(exports_dir: Path) -> list[dict]:
    return load_export(exports_dir, "parts.json")


def load_circles(exports_dir: Path) -> list[StoryCircle]:
    return _load_list(exports_dir, "circles.json", StoryCircle)


def load_acts(exports_dir: Path) -> list[Act]:
    return _load_list(exports_dir, "acts.json", Act)


def load_arcs(exports_dir: Path) -> list[Arc]:
    return _load_list(exports_dir, "arcs.json", Arc)


def load_doses(exports_dir: Path) -> list[Dose]:
    return _load_list(exports_dir, "doses.json", Dose)


def load_documents(exports_dir: Path) -> list[DocumentSpec]:
    return _load_list(exports_dir, "documents.json", DocumentSpec)


def load_chronology(exports_dir: Path) -> list[ChronologyEvent]:
    return _load_list(exports_dir, "chronology.json", ChronologyEvent)


def load_chronicle(exports_dir: Path) -> list[ChronicleEvent]:
    return _load_list(exports_dir, "chronicle.json", ChronicleEvent)


def load_narration(exports_dir: Path) -> list[NarrationRules]:
    return _load_list(exports_dir, "narration.json", NarrationRules)


def load_method(exports_dir: Path) -> list[MethodNote]:
    return _load_list(exports_dir, "method.json", MethodNote)


def load_world(exports_dir: Path, name: str = "world.json") -> list[WorldEntry]:
    return _load_list(exports_dir, name, WorldEntry)


def load_volumes(exports_dir: Path) -> list[VolumePlan]:
    return _load_list(exports_dir, "volumes.json", VolumePlan)


def load_decisions(exports_dir: Path) -> list[Decision]:
    return _load_list(exports_dir, "decisions.json", Decision)


def load_checklists(exports_dir: Path) -> list[Checklist]:
    return _load_list(exports_dir, "checklists.json", Checklist)


def export_volume(exports_dir: Path) -> int | None:
    """Том, для которого сделаны выгрузки (индекс); None — экспорта не было."""
    try:
        data = json.loads((exports_dir / INDEX).read_text(encoding="utf-8"))
        v = data.get("том", data.get("volume")) if isinstance(data, dict) else None
        return int(v) if v is not None else None
    except (OSError, ValueError, TypeError):
        return None


def export_fingerprint(exports_dir: Path) -> str | None:
    try:
        data = json.loads((exports_dir / INDEX).read_text(encoding="utf-8"))
        return str(data.get("отпечаток_канона")) if isinstance(data, dict) and data.get("отпечаток_канона") else None
    except (OSError, ValueError):
        return None
