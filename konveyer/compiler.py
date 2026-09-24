"""Compiler: сборка окна контекста главы (FR-WN-1…FR-WN-7).

Окно собирается строго по шаблону проекта (`окно.md.j2`, наследуется из движка), детерминированно:
одинаковые вход и выгрузки → байт-в-байт одинаковое окно (П-6). Секции появляются только для включённых
модулей и только при наличии данных — без заглушек (FR-MD-2, FR-WN-7). Всё, что идёт Писателю из канона,
проходит фильтр знания (FR-WN-3): маркеры тайн, которых фокал не знает, ссылки на будущие тома и клаузы
читателю/инструменту вычищаются; запрет показывается фактом запрета, а не содержанием (FR-WN-4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from . import catalog, circles, exporter, guard, lang as lang_mod, manifest as manifest_mod, mdparse, names
from .paths import Workspace
from .schemas import Act, Arc, Brief, Scene, StopRule

SECTION_RE = re.compile(r"<!-- СЕКЦИЯ: (.+?) -->")

# Нормы прозы, показываемые Писателю по умолчанию (идентификаторы реестра метрик Э1); служебные пороги
# верификатора (n-граммы, TTR-окно, допуск объёма) в окно не входят. Проект переопределяет список в типе
# «стиль» (`окно.нормы_писателю`); нормы лексем автора (`лексемы_*`) показываются всегда.
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
LEXEME_NORM_PREFIX = "лексемы_"
# пометка частичного/неверного знания в примечании матрицы (ставит разбор эпистемики, declparse)
PARTIAL_NOTE_PREFIX = "частично"

# секции, которые сокращать нельзя (подсказка при превышении лимита, FR-WN-6)
ESSENTIAL_SECTIONS = {"роль и запреты", "бриф", "формат выдачи", "строгие запреты"}
SECTION_LIMIT_HINTS = {
    "что было раньше": "лимиты `окно_событий_макс` и `окно_хвост_знаков`/`окно_хвост_абзацев` в конфиг.yaml",
    "континуити": "лимит `окно_континуити_макс` в конфиг.yaml",
}


@dataclass(frozen=True)
class WindowLimits:
    """Настраиваемые лимиты окна (FR-WN-5, FR-WN-6, Д-20) — из конфиг.yaml."""

    soft_limit_chars: int = 80_000
    prior_events_max: int = 24        # событий предыдущих глав с участием фокала
    prior_continuity_max: int = 30    # закреплённых деталей континуити
    tail_paragraphs: int = 3          # хвост предыдущей главы того же фокала (сцепка голоса)
    tail_chars: int = 1200

    @classmethod
    def from_config(cls, cfg) -> WindowLimits:
        return cls(
            soft_limit_chars=cfg.window_soft_limit_chars,
            prior_events_max=cfg.window_prior_events_max,
            prior_continuity_max=cfg.window_prior_continuity_max,
            tail_paragraphs=cfg.window_tail_paragraphs,
            tail_chars=cfg.window_tail_chars,
        )


# совместимость: прежние константы лимитов
PRIOR_EVENTS_MAX = WindowLimits.prior_events_max
PRIOR_CONTINUITY_MAX = WindowLimits.prior_continuity_max
PRIOR_TAIL_CHARS = WindowLimits.tail_chars
PRIOR_TAIL_PARAGRAPHS = WindowLimits.tail_paragraphs
TAIL_BEGIN = "<!-- ХВОСТ ПРОЗЫ: не повторять, исключён из V1.6 -->"
TAIL_END = "<!-- КОНЕЦ ХВОСТА -->"


def _template_text(ws: Workspace) -> str:
    override = ws.templates / "окно.md.j2"
    if override.exists():
        return override.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/окно.md.j2").read_text(encoding="utf-8")


# ------------------------------------------------------------ регистр и стиль

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _strip_tables(body: str) -> str:
    """Таблицы машинных данных (нормы, словари) из секции регистра убираются: их содержимое Писателю
    показывается отдельно и только в объявленной части (`окно.нормы_писателю`)."""
    lines = [ln for ln in body.splitlines() if not _TABLE_LINE_RE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _register_pattern(spec: catalog.TypeSpec | None, man: manifest_mod.Manifest) -> re.Pattern | None:
    """Какие секции документа стиля идут в «Регистр и стиль»: манифест (`секции: {регистр: …}` у документа)
    сильнее типа (`окно.секции_регистра`); ничего не объявлено — все непустые секции без таблиц."""
    for entry in man.entries_of_type("стиль"):
        chosen = entry.секции.get("регистр") if isinstance(entry.секции, dict) else None
        if chosen:
            parts = [chosen] if isinstance(chosen, str) else [str(x) for x in chosen]
            return re.compile("|".join(f"(?:{p})" for p in parts))
    pattern = spec.window.get("секции_регистра") if spec else None
    return re.compile(str(pattern)) if pattern else None


def _style_sections(library: Path, root: Path | None = None) -> str:
    """«Регистр и стиль» — секции документа стиля, объявленные типом или манифестом (FR-DT-3).
    Полный файл не включается (FR-WN-3); таблицы норм/словарей вырезаются (служебные пороги
    верификатора Писателю не показываются, `окно.запрещено`); без документа стиля — пусто (FR-WN-7)."""
    project_root = exporter.project_root_of(library, root)
    types = catalog.load_types(project_root)
    man = manifest_mod.effective(project_root, library, types)
    spec = types.get("стиль")
    rx = _register_pattern(spec, man)
    table_sections = [
        re.compile(str(fmt["секция"]))
        for ext in (spec.extractions if spec else ())
        for fmt in ext.get("форматы", [])
        if fmt.get("вид") == "таблица" and fmt.get("секция")
    ]
    wanted: list[str] = []
    for path in exporter.docs_of_type(library, "стиль", None, root):
        for sec in mdparse.parse_sections(path):
            if not sec.level or (rx is not None and not rx.match(sec.title + " ")):
                continue
            body = sec.body
            if any(t.search(sec.title) for t in table_sections):
                body = _strip_tables(body)
            if body.strip():
                wanted.append(f"### {sec.title}\n{body}")
    return "\n\n".join(wanted)


def window_norm_ids(norms: dict, root: Path | None) -> list[str]:
    """Идентификаторы норм, показываемых Писателю: список типа «стиль» (`окно.нормы_писателю`) или умолчание
    движка, плюс нормы лексем автора."""
    spec = catalog.load_types(root).get("стиль")
    declared = (spec.window.get("нормы_писателю") if spec else None) or WINDOW_NORM_IDS
    return sorted(k for k in norms if k in declared or k.startswith(LEXEME_NORM_PREFIX))


# ------------------------------------------------------------ маркеры и язык

# ссылка на том где угодно во фразе: «т.6», «т.3–4», «тома 2–3», «в томе 3», «(т.1)»; плюс маркеры проекта
# (префиксы идентификаторов хронологии и решений, слово «цикл»…) — объявлены типами каталога, не кодом (П-1)
_FUTURE_BASE = r"(?:\bт\.\s*(\d+)(?:\s*[–—-]\s*(\d+))?|\bтом(?:а|е|у|ах|ов|ы)?\s+(\d+)(?:\s*[–—-]\s*(\d+))?"
_FUTURE_RE = re.compile(_FUTURE_BASE + r")", re.IGNORECASE)
# траектория «от … к …» при любой ссылке на том — путь через тома; стрелка «→» в досье — нотация арки (всегда)
_TRAJECTORY_RE = re.compile(r"\bот\b.+?\bк\b", re.IGNORECASE)
_ARC_RE = re.compile(r"→")
# пометки инструменту/автору: «(⚠ …)», «(🔧)»; список слов расширяется типами каталога (configure_markers)
_TOOL_NOTE_BASE = r"⚠|🔧|инструмент"
_TOOL_NOTE_RE = re.compile(r"\s*\((?:[^()]*(?:" + _TOOL_NOTE_BASE + r")[^()]*)\)", re.IGNORECASE)
_TOOL_MARK_RE = re.compile(r"[⚠🔧]")
# клаузы плана глав, адресованные читателю/инструменту, а не Писателю: «читатель знает…», «саспенс», «→ т.6»,
# «⚠», «🔧» — клауза (между «;» или в скобках) с маркером убирается целиком; список расширяется типами каталога
_READER_MARK_BASE = r"читател|саспенс|→\s*т\.\s*\d|закладка\s*→|[⚠🔧]"
_READER_MARK_RE = re.compile(_READER_MARK_BASE, re.IGNORECASE)
_INNER_PAREN_RE = re.compile(r"\s*\([^()]*\)")
# языковой слой (деление на фразы по словарю сокращений, основы слов для маркеров) — движок, проект переопределяет
_LANG = lang_mod.get()
# фразы, которые Писателю не показываются целиком (объявлены типами: `окно.скрывать_фразы`)
_HIDDEN_PREFIXES: tuple[str, ...] = ()
_MARKER_CACHE: dict[tuple[str, ...], re.Pattern | None] = {}


def configure_markers(root: Path | None) -> None:
    """Маркеры будущего/инструмента/читателя, скрываемые фразы и языковой слой — из каталога типов и языкового
    модуля проекта (`окно.маркеры_будущего`, `окно.маркеры_инструмента`, `окно.маркеры_читателя`,
    `окно.скрывать_фразы`, `префикс_id_будущего`). Вызывается перед сборкой окна."""
    global _FUTURE_RE, _TOOL_NOTE_RE, _READER_MARK_RE, _LANG, _HIDDEN_PREFIXES
    types = catalog.load_types(root)
    extra: list[str] = []
    tool: list[str] = []
    reader: list[str] = []
    hidden: list[str] = []
    for t in types.values():
        extra += [re.escape(m) for m in (t.window.get("маркеры_будущего") or [])]
        tool += [re.escape(m) for m in (t.window.get("маркеры_инструмента") or [])]
        reader += [str(m) for m in (t.window.get("маркеры_читателя") or [])]
        hidden += [str(m).lower() for m in (t.window.get("скрывать_фразы") or [])]
        for ext in t.extractions:
            for fmt in ext.get("форматы", []):
                if fmt.get("префикс_id_будущего"):
                    extra.append(re.escape(str(fmt["префикс_id_будущего"])) + r"\d")
    _FUTURE_RE = re.compile(_FUTURE_BASE + ("|" + "|".join(sorted(set(extra))) if extra else "") + r")", re.IGNORECASE)
    _TOOL_NOTE_RE = re.compile(r"\s*\((?:[^()]*(?:" + "|".join([_TOOL_NOTE_BASE, *sorted(set(tool))]) + r")[^()]*)\)",
                               re.IGNORECASE)
    _READER_MARK_RE = re.compile("|".join([_READER_MARK_BASE, *sorted(set(reader))]), re.IGNORECASE)
    _HIDDEN_PREFIXES = tuple(sorted(set(hidden)))
    _LANG = lang_mod.for_project(root)
    _MARKER_CACHE.clear()


def _norm_text(text: str) -> str:
    """Текст в форме для сравнения с маркерами: нижний регистр, нормализация букв языка (ё → е)."""
    return _LANG.normalize_word(text)


def _marker_re(markers) -> re.Pattern | None:
    """Единый матчер маркеров тайн: по границам слова и основе с допустимыми окончаниями (как стоп-лексика Э1
    и линтер), а не подстрокой — склонённая форма («дочерью сторожа») ловится, «сынок» на маркер «сын» — нет."""
    items = tuple(sorted({m.strip() for m in markers if m and m.strip()}))
    if items in _MARKER_CACHE:
        return _MARKER_CACHE[items]
    rx = re.compile("|".join(_LANG.item_pattern(m).pattern for m in items), re.IGNORECASE) if items else None
    _MARKER_CACHE[items] = rx
    return rx


def marker_hit(text: str, markers) -> bool:
    rx = _marker_re(markers)
    return bool(rx and rx.search(_norm_text(text)))


def _future_ref_bad(m: re.Match, volume: int) -> bool:
    """Ссылка на том за пределами текущего: любое число ссылки (диапазон «т.3–4» — оба) больше тома,
    либо маркер проекта без номера (идентификаторы будущего)."""
    nums = [int(g) for g in m.groups() if g]
    return not nums or max(nums) > volume


def _split_phrases(text: str) -> list[str]:
    """Фразы текста по правилам языкового модуля (словарь сокращений: «гл.», «т.», «см.», «рожд.»…);
    перенос строки — всегда граница."""
    out: list[str] = []
    for line in re.split(r"\n+", text):
        if line.strip():
            out.extend(_LANG.split_sentences(line))
    return out


def _phrase_safe(phrase: str, marker_rx: re.Pattern | None, volume: int) -> bool:
    """Элемент фразы без маркеров незнакомых фокалу тайн, без ссылок на будущие тома (все ссылки
    проверяются), без арки «A → B» и без траектории «от … к …» через тома."""
    if marker_rx and marker_rx.search(_norm_text(phrase)):
        return False
    refs = list(_FUTURE_RE.finditer(phrase))
    if any(_future_ref_bad(m, volume) for m in refs):
        return False
    if _ARC_RE.search(phrase):
        return False
    return not (refs and _TRAJECTORY_RE.search(phrase))


def _safe_sentences(text: str, markers, volume: int) -> str:
    """Оставляет только фразы без маркеров незнакомых фокалу тайн и без ссылок на будущие тома.

    Фраза = предложение до точки; элементы перечисления внутри неё разделены «;». Если хотя бы один
    элемент вычищен, фраза убирается целиком — обрывков списков в окне не бывает (FR-WN-3). Скобочные
    пометки инструменту («⚠ решить при арке», «инструмент обязан…», «🔧») вырезаются до проверки."""
    kept: list[str] = []
    rx = _marker_re(markers)
    for sent in _split_phrases(text):
        sent = _TOOL_NOTE_RE.sub("", sent).strip()
        if not sent or sent.lower().startswith(_HIDDEN_PREFIXES or ("\0",)):
            continue
        items = [it.strip() for it in sent.split(";")]
        items = [it for it in items if it]
        if not items or _TOOL_MARK_RE.search(sent):
            continue
        if not all(_phrase_safe(it, rx, volume) for it in items):
            continue  # список с вычищенным элементом убирается целиком
        kept.append("; ".join(items))
    return " ".join(kept)


def secret_markers(infobans: list, brief: Brief) -> list[str]:
    """Маркеры тайн реестра, которых фокал главы ещё не знает — фразы с ними в окно не идут (FR-WN-3)."""
    markers: list[str] = []
    for b in infobans:
        if b.secret and not b.known_to(brief.focal, brief.chapter):
            markers.extend(b.markers)
    return markers


_focal_markers = secret_markers


def _clause_bad(chunk: str, marker_rx: re.Pattern | None, volume: int) -> bool:
    if _READER_MARK_RE.search(chunk) or (marker_rx and marker_rx.search(_norm_text(chunk))):
        return True
    return any(_future_ref_bad(m, volume) for m in _FUTURE_RE.finditer(chunk))


def strip_reader_clauses(text: str, markers=(), volume: int = 1) -> str:
    """Вычищает из текста канона клаузы не для Писателя: скобочные группы изнутри наружу, затем
    элементы через «;» верхнего уровня; клауза с маркером читателя/инструмента, с маркером тайны, которой
    фокал не знает, или со ссылкой на будущий том убирается целиком (FR-WN-3)."""
    rx = _marker_re(markers)
    kept: list[str] = []

    def _paren(m: re.Match) -> str:
        if _clause_bad(m.group(), rx, volume):
            return ""
        kept.append(m.group())
        return f"\x00{len(kept) - 1}\x00"

    prev = None
    while prev != text:
        prev, text = text, _INNER_PAREN_RE.sub(_paren, text)
    items = [it for it in names.split_items(text) if not _clause_bad(it, rx, volume)]
    out = "; ".join(items)
    while "\x00" in out:  # вложенные чистые скобки восстанавливаются снаружи внутрь
        out = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], out)
    return re.sub(r"\s{2,}", " ", out).strip(" ;,—–-")


def writer_text(text: str, markers, volume: int) -> str:
    """Текст поля канона, как его видит Писатель: клаузы не для него вычищены, затем — фразовый фильтр
    (маркеры тайн, будущие тома, арки); пусто — поле в окно не идёт."""
    return _safe_sentences(strip_reader_clauses(text or "", markers, volume), markers, volume)


# ------------------------------------------------------------ «что было раньше»


def prior_events(briefs: list[Brief], brief: Brief, infobans: list, limit: int = PRIOR_EVENTS_MAX) -> list[str]:
    """События предыдущих глав в формулировке «что стало известно» — память фокала (FR-WN-5).

    Своя глава фокала — все её биты; глава, где фокал был лишь участником, — только биты, в которых он назван
    (сцены без него ему не видны), иначе одна строка об участии без содержания. Каждый бит проходит тот же
    фильтр, что досье: клаузы читателю, маркеры незнакомых тайн, будущие тома."""
    markers = secret_markers(infobans, brief)
    focal_rx = names.name_pattern(brief.focal) if brief.focal else None
    out: list[str] = []
    for b in sorted(briefs, key=lambda x: x.chapter):
        if b.volume != brief.volume or b.chapter >= brief.chapter:
            continue
        own = b.focal == brief.focal
        if not own and brief.focal not in b.participants:
            continue
        beats: list[str] = []
        for beat in b.beats:
            if not own and not (focal_rx and focal_rx.search(beat)):
                continue
            text = writer_text(beat, markers, brief.volume)
            if text:
                beats.append(text)
        how = "фокал" if own else f"глазами: {b.focal}"
        head = f"гл. {b.chapter} ({b.date or 'дата не указана'}; {how})"
        if beats:
            out.append(f"{head}: {'; '.join(beats)}")
        elif not own:
            out.append(f"{head}: фокал участвовал — содержание сцен без него ему не известно")
    return out[-limit:] if limit > 0 else []


# ссылки на главы в континуити: «т.1 гл.4», «гл. 3, 5», «гл. 1–3»
_CONT_REF_RE = re.compile(r"(?:\bт\.?\s*(\d+)\s*)?\bгл\.?\s*(\d+(?:\s*[,–—-]\s*\d+)*)", re.IGNORECASE)


def continuity_refs(e) -> tuple[int | None, list[int]]:
    """(том, главы) записи континуити по колонкам «главы» и «дата»: «т.1 гл.4» → (1, [4]); голые числа
    в колонке «главы» — номера глав текущего тома; том без главы → (том, [])."""
    volume: int | None = None
    chapters: list[int] = []
    for field in (e.chapters or "", e.date or ""):
        refs = list(_CONT_REF_RE.finditer(field))
        for m in refs:
            if m.group(1):
                volume = int(m.group(1)) if volume is None else max(volume, int(m.group(1)))
            chapters += [int(x) for x in re.findall(r"\d+", m.group(2))]
        if not refs and volume is None:
            vm = re.search(r"\bт\.?\s*(\d+)", field, re.IGNORECASE)
            if vm:
                volume = int(vm.group(1))
    if not chapters:
        chapters = [int(x) for x in re.findall(r"\d+", e.chapters or "") if "гл" not in (e.chapters or "").lower()]
    return volume, sorted(set(chapters))


def _present(briefs: list[Brief], volume: int) -> dict[int, set[str]]:
    """Кто был в каждой главе тома: фокал + участники сцен (по брифам)."""
    return {b.chapter: {b.focal, *b.participants} - {""} for b in briefs if b.volume == volume}


def prior_continuity(events: list, brief: Brief, infobans: list, participants: list[str],
                     briefs: list[Brief] | None = None, limit: int = PRIOR_CONTINUITY_MAX) -> list[str]:
    """Закреплённые детали континуити, касающиеся участников сцены (внешность, предметы, кабинет).
    Деталь показывается фокалу, только если он ПРИСУТСТВОВАЛ в главе, где она закреплена (правило присутствия,
    FR-WN-5), либо это внешность/манера участника сцены («Имя: …» — видна любому, кто с ним встречался).
    Детали будущих томов не показываются; из прошлых томов — только внешность (присутствие там неизвестно)."""
    markers = secret_markers(infobans, brief)
    people = [n for n in participants if n]
    low_names = [n.lower() for n in people]
    present = _present(briefs or [], brief.volume)
    out: list[str] = []
    for e in events:
        volume, chs = continuity_refs(e)
        if volume is not None and volume > brief.volume:
            continue
        same_volume = volume is None or volume == brief.volume
        if same_volume and (not chs or min(chs) >= brief.chapter):
            continue
        low = e.event.lower()
        if low_names and not any(n in low for n in low_names):
            continue
        appearance = any(e.event.startswith(f"{n}:") or e.event.startswith(f"{n} ") and ":" in e.event[:40] for n in people)
        was_there = same_volume and any(brief.focal in present.get(ch, set()) for ch in chs)
        if not (was_there or appearance):
            continue
        text = _safe_sentences(e.event, markers, brief.volume)
        if not text:
            continue
        line = f"{text} ({e.date})" if e.date else text
        if _phrase_safe(line, None, brief.volume):
            out.append(line)
    return out[:limit] if limit > 0 else []


def prior_tail(library: Path, briefs: list[Brief], brief: Brief, root: Path | None = None,
               paragraphs_max: int = PRIOR_TAIL_PARAGRAPHS, chars_max: int = PRIOR_TAIL_CHARS) -> tuple[int | None, str]:
    """Финал последней принятой в канон главы ТОГО ЖЕ фокала перед текущей — только для сцепки голоса (FR-WN-5).
    Текст уже прошёл Э2 и написан из головы фокала; из проверки утечки окна исключается маркерами TAIL_BEGIN/TAIL_END."""
    focal_chapters = {b.chapter for b in briefs if b.volume == brief.volume and b.focal == brief.focal and b.chapter < brief.chapter}
    best: tuple[int, Path] | None = None
    for ch, path in exporter.prose_files(library, brief.volume, root):
        if ch in focal_chapters and (best is None or ch > best[0]):
            best = (ch, path)
    if best is None or paragraphs_max <= 0 or chars_max <= 0:
        return None, ""
    text = best[1].read_text(encoding="utf-8")
    paragraphs = [
        pg.strip() for pg in re.split(r"\n\s*\n", text)
        # служебное: заголовки, линейки, курсивные пометки («*Конец макета v2. Правки автора…*»)
        if pg.strip() and not pg.strip().startswith(("#", "---", "***")) and not re.fullmatch(r"\*[^*]+\*", pg.strip())
    ]
    tail = "\n\n".join(paragraphs[-paragraphs_max:])
    if len(tail) > chars_max:
        tail = tail[-chars_max:]
        tail = tail[tail.find(" ") + 1:] if " " in tail[:80] else tail
    return best[0], tail


def _focalization_laws(exports_dir: Path) -> str:
    """Общие законы повествования — из выгрузки narration.json (документ типа «повествование»)."""
    return "\n\n".join(n.laws for n in exporter.load_narration(exports_dir) if n.laws)


def _line_rules(stoplists: list[StopRule], participants: list[str], year: int | None,
                brief: Brief | None = None, markers=()) -> list[dict]:
    """Правила лексики: линий участников сцены (`epoch: False`) и года главы (`epoch: True`); правило с ограничением
    томом/«до главы» — только в своём томе и до своей главы; слово, совпадающее с маркером недоступной фокалу
    тайны, из списка убирается."""
    result = []
    for rule in sorted(stoplists, key=lambda r: (r.scope, r.rule_id)):
        if rule.kind != "лексика":  # усилители и прозаические запреты линий выводятся отдельно
            continue
        applies = rule.applies_to
        if "focal" in applies and applies["focal"] not in participants:
            continue
        if brief is not None and "volume" in applies and int(applies["volume"]) != brief.volume:
            continue
        if brief is not None and "until_chapter" in applies and brief.chapter > int(applies["until_chapter"]):
            continue
        if "year" in applies and year is not None:
            y = applies["year"]
            if "before" in y and year >= y["before"]:
                continue
            if "from" in y and not (y["from"] <= year <= y.get("to", 9999)):
                continue
        if "focal" in applies:
            scope_note, epoch = f"линия «{applies['focal']}»", False
        elif rule.narrator_only:
            scope_note, epoch = "все линии (речь повествователя)", False
        else:
            scope_note, epoch = "лексика эпохи (весь текст)", True
        items = sorted(w for w in rule.items if not marker_hit(w, markers))
        if not items:
            continue
        result.append(
            {"rule_id": rule.rule_id, "items": items, "action": rule.action, "scope_note": scope_note, "epoch": epoch}
        )
    return result


def _prose_rules(stoplists: list[StopRule], participants: list[str], markers=(), volume: int = 1) -> list[dict]:
    """Прозаические запреты линий (kind «проза»: правила фразами, не словами) участников сцены — те же, что видит Э2,
    через фильтр знания: правило, говорящее о тайне, которой фокал не знает, Писателю не показывается."""
    out = []
    for rule in sorted(stoplists, key=lambda r: (r.scope, r.rule_id)):
        if rule.kind != "проза":
            continue
        focal = rule.applies_to.get("focal")
        if focal and focal not in participants:
            continue
        note = f"линия «{focal}»" if focal else "все линии"
        for item in rule.items:
            text = writer_text(item, markers, volume)
            if text:
                out.append({"rule_id": rule.rule_id, "scope_note": note, "text": text})
    return out


def ban_active(b, brief: Brief) -> bool:
    """Запрет информрежима действует для главы? Единый фильтр компилятора и Э2."""
    if b.until_chapter is not None:  # реестр тайн: до главы раскрытия читателю
        return brief.chapter < b.until_chapter
    return b.until_volume is None or brief.volume <= b.until_volume


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
    """Проекция досье для окна (FR-WN-3): без каркаса/арки/статуса, без фраз о тайнах,
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
    """Акт главы по таблице актов; None — актов нет или глава вне их границ."""
    return next((a for a in acts if a.from_chapter <= chapter <= a.to_chapter), None)


def arc_lines(arcs: list[Arc], acts: list[Act], brief: Brief, infobans: list, participants: list[str]) -> list[str]:
    """Строки «Имя: что видно снаружи» из арок для участников сцены и фокала — по акту главы.

    Ложь / желание / потребность / «где на арке» в окно НЕ выводятся никогда; «что видно снаружи» —
    через тот же фильтр, что досье (маркеры тайн, которых фокал не знает), и без любых ссылок
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
    """Закладки, назначенные главе: по брифу и/или по реестру (placed = том/глава)."""
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
    """Доза прошлого главы: ТОЛЬКО доза этой главы — содержание доз других глав
    (в том числе будущее относительно фокала) в окно не идёт (FR-WN-3)."""
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
    """Документы-вставки главы: реестр («После гл.» = N) и строки «→ ДОКУМЕНТ №N» поглавника,
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


def _documents_for_writer(documents: list[dict], markers, volume: int) -> list[dict]:
    """Поля документа-вставки через фильтр знания: клаузы читателю («читатель уже видел…»), маркеры тайн,
    будущие тома вычищаются (FR-WN-3)."""
    out = []
    for d in documents:
        clean = dict(d)
        for key in ("note", "style", "divergence", "form", "scale", "kind"):
            clean[key] = strip_reader_clauses(d.get(key) or "", markers, volume)
        out.append(clean)
    return out


def _doses_for_writer(doses: list, markers, volume: int) -> list:
    return [
        d.model_copy(update={k: strip_reader_clauses(getattr(d, k) or "", markers, volume)
                             for k in ("trigger", "reader_gets", "reader_not_gets", "form", "rule")})
        for d in doses
    ]


def _plants_for_writer(plants: list, markers, volume: int) -> tuple[list, int]:
    """Закладки с вычищенным текстом; закладка, потерявшая текст целиком, скрывается (число сообщается)."""
    kept, hidden = [], 0
    for p in plants:
        what = strip_reader_clauses(p.what or "", markers, volume)
        if what:
            kept.append(p.model_copy(update={"what": what}))
        else:
            hidden += 1
    return kept, hidden


def _known_fact(f):
    """Частичное/неверное знание (курсив матрицы) — Писателю показывается текст пометки, не сам факт."""
    if f.note.startswith(PARTIAL_NOTE_PREFIX):
        rest = f.note.partition(":")[2].strip()
        return f.model_copy(update={"fact": (rest or "знание неполное") + " (знание неполное)"})
    return f


def compile_window(ws: Workspace, library: Path, chapter: int, soft_limit_chars: int = 80_000,
                   limits: WindowLimits | None = None) -> tuple[Path, dict[str, int]]:
    """Собирает окно главы N (FR-WN-1…FR-WN-7). Возвращает (путь, раскладка размеров по секциям).
    Секции появляются только для включённых модулей и только при наличии данных (FR-MD-2)."""
    lim = limits or WindowLimits(soft_limit_chars=soft_limit_chars)
    exports_dir = ws.exports
    root = ws.root
    configure_markers(root)
    man = manifest_mod.effective(root, library, catalog.load_types(root))
    mods = catalog.load_modules(root)
    enabled = {m: (man.module_enabled(m, mods)) for m in mods}
    brief = exporter.load_brief(exports_dir, chapter)
    briefs = exporter.load_briefs(exports_dir)
    norms = exporter.load_norms(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    dossiers = exporter.load_dossiers(exports_dir)
    infobans = exporter.load_infobans(exports_dir)

    participants = sorted(set([brief.focal, *brief.participants]) - {""})
    markers = secret_markers(infobans, brief)
    volume = brief.volume

    def clean(text: str) -> str:
        return writer_text(text, markers, volume)

    # «что знает фокал»: факты с from_chapter < N — известны к началу главы; from_chapter == N — узнаёт
    # по ходу главы (только после соответствующего бита), отдельным списком (FR-WN-3)
    own_facts = sorted(
        (_known_fact(f) for f in matrix if f.subject == brief.focal and f.from_chapter is not None and f.from_chapter <= chapter),
        key=lambda f: f.fact_id,
    )
    known = [f for f in own_facts if f.from_chapter < chapter]
    learns_now = [f for f in own_facts if f.from_chapter == chapter]

    scene_dossiers = sorted(
        (safe_dossier(d, brief, infobans, participants) for d in dossiers if d.name in participants),
        key=lambda d: d.name,
    )

    # «НЕ знает»: явные формулировки брифа — всегда (базовый план глав); число скрытых фактов матрицы —
    # только при включённой эпистемике. Содержание тайн в окно НЕ попадает: формулировка брифа с маркером
    # недоступной фокалу тайны или ссылкой на будущий том показывается фактом запрета (FR-WN-4)
    hidden = sum(
        1 for f in matrix
        if f.subject == brief.focal and (f.from_chapter is None or f.from_chapter > chapter)
    ) if enabled.get("эпистемика") else 0
    explicit: list[str] = []
    for nk in brief.not_knows:
        text = clean(nk)
        if text:
            explicit.append(text)
        else:
            hidden += 1
    not_knows = explicit + (
        [f"ещё {hidden} факт(ов) фокалу недоступны — никаких намёков в их сторону"] if hidden else []
    )

    intensifiers = sorted(
        {w for r in stoplists if r.kind == "усилитель" for w in r.items}
    )

    # запреты брифа: с содержанием недоступной тайны — фактом запрета (FR-WN-4); клаузы читателю вычищены
    bans: list[str] = []
    hidden_bans = 0
    for ban in brief.bans:
        text = clean(ban)
        if text:
            bans.append(text)
        else:
            hidden_bans += 1
    if hidden_bans:
        bans.append(f"ещё {hidden_bans} запрет(ов) касаются тайн, недоступных фокалу — никаких намёков в их сторону")

    # запреты информрежима как явные «НЕ упоминать» (модуль «информрежим»)
    def _ban_text(b) -> str:
        if b.secret:
            # тайна реестра: содержание Писателю не сообщаем, только факт запрета
            when = f"читатель узнаёт в гл. {b.until_chapter}" if b.until_chapter else "не раскрывается в этом томе"
            return f"НЕ раскрывать и не намекать: тайна {b.ban_id} реестра информрежима ({when})"
        return f"НЕ упоминать (информрежим {b.ban_id}): {clean(b.text) or 'содержание недоступно фокалу'}"

    infoban_lines = [_ban_text(b) for b in sorted(infobans, key=lambda b: b.ban_id) if ban_active(b, brief)] \
        if enabled.get("информрежим") else []

    # биты и сцены брифа — через тот же фильтр, что карточки сцен; бит с содержанием недоступной тайны
    # скрывается целиком, число скрытых сообщается
    beats: list[str] = []
    hidden_beats = 0
    for beat in brief.beats:
        text = clean(beat)
        if text:
            beats.append(text)
        else:
            hidden_beats += 1

    # каркас драматургии: только из канона (circles.json), не из черновиков; обязательные и необязательные
    # шаги — по методике главы (как в Э2), не по умолчанию движка
    try:
        acts = exporter.load_acts(exports_dir)
        drama = circles.frame_for_chapter(exporter.load_circles(exports_dir), acts, chapter)
    except FileNotFoundError:
        acts = []
        drama = circles.frame_for_chapter([], [], chapter)
    if drama.get("has_any"):
        methodic = circles.methodic_for(ws, "глава")
        drama_lines = circles.frame_lines(drama, required=methodic.required_steps("глава", man),
                                          optional=methodic.optional_steps("глава", man))
    else:
        drama_lines = []
    # арки тома: Писателю — только «что видно снаружи» участников сцены по акту главы
    arcs = arc_lines(exporter.load_arcs(exports_dir), acts, brief, infobans, participants)

    # «что было раньше» глазами фокала: события, детали, хвост предыдущей главы
    try:
        continuity = exporter.load_continuity(exports_dir)
    except FileNotFoundError:
        continuity = []
    tail_chapter, tail_text = prior_tail(library, briefs, brief, root, lim.tail_paragraphs, lim.tail_chars)
    # карточки сцен поглавника: клаузы для читателя/инструмента вырезаны;
    # «кладём» сцен — в техзадание закладок, не в биты; без карточек — строки сцен через тот же фильтр
    cards = [scene_for_window(sc, markers, volume) for sc in brief.scene_cards]
    scene_lines = [scene_line(sc) for sc in cards] or [s for s in (clean(x) for x in brief.scenes) if s]
    scene_plants = [f"сц. {sc.number}: {p}" for sc in cards for p in sc.plants]
    plants, hidden_plants = _plants_for_writer(chapter_plants(exports_dir, brief), markers, volume)
    rules = _line_rules(stoplists, participants, brief.year, brief, markers)
    norm_ids = window_norm_ids(norms, root)

    env = Environment(undefined=StrictUndefined, trim_blocks=False, lstrip_blocks=False)
    window = env.from_string(_template_text(ws)).render(
        series=man.проект.имя,
        modules=enabled,
        brief=brief,
        beats=beats,
        hidden_beats=hidden_beats,
        prior_events=prior_events(briefs, brief, infobans, lim.prior_events_max),
        prior_continuity=prior_continuity(continuity, brief, infobans, participants, briefs, lim.prior_continuity_max),
        prior_tail_chapter=tail_chapter,
        prior_tail=tail_text,
        tail_begin=TAIL_BEGIN,
        tail_end=TAIL_END,
        norms={k: norms[k] for k in norm_ids},
        style_sections=_style_sections(library, root),
        focalization_laws=_focalization_laws(exports_dir),
        line_rules=[r for r in rules if not r["epoch"]],
        epoch_rules=[r for r in rules if r["epoch"]],
        prose_rules=_prose_rules(stoplists, participants, markers, volume),
        dossiers=scene_dossiers,
        known_facts=known,
        learns_now=learns_now,
        not_knows=not_knows,
        plants=plants,
        hidden_plants=hidden_plants,
        doses=_doses_for_writer(chapter_doses(exports_dir, brief), markers, volume),
        documents=_documents_for_writer(chapter_documents(exports_dir, brief), markers, volume),
        scene_lines=scene_lines,
        scene_plants=scene_plants,
        bans=bans,
        infoban_lines=infoban_lines,
        intensifiers=intensifiers,
        volume_norm=norms.get("объём_главы"),
        drama=drama,
        drama_lines=drama_lines,
        drama_intro=circles.window_intro(ws, drama) if drama_lines else "",
        arc_lines=arcs,
    )

    window = re.sub(r"\n{3,}", "\n\n", window)  # пустые строки от условных блоков шаблона не копятся
    path = ws.window_path(chapter)
    guard.write_text(path, window)

    breakdown = section_breakdown(window)
    flag_path = ws.chapter_dir(chapter) / "window_size_флаг.md"
    if len(window) > lim.soft_limit_chars:
        guard.write_text(flag_path, size_flag_text(chapter, len(window), lim.soft_limit_chars, breakdown))
    else:
        guard.remove(flag_path)  # окно уложилось в лимит — устаревший флаг не остаётся (FR-WN-6)
    return path, breakdown


def size_flag_text(chapter: int, size: int, soft_limit_chars: int, breakdown: dict[str, int]) -> str:
    """Текст флага превышения лимита: раскладка по секциям и подсказка, какие секции сокращать (FR-WN-6)."""
    lines = [
        f"⚠ Окно главы {chapter} превышает мягкий лимит {soft_limit_chars} символов (Д-20): {size}.",
        "Раскладка по секциям (символов):",
    ] + [f"  - {name}: {n}" for name, n in breakdown.items()]
    candidates = sorted(((n, s) for n, s in breakdown.items() if n not in ESSENTIAL_SECTIONS and s),
                        key=lambda x: (-x[1], x[0]))[:3]
    if candidates:
        lines.append("Сократить в первую очередь (самые крупные необязательные секции):")
        for name, s in candidates:
            hint = SECTION_LIMIT_HINTS.get(name, "сократите документ канона, из которого собрана секция, или выключите модуль")
            lines.append(f"  - {name} ({s}): {hint}")
    return "\n".join(lines) + "\n"


def section_breakdown(window: str) -> dict[str, int]:
    """Размер окна по секциям (FR-WN-6) — по маркерам <!-- СЕКЦИЯ: ... -->."""
    parts = SECTION_RE.split(window)
    breakdown: dict[str, int] = {}
    # parts: [до первой секции, имя1, тело1, имя2, тело2, ...]
    for i in range(1, len(parts) - 1, 2):
        breakdown[parts[i]] = len(parts[i + 1])
    return breakdown
