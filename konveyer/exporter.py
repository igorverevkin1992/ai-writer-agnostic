"""Exporter: MD-библиотека → JSON-выгрузки (FR-X1…FR-X3, модель данных 5.2).

Выгрузки генерируются ТОЛЬКО экспортёром; правка руками бессмысленна —
перетираются. Идемпотентен: одинаковый канон → байт-в-байт одинаковые файлы.
Хэши выгрузок пишутся в журналы/экспорт.jsonl для контроля дрейфа канона.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from . import guard, mdparse, realcanon, textutils
from .mdparse import MarkupError, cell, parse_number
from .schemas import (
    ChronicleEvent,
    ChronologyEvent,
    Brief,
    ContinuityEvent,
    DocumentSpec,
    Dose,
    Dossier,
    InfoBan,
    MatrixFact,
    Norm,
    Plant,
    StopRule,
    StoryCircle,
    Act,
    Arc,
)

# Обязательные идентификаторы норм (02 §5) — verifier-1 берёт пороги только отсюда.
REQUIRED_NORMS = [
    "средняя_длина",
    "доля_коротких",
    "доля_длинных",
    "короткая_фраза_порог",
    "длинная_фраза_порог",
    "был_на_250",
    "ttr_окно_слов",
    "ttr_мин",
]
# Опциональные нормы: максимум_длины, объём_допуск — проверки пропускаются, если их нет.
# Пороги n-грамм заданы утверждённым ТЗ (Р-017) и берутся оттуда, если канон их не переопределил.
TZ_DEFAULT_NORMS = {
    "утечка_нграмма": Norm(min=6, max=6, unit="слов", source="ТЗ FR-V1.6 (Р-017)"),
    "повтор_нграмма": Norm(min=5, max=5, unit="слов", source="ТЗ FR-V1.7 (Р-017)"),
}


# ------------------------------------------------------------------ документы по томам

# Маркер тома в имени документа: «…_Том2.md», «УГАР_Том2_Реестр…», «…_Т2.md» (аудит 2, п. 27).
VOLUME_MARK_RE = re.compile(r"(?:Том|_Т)\s*0*(\d+)(?!\d)")
# Документы, которые ведутся ПО ТОМАМ: для тома N ≥ 2 нужен файл с маркером тома, документы без
# маркера принадлежат тому 1 (совместимость реальной библиотеки: `23_Поглавник_Часть_I.md`).
PER_VOLUME_PATTERNS = ("23_*.md", "21_*.md", "22_*.md", "31_*.md", "35_*.md", "*Реестр_информационного_режима*.md")


def doc_volume(path: Path) -> int | None:
    """Том по имени документа (None — маркера нет: документ общий для серии или тома 1)."""
    m = VOLUME_MARK_RE.search(path.stem)
    return int(m.group(1)) if m else None


def volume_docs(library: Path, pattern: str, volume: int = 1) -> list[Path]:
    """Документы канона по glob-шаблону ДЛЯ ТОМА `volume` (единственная точка выбора документа по тому):

    * есть файлы с маркером нужного тома (`Том{N}`/`_Т{N}` в имени) — только они;
    * иначе — файлы без маркера тома (общие для серии; для тома 1 — как раньше), но для потомных
      документов (`PER_VOLUME_PATTERNS`) и тома N ≥ 2 файлы без маркера не подходят — это том 1;
    * файлы с маркером ДРУГОГО тома не берутся никогда (документы томов не смешиваются).
    """
    matches = sorted(library.glob(pattern))
    marked = [p for p in matches if doc_volume(p) == volume]
    if marked:
        return marked
    if volume >= 2 and pattern in PER_VOLUME_PATTERNS:
        return []
    return [p for p in matches if doc_volume(p) is None]


def _find_file(library: Path, pattern: str, volume: int = 1) -> Path:
    matches = volume_docs(library, pattern, volume)
    if not matches:
        hint = f" для тома {volume} (имя с «Том{volume}»)" if volume >= 2 else ""
        raise MarkupError(library / pattern, 0, f"файл канона не найден{hint}")
    return matches[0]


def missing_volume_docs(library: Path, volume: int) -> list[str]:
    """Чего не хватает в библиотеке, чтобы вести том `volume`: поглавник/реестр (источник брифов)
    и матрица 3.1. Пусто — том можно открыть (`konveyer volume open N`)."""
    missing: list[str] = []
    if not volume_docs(library, "23_*.md", volume) and not volume_docs(library, "*Реестр_информационного_режима*.md", volume):
        missing.append(f"23_Поглавник_Том{volume}.md (или УГАР_Том{volume}_Реестр_информационного_режима.md)")
    if not volume_docs(library, "31_*.md", volume):
        missing.append(f"31_…_Том{volume}.md (эпистемическая матрица тома)")
    return missing


def _render(model: BaseModel | list | dict) -> str:
    """Текст выгрузки (детерминированный JSON, FR-X3) — без записи."""
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
    """Инкрементальная запись (26а): файл перезаписывается, только если его содержимое изменилось —
    без лишних fsync и `os.replace` под чужим чтением (Windows: PermissionError). `known_hash` — хэш из
    индекс.json прошлого экспорта: расходится с новым — пишем сразу; иначе истина — содержимое на
    диске (правка выгрузок руками перетирается, как обещает докстринг модуля)."""
    digest = _sha(text)
    if known_hash is not None and known_hash != digest:
        guard.write_text(path, text)  # прошлый экспорт был другим — без чтения диска
        return digest
    try:
        if path.read_text(encoding="utf-8") == text:
            return digest
    except (OSError, UnicodeDecodeError):
        pass
    guard.write_text(path, text)
    return digest


def _dump(path: Path, model: BaseModel | list | dict, known_hash: str | None = None) -> str:
    return _write_if_changed(path, _render(model), known_hash)


def load_manifest(exports_dir: Path) -> dict[str, str]:
    """{файл: sha256} прошлого экспорта; пусто, если индекс.json нет или он повреждён."""
    path = exports_dir / "индекс.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


# ------------------------------------------------------------------ разборы


def export_norms(library: Path, volume: int = 1) -> dict[str, Norm]:
    path = _find_file(library, "02_*.md", volume)
    norms: dict[str, Norm] = {}
    try:
        table = mdparse.require_table(path, ["id", "мин", "макс"], section_pattern=r"§\s*5")
        for row in table.rows:
            norm_id = cell(row, "id")
            if not norm_id:
                continue
            norms[norm_id] = Norm(
                min=parse_number(cell(row, "мин")),
                max=parse_number(cell(row, "макс")),
                brak=parse_number(cell(row, "брак")),
                unit=cell(row, "единиц"),
                source=f"{path.name} §5",
            )
    except MarkupError:
        # реальный канон: числовые ориентиры прозой (Р-015)
        prose = realcanon.parse_norms_prose(path)
        if prose is None:
            raise
        norms = prose
    for norm_id, default in TZ_DEFAULT_NORMS.items():
        norms.setdefault(norm_id, default)
    if "усилители_на_1000" not in norms:
        found = realcanon.parse_intensifier_norm(_journal(library))
        if found:
            norms["усилители_на_1000"] = found[1]
    missing = [n for n in REQUIRED_NORMS if n not in norms]
    if missing:
        raise MarkupError(path, 1, f"в нормах §5 нет обязательных id: {missing}")
    empty = [
        n for n in REQUIRED_NORMS
        if norms[n].min is None and norms[n].max is None and norms[n].brak is None
    ]
    if empty:
        raise MarkupError(
            path, 1,
            f"обязательные нормы без числового значения (мин/макс/брак): {empty} (критерий приёмки 6)",
        )
    return norms


def _journal(library: Path):
    matches = sorted(library.glob("36_*.md"))
    return matches[0] if matches else library / "36_Журнал.md"


def _registry(library: Path, volume: int = 1) -> Path | None:
    matches = volume_docs(library, "*Реестр_информационного_режима*.md", volume)
    return matches[0] if matches else None


def export_stoplists(library: Path, volume: int = 1) -> list[StopRule]:
    rules: list[StopRule] = []

    # 0.3 — стоп-листы линий (по фокалу)
    p03 = _find_file(library, "03_*.md", volume)
    try:
        t = mdparse.require_table(p03, ["rule_id", "фокал", "слова"])
        rows03 = t.rows
    except MarkupError:
        rules.extend(realcanon.parse_focal_stoplists(p03))  # «Персональные запреты линий»
        rows03 = []
    # запреты линий фразами («канцелярит — панцирь страха», «сентимент запрещён») — для Э2 (4.1)
    rules.extend(realcanon.parse_line_prose_bans(p03))
    for row in rows03:
        rules.append(
            StopRule(
                scope="0.3",
                rule_id=cell(row, "rule_id"),
                items=[w.strip() for w in cell(row, "слова").split(";") if w.strip()],
                applies_to={"focal": cell(row, "фокал")} if cell(row, "фокал") not in {"", "все"} else {"all": True},
                action="запрет" if "запрет" in cell(row, "действ") else "флаг",
            )
        )

    # 0.4 — лексика эпохи (по году главы)
    p04 = _find_file(library, "04_*.md", volume)
    try:
        t = mdparse.require_table(p04, ["rule_id", "слова", "годы"])
        rows04 = t.rows
    except MarkupError:
        rules.extend(realcanon.parse_anachronisms(p04))  # раздел Е: анахронизмы
        rows04 = []
    for row in rows04:
        years = cell(row, "годы")
        applies: dict = {"all": True}
        m = re.match(r"до\s+(\d{4})", years)
        if m:
            applies = {"year": {"before": int(m.group(1))}}
        else:
            m = re.match(r"(\d{4})\s*[–-]\s*(\d{4})", years)
            if m:
                applies = {"year": {"from": int(m.group(1)), "to": int(m.group(2))}}
        rules.append(
            StopRule(
                scope="0.4",
                rule_id=cell(row, "rule_id"),
                items=[w.strip() for w in cell(row, "слова").split(";") if w.strip()],
                applies_to=applies,
                action="запрет" if "запрет" in cell(row, "действ") else "флаг",
            )
        )

    # Р-016 — словарь наречий-усилителей: таблица в 02 либо текст решения в журнале 36
    p02 = _find_file(library, "02_*.md", volume)
    intensifiers: list[str] = []
    try:
        t = mdparse.require_table(p02, ["слово"], section_pattern=r"[Уу]силител")
        intensifiers = [cell(row, "слово") for row in t.rows if cell(row, "слово")]
    except MarkupError:
        found = realcanon.parse_intensifier_norm(_journal(library))
        if found:
            intensifiers = found[0]
    if intensifiers:
        rules.append(
            StopRule(
                scope="0.4",
                rule_id="Р-016",
                items=intensifiers,
                applies_to={"all": True},
                action="флаг",
                kind="усилитель",
            )
        )
    return rules


def export_matrix(library: Path, volume: int = 1) -> list[MatrixFact]:
    path = _find_file(library, "31_*.md", volume)
    try:
        t = mdparse.require_table(path, ["fact_id", "факт", "субъект"])
    except MarkupError:
        return realcanon.parse_wide_matrix(path)  # широкая матрица реального канона
    facts = []
    for row in t.rows:
        ch = parse_number(cell(row, "узнаёт"))
        facts.append(
            MatrixFact(
                fact_id=cell(row, "fact_id"),
                fact=cell(row, "факт"),
                subject=cell(row, "субъект"),
                from_chapter=int(ch) if ch is not None else None,
                source=cell(row, "источник"),
                note=cell(row, "примечани"),
            )
        )
    return facts


_PLACE_RE = re.compile(r"т\s*(\d+)(?:\s*гл\s*(\d+))?", re.IGNORECASE)


def _parse_place(text: str) -> dict:
    m = _PLACE_RE.search(text)
    if not m:
        return {}
    place: dict = {"vol": int(m.group(1))}
    if m.group(2):
        place["ch"] = int(m.group(2))
    return place


def export_plants(library: Path, volume: int = 1) -> list[Plant]:
    if not volume_docs(library, "32_*.md", volume):
        reg = _registry(library, volume)
        if reg is not None:
            plants = realcanon.parse_plants_registry(reg)  # §7 «Реестр дальних закладок»
            _, reg_volume = realcanon.registry_year_volume(reg)
            for p23 in volume_docs(library, "23_*.md", volume):
                plants.extend(realcanon.parse_poglavnik_plants(p23, reg_volume, plants))
            return plants
    path = _find_file(library, "32_*.md", volume)
    t = mdparse.require_table(path, ["plant_id", "что", "положена"])
    plants = []
    for row in t.rows:
        fires = [
            _parse_place(x) for x in cell(row, "выстрел").split(";") if _parse_place(x)
        ]
        plants.append(
            Plant(
                plant_id=cell(row, "plant_id"),
                what=cell(row, "что"),
                placed=_parse_place(cell(row, "положена")),
                fires=fires,
                status=cell(row, "статус"),
            )
        )
    return plants


def export_continuity(library: Path, volume: int = 1) -> list[ContinuityEvent]:
    path = _find_file(library, "33_*.md", volume)
    try:
        t = mdparse.require_table(path, ["дата", "событие"])
    except MarkupError:
        return realcanon.parse_continuity_bullets(path)  # буллеты «факт · т.X гл.Y · статус»
    return [
        ContinuityEvent(
            date=cell(row, "дата"),
            event=cell(row, "событие"),
            chapters=cell(row, "глав"),
            note=cell(row, "примечани"),
        )
        for row in t.rows
    ]


def export_briefs(library: Path, volume: int = 1) -> list[Brief]:
    """Брифы глав ТОМА `volume` (поглавник 23 или реестр информрежима этого тома)."""
    briefs: list[Brief] = []
    for path in volume_docs(library, "23_*.md", volume):
        doc_vol = doc_volume(path) or volume  # без маркера в имени — документ текущего тома (том 1)
        for sec in mdparse.parse_sections(path):
            m = re.match(r"Глава\s+(\d+)", sec.title)
            if not m:
                continue
            body = sec.body
            date = mdparse.parse_kv(body, "Дата")
            year_s = mdparse.parse_kv(body, "Год")
            year = int(year_s) if year_s.isdigit() else None
            if year is None:
                ym = re.search(r"(19|20)\d{2}", date)
                year = int(ym.group()) if ym else None
            vol = parse_number(mdparse.parse_kv(body, "Объём"))
            briefs.append(
                Brief(
                    chapter=int(m.group(1)),
                    volume=doc_vol,
                    date=date,
                    year=year,
                    focal=mdparse.parse_kv(body, "Фокал"),
                    scenes=mdparse.parse_list_items(body, "Сцены"),
                    participants=mdparse.parse_list_items(body, "Участники"),
                    beats=mdparse.parse_list_items(body, "Биты"),
                    bans=mdparse.parse_list_items(body, "Запреты"),
                    not_knows=mdparse.parse_list_items(body, "НЕ знает"),
                    volume_words=int(vol) if vol else None,
                    plants=[p.strip() for p in mdparse.parse_kv(body, "Закладки").split(";") if p.strip()],
                )
            )
    if not briefs:
        reg = _registry(library, volume)
        if reg is not None:
            known = _known_names(library)
            briefs = realcanon.parse_registry_briefs(reg, known)  # постраничная сетка на весь том
            for p23 in volume_docs(library, "23_*.md", volume):
                realcanon.enrich_from_poglavnik(briefs, p23, known)
            realcanon.enrich_from_dossiers(briefs, _real_dossier_paths(library), known)  # «Т.1: … гл. 41» в арке
    if not briefs:
        hint = f" для тома {volume} (нужен документ с «Том{volume}» в имени)" if volume >= 2 else ""
        raise MarkupError(library / "23_*.md", 0, f"поглавник не найден{hint} или в нём нет секций «## Глава N»")
    return briefs


def _real_dossier_paths(library: Path) -> list[Path]:
    """Файлы карточек фактического формата («# Досье 1.3: ИМЯ»)."""
    return [p for p in sorted(library.glob("Досье/*.md")) if "# Досье" in p.read_text(encoding="utf-8")[:200]]


def _known_names(library: Path) -> set[str]:
    """Имена субъектов для распознавания участников сцен и досье: субъекты матрицы 3.1, линии 03
    и имена всех карточек досье (аудит 1.6). Имя из нескольких слов («Куратор ОГПУ») — одно,
    без дубля по первому слову: падежные формы и «куратор» без уточнения находит `find_names`."""
    try:
        names = {f.subject for f in export_matrix(library)}
    except MarkupError:
        names = set()
    for p03 in sorted(library.glob("03_*.md")):
        names |= realcanon.focal_names(p03)
    names = {n for n in names if n}
    return names | realcanon.dossier_names(_real_dossier_paths(library), names)


def export_dossiers(library: Path) -> list[Dossier]:
    paths = sorted(library.glob("Досье/*.md"))
    real = _real_dossier_paths(library)
    if real:
        return realcanon.parse_dossiers_real(real, _known_names(library))
    dossiers = []
    for path in paths:
        sections = mdparse.parse_sections(path)
        name = next((s.title for s in sections if s.level == 1), path.stem)
        rel: dict[str, str] = {}
        rel_sec = mdparse.find_section(sections, r"Отношени")
        if rel_sec:
            for t in mdparse.parse_tables(path, rel_sec.body, start_line=rel_sec.line + 1):
                for row in t.rows:
                    rel[cell(row, "к кому")] = cell(row, "отношени")

        def body_of(pattern: str) -> str:
            s = mdparse.find_section(sections, pattern)
            return s.body if s else ""

        dossiers.append(
            Dossier(
                name=name,
                profile=body_of(r"Профил"),
                physique=body_of(r"Физик"),
                speech=body_of(r"Речев"),
                code=body_of(r"Опознавательн"),
                relations=rel,
            )
        )
    return dossiers


def export_infobans(library: Path, volume: int = 1) -> list[InfoBan]:
    matches = volume_docs(library, "2.2_*.md", volume)
    if not matches:
        reg = _registry(library, volume)
        if reg is not None:
            # реестр тайн тома: глава раскрытия — из ячейки либо из матрицы 3.1 («Читатель»);
            # плюс строки §7 «НЕ упоминается в томе N» — запреты информрежима на весь том (аудит 1.5)
            return realcanon.parse_secrets(reg, _known_names(library), export_matrix(library, volume)) + realcanon.parse_plant_bans(reg)
        return []
    path = matches[0]
    t = mdparse.require_table(path, ["ban_id", "запрет"])
    bans = []
    for row in t.rows:
        until = parse_number(cell(row, "до тома"))
        bans.append(
            InfoBan(
                ban_id=cell(row, "ban_id"),
                text=cell(row, "запрет"),
                until_volume=int(until) if until else None,
            )
        )
    return bans


def find_corpus_file(corpus_dir: Path, chapter: int, volume: int | None = None) -> Path | None:
    """Файл корпуса главы по номеру (и тому). Точное совпадение номера:
    «Глава1» не должна ловить «Глава10» (границы числа обязательны)."""
    if not corpus_dir.exists():
        return None
    ch_rx = re.compile(rf"Глава0*{chapter}(?!\d)")
    vol_rx = re.compile(rf"Том0*{volume}(?!\d)") if volume is not None else None
    for f in sorted(corpus_dir.glob("*.txt")):
        if ch_rx.search(f.stem) and (vol_rx is None or vol_rx.search(f.stem)):
            return f
    return None


def export_parts(library: Path, volume: int = 1) -> list[dict]:
    """Части (акты) тома — из заголовков реестра информрежима; в демо — пусто."""
    reg = _registry(library, volume)
    return realcanon.parse_parts(reg) if reg is not None else []


def load_parts(exports_dir: Path) -> list[dict]:
    return load_export(exports_dir, "parts.json")


CIRCLES_DOC_GLOB = "21_Круги_истории*.md"


def export_circles(library: Path, volume: int = 1) -> list[StoryCircle]:
    """Круги истории (2.1, Р-020) — несущий каркас драматургии; документа может ещё не быть."""
    docs = volume_docs(library, CIRCLES_DOC_GLOB, volume)
    return realcanon.parse_circles(docs[0]) if docs else []


def load_circles(exports_dir: Path) -> list[StoryCircle]:
    return [StoryCircle.model_validate(c) for c in load_export(exports_dir, "circles.json")]


def export_acts(library: Path, volume: int = 1) -> list[Act]:
    """Акты тома (Р-021) — таблица документа 2.1; если её нет — акты = части реестра."""
    docs = volume_docs(library, CIRCLES_DOC_GLOB, volume)
    acts = realcanon.parse_acts(docs[0]) if docs else []
    if acts:
        return acts
    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
    return [
        Act(act=p["part"], title=p["title"], from_chapter=p["from_chapter"], to_chapter=p["to_chapter"],
            parts=roman[p["part"] - 1] if 0 < p["part"] <= len(roman) else str(p["part"]))
        for p in export_parts(library, volume)
    ]


def load_acts(exports_dir: Path) -> list[Act]:
    return [Act.model_validate(a) for a in load_export(exports_dir, "acts.json")]


ARCS_DOC_GLOB = "22_Арки*.md"


def export_arcs(library: Path, volume: int = 1) -> list[Arc]:
    """Арки тома (2.5, Р-025) — таблица «персонаж × акт»; документа может не быть (деградация: пусто)."""
    docs = volume_docs(library, ARCS_DOC_GLOB, volume)
    return realcanon.parse_arcs(docs[0]) if docs else []


def load_arcs(exports_dir: Path) -> list[Arc]:
    """arcs.json; без выгрузки (старые выгрузки/) — пустой список, как без документа."""
    try:
        return [Arc.model_validate(a) for a in load_export(exports_dir, "arcs.json")]
    except FileNotFoundError:
        return []


def export_doses(library: Path, volume: int = 1) -> list[Dose]:
    """Дозы прошлого — §5 реестра информрежима «Три дозы 1913 года»; без реестра/раздела — пусто."""
    reg = _registry(library, volume)
    return realcanon.parse_doses(reg) if reg is not None else []


def load_doses(exports_dir: Path) -> list[Dose]:
    return [Dose.model_validate(d) for d in load_export(exports_dir, "doses.json")]


def export_documents(library: Path, volume: int = 1) -> list[DocumentSpec]:
    """Документы-вставки — §6 реестра информрежима «Реестр документов»; без реестра/раздела — пусто."""
    reg = _registry(library, volume)
    return realcanon.parse_documents(reg) if reg is not None else []


def load_documents(exports_dir: Path) -> list[DocumentSpec]:
    return [DocumentSpec.model_validate(d) for d in load_export(exports_dir, "documents.json")]


CORPUS_INDEX = ".index.json"  # кэш корпуса: имя главы → mtime_ns/size источника и хэш результата


def _load_corpus_index(corpus_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((corpus_dir / CORPUS_INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _corpus_plan(library: Path, exports_dir: Path) -> tuple[list[tuple[Path, str | None, dict]], dict[str, dict]]:
    """Что писать в корпус/ — без записи. Для каждой главы Проза/*.md: (файл корпуса, текст или None,
    запись индекса). Текст None — источник не менялся (mtime_ns и size те же, что в `.index.json`)
    и файл корпуса на месте: нормализация и запись пропускаются (26а)."""
    corpus_dir = exports_dir / "корпус"
    index = _load_corpus_index(corpus_dir)
    plan: list[tuple[Path, str | None, dict]] = []
    for path in sorted(library.glob("Проза/*.md")):
        out = corpus_dir / (path.stem + ".txt")
        st = path.stat()
        entry = index.get(out.name)
        if (
            isinstance(entry, dict)
            and entry.get("mtime_ns") == st.st_mtime_ns
            and entry.get("size") == st.st_size
            and isinstance(entry.get("hash"), str)
            and out.exists()
        ):
            plan.append((out, None, entry))
            continue
        tokens = textutils.normalize(textutils.narrator_text(path.read_text(encoding="utf-8")))
        text = " ".join(tokens) + "\n"
        plan.append((out, text, {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "hash": _sha(text)}))
    return plan, index


def _write_corpus(plan: list[tuple[Path, str | None, dict]], exports_dir: Path, old_index: dict[str, dict]) -> dict[str, str]:
    """Запись корпуса по плану: только изменённые главы; устаревшие файлы удаляются через guard."""
    corpus_dir = exports_dir / "корпус"
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
        guard.write_text(corpus_dir / CORPUS_INDEX,
                         json.dumps(new_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return hashes


def export_corpus(library: Path, exports_dir: Path) -> dict[str, str]:
    """корпус/ — принятые главы в нормализованном виде (для n-грамм и TTR).
    Инкрементально: пересчитываются и перезаписываются только изменённые `Проза/*.md`
    (кэш по mtime+size в `корпус/.index.json`), неизменённые файлы не трогаются."""
    plan, old_index = _corpus_plan(library, exports_dir)
    return _write_corpus(plan, exports_dir, old_index)


# --------------------------------------------------------------- запуск


def run_export(library: Path, exports_dir: Path, logs_dir: Path, volume: int = 1) -> dict[str, str]:
    """Перегенерирует все выгрузки (FR-X1) ДЛЯ ТОМА `volume` (текущий том рабочей области, `ws.volume`):
    потомные документы (поглавник/реестр, матрица 3.1, круги 2.1, арки 2.5) берутся по тому через `volume_docs`,
    документы других томов в выгрузки не попадают; общие документы серии (02–04, 12, 17, 33…) — как есть.
    Корпус (`корпус/`) — по всей `Проза/` (том в имени файла, `find_corpus_file`/`corpus_scope` фильтруют).
    Возвращает {файл: sha256}.

    Сначала разбирается ВЕСЬ канон (включая план корпуса), и только затем пишутся файлы: ошибка
    структуры в одном документе не оставляет выгрузки/ в смешанном состоянии
    со старым индекс.json (контроль дрейфа, FR-X3). Пишутся только изменившиеся
    выгрузки (сравнение с индекс.json и содержимым на диске, 26а).
    """
    parsed = {
        "norms.json": export_norms(library, volume),
        "stoplists.json": export_stoplists(library, volume),
        "matrix.json": export_matrix(library, volume),
        "plants.json": export_plants(library, volume),
        "continuity.json": export_continuity(library, volume),
        "briefs.json": export_briefs(library, volume),
        "dossiers.json": export_dossiers(library),
        "infobans.json": export_infobans(library, volume),
        "parts.json": export_parts(library, volume),
        "circles.json": export_circles(library, volume),
        "acts.json": export_acts(library, volume),
        "arcs.json": export_arcs(library, volume),
        "doses.json": export_doses(library, volume),
        "documents.json": export_documents(library, volume),
        "chronicle.json": export_chronicle(library, volume),
        "chronology.json": export_chronology(library, volume),
    }
    corpus_plan, old_index = _corpus_plan(library, exports_dir)  # тоже до записи
    known = load_manifest(exports_dir)
    hashes: dict[str, str] = {}
    for name, data in parsed.items():
        hashes[name] = _dump(exports_dir / name, data, known.get(name))
    hashes.update(_write_corpus(corpus_plan, exports_dir, old_index))

    manifest = {"files": hashes, "volume": volume}
    _write_if_changed(exports_dir / "индекс.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    guard.append_text(
        logs_dir / "экспорт.jsonl",
        json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "hashes": hashes},
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )
    return hashes


# --------------------------------------------------------------- чтение


def load_export(exports_dir: Path, name: str):
    path = exports_dir / name
    if not path.exists():
        raise FileNotFoundError(
            f"Выгрузка {name} не найдена. Выполните `konveyer export` (экспорт обязателен перед compile, риск R-5)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_norms(exports_dir: Path) -> dict[str, Norm]:
    return {k: Norm.model_validate(v) for k, v in load_export(exports_dir, "norms.json").items()}


def export_chronicle(library: Path, volume: int = 1) -> list:
    """Историческая хроника 17 (анахронизмы, 4.2). Документа может не быть (демо) — пустой список."""
    for path in volume_docs(library, "17_*.md", volume):
        return realcanon.parse_chronicle(path)
    return []


CHRONOLOGY_DOC_GLOB = "12_*.md"


def export_chronology(library: Path, volume: int = 1) -> list[ChronologyEvent]:
    """Генеральная хронология фабулы 12 (позвоночник цикла: события Ф-19xx-NN, тома, главы).
    Документа может не быть (демо) — пустой список."""
    for path in volume_docs(library, CHRONOLOGY_DOC_GLOB, volume):
        return realcanon.parse_chronology(path)
    return []


def load_chronology(exports_dir: Path) -> list[ChronologyEvent]:
    try:
        return [ChronologyEvent.model_validate(r) for r in load_export(exports_dir, "chronology.json")]
    except FileNotFoundError:
        return []


def load_chronicle(exports_dir: Path) -> list[ChronicleEvent]:
    try:
        return [ChronicleEvent.model_validate(r) for r in load_export(exports_dir, "chronicle.json")]
    except FileNotFoundError:
        return []


def load_stoplists(exports_dir: Path) -> list[StopRule]:
    return [StopRule.model_validate(r) for r in load_export(exports_dir, "stoplists.json")]


def load_matrix(exports_dir: Path) -> list[MatrixFact]:
    return [MatrixFact.model_validate(r) for r in load_export(exports_dir, "matrix.json")]


def load_plants(exports_dir: Path) -> list[Plant]:
    return [Plant.model_validate(r) for r in load_export(exports_dir, "plants.json")]


def load_briefs(exports_dir: Path) -> list[Brief]:
    return [Brief.model_validate(r) for r in load_export(exports_dir, "briefs.json")]


def load_brief(exports_dir: Path, chapter: int) -> Brief:
    """Бриф главы текущего тома (выгрузки всегда одного тома — `run_export(volume=…)`)."""
    for b in load_briefs(exports_dir):
        if b.chapter == chapter:
            return b
    raise FileNotFoundError(f"В поглавнике (briefs.json) нет главы {chapter}.")


def export_volume(exports_dir: Path) -> int | None:
    """Том, для которого сделаны выгрузки (индекс.json); None — экспорта не было или он старого формата."""
    try:
        data = json.loads((exports_dir / "индекс.json").read_text(encoding="utf-8"))
        v = data.get("volume") if isinstance(data, dict) else None
        return int(v) if v is not None else None
    except (OSError, ValueError, TypeError):
        return None


def load_continuity(exports_dir: Path) -> list[ContinuityEvent]:
    return [ContinuityEvent.model_validate(r) for r in load_export(exports_dir, "continuity.json")]


def load_dossiers(exports_dir: Path) -> list[Dossier]:
    return [Dossier.model_validate(r) for r in load_export(exports_dir, "dossiers.json")]


def load_infobans(exports_dir: Path) -> list[InfoBan]:
    return [InfoBan.model_validate(r) for r in load_export(exports_dir, "infobans.json")]
