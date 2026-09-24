"""Предложение «файл → тип» по сырью (FR-ON-7…FR-ON-14): `konveyer онбординг`.

Машинный слой работает всегда (сигнатуры типов из каталога); модельный слой — роль «Архивариус» — по запросу,
кэшируется по хэшу входа (FR-ON-8, FR-ON-10), ограничен `onboarding_max_docs` за прогон (R-9), а в ручном режиме
оставляет готовый промпт в `онбординг/промпты/` и принимает ответ файлом (FR-RL-3). Для каждого файла считаются:
гипотезы типов, сопоставление колонок таблицы с колонками типа с результатом на трёх строках (FR-ON-13),
предложение разбить по разделам и склеить с другими файлами того же типа (FR-ON-9), предпросмотр разбора —
исходный фрагмент и те самые строки, что станут машинными данными, и что из них попадёт в окно Писателя (FR-ON-11),
вопросы автору с файлом и строкой (FR-ON-14). Результат — `онбординг/предложение.json` (решения автора правятся в нём
же: поле «решение») и `онбординг/предложение.md`; оба воспроизводимы при том же сырье (П-6).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import catalog, declparse, manifest as manifest_mod, mdparse
from ..paths import Workspace
from . import classify, importer, normalize

PROPOSAL_JSON = "предложение.json"
PROPOSAL_MD = "предложение.md"
MODEL_CACHE = "кэш_модели"
PROMPTS_DIR = "промпты"
ANSWERS_DIR = "ответы"
PREVIEW_DIR = "предпросмотр"
REJECTED_LOG = "отклонено_архивариусом.jsonl"   # П-7: отклонённые автором предложения модели
MIN_CONFIDENCE = 0.35          # запасной порог, когда конфига нет (Д-11: порог — из конфига)
MAX_MODEL_DOCS = 60            # запасной лимит документов за прогон архивариуса (R-9), когда конфига нет
SPLIT_MIN_SECTIONS = 2
PREVIEW_ROWS = 3
FRAGMENT_LINES = 12
FRAGMENT_CHARS = 700
FENCE_OPEN = "<сырьё>"         # FR-SC-8: фрагменты материалов — данные, не инструкции
FENCE_CLOSE = "</сырьё>"
DECISIONS = ("принять", "сырьё", "отклонить", "разбить", "источник", "канон")
# «тип:<имя>», «склеить:<файл сырья>» (FR-ON-9), «колонка:<поле>=<заголовок>» (FR-ON-13, решение не меняется)
DECISION_PREFIXES = ("тип:", "склеить:", "колонка:")
NUM_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)?_")
VERSION_SUFFIX_RE = re.compile(r"~\d{4}-\d{2}-\d{2}(?:~\d+)?$")


@dataclass
class Preview:
    """Что машина прочитает из документа (FR-ON-11): записи разбора и поля, идущие в окно Писателя."""
    format: str = ""                    # вид формата, которым разобран
    records: int = 0
    rows: list[dict] = field(default_factory=list)      # первые записи (поле → значение)
    to_window: list[str] = field(default_factory=list)  # что из этого увидит Писатель (из «окно.показывать» типа)
    internal: list[str] = field(default_factory=list)   # что останется внутренним («окно.запрещено»)
    error: str = ""                     # почему разбор не удался
    note: str = ""                      # пояснение без ошибки (тип без машинного чтения)
    fragment: str = ""                  # исходный фрагмент извлечения — сверить «что прочитано» с оригиналом


@dataclass
class ColumnMap:
    """Сопоставление колонок таблицы файла с колонками типа (FR-ON-13)."""
    extraction: str
    mapping: dict[str, str] = field(default_factory=dict)   # поле типа → заголовок в файле
    missing: list[str] = field(default_factory=list)        # обязательные поля без колонки
    generated: str = ""                                     # ключевая колонка, которую заполнит нормализация
    sample: list[dict] = field(default_factory=list)        # результат на трёх строках
    headers: list[str] = field(default_factory=list)        # заголовки таблицы-реестра в файле
    автор: dict[str, str] = field(default_factory=dict)     # правки автора (решение «колонка:поле=заголовок»)


@dataclass
class SplitPart:
    heading: str
    line: int
    type: str
    confidence: float


@dataclass
class Proposal:
    файл: str                        # имя в сырьё/оригиналы
    извлечено_в: str | None
    гипотезы: list[dict] = field(default_factory=list)   # [{тип, уверенность, почему}]
    тип: str = "сырьё"               # предложенный тип (лучшая гипотеза выше порога) или «сырьё»
    уверенность: float = 0.0
    источник_решения: str = "машина" # машина | модель | автор
    колонки: dict | None = None      # ColumnMap
    разбить: list[dict] = field(default_factory=list)   # SplitPart
    остаток: list[str] = field(default_factory=list)    # разделы, не вошедшие в части разбиения (и «вступление»)
    склеить_с: str = ""              # предложение: этот файл — продолжение другого файла того же типа (FR-ON-9)
    предпросмотр: dict | None = None # Preview
    вопросы: list[str] = field(default_factory=list)    # FR-ON-14
    том: int | None = None           # для потомных типов
    имя_документа: str = ""          # предлагаемое имя файла в библиотеке
    обновление: bool = False         # новая версия уже применённого источника: документ будет обновлён
    решение: str = ""                # автор: принять | тип:<имя> | сырьё | отклонить | разбить | склеить:<файл> | источник | канон
    модель: dict | None = None       # ответ Архивариуса (если был)


def onboarding_dir(ws: Workspace) -> Path:
    return ws.root / "онбординг"


def threshold_of(cfg) -> float:
    """Порог классификации — из конфига (Д-11); без конфига — запасной."""
    value = getattr(cfg, "classification_threshold", None) if cfg is not None else None
    return float(value) if value is not None else MIN_CONFIDENCE


# ------------------------------------------------------------------ сопоставление колонок и предпросмотр


def _table_formats(spec: catalog.TypeSpec) -> list[tuple[str, dict]]:
    return [(ext.get("имя", ""), fmt) for ext in spec.extractions for fmt in ext.get("форматы", [])
            if fmt.get("вид") == "таблица" and fmt.get("колонки")]


def _closest(field: str, spec: dict, headers: list[str]) -> str | None:
    """Ближайший заголовок к полю: общая основа ≥ 4 знаков (без регистра)."""
    cands = [field] + [str(s) for s in (spec.get("синонимы") or [])] if isinstance(spec, dict) else [field]
    for h in headers:
        hl = h.lower()
        if len(hl) < 3:
            continue  # «a», «№»: слишком коротко, чтобы угадывать
        for c in cands:
            c = c.lower()
            if len(c) >= 4 and (c[:4] in hl or hl[:4] in c):
                return h
    return None


def column_map(path: Path, spec: catalog.TypeSpec, overrides: dict | None = None) -> ColumnMap | None:
    """Таблица-реестр файла (та, что лучше всего совпала с колонками первого табличного формата типа) против
    колонок типа; None — у типа нет таблиц или в файле их нет."""
    formats = _table_formats(spec)
    if not formats:
        return None
    try:
        tables = mdparse.parse_tables(path)
    except mdparse.MarkupError:
        return None
    if not tables:
        return None
    name, fmt = formats[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    m, _ = normalize.find_registry_table(text, fmt["колонки"], overrides)
    table = tables[0]
    if m is not None:
        first = m.group(0).splitlines()[0]
        table = next((t for t in tables if t.headers == normalize._split_row(first)), tables[0])
    headers = table.headers
    # нестрогое сопоставление: обязательность колонок не мешает увидеть, что уже совпало
    lenient = normalize._lenient(fmt["колонки"])
    found = declparse.match_columns(headers, lenient, overrides or {}) or {}
    cm = ColumnMap(extraction=name, mapping=dict(found), headers=list(headers),
                   автор={k: v for k, v in (overrides or {}).items() if v in headers})
    for fld, cspec in fmt["колонки"].items():
        if fld in found:
            continue
        guess = _closest(fld, cspec, [h for h in headers if h not in found.values()])
        if guess:
            cm.mapping[fld] = guess
        elif isinstance(cspec, dict) and cspec.get("роль") == "ключ":
            cm.generated = fld  # ключевая колонка без соответствия — идентификаторы сгенерирует нормализация
        elif isinstance(cspec, dict) and cspec.get("обязательна"):
            cm.missing.append(fld)
    for row in table.rows[:PREVIEW_ROWS]:
        cm.sample.append({fld: row.get(h, "") for fld, h in cm.mapping.items()})
    return cm


def normalized_preview_path(ws: Workspace, spec: catalog.TypeSpec, path: Path, mapping: dict[str, str] | None,
                            source: str, questions: list[str] | None = None) -> Path:
    """Нормализованный документ для предпросмотра — ровно тот текст, что уйдёт в библиотеку при применении."""
    d = onboarding_dir(ws) / PREVIEW_DIR
    d.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="replace")
    norm = normalize.normalize(text, spec, source=source, mapping=mapping, title=path.stem, questions=questions)
    target = d / (path.name if path.name.endswith(".md") else path.name + ".md")
    target.write_text(norm.text, encoding="utf-8")
    return target


def write_preview(ws: Workspace, name: str, text: str) -> Path:
    """Текст документа в папке предпросмотра — для проверки разбора теми же примитивами, что и экспорт."""
    d = onboarding_dir(ws) / PREVIEW_DIR
    d.mkdir(parents=True, exist_ok=True)
    target = d / (name if name.endswith(".md") else name + ".md")
    target.write_text(text, encoding="utf-8")
    return target


def fragment_of(path: Path) -> str:
    """Исходный фрагмент извлечения для предпросмотра (FR-ON-11а): первые строки, без пустых."""
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    text = "\n".join(lines[:FRAGMENT_LINES])
    return text if len(text) <= FRAGMENT_CHARS else text[:FRAGMENT_CHARS - 1] + "…"


def preview(path: Path, spec: catalog.TypeSpec, root: Path, overrides: dict | None = None, volume: int = 1,
            fragment_from: Path | None = None) -> Preview:
    """Разбор документа форматами типа: те же примитивы, что и экспорт (предпросмотр = выгрузка)."""
    pv = Preview()
    window = spec.window or {}
    pv.to_window = [str(x) for x in (window.get("показывать") or [])]
    pv.internal = [str(x) for x in (window.get("запрещено") or [])]
    pv.fragment = fragment_of(fragment_from or path)
    if not spec.extractions:
        pv.note = "тип без машинного чтения: документ попадёт в библиотеку целиком, машина его не разбирает"
        return pv
    ctx = declparse.ParseContext(volume=volume, overrides=overrides or {}, project_root=root, library=path.parent,
                                 params={"known_names": set(), "exports": {}, "pseudo": set()})
    total = 0
    for ext in spec.extractions:
        try:
            records, fmt = declparse.parse_document(path, list(ext.get("форматы") or []), ctx)
        except (ValueError, KeyError) as e:
            pv.error = f"{ext.get('имя')}: {e}"
            continue
        if records is None:
            if not ext.get("необязательно"):
                pv.error = f"{ext.get('имя')}: " + declparse.describe_expected(list(ext.get("форматы") or []))
            continue
        items = list(records.values()) if isinstance(records, dict) else list(records)
        total += len(items)
        if not pv.format:
            pv.format = str((fmt or {}).get("вид", ""))
        for r in items[:PREVIEW_ROWS]:
            data = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            pv.rows.append({k: _short(v) for k, v in data.items() if not str(k).startswith("_")})
    pv.records = total
    return pv


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ("" if v is None else str(v))
    return s if len(s) <= 80 else s[:77] + "…"


# ------------------------------------------------------------------ разбиение


def split_candidates(path: Path, types: dict[str, catalog.TypeSpec], threshold: float = MIN_CONFIDENCE) -> list[SplitPart]:
    """Разделы 2-го уровня, классифицируемые в РАЗНЫЕ типы, — кандидаты на разбиение (FR-ON-9)."""
    sections = [s for s in mdparse.parse_sections(path) if s.level == 2 and s.body]
    if len(sections) < SPLIT_MIN_SECTIONS:
        return []
    parts: list[SplitPart] = []
    for s in sections:
        text = f"# {s.title}\n\n{s.body}\n"
        hyps = classify.classify_file(path.with_name(re.sub(r"[^\w]+", "_", s.title) + ".md"), types, text)
        if hyps and hyps[0].confidence >= threshold:
            parts.append(SplitPart(s.title, s.line, hyps[0].type, hyps[0].confidence))
    if len({p.type for p in parts}) < 2:
        return []
    return parts


def split_remainder(path: Path, parts: list[dict]) -> list[str]:
    """Что при разбиении НЕ войдёт в части: разделы 2-го уровня без типа и вступление до первого раздела."""
    if not parts:
        return []
    wanted = {p["heading"] for p in parts}
    sections = mdparse.parse_sections(path)
    out = [s.title for s in sections if s.level == 2 and s.title not in wanted]
    if any(s.level < 2 and s.body for s in sections):
        out.insert(0, "вступление до первого раздела")
    return out


def _model_split(answer: dict, path: Path, types: dict[str, catalog.TypeSpec], confidence: float) -> list[SplitPart]:
    """Части из ответа Архивариуса («разбить»: [{часть, тип}]) по реальным разделам файла."""
    items = answer.get("разбить") if isinstance(answer, dict) else None
    if not isinstance(items, list) or not items:
        return []
    sections = {s.title.strip().lower(): s for s in mdparse.parse_sections(path) if s.level == 2}
    parts: list[SplitPart] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("часть", "")).strip().lower()
        ptype = str(it.get("тип", "")).strip()
        sec = sections.get(title)
        if sec is not None and ptype in types:
            parts.append(SplitPart(sec.title, sec.line, ptype, round(confidence, 3)))
    return parts if len({p.type for p in parts}) >= 1 else []


# ------------------------------------------------------------------ вопросы автору


def questions_for(path: Path, spec: catalog.TypeSpec | None, pv: Preview | None, cm: ColumnMap | None) -> list[str]:
    """Вопросы автору (FR-ON-14): с файлом и строкой, где строка известна; формат «файл:строка: вопрос»."""
    out: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # имя в двух написаниях: «Ё/Е» и регистр
    first_line: dict[str, int] = {}
    for i, ln in enumerate(lines, start=1):
        for m in re.finditer(r"\b[А-ЯЁ][а-яё]{2,}\b", ln):
            first_line.setdefault(m.group(0), i)
    by_key: dict[str, set[str]] = {}
    for n in first_line:
        by_key.setdefault(n.replace("ё", "е").replace("Ё", "Е").lower(), set()).add(n)
    for k, variants in sorted(by_key.items()):
        if len(variants) > 1 and not all(v.lower() == v for v in variants):
            ordered = sorted(variants, key=lambda v: (first_line[v], v))
            out.append(f"{path.name}:{first_line[ordered[0]]}: имя в разных написаниях: "
                       + ", ".join(f"{v} (стр. {first_line[v]})" for v in ordered))
    # две даты в одной строке таблицы
    for i, ln in enumerate(lines, start=1):
        if ln.startswith("|") and len(re.findall(r"\b\d{1,2}\.\d{2}\.\d{4}\b", ln)) >= 2:
            out.append(f"{path.name}:{i}: две даты в одной строке — какая относится к событию?")
    if cm and cm.missing:
        line = _table_line(lines, cm.headers)
        where = f"{path.name}:{line}" if line else path.name
        out.append(f"{where}: в таблице нет обязательных колонок типа «{spec.name if spec else ''}»: {', '.join(cm.missing)}")
    if pv and pv.error:
        out.append(f"{path.name}: {pv.error}")
    if spec and spec.name == "стиль":
        for i, ln in enumerate(lines, start=1):
            if ln.startswith("|") and re.search(r"\d", ln) and not re.search(r"\d+(?:[.,]\d+)?\s*(слов|%|на\s*\d+|знак)", ln) \
                    and re.search(r"норм|порог|брак", text.lower()) and i > 2 and "---" not in ln:
                cells = [c.strip() for c in ln.strip("|").split("|")]
                if not any(re.fullmatch(r"[\d.,—-]+", c) for c in cells[2:]):
                    out.append(f"{path.name}:{i}: число не похоже на порог (нет единицы измерения)")
                    break
    return out[:20]


def _table_line(lines: list[str], headers: list[str]) -> int:
    for i, ln in enumerate(lines, start=1):
        if ln.strip().startswith("|") and normalize._split_row(ln) == headers:
            return i
    return 0


# ------------------------------------------------------------------ модельный слой (Архивариус)


def _model_input(path: Path, types: dict[str, catalog.TypeSpec]) -> str:
    """Вход Архивариуса (Д-10): оглавление, начала разделов, сводка таблиц — внутри ограждения (FR-SC-8)."""
    sections = mdparse.parse_sections(path)
    toc = [f"{'#' * max(1, s.level)} {s.title}" for s in sections if s.level]
    heads = [f"{'#' * max(1, s.level)} {s.title}\n{s.body[:2000]}" for s in sections if s.level][:12]
    tables: list[str] = []
    try:
        for t in mdparse.parse_tables(path)[:6]:
            tables.append("| " + " | ".join(t.headers) + " |\n" + "\n".join(
                "| " + " | ".join(r.get(h, "") for h in t.headers) + " |" for r in t.rows[:2]))
    except mdparse.MarkupError:
        pass
    body = "\n\n".join(["## Оглавление", "\n".join(toc) or "(нет заголовков)",
                        "## Фрагменты разделов", "\n\n".join(heads) or path.read_text(encoding="utf-8", errors="replace")[:2000],
                        "## Таблицы (заголовки и две строки)", "\n\n".join(tables) or "(таблиц нет)"])
    body = body.replace(FENCE_CLOSE, "</ сырьё>")
    return f"# Файл: {path.name}\n\n{FENCE_OPEN}\n{body}\n{FENCE_CLOSE}"


def _system_prompt(ws: Workspace, types: dict[str, catalog.TypeSpec]) -> str:
    from importlib import resources

    from jinja2 import Environment

    man = manifest_mod.load(ws.root)
    series = man.проект.имя if man else "серия"
    tpl_path = ws.root / "промпты" / "архивариус.md"
    template = tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else \
        resources.files("konveyer").joinpath("шаблоны/архивариус_система.md").read_text(encoding="utf-8")
    # весь каталог, включая типы без машинного чтения (проза, снапшоты…): иначе модель их не знает и зовёт «сырьё»
    return Environment().from_string(template).render(series=series, types=[
        {"name": t.name, "purpose": t.purpose, "machine": bool(t.extractions)} for t in sorted(types.values(), key=lambda t: t.name)])


def prompt_for(ws: Workspace, path: Path, types: dict[str, catalog.TypeSpec], system: str | None = None) -> tuple[str, str, str]:
    """(system, user, ключ кэша) для файла сырья — общий для вызова модели и для приёма ответа вручную."""
    system = system if system is not None else _system_prompt(ws, types)
    user = _model_input(path, types)
    key = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()
    return system, user, key


def _pick_answer(data, path: Path) -> dict | None:
    """Элемент ответа для файла: по полю «файл» (имя или основа), при одном элементе — он и есть ответ."""
    items = [it for it in (data if isinstance(data, list) else []) if isinstance(it, dict) and it.get("тип")]
    for it in items:
        name = str(it.get("файл", "")).strip()
        if name and name in (path.name, path.stem, path.stem.removesuffix(".md")):
            return it
    return items[0] if len(items) == 1 else None


def model_layer(ws: Workspace, cfg, files: list[Path], types: dict[str, catalog.TypeSpec],
                priority: dict[str, float] | None = None) -> tuple[dict[str, dict], str]:
    """Ответы Архивариуса по файлам: {имя: {тип, уверенность, обоснование, …}}; кэш по хэшу входа (FR-ON-8, FR-ON-10).
    Не больше `onboarding_max_docs` файлов за прогон (R-9), первыми — файлы с низкой машинной уверенностью.
    Промпт каждого файла сохраняется в `онбординг/промпты/` (ручной режим, FR-RL-3); нечитаемый ответ — в
    `онбординг/ответы/<файл>_сырой.md` (FR-AD-3). Возвращает (ответы, заметка о модели — пусто, если всё прошло)."""
    from .. import adapters, llmjson

    cache_dir = onboarding_dir(ws) / MODEL_CACHE
    prompts_dir = onboarding_dir(ws) / PROMPTS_DIR
    answers_dir = onboarding_dir(ws) / ANSWERS_DIR
    for d in (cache_dir, prompts_dir):
        d.mkdir(parents=True, exist_ok=True)
    system = _system_prompt(ws, types)
    limit = int(getattr(cfg, "onboarding_max_docs", MAX_MODEL_DOCS) or MAX_MODEL_DOCS) if cfg is not None else MAX_MODEL_DOCS
    ordered = sorted(files, key=lambda p: ((priority or {}).get(p.name, 0.0), p.name))
    chosen, left = ordered[:limit], ordered[limit:]
    out: dict[str, dict] = {}
    reason = ""
    for path in chosen:
        _, user, key = prompt_for(ws, path, types, system)
        cached = cache_dir / f"{key}.json"
        prompt_path = prompts_dir / f"{path.stem}.md"
        prompt_path.write_text(f"# Системный промпт\n\n{system}\n\n# Запрос\n\n{user}\n", encoding="utf-8")
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
        else:
            raw = ""
            try:
                raw = adapters.call_role(cfg, "архивариус", system, user, ws.logs, role="архивариус")
                data = llmjson.extract_json(raw, list)
            except adapters.ManualModeNeeded as e:
                reason = reason or (f"модельный слой недоступен: {e.reason} Промпты сохранены в онбординг/{PROMPTS_DIR}/; "
                                    f"ответ модели принимается файлом: `konveyer онбординг --модель --ответ <файл сырья>=<путь к ответу>`")
                continue
            except ValueError as e:  # ответ не разобран — сохраняем целиком и предъявляем (FR-AD-3)
                answers_dir.mkdir(parents=True, exist_ok=True)
                raw_path = answers_dir / f"{path.stem}_сырой.md"
                raw_path.write_text(raw, encoding="utf-8")
                reason = reason or f"ответ модели по «{path.name}» не разобран ({e}); сырой ответ: онбординг/{ANSWERS_DIR}/{raw_path.name}"
                continue
            except Exception as e:  # noqa: BLE001 — сеть/биллинг: машинный слой остаётся
                reason = reason or f"модельный слой не ответил: {type(e).__name__}: {str(e)[:120]}"
                continue
            cached.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        item = _pick_answer(data, path)
        if item:
            out[path.name] = item
    if left:
        note = (f"модельному слою отправлено {len(chosen)} из {len(files)} файлов (лимит onboarding_max_docs={limit}); "
                f"не отправлены: {', '.join(p.name for p in left[:5])}{'…' if len(left) > 5 else ''}")
        reason = f"{reason}; {note}" if reason else note
    return out, reason


def manual_answer(ws: Workspace, file: str, answer_text: str) -> dict:
    """Приём ответа Архивариуса вручную (FR-RL-3): текст ответа (JSON, можно с обрамлением) кладётся в кэш под тем же
    ключом, что и автоматический вызов; следующий `konveyer онбординг --модель` использует его без обращения к API."""
    from .. import llmjson

    entry = importer.entry_by_name(importer.load_index(ws), file)
    if entry is None or not entry.извлечено_в:
        raise KeyError(f"файла «{file}» нет в сырье с извлечением")
    path = ws.root / entry.извлечено_в
    types = catalog.load_types(ws.root)
    _, _, key = prompt_for(ws, path, types)
    data = llmjson.extract_json(answer_text, list)
    cache_dir = onboarding_dir(ws) / MODEL_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _pick_answer(data, path) or {}


# ------------------------------------------------------------------ сборка предложения


def display_stem(source: str) -> str:
    """Имя файла сырья без пути (`папка__`), расширения и суффикса версии «~дата» — для заголовка документа."""
    return VERSION_SUFFIX_RE.sub("", Path(source.split("__")[-1]).stem) or "документ"


def source_stem(source: str, strip_volume: bool = True) -> str:
    """Основа имени документа из имени сырья: последняя часть пути, без расширения, без суффикса версии «~дата»,
    без уже стоящего числового префикса и (когда том приписывается отдельно) без маркера тома — иначе получается
    «23_23_Поглавник_Том2_Том1». В папочных типах (проза) маркер тома в имени файла — единственный носитель тома."""
    stem = NUM_PREFIX_RE.sub("", display_stem(source))
    if strip_volume:
        stem = manifest_mod.VOLUME_MARK_RE.sub("", stem)
    stem = re.sub(r"[^\w\- ]+", "_", stem).replace(" ", "_").strip("_")
    stem = re.sub(r"_{2,}", "_", stem)
    return stem or "документ"


def _proposed_name(spec: catalog.TypeSpec, source: str, volume: int | None, taken: set[str],
                   reusable: set[str] | None = None) -> str:
    """Имя документа библиотеки по правилу проекта (FR-ON-16): имя по умолчанию типа; занято — NN_Название[_ТомN].md;
    для папочных типов — Папка/Название.md. Занятое имя всегда получает суффикс: два файла не сливаются в один.
    Нетронутый каркас стартового комплекта (`reusable`) уступает своё имя импортированному документу."""
    if spec.default_name and spec.default_name.endswith("/"):
        stem = source_stem(source, strip_volume=False)
        name = f"{spec.default_name}{stem}.md"
        n = 2
        while name in taken:
            name = f"{spec.default_name}{stem}_{n}.md"
            n += 1
        return name
    stem = source_stem(source)
    default = (spec.default_name or f"{spec.name}.md").replace("{том}", str(volume or 1))
    if default not in taken:
        return default
    if reusable is not None and default in reusable:
        reusable.discard(default)
        return default
    prefix = re.match(r"^(\d+(?:\.\d+)?)_", default)
    num = prefix.group(1) + "_" if prefix else ""
    vol = f"_Том{volume}" if spec.per_volume and volume else ""
    name = f"{num}{stem}{vol}.md"
    n = 2
    while name in taken:
        name = f"{num}{stem}{vol}_{n}.md"
        n += 1
    return name


def untouched_skeletons(library: Path | None, man: manifest_mod.Manifest | None, types: dict[str, catalog.TypeSpec]) -> set[str]:
    """Документы стартового комплекта, которые автор не трогал (текст равен каркасу типа): онбординг может
    занять их имя вместо того, чтобы класть «14_Мир_2.md» рядом с пустым «14_Мир.md»."""
    from .. import project

    out: set[str] = set()
    if library is None or man is None or not library.is_dir():
        return out
    for e in man.библиотека:
        spec = types.get(e.тип)
        path = library / e.файл
        if e.is_folder or spec is None or e.источник or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip() == project.skeleton_for(spec, e.том or 1).strip():
            out.add(e.файл)
    return out


def library_files(library: Path | None) -> set[str]:
    """Существующие документы библиотеки (относительные пути) — чтобы имя нового документа не затёрло файл на диске."""
    if library is None or not library.is_dir():
        return set()
    return {p.relative_to(library).as_posix() for p in library.rglob("*.md") if p.is_file() and ".git" not in p.parts}


def _sort_key(name: str) -> tuple:
    """Порядок частей при склейке: числа в имени — как числа («часть_2» раньше «часть_10»)."""
    return tuple(int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(name).stem))


def suggest_glue(proposals: list[Proposal], types: dict[str, catalog.TypeSpec]) -> None:
    """FR-ON-9: несколько файлов одного одиночного/потомного типа (пронумерованные части поглавника) — предложение
    склеить в один документ: у всех, кроме первого по порядку, `склеить_с` = первый."""
    groups: dict[tuple[str, int | None], list[Proposal]] = {}
    for pr in proposals:
        spec = types.get(pr.тип)
        if spec is None or spec.multiplicity not in ("один", "по_тому"):
            continue
        groups.setdefault((pr.тип, pr.том), []).append(pr)
    for members in groups.values():
        if len(members) < 2:
            continue
        # головной — тот, чей документ уже существует (обновление), иначе первый по порядку частей
        members.sort(key=lambda p: (not p.обновление, _sort_key(p.файл)))
        head = members[0]
        for pr in members[1:]:
            pr.склеить_с = head.файл
            pr.вопросы.append(f"{pr.файл}: файл того же типа, что «{head.файл}» — склеить в один документ "
                              f"(решение `склеить:{head.файл}`) или оставить отдельным?")


def build(ws: Workspace, *, cfg=None, use_model: bool = False, only: list[str] | None = None,
          library: Path | None = None) -> tuple[list[Proposal], str]:
    """Предложение по всему сырью со статусом «сырьё» (или по `only`). Возвращает (предложения, заметка о модели)."""
    root = ws.root
    types = catalog.load_types(root)
    man = manifest_mod.load(root)
    volume = man.проект.текущий_том if man else 1
    threshold = threshold_of(cfg)
    all_entries = importer.load_index(ws)
    entries = [e for e in all_entries if e.извлечено_в and e.статус == "сырьё" and (only is None or e.файл in only)]
    previous = {p.файл: p for p in load(ws)}
    by_file = {e.файл: e for e in all_entries}
    canon_by_source = {e.исходный_путь: e for e in all_entries if e.статус == "в_каноне" and e.документ_канона}
    lib_path = library if library is not None else ws.library
    taken = {e.файл for e in (man.библиотека if man else [])} | library_files(lib_path)
    reusable = untouched_skeletons(lib_path, man, types)
    files = [(e, root / e.извлечено_в) for e in entries if (root / e.извлечено_в).exists()]
    machine: dict[str, list[classify.Hypothesis]] = {p.name: classify.classify_file(p, types) for _, p in files}
    model_answers: dict[str, dict] = {}
    model_note = ""
    if use_model and files:
        priority = {name: (h[0].confidence if h else 0.0) for name, h in machine.items()}
        model_answers, model_note = model_layer(ws, cfg, [p for _, p in files], types, priority)
    elif use_model:
        model_note = "сырья для модельного слоя нет"
    else:
        model_note = "модельный слой не запрашивался (машинный слой по сигнатурам)"
    proposals: list[Proposal] = []
    for entry, path in files:
        hyps = machine[path.name]
        pr = Proposal(файл=entry.файл, извлечено_в=entry.извлечено_в,
                      гипотезы=[{"тип": h.type, "уверенность": h.confidence, "почему": "; ".join(h.reasons)} for h in hyps[:5]])
        if hyps and hyps[0].confidence >= threshold:
            pr.тип, pr.уверенность = hyps[0].type, hyps[0].confidence
        answer = model_answers.get(path.name)
        if answer:
            pr.модель = answer
            mt = str(answer.get("тип", "")).strip()
            mc = float(answer.get("уверенность", 0) or 0)
            if mt in types and mc >= max(pr.уверенность, threshold):
                pr.тип, pr.уверенность, pr.источник_решения = mt, round(mc, 3), "модель"
            elif mt == "сырьё" and pr.тип == "сырьё" and mc > pr.уверенность:
                pr.уверенность, pr.источник_решения = round(mc, 3), "модель"
            elif mt == "сырьё" and pr.тип != "сырьё":
                # «сырьё» от модели не перебивает машинную гипотезу выше порога — это вопрос автору, не решение
                pr.вопросы.append(f"{path.name}: Архивариус предлагает оставить сырьём ({mc:.0%}): "
                                  f"{answer.get('обоснование', '—')}; машинный слой видит «{pr.тип}» ({pr.уверенность:.0%})")
        # решение автора из прежнего предложения сохраняется (FR-ON-10): по имени, а для новой версии источника —
        # по исходному пути прежней (вытесненной) записи
        prev = previous.get(entry.файл) or _previous_by_source(previous, by_file, entry)
        if prev and prev.решение:
            pr.решение = prev.решение
            if prev.решение.startswith("тип:"):
                pr.тип, pr.источник_решения = prev.решение[4:].strip(), "автор"
        spec = types.get(pr.тип)
        canon_prev = canon_by_source.get(entry.исходный_путь)
        if spec is not None:
            pr.том = (manifest_mod.doc_volume(Path(entry.файл)) or volume) if spec.per_volume else None
            overrides: dict[str, str] = {}
            if canon_prev is not None and man is not None:
                ce = man.entry_for(canon_prev.документ_канона)
                if ce is not None and ce.колонки:
                    overrides.update({k: v for k, v in ce.колонки.items() if isinstance(v, str)})
            if prev and prev.колонки:
                overrides.update(dict((prev.колонки or {}).get("автор") or {}))
            cm = column_map(path, spec, overrides)
            pr.колонки = asdict(cm) if cm else None
            source_mark = f"сырьё/оригиналы/{entry.файл}"
            shown = normalized_preview_path(ws, spec, path, cm.mapping if cm else None, source_mark)
            pv = preview(shown, spec, root, {}, pr.том or volume, fragment_from=path)
            pr.предпросмотр = asdict(pv)
            pr.вопросы += questions_for(path, spec, pv, cm)
            # предпросмотр с пометками «⚠ решение автора» — ровно то, что уйдёт в библиотеку
            normalized_preview_path(ws, spec, path, cm.mapping if cm else None, source_mark, pr.вопросы)
            if canon_prev is not None and canon_prev.тип == spec.name:
                pr.имя_документа, pr.обновление = canon_prev.документ_канона, True
            else:
                pr.имя_документа = _proposed_name(spec, entry.файл, pr.том, taken, reusable)
                pr.обновление = pr.имя_документа in taken  # занял имя нетронутого каркаса
                taken.add(pr.имя_документа)
        else:
            pr.вопросы += questions_for(path, None, None, None)
        pr.разбить = [asdict(p) for p in split_candidates(path, types, threshold)]
        if not pr.разбить and answer:
            pr.разбить = [asdict(p) for p in _model_split(answer, path, types, float(answer.get("уверенность", 0) or 0))]
        pr.остаток = split_remainder(path, pr.разбить)
        proposals.append(pr)
    suggest_glue(proposals, types)
    return proposals, model_note


def _previous_by_source(previous: dict[str, Proposal], by_file: dict[str, importer.RawEntry],
                        entry: importer.RawEntry) -> Proposal | None:
    for name, pr in previous.items():
        old = by_file.get(name)
        if old is not None and old.файл != entry.файл and old.исходный_путь == entry.исходный_путь and old.статус == "заменён":
            return pr
    return None


# ------------------------------------------------------------------ сохранение


def save(ws: Workspace, proposals: list[Proposal], model_note: str = "") -> tuple[Path, Path]:
    d = onboarding_dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    data = {"версия": 1, "модель": model_note, "предложения": [asdict(p) for p in proposals]}
    pj = d / PROPOSAL_JSON
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pm = d / PROPOSAL_MD
    pm.write_text(render_md(proposals, model_note), encoding="utf-8")
    return pj, pm


def load(ws: Workspace) -> list[Proposal]:
    pj = onboarding_dir(ws) / PROPOSAL_JSON
    if not pj.exists():
        return []
    data = json.loads(pj.read_text(encoding="utf-8"))
    out = []
    for d in data.get("предложения", []):
        out.append(Proposal(**{k: v for k, v in d.items() if k in Proposal.__dataclass_fields__}))
    return out


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    return ["| " + " | ".join(esc(h) for h in headers) + " |", "|" + "---|" * len(headers)] + \
           ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]


def render_md(proposals: list[Proposal], model_note: str = "") -> str:
    """Человекочитаемое предложение; без даты и иного шума — повторный запуск даёт тот же файл (FR-ON-10, П-6)."""
    lines = ["# Предложение онбординга", "",
             f"_{model_note or 'машинный слой'}_", "",
             "Решение по каждому файлу — в `онбординг/предложение.json`, поле `решение`: `принять`, `тип:<имя типа>`, "
             "`сырьё` (доступен поиском, в реестры не идёт), `отклонить`, `разбить`, `склеить:<файл сырья>`; "
             "сопоставление колонок — `колонка:<поле>=<заголовок>`; для повторного импорта в конфликте — "
             "`источник` / `канон`. Затем `konveyer онбординг --применить`.", "",
             "| Файл | Тип | Уверенность | Записей | Документ канона | Решение |", "|---|---|---|---|---|---|"]
    for p in proposals:
        recs = (p.предпросмотр or {}).get("records", "—") if p.предпросмотр else "—"
        doc = (p.имя_документа + (" (обновление)" if p.обновление else "")) if p.имя_документа else "—"  # обновление: документ уже есть
        lines.append(f"| {p.файл} | {p.тип} | {p.уверенность:.0%} | {recs} | {doc} | {p.решение or '—'} |")
    for p in proposals:
        lines += ["", f"## {p.файл} → `{p.тип}` ({p.уверенность:.0%}, {p.источник_решения})", ""]
        if p.гипотезы:
            lines.append("Гипотезы: " + "; ".join(f"{h['тип']} {h['уверенность']:.0%}" for h in p.гипотезы[:4]))
        pv = p.предпросмотр or {}
        if pv.get("fragment"):
            lines += ["", "**Исходный фрагмент:**", "", "```text", pv["fragment"], "```"]
        if pv:
            lines += ["", f"**Что прочитает машина** ({pv.get('format') or '—'}; записей: {pv.get('records', 0)}):"]
            for row in pv.get("rows", []):
                lines.append("- " + "; ".join(f"{k}: {v}" for k, v in row.items() if v not in ("", None, [], {})))
            if pv.get("error"):
                lines.append(f"- ⚠ {pv['error']}")
            if pv.get("note"):
                lines.append(f"- {pv['note']}")
            if pv.get("to_window"):
                lines.append("**В окно Писателя:** " + "; ".join(pv["to_window"]))
            if pv.get("internal"):
                lines.append("**Останется внутренним:** " + "; ".join(pv["internal"]))
        if p.колонки:
            cm = p.колонки
            lines += ["", "**Сопоставление колонок:** " + ", ".join(f"{k} ← «{v}»" for k, v in cm.get("mapping", {}).items())]
            if cm.get("missing"):
                lines.append(f"⚠ нет обязательных колонок: {', '.join(cm['missing'])}")
            if cm.get("generated"):
                lines.append(f"колонка «{cm['generated']}» будет заполнена порядковыми идентификаторами")
            if cm.get("sample") and cm.get("mapping"):
                fields = list(cm["mapping"])
                lines += ["", "Результат сопоставления на первых строках:", ""]
                lines += _md_table(fields, [[row.get(f, "") for f in fields] for row in cm["sample"]])
        if p.разбить:
            lines += ["", "**Можно разбить:** " + "; ".join(f"«{s['heading']}» → {s['type']}" for s in p.разбить)]
            if p.остаток:
                where = f"документ типа «{p.тип}»" if p.тип != "сырьё" else "сырьё (в канон не войдёт)"
                lines.append(f"Не войдёт в части: {', '.join(f'«{s}»' for s in p.остаток)} → {where}")
        if p.склеить_с:
            lines += ["", f"**Можно склеить** с «{p.склеить_с}» в один документ: решение `склеить:{p.склеить_с}`"]
        if p.модель:
            lines += ["", f"**Архивариус:** {p.модель.get('обоснование', '')} "
                          f"(читать: {p.модель.get('что_машина_сможет_читать', '—')}; не хватает: {p.модель.get('чего_не_хватает', '—')})"]
        if p.вопросы:
            lines += ["", "**Вопросы автору:**"] + [f"- {q}" for q in p.вопросы]
    return "\n".join(lines) + "\n"


def valid_decision(decision: str) -> bool:
    return decision in DECISIONS or any(decision.startswith(p) and len(decision) > len(p) for p in DECISION_PREFIXES)


def set_decision(ws: Workspace, file: str, decision: str) -> Proposal:
    """Решение автора по файлу (FR-ON-12) — записывается в предложение.json. `колонка:<поле>=<заголовок>` правит
    сопоставление колонок (FR-ON-13), не меняя решения. Решение, расходящееся с ответом Архивариуса, пишется в
    `журналы/отклонено_архивариусом.jsonl` (П-7)."""
    decision = decision.strip()
    if not valid_decision(decision):
        raise ValueError(f"решение «{decision}»: допустимо {', '.join(DECISIONS)}, тип:<имя>, склеить:<файл>, колонка:<поле>=<заголовок>")
    proposals = load(ws)
    pr = next((p for p in proposals if p.файл == file), None)
    if pr is None:
        raise KeyError(f"файла «{file}» нет в предложении — выполните `konveyer онбординг`")
    if decision.startswith("колонка:"):
        fld, _, header = decision[len("колонка:"):].partition("=")
        fld, header = fld.strip(), header.strip()
        if not fld or not header:
            raise ValueError("сопоставление задаётся как колонка:<поле>=<заголовок в файле>")
        cm = pr.колонки or asdict(ColumnMap(extraction=""))
        cm.setdefault("mapping", {})[fld] = header
        cm.setdefault("автор", {})[fld] = header
        cm["missing"] = [m for m in cm.get("missing", []) if m != fld]
        pr.колонки = cm
    else:
        if decision.startswith("склеить:"):
            head = decision[len("склеить:"):].strip()
            if head == file or not any(p.файл == head for p in proposals):
                raise ValueError(f"склеить:{head}: такого файла нет в предложении (нужен другой файл сырья)")
        pr.решение = decision
        if decision.startswith("тип:"):
            pr.тип, pr.источник_решения = decision[4:].strip(), "автор"
        _log_model_rejection(ws, pr, decision)
    data = json.loads((onboarding_dir(ws) / PROPOSAL_JSON).read_text(encoding="utf-8"))
    save(ws, proposals, data.get("модель", ""))
    return pr


def _log_model_rejection(ws: Workspace, pr: Proposal, decision: str) -> None:
    if not pr.модель:
        return
    proposed = str(pr.модель.get("тип", "")).strip()
    chosen = decision[4:].strip() if decision.startswith("тип:") else decision
    if not proposed or chosen == "принять" or chosen == proposed or (proposed == "сырьё" and chosen == "сырьё"):
        return
    ws.logs.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"файл": pr.файл, "предложено_моделью": proposed, "уверенность": pr.модель.get("уверенность"),
                       "обоснование": pr.модель.get("обоснование", ""), "решение_автора": decision}, ensure_ascii=False)
    with (ws.logs / REJECTED_LOG).open("a", encoding="utf-8") as f:
        f.write(line + "\n")
