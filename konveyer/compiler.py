"""Compiler: сборка окна контекста главы (FR-C1…FR-C6).

Окно собирается строго по шаблону v1.1 (шаблон `окно.md.j2`), детерминированно:
одинаковые вход и выгрузки → байт-в-байт одинаковое окно (FR-C4).
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from . import circles, exporter, guard, mdparse, realcanon
from .paths import Workspace
from .schemas import Arc, Act, Brief, Scene, StopRule

SECTION_RE = re.compile(r"<!-- СЕКЦИЯ: (.+?) -->")

# Нормы прозы, показываемые Писателю; служебные пороги верификатора
# (n-граммы, TTR-окно, допуск объёма) в окно не входят.
WINDOW_NORM_IDS = [
    "средняя_длина",
    "доля_коротких",
    "доля_длинных",
    "максимум_длины",
    "короткая_фраза_порог",
    "длинная_фраза_порог",
    "был_на_250",
    "усилители_на_1000",
    "объём_главы",
]


def _template_text(ws: Workspace) -> str:
    override = ws.templates / "окно.md.j2"
    if override.exists():
        return override.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/окно.md.j2").read_text(encoding="utf-8")


def _style_sections(library: Path) -> str:
    """«Регистр и стиль» — из 02 §1–4 и §6.1 (FR-C1). Полный файл не включается (FR-C3)."""
    path = sorted(library.glob("02_*.md"))[0]
    sections = mdparse.parse_sections(path)
    wanted: list[str] = []
    for s in sections:
        # «§1. …» (демо) либо «## 1. …» / «## 6.1. …» (реальный регламент)
        m = re.match(r"(?:§\s*)?(\d+(?:\.\d+)?)\.?\s", s.title + " ")
        if not m:
            continue
        num = m.group(1)
        # §5 «Референсная формула» с числовыми ориентирами Р-015 и эталоны/анти-эталоны §6.2–6.3 —
        # калибровка голоса, без которой Писатель уходит в телеграф (аудит 2, находка 1.9)
        if num in {"1", "2", "3", "4", "5", "6.1", "6.2", "6.3"}:
            wanted.append(f"### {s.title}\n{s.body}")
    return "\n\n".join(wanted)


# ------------------------------------------------------------ «что было раньше»

PRIOR_EVENTS_MAX = 24        # событий сетки предыдущих глав с участием фокала
PRIOR_CONTINUITY_MAX = 30    # закреплённых деталей континуити
PRIOR_TAIL_CHARS = 1200      # хвост предыдущей главы того же фокала (сцепка голоса)
PRIOR_TAIL_PARAGRAPHS = 3
TAIL_BEGIN = "<!-- ХВОСТ ПРОЗЫ: не повторять, исключён из V1.6 -->"
TAIL_END = "<!-- КОНЕЦ ХВОСТА -->"


def _focal_markers(brief: Brief, infobans: list) -> list[str]:
    return [m for b in infobans if b.secret and not b.known_to(brief.focal, brief.chapter) for m in b.markers]


def prior_events(briefs: list[Brief], brief: Brief, infobans: list) -> list[str]:
    """События сетки 2.2 предыдущих глав, где фокал был фокалом или участником, — память фокала.
    Фразы с маркерами незнакомых ему тайн и с будущими томами вычищаются тем же фильтром, что досье."""
    markers = _focal_markers(brief, infobans)
    out: list[str] = []
    for b in sorted(briefs, key=lambda x: x.chapter):
        if b.volume != brief.volume or b.chapter >= brief.chapter:
            continue
        if brief.focal not in ([b.focal] + list(b.participants)):
            continue
        event = _safe_sentences(" ".join(b.beats[:1]) if b.beats else "", markers, brief.volume)
        if not event:
            continue
        how = "фокал" if b.focal == brief.focal else f"глазами: {b.focal}"
        out.append(f"гл. {b.chapter} ({b.date or 'дата не указана'}; {how}): {event}")
    return out[-PRIOR_EVENTS_MAX:]


_CONT_CH_RE = re.compile(r"гл\.\s*(\d+)")


def _present(briefs: list[Brief], volume: int) -> dict[int, set[str]]:
    """Кто был в каждой главе тома: фокал + участники сцен (по брифам)."""
    return {b.chapter: {b.focal, *b.participants} - {""} for b in briefs if b.volume == volume}


def prior_continuity(events: list, brief: Brief, infobans: list, participants: list[str], briefs: list[Brief] | None = None) -> list[str]:
    """Закреплённые детали континуити 3.3, касающиеся участников сцены (внешность, предметы, кабинет):
    без них Писатель дрейфует (аудит 2, находка 1.7). FR-C3: деталь показывается фокалу, только если
    он ПРИСУТСТВОВАЛ в главе, где она закреплена, либо это внешность/манера участника сцены
    («Имя: …» — видна любому, кто с ним встречался). Зола в печи Лемма (гл. 6, Лемм один) Штерну не показывается."""
    markers = _focal_markers(brief, infobans)
    names = [n for n in participants if n]
    low_names = [n.lower() for n in names]
    present = _present(briefs or [], brief.volume)
    out: list[str] = []
    for e in events:
        chs = [int(x) for x in re.findall(r"\d+", e.chapters or "")] or [int(x) for x in _CONT_CH_RE.findall(e.date or "")]
        if not chs or min(chs) >= brief.chapter:
            continue
        low = e.event.lower()
        if low_names and not any(n in low for n in low_names):
            continue
        appearance = any(e.event.startswith(f"{n}:") or e.event.startswith(f"{n} ") and ":" in e.event[:40] for n in names)
        was_there = any(brief.focal in present.get(ch, set()) for ch in chs)
        if not (was_there or appearance):
            continue
        text = _safe_sentences(e.event, markers, brief.volume)
        if text:
            out.append(f"{text} ({e.date})" if e.date else text)
    return out[:PRIOR_CONTINUITY_MAX]


def prior_tail(library: Path, briefs: list[Brief], brief: Brief) -> tuple[int | None, str]:
    """Финал последней принятой в канон главы ТОГО ЖЕ фокала перед текущей — только для сцепки голоса.
    Текст уже прошёл Э2 и написан из головы фокала, поэтому FR-C3 не нарушает; из проверки утечки
    окна (V1.6) исключается маркерами TAIL_BEGIN/TAIL_END."""
    focal_chapters = {b.chapter for b in briefs if b.volume == brief.volume and b.focal == brief.focal and b.chapter < brief.chapter}
    best: tuple[int, Path] | None = None
    for path in (library / "Проза").glob(f"Том{brief.volume}_Глава*.md") if (library / "Проза").exists() else []:
        m = re.search(r"Глава(\d+)", path.name)
        if not m:
            continue
        ch = int(m.group(1))
        if ch in focal_chapters and (best is None or ch > best[0]):
            best = (ch, path)
    if best is None:
        return None, ""
    text = best[1].read_text(encoding="utf-8")
    paragraphs = [
        pg.strip() for pg in re.split(r"\n\s*\n", text)
        # служебное: заголовки, линейки, курсивные пометки («*Конец макета v2. Правки автора…*»)
        if pg.strip() and not pg.strip().startswith(("#", "---", "***")) and not re.fullmatch(r"\*[^*]+\*", pg.strip())
    ]
    tail = "\n\n".join(paragraphs[-PRIOR_TAIL_PARAGRAPHS:])
    if len(tail) > PRIOR_TAIL_CHARS:
        tail = tail[-PRIOR_TAIL_CHARS:]
        tail = tail[tail.find(" ") + 1:] if " " in tail[:80] else tail
    return best[0], tail


def _focalization_laws(library: Path) -> str:
    """Общие законы фокализации — секция «Общие законы» из 03 (FR-C1)."""
    path = sorted(library.glob("03_*.md"))[0]
    sec = mdparse.find_section(mdparse.parse_sections(path), r"Общие законы")
    return sec.body if sec else ""


def _line_rules(stoplists: list[StopRule], participants: list[str], year: int | None) -> list[dict]:
    """Правила линий только участников сцены + лексика года главы (FR-C1, FR-V1.5)."""
    result = []
    for rule in sorted(stoplists, key=lambda r: (r.scope, r.rule_id)):
        if rule.kind != "лексика":  # усилители и прозаические запреты линий выводятся отдельно
            continue
        applies = rule.applies_to
        if "focal" in applies and applies["focal"] not in participants:
            continue
        if "year" in applies and year is not None:
            y = applies["year"]
            if "before" in y and year >= y["before"]:
                continue
            if "from" in y and not (y["from"] <= year <= y.get("to", 9999)):
                continue
        if "focal" in applies:
            scope_note = f"линия «{applies['focal']}»"
        elif rule.scope == "0.3":
            scope_note = "все линии (0.3)"
        else:
            scope_note = f"лексика эпохи ({rule.scope})"
        result.append(
            {"rule_id": rule.rule_id, "items": sorted(rule.items), "action": rule.action, "scope_note": scope_note}
        )
    return result


def ban_active(b, brief: Brief) -> bool:
    """Запрет информрежима действует для главы? Единый фильтр компилятора и Э2 (FR-C3)."""
    if b.until_chapter is not None:  # реестр тайн: до главы раскрытия читателю
        return brief.chapter < b.until_chapter
    return b.until_volume is None or brief.volume <= b.until_volume


# ссылка на том где угодно во фразе: «т.6», «т.3–4», «тома 2–3», «в томе 3», «(т.1)», «цикл II», Ф-19xx, Р-№
_FUTURE_RE = re.compile(
    r"(?:\bт\.\s*(\d+)|\bтом(?:а|е|у|ах|ов|ы)?\s+(\d+)|Ф-19\d\d|Р-\d{3}|\bцикл)", re.IGNORECASE
)
# траектория «от … к …» при любой ссылке на том — путь через тома; стрелка «→» в досье — нотация арки (всегда)
_TRAJECTORY_RE = re.compile(r"\bот\b.+?\bк\b", re.IGNORECASE)
_ARC_RE = re.compile(r"→")
# пометки инструменту/автору: «(⚠ решить при арке т.7)», «(держать в каждой сцене; инструмент обязан …)», «(🔧)»
_TOOL_NOTE_RE = re.compile(
    r"\s*\((?:[^()]*(?:⚠|🔧|инструмент|при арке|держать|проверять|финализировать|спроектировать|сформулировать)[^()]*)\)",
    re.IGNORECASE,
)
_TOOL_MARK_RE = re.compile(r"[⚠🔧]")
# граница фразы — точка/восклицание/вопрос + пробел (не после «гл.», «т.», «сц.», «ср.», «Рожд.») или перенос строки
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])(?<!\bгл\.)(?<!\bт\.)(?<!\bсц\.)(?<!\bср\.)(?<!\bРожд\.)(?<!\bрожд\.)\s+|\n+")


def _phrase_safe(phrase: str, low_markers: list[str], volume: int) -> bool:
    """Элемент фразы без маркеров незнакомых фокалу тайн, без ссылок на будущие тома (все ссылки
    проверяются), без арки «A → B» и без траектории «от … к …» через тома (Р-022)."""
    low = phrase.lower()
    if any(m in low for m in low_markers):
        return False
    refs = list(_FUTURE_RE.finditer(phrase))
    for m in refs:
        num = next((g for g in m.groups() if g), None)
        if num is None or int(num) > volume:
            return False
    if _ARC_RE.search(phrase):
        return False
    return not (refs and _TRAJECTORY_RE.search(phrase))


def _safe_sentences(text: str, markers: list[str], volume: int) -> str:
    """Оставляет только фразы без маркеров незнакомых фокалу тайн и без ссылок на будущие тома (Р-022).

    Фраза = предложение до точки; элементы перечисления внутри неё разделены «;». Если хотя бы один
    элемент вычищен, фраза убирается целиком — обрывков списков в окне не бывает. Скобочные пометки
    инструменту («⚠ решить при арке», «инструмент обязан…», «🔧») вырезаются до проверки."""
    kept: list[str] = []
    low_markers = [m.lower() for m in markers if m]
    for sent in _SENT_SPLIT_RE.split(text):
        sent = _TOOL_NOTE_RE.sub("", sent).strip()
        if not sent or sent.lower().startswith("возраст по томам"):
            continue
        items = [it.strip() for it in sent.split(";")]
        items = [it for it in items if it]
        if not items or _TOOL_MARK_RE.search(sent):
            continue
        if not all(_phrase_safe(it, low_markers, volume) for it in items):
            continue  # список с вычищенным элементом убирается целиком
        kept.append("; ".join(items))
    return " ".join(kept)


def secret_markers(infobans: list, brief: Brief) -> list[str]:
    """Маркеры тайн реестра, которых фокал главы ещё не знает (Р-022) — фразы с ними в окно не идут."""
    markers: list[str] = []
    for b in infobans:
        if b.secret and not b.known_to(brief.focal, brief.chapter):
            markers.extend(b.markers)
    return markers


# клаузы поглавника, адресованные читателю/инструменту, а не Писателю (аудит 2, 1.10): «читатель знает…»,
# «саспенс читателя», «(матрица №17)», «→ т.6», «ЗАКЛАДКА → т.6», «⚠», «реш. при прозе», «(эхо в гл. 5)»,
# «улика слоя 1 №3», «арка-парабола …» — клауза (между «;» или в скобках) с маркером убирается целиком
_READER_MARK_RE = re.compile(
    r"читател|саспенс|матриц[аы]\s*№|→\s*т\.\s*\d|закладка\s*→|[⚠🔧]|реш\.\s*при\s*прозе|эхо\s+в\s+гл|"
    r"улика\s+слоя\s+\d|арка-парабол",
    re.IGNORECASE,
)
_INNER_PAREN_RE = re.compile(r"\s*\([^()]*\)")


def _clause_bad(chunk: str, low_markers: list[str], volume: int) -> bool:
    low = chunk.lower()
    if _READER_MARK_RE.search(chunk) or any(m in low for m in low_markers):
        return True
    for m in _FUTURE_RE.finditer(chunk):
        num = next((g for g in m.groups() if g), None)
        if num is None or int(num) > volume:
            return True
    return False


def strip_reader_clauses(text: str, markers: list[str] = (), volume: int = 1) -> str:
    """Вычищает из текста поглавника клаузы не для Писателя: скобочные группы изнутри наружу, затем
    элементы через «;» верхнего уровня; клауза с маркером читателя/инструмента, с маркером тайны, которой
    фокал не знает, или со ссылкой на будущий том убирается целиком (FR-C3)."""
    low_markers = [m.lower() for m in markers if m]
    kept: list[str] = []

    def _paren(m: re.Match) -> str:
        if _clause_bad(m.group(), low_markers, volume):
            return ""
        kept.append(m.group())
        return f"\x00{len(kept) - 1}\x00"

    prev = None
    while prev != text:
        prev, text = text, _INNER_PAREN_RE.sub(_paren, text)
    items = [it for it in realcanon._split_items(text) if not _clause_bad(it, low_markers, volume)]
    out = "; ".join(items)
    while "\x00" in out:  # вложенные чистые скобки восстанавливаются снаружи внутрь
        out = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], out)
    return re.sub(r"\s{2,}", " ", out).strip(" ;,—–-")


def scene_for_window(scene: Scene, markers: list[str], volume: int) -> Scene:
    """Карточка сцены, как её видит Писатель: все поля через `strip_reader_clauses`."""
    f = lambda t: strip_reader_clauses(t, markers, volume)  # noqa: E731
    return scene.model_copy(update={
        "place": f(scene.place), "time": f(scene.time), "participants": f(scene.participants),
        "goal": f(scene.goal), "enters": f(scene.enters), "exits": f(scene.exits),
        "plants": [p for p in (f(x) for x in scene.plants) if p],
    })


def scene_line(sc: Scene) -> str:
    """«**Сц. 5.1** · место · время · участники · цель · входит: … · выходит: …» (пустые поля опускаются)."""
    fields = [sc.place, sc.time, sc.participants, sc.goal,
              f"входит: {sc.enters}" if sc.enters else "", f"выходит: {sc.exits}" if sc.exits else ""]
    return " · ".join([f"**Сц. {sc.number}**", *[x for x in fields if x]])


def safe_dossier(d, brief: Brief, infobans: list, participants: list[str]):
    """Проекция досье для окна (FR-C3, Р-022): без каркаса/арки/статуса, без фраз о тайнах,
    которых фокал не знает к этой главе, без будущих томов; отношения — только к участникам сцены."""
    markers = secret_markers(infobans, brief)
    relations = {
        k: _safe_sentences(v, markers, brief.volume)
        for k, v in d.relations.items()
        if any(k.lower().startswith(n.lower()) or n.lower().startswith(k.lower()) for n in participants if n != d.name)
    }
    return d.model_copy(update={
        "profile": _safe_sentences(d.profile, markers, brief.volume),
        "physique": _safe_sentences(d.physique, markers, brief.volume),
        "speech": _safe_sentences(d.speech, markers, brief.volume),
        "code": _safe_sentences(d.code, markers, brief.volume),
        "relations": {k: v for k, v in relations.items() if v},
    })


def chapter_act(acts: list[Act], chapter: int) -> Act | None:
    """Акт главы по таблице актов 2.1 (Р-021); None — актов нет или глава вне их границ."""
    return next((a for a in acts if a.from_chapter <= chapter <= a.to_chapter), None)


def arc_lines(arcs: list[Arc], acts: list[Act], brief: Brief, infobans: list, participants: list[str]) -> list[str]:
    """Строки «Имя: что видно снаружи» из арок 2.5 (Р-025) для участников сцены и фокала — по акту главы.

    FR-C3: ложь / желание / потребность / «где на арке» в окно НЕ выводятся никогда; «что видно снаружи» —
    через тот же фильтр, что досье (маркеры тайн, которых фокал не знает, Р-022), и без любых ссылок
    на тома; пустая ячейка и «⚠ заполнить» — строки нет. Пусто → секции в окне нет."""
    act = chapter_act(acts, brief.chapter)
    if act is None or not arcs:
        return []
    markers = secret_markers(infobans, brief)
    out: list[str] = []
    for name in participants:
        row = next((a for a in arcs if a.act == act.act and a.character == name), None)
        if row is None or not row.visible or _TOOL_MARK_RE.search(row.visible):
            continue
        text = _safe_sentences(row.visible, markers, brief.volume)
        if not text or _FUTURE_RE.search(text):
            continue
        out.append(f"{name}: {text}")
    return out


def chapter_plants(exports_dir: Path, brief: Brief) -> list:
    """Закладки, назначенные главе (FR-C2): по брифу и/или по реестру (placed = том/глава)."""
    plants = exporter.load_plants(exports_dir)
    selected = [
        p
        for p in plants
        if p.plant_id in brief.plants
        or (p.placed.get("vol") == brief.volume and p.placed.get("ch") == brief.chapter)
        or (p.placed.get("vol") == brief.volume and brief.chapter in p.chapters)
    ]
    return sorted(selected, key=lambda p: p.plant_id)


def chapter_doses(exports_dir: Path, brief: Brief) -> list:
    """Доза прошлого главы (§5 реестра): ТОЛЬКО доза этой главы — содержание доз других глав
    (в том числе будущее относительно фокала) в окно не идёт (FR-C3)."""
    try:
        doses = exporter.load_doses(exports_dir)
    except FileNotFoundError:
        return []
    return sorted(
        (d for d in doses if d.volume == brief.volume and d.chapter == brief.chapter),
        key=lambda d: d.dose_id,
    )


# строка брифа из поглавника: «№1 (после главы): описание»
_BRIEF_DOC_RE = re.compile(r"№\s*(\d+)\s*(?:\(([^)]*)\))?\s*:?\s*(.*)")


def chapter_documents(exports_dir: Path, brief: Brief) -> list[dict]:
    """Документы-вставки главы: §6 реестра («После гл.» = N) и строки «→ ДОКУМЕНТ №N» поглавника,
    объединённые по номеру — один документ, без дублей; в окно — только документы своей главы."""
    try:
        specs = exporter.load_documents(exports_dir)
    except FileNotFoundError:
        specs = []
    docs: dict[int, dict] = {}
    for sp in specs:
        if sp.volume == brief.volume and sp.after_chapter == brief.chapter:
            docs[sp.number] = {
                "number": sp.number, "kind": sp.kind, "position": "после главы", "note": "",
                "style": sp.style, "divergence": sp.divergence, "scale": sp.scale, "form": sp.form,
            }
    for line in brief.documents:
        m = _BRIEF_DOC_RE.match(line.strip())
        if not m:
            continue
        num = int(m.group(1))
        doc = docs.setdefault(num, {"number": num, "kind": "", "position": "после главы", "note": "",
                                    "style": "", "divergence": "", "scale": "", "form": ""})
        if m.group(2):
            doc["position"] = m.group(2).strip()
        doc["note"] = m.group(3).strip()
    return [docs[n] for n in sorted(docs)]


def compile_window(ws: Workspace, library: Path, chapter: int, soft_limit_chars: int = 80_000) -> tuple[Path, dict[str, int]]:
    """Собирает окно главы N. Возвращает (путь, раскладка размеров по секциям)."""
    exports_dir = ws.exports
    brief = exporter.load_brief(exports_dir, chapter)
    briefs = exporter.load_briefs(exports_dir)
    norms = exporter.load_norms(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    dossiers = exporter.load_dossiers(exports_dir)
    infobans = exporter.load_infobans(exports_dir)

    participants = sorted(set([brief.focal, *brief.participants]) - {""})

    # «что знает фокал»: только факты с from_chapter ≤ N (FR-C1, FR-C3)
    known = sorted(
        (
            # частичное/неверное знание (курсив матрицы) — Писателю показывается текст пометки, не сам факт (FR-C3)
            f.model_copy(update={"fact": f.note.split(":", 1)[1].strip() + " (знание неполное)"})
            if f.note.startswith("частично") else f
            for f in matrix
            if f.subject == brief.focal and f.from_chapter is not None and f.from_chapter <= chapter
        ),
        key=lambda f: f.fact_id,
    )

    scene_dossiers = sorted(
        (safe_dossier(d, brief, infobans, participants) for d in dossiers if d.name in participants),
        key=lambda d: d.name,
    )

    # «НЕ знает»: только явные формулировки брифа. Содержание тайн из матрицы
    # в окно НЕ попадает (FR-C3) — Писатель не должен знать то, чего не знает фокал;
    # число скрытых фактов сообщается без раскрытия.
    hidden = sum(
        1 for f in matrix
        if f.subject == brief.focal and (f.from_chapter is None or f.from_chapter > chapter)
    )
    not_knows = list(brief.not_knows) + (
        [f"ещё {hidden} факт(ов) матрицы фокалу недоступны — никаких намёков в их сторону"] if hidden else []
    )

    intensifiers = sorted(
        {w for r in stoplists if r.kind == "усилитель" for w in r.items}
    )

    # запреты брифа + запреты информрежима 2.2 как явные «НЕ упоминать» (FR-C3)
    def _ban_text(b) -> str:
        if b.secret:
            # тайна реестра: содержание Писателю не сообщаем (FR-C3), только факт запрета
            when = f"читатель узнаёт в гл. {b.until_chapter}" if b.until_chapter else "не раскрывается в этом томе"
            return f"НЕ раскрывать и не намекать: тайна {b.ban_id} реестра информрежима ({when})"
        return f"НЕ упоминать (информрежим {b.ban_id}): {b.text}"

    bans = list(brief.bans) + [
        _ban_text(b) for b in sorted(infobans, key=lambda b: b.ban_id) if ban_active(b, brief)
    ]

    # каркас драматургии (Р-020): только из канона (2.1 → circles.json), не из черновиков
    try:
        acts = exporter.load_acts(exports_dir)
        drama = circles.frame_for_chapter(exporter.load_circles(exports_dir), acts, chapter)
    except FileNotFoundError:
        acts = []
        drama = circles.frame_for_chapter([], [], chapter)
    # арки тома 2.5 (Р-025): Писателю — только «что видно снаружи» участников сцены по акту главы
    arcs = arc_lines(exporter.load_arcs(exports_dir), acts, brief, infobans, participants)

    # «что было раньше» глазами фокала (аудит 2, вывод 1): события, детали, хвост предыдущей главы
    try:
        continuity = exporter.load_continuity(exports_dir)
    except FileNotFoundError:
        continuity = []
    tail_chapter, tail_text = prior_tail(library, briefs, brief)
    # карточки сцен поглавника (аудит 2, 1.10): клаузы для читателя/инструмента вырезаны;
    # «кладём» сцен — в техзадание закладок, не в биты; без карточек — строки сцен как есть
    markers = secret_markers(infobans, brief)
    cards = [scene_for_window(sc, markers, brief.volume) for sc in brief.scene_cards]
    scene_lines = [scene_line(sc) for sc in cards] or list(brief.scenes)
    scene_plants = [f"сц. {sc.number}: {p}" for sc in cards for p in sc.plants]

    env = Environment(undefined=StrictUndefined, trim_blocks=False, lstrip_blocks=False)
    window = env.from_string(_template_text(ws)).render(
        brief=brief,
        prior_events=prior_events(briefs, brief, infobans),
        prior_continuity=prior_continuity(continuity, brief, infobans, participants, briefs),
        prior_tail_chapter=tail_chapter,
        prior_tail=tail_text,
        tail_begin=TAIL_BEGIN,
        tail_end=TAIL_END,
        norms={k: v for k, v in norms.items() if k in WINDOW_NORM_IDS},
        style_sections=_style_sections(library),
        focalization_laws=_focalization_laws(library),
        line_rules=_line_rules(stoplists, participants, brief.year),
        dossiers=scene_dossiers,
        known_facts=known,
        not_knows=not_knows,
        plants=chapter_plants(exports_dir, brief),
        doses=chapter_doses(exports_dir, brief),
        documents=chapter_documents(exports_dir, brief),
        scene_lines=scene_lines,
        scene_plants=scene_plants,
        bans=bans,
        intensifiers=intensifiers,
        volume_norm=norms.get("объём_главы"),
        drama=drama,
        drama_lines=circles.frame_lines(drama),
        arc_lines=arcs,
    )

    path = ws.window_path(chapter)
    guard.write_text(path, window)

    breakdown = section_breakdown(window)
    if len(window) > soft_limit_chars:
        lines = [
            f"⚠ Окно главы {chapter} превышает мягкий лимит {soft_limit_chars} символов (Д-12): {len(window)}.",
            "Раскладка по секциям (символов):",
        ] + [f"  - {name}: {size}" for name, size in breakdown.items()]
        guard.write_text(ws.chapter_dir(chapter) / "window_size_флаг.md", "\n".join(lines) + "\n")
    return path, breakdown


def section_breakdown(window: str) -> dict[str, int]:
    """Размер окна по секциям (FR-C5) — по маркерам <!-- СЕКЦИЯ: ... -->."""
    parts = SECTION_RE.split(window)
    breakdown: dict[str, int] = {}
    # parts: [до первой секции, имя1, тело1, имя2, тело2, ...]
    for i in range(1, len(parts) - 1, 2):
        breakdown[parts[i]] = len(parts[i + 1])
    return breakdown
