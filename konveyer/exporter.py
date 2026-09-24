"""Экспорт: библиотека канона → JSON-выгрузки (FR-EX-1…FR-EX-5, П-2, П-6).

Экспорт читает манифест проекта и спецификации типов каталога (`catalog`, `manifest`, `declparse`): какой файл
какого типа, что из него читает машина. Ни одного имени документа серии в коде нет (П-1). Все документы
разбираются ДО первой записи (атомарность, FR-SC-5); ошибки разбора собираются разом (FR-EX-3); файл выгрузки
перезаписывается только при изменении содержимого (FR-EX-2); корпус прозы пересчитывается только для изменившихся
текстов. Заголовок выгрузок (версия схемы, том, отпечаток канона) — `выгрузки/индекс.json` (FR-EX-5).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from . import catalog, declparse, guard, manifest as manifest_mod, names, textutils
from .mdparse import MarkupError
from .schemas import (
    Act, Arc, Brief, Checklist, ChronicleEvent, ChronologyEvent, ContinuityEvent, Decision, DocumentSpec, Dose,
    Dossier, InfoBan, MatrixFact, MethodNote, NarrationRules, Norm, Plant, StopRule, StoryCircle, VolumePlan,
    WorldEntry,
)

SCHEMA_VERSION = 1
INDEX = "индекс.json"
CORPUS_DIR = "корпус"
CORPUS_INDEX = ".index.json"  # кэш корпуса: имя главы → mtime_ns/size источника и хэш результата

SCHEMAS: dict[str, type[BaseModel]] = {
    "Norm": Norm, "StopRule": StopRule, "MatrixFact": MatrixFact, "Plant": Plant, "ContinuityEvent": ContinuityEvent,
    "Brief": Brief, "Dossier": Dossier, "InfoBan": InfoBan, "StoryCircle": StoryCircle, "Act": Act, "Arc": Arc,
    "Dose": Dose, "DocumentSpec": DocumentSpec, "ChronicleEvent": ChronicleEvent, "ChronologyEvent": ChronologyEvent,
    "NarrationRules": NarrationRules, "MethodNote": MethodNote, "WorldEntry": WorldEntry, "VolumePlan": VolumePlan,
    "Decision": Decision, "Checklist": Checklist,
}

# все файлы выгрузок, которые пишутся всегда (пустые списки/словари — деградация, а не отсутствие файла)
EXPORT_FILES = [
    "norms.json", "stoplists.json", "matrix.json", "plants.json", "continuity.json", "briefs.json", "dossiers.json",
    "infobans.json", "parts.json", "circles.json", "acts.json", "arcs.json", "doses.json", "documents.json",
    "chronicle.json", "chronology.json", "narration.json", "method.json", "world.json", "objects.json", "places.json",
    "volumes.json", "decisions.json", "checklists.json",
]
DICT_EXPORTS = {"norms.json"}
# порядок типов при разборе: сначала источники известных имён (участники сцен, знающие тайну)
TYPE_ORDER = ["повествование", "эпистемика", "персонажи", "стиль", "язык", "план_глав"]

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


class ExportErrors(MarkupError):
    """Несколько ошибок разбора разом (FR-EX-3): каждая — файл, строка, что ожидалось."""

    def __init__(self, errors: list[MarkupError]):
        self.errors = errors
        first = errors[0]
        super().__init__(first.path, first.line, "; ".join(str(e).split(": ", 1)[-1] for e in errors[:1])
                         + (f" (и ещё {len(errors) - 1})" if len(errors) > 1 else ""))
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


def prose_files(library: Path, volume: int | None = None, root: Path | None = None) -> list[tuple[int, Path]]:
    """Принятые главы тома по документам типа «проза»: [(номер главы, путь)] по возрастанию; макеты не берутся."""
    root = project_root_of(library, root)
    spec = catalog.load_types(root).get("проза")
    rx = re.compile(spec.raw.get("регэксп_главы", r"Том0*(\d+)_Глава0*(\d+)") if spec else r"Том0*(\d+)_Глава0*(\d+)")
    exclude = tuple(spec.raw.get("исключить", ["МАКЕТ"])) if spec else ("МАКЕТ",)
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


def _validate(records: list[dict], schema_name: str, path: Path, errors: list[MarkupError]) -> list[tuple[dict, BaseModel]]:
    """Записи → (исходная запись, модель схемы); запись, не прошедшая схему, — ошибка с файлом и строкой."""
    model = SCHEMAS.get(schema_name)
    if model is None:
        raise ValueError(f"неизвестная схема выгрузки «{schema_name}» в каталоге типов")
    out: list[tuple[dict, BaseModel]] = []
    for rec in records:
        line = int(rec.get("_строка") or 0) or 1
        data = {k: v for k, v in rec.items() if not k.startswith("_")}
        if "line" in model.model_fields and not data.get("line") and rec.get("_строка"):
            data["line"] = int(rec["_строка"])  # строка записи — для находок линтера (FR-LT-1)
        if model is Act:
            try:
                from .dramaturgy_doc import acts_from_rows
                acts = acts_from_rows([data])
            except (ValueError, ValidationError):
                acts = []
            out.extend((rec, a) for a in acts)
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
        return [StopRule(scope=str(build.get("scope", "0.3")), rule_id=str(build.get("rule_id", "правило")),
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


class Collected:
    """Все разобранные данные тома до записи: {файл выгрузки: список моделей | словарь}."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {name: ({} if name in DICT_EXPORTS else []) for name in EXPORT_FILES}
        self.errors: list[MarkupError] = []
        self.known_names: set[str] = set()
        self.pseudo: set[str] = set()

    def add(self, export: str, items: list[BaseModel] | dict) -> None:
        if export in DICT_EXPORTS:
            self.data.setdefault(export, {}).update(items)  # type: ignore[arg-type]
        else:
            self.data.setdefault(export, []).extend(items)  # type: ignore[arg-type]


def _type_sequence(types: dict[str, catalog.TypeSpec]) -> list[catalog.TypeSpec]:
    first = [types[n] for n in TYPE_ORDER if n in types]
    rest = [t for n, t in sorted(types.items()) if n not in TYPE_ORDER]
    return first + rest


def collect(library: Path, volume: int = 1, root: Path | None = None, *, require_docs: bool = True) -> Collected:
    """Разбор всех документов тома по манифесту и каталогу типов. Ошибки собираются, ничего не пишется.
    `require_docs=False` — отсутствие обязательных для такта документов не ошибка (онбординг, FR-LC-1)."""
    root = project_root_of(library, root)
    types = catalog.load_types(root)
    man = manifest_mod.effective(root, library, types)
    col = Collected()
    for spec in _type_sequence(types):
        col.pseudo |= set(spec.raw.get("псевдосубъекты") or [])
        if not spec.extractions:
            continue
        docs = man.docs(library, spec.name, volume, types)
        if not docs and spec.required_for_tact and require_docs:
            expected = spec.default_name.format(том=volume) if spec.default_name else f"документ типа «{spec.name}»"
            col.errors.append(MarkupError(library / expected, 0,
                                          f"для тома {volume} нет документа типа «{spec.name}» (ожидается {expected}); "
                                          f"такт без него невозможен (FR-EX-4)"))
            continue
        for doc in docs:
            entry = man.entry_for(doc.relative_to(library).as_posix())
            doc_vol = entry.том if entry and entry.том else (doc_volume(doc) or volume)
            ctx = declparse.ParseContext(volume=doc_vol, overrides=_overrides(entry), project_root=root, library=library,
                                         params={"known_names": col.known_names, "exports": col.data, "pseudo": col.pseudo})
            for ext in spec.extractions:
                formats = list(ext.get("форматы") or [])
                try:
                    records, fmt = declparse.parse_document(doc, formats, ctx)
                except MarkupError as e:
                    col.errors.append(e)
                    continue
                except UnicodeDecodeError:
                    col.errors.append(MarkupError(doc, 1, "файл не в UTF-8 (NFR-2) — пересохраните его в UTF-8"))
                    continue
                except (ValueError, KeyError) as e:
                    col.errors.append(MarkupError(doc, 1, f"разбор «{ext['имя']}»: {e}"))
                    continue
                if records is None:
                    if not ext.get("необязательно"):
                        col.errors.append(declparse.markup_error(doc, formats))
                    continue
                export = ext.get("выгрузка")
                if not export:
                    continue
                result = str(ext.get("результат", "список"))
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
                    pairs = _validate(list(records) if isinstance(records, list) else [], ext.get("схема", ""), doc, col.errors)
                for _, m in pairs:
                    _stamp_file(m, doc.relative_to(library).as_posix())
                if result.startswith("словарь:"):
                    key = result.split(":", 1)[1]
                    col.add(export, {str(rec.get(key, rec.get("_ключ", getattr(m, key, "")))): m for rec, m in pairs})
                else:
                    col.add(export, [m for _, m in pairs])
        _after_type(spec.name, col, volume)
    _postprocess(col, volume, library, root)
    return col


def _stamp_file(model: BaseModel, rel: str) -> None:
    if hasattr(model, "file") and not getattr(model, "file"):
        try:
            object.__setattr__(model, "file", rel)
        except (AttributeError, ValueError):
            pass


def _after_type(name: str, col: Collected, volume: int) -> None:
    """Известные имена накапливаются по мере разбора: субъекты эпистемики, карточки, фокалы."""
    if name == "эпистемика":
        col.known_names |= {f.subject for f in col.data["matrix.json"] if f.subject and f.subject not in col.pseudo}
    elif name == "персонажи":
        col.known_names |= {d.name for d in col.data["dossiers.json"] if d.name}
    elif name == "повествование":
        for n in col.data["narration.json"]:
            n.focal_names = n.focal_names or _focal_names(n.focals_text)
            col.known_names |= set(n.focal_names)


def _focal_names(text: str) -> list[str]:
    """Имена из таблицы/списка фокалов документа повествования: заглавные слова ячеек, кроме ячеек-предложений
    («Имя — никогда не фокален» — оговорка, не список)."""
    out: set[str] = set()
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        for cell_text in line.strip().strip("|").split("|"):
            if re.match(r"^\s*[А-ЯЁ][а-яё]+\s+[—–-]\s", cell_text):
                continue
            if set(cell_text.strip()) <= set(":- "):
                continue
            out.update(re.findall(r"\b([А-ЯЁ][а-яё]{2,})\b", cell_text))
    return sorted(out - {"Тома", "Без", "Открывается", "Фокальные", "Линии", "Фокал"})


# ------------------------------------------------------------------ постобработка (общая, без серии)


def _postprocess(col: Collected, volume: int, library: Path, root: Path) -> None:
    d = col.data
    known = col.known_names
    # хроника: месяц события
    for e in d["chronicle.json"]:
        e.month = _month(e.date)
    # хронология: тома и главы видимости, годы разделов
    for e in d["chronology.json"]:
        vis = e.visibility or ""
        e.volumes = sorted({int(x) for m in re.finditer(r"т\.?\s*(\d+)", vis, re.IGNORECASE) for x in [m.group(1)]})
        e.chapters = sorted({int(x) for m in re.finditer(r"гл\.?\s*(\d+)", vis, re.IGNORECASE) for x in [m.group(1)]})
        if e.year is None:
            ym = re.search(r"(1[6-9]\d\d|20\d\d)", e.event_id + " " + e.date)
            e.year = int(ym.group(1)) if ym else None
        e.hidden = e.hidden or "скрыт" in vis.lower()
        e.background = e.background or "фон" in vis.lower()
    # брифы: год из даты, участники из сцен/битов, дубли глав (секции + таблица одного документа)
    briefs: dict[tuple[int, int], Brief] = {}
    for b in sorted(d["briefs.json"], key=lambda b: (b.volume, b.chapter, -len(b.beats))):
        if isinstance(b.beats, str):
            b.beats = [b.beats] if b.beats else []
        if b.year is None and b.date:
            ym = re.search(r"(1[6-9]\d\d|20\d\d)", b.date)
            b.year = int(ym.group(1)) if ym else None
        b.focal = names.normalize_name(b.focal, known) if known and b.focal else b.focal
        eyes = re.search(r"глазами\s+([А-ЯЁ][а-яё]+)", b.focal or "")
        if eyes:
            b.focal = names.normalize_name(eyes.group(1), known)
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
    # досье: год рождения, возраст по томам, ссылки [[Имя]]
    for doss in d["dossiers.json"]:
        body = "\n".join([doss.profile, doss.physique, doss.status, doss.arc])
        bm = re.search(r"Рожд\.\s*≈?\s*(\d{4})", body)
        doss.born_year = int(bm.group(1)) if bm else doss.born_year
        for am in re.finditer(r"(\d{2,3})\s*\(\s*т\.\s*(\d+)\s*\)|(\d{2,3})\s*(?:лет|года)\s+в\s+томе\s+(\d+)", body):
            doss.ages[f"т.{am.group(2) or am.group(4)}"] = int(am.group(1) or am.group(3))
        doss.refs = sorted({m.group(1).strip() for m in re.finditer(r"\[\[([^\]]+)\]\]", body + "\n".join(doss.relations.values()))})
        rel_prose = doss.relations
        if not rel_prose and "[[" in body:
            pass
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

    metrics_mod.register_lexeme_norms(d["norms.json"])
    for norm_id in metrics_mod.unknown_norms(d["norms.json"]):
        src = d["norms.json"][norm_id].source.split(" (")[0]
        col.errors.append(MarkupError(library / src, 1, f"норма «{norm_id}» не соответствует ни одной метрике реестра Э1; "
                                      f"доступные: {', '.join(metrics_mod.available())}"))


def _month(date: str) -> int | None:
    m = re.search(r"\b\d{1,2}\.(\d{2})\b", date)
    if m:
        return int(m.group(1))
    low = date.lower()
    for stem, num in MONTHS.items():
        if stem in low:
            return num
    return None


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


def known_names_of(col_or_exports: Collected | Path) -> set[str]:
    """Известные имена проекта: субъекты эпистемики (без псевдосубъектов), карточки персонажей, фокалы."""
    if isinstance(col_or_exports, Collected):
        return set(col_or_exports.known_names)
    exports_dir = col_or_exports
    out: set[str] = set()
    pseudo = pseudo_subjects(exports_dir)
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


def pseudo_subjects(exports_dir: Path | None = None, root: Path | None = None) -> set[str]:
    """Субъекты эпистемики, не являющиеся персонажами (например «Читатель») — объявлены типом."""
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


def load_manifest(exports_dir: Path) -> dict[str, str]:
    """{файл: sha256} прошлого экспорта; пусто, если индекса нет или он повреждён."""
    path = exports_dir / INDEX
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def canon_fingerprint(library: Path) -> str:
    """Отпечаток канона: sha256 по именам и содержимому всех документов библиотеки (FR-EX-5, FR-LT-5)."""
    h = hashlib.sha256()
    if library.is_dir():
        for p in sorted(library.rglob("*.md")):
            h.update(p.relative_to(library).as_posix().encode("utf-8"))
            h.update(b"\0")
            try:
                h.update(p.read_bytes())
            except OSError:
                pass
            h.update(b"\0")
    return h.hexdigest()


# ------------------------------------------------------------------ корпус прозы


def _load_corpus_index(corpus_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((corpus_dir / CORPUS_INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _corpus_plan(library: Path, exports_dir: Path, root: Path | None) -> tuple[list[tuple[Path, str | None, dict]], dict[str, dict]]:
    corpus_dir = exports_dir / CORPUS_DIR
    index = _load_corpus_index(corpus_dir)
    plan: list[tuple[Path, str | None, dict]] = []
    for path in docs_of_type(library, "проза", None, root):
        out = corpus_dir / (path.stem + ".txt")
        st = path.stat()
        entry = index.get(out.name)
        if (isinstance(entry, dict) and entry.get("mtime_ns") == st.st_mtime_ns and entry.get("size") == st.st_size
                and isinstance(entry.get("hash"), str) and out.exists()):
            plan.append((out, None, entry))
            continue
        tokens = textutils.normalize(textutils.narrator_text(path.read_text(encoding="utf-8")))
        text = " ".join(tokens) + "\n"
        plan.append((out, text, {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "hash": _sha(text)}))
    return plan, index


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
    plan, old_index = _corpus_plan(library, exports_dir, root)
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
    col = collect(library, volume, root, require_docs=require_docs)
    if col.errors:
        raise ExportErrors(col.errors)
    corpus_plan, old_index = _corpus_plan(library, exports_dir, root)
    known = load_manifest(exports_dir)
    hashes: dict[str, str] = {}
    for name in EXPORT_FILES:
        hashes[name] = _write_if_changed(exports_dir / name, _render(col.data[name]), known.get(name))
    hashes.update(_write_corpus(corpus_plan, exports_dir, old_index))
    index = {"версия_схемы": SCHEMA_VERSION, "том": volume, "отпечаток_канона": canon_fingerprint(library),
             "files": hashes, "volume": volume}
    _write_if_changed(exports_dir / INDEX, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    guard.append_text(logs_dir / "экспорт.jsonl", json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(), "том": volume, "отпечаток": index["отпечаток_канона"], "hashes": hashes},
        ensure_ascii=False, sort_keys=True) + "\n")
    return hashes


# ------------------------------------------------------------------ чтение


def load_export(exports_dir: Path, name: str):
    path = exports_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Выгрузка {name} не найдена. Выполните `konveyer экспорт` (экспорт обязателен перед сборкой окна).")
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
