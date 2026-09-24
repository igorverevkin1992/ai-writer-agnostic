"""Линтер канона (FR-LT-1…FR-LT-6): противоречия между реестрами проекта.

Машинный слой — детерминированные проверки по выгрузкам; каждая объявлена кодом, серьёзностью и модулем,
который её включает (`модули/*.yaml: линтер`): выключенный модуль → проверка не выполняется и ложных находок
не даёт (FR-MD-2, FR-LT-6). Каждая находка — файл, строка, сообщение и подсказка, что делать; механическое
исправление — `LintFix`, применяет только автор внутри сессии записи (FR-LT-4). Результат кэшируется по
отпечатку канона (FR-LT-5). Модельный слой (`run_lint_llm`) — по требованию, с лимитом документов и сметой.

Форматно-специфичные проверки серии (например, по документам эталона) подключаются профилем проекта как
плагины `линтер/*.py` (функция `checks(ctx) -> list[LintFinding]`), не правкой движка (П-1).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Callable

from . import adapters, catalog, exporter, guard, llmjson, manifest as manifest_mod, names, textutils, verifier1
from .config import Config
from .mdparse import MarkupError
from .paths import Workspace
from .schemas import Brief, InfoBan, LintFinding, LintFix, LintReport, MatrixFact

# основы месяцев — из языкового слоя (`языки/ru.yaml: месяцы`); запасной набор на случай урезанного файла языка
_MONTHS_FALLBACK = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йя]": 5, "июн": 6, "июл": 7,
                    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
_MONTH_ENDINGS = r"(?:[аеуяюь]|ем|ом|ах|ям|ями)?"
DATE_NUM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")
DATE_WORD_RE = re.compile(r"\b(\d{1,2})\s+([а-яё]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)(1\d{3}|20\d{2})(?!\d)")


def _month_table() -> list[tuple[re.Pattern, int]]:
    try:
        raw = textutils._lang().raw.get("месяцы") or {}
    except (ValueError, OSError, AttributeError):
        raw = {}
    stems = {str(k): int(v) for k, v in raw.items()} if raw else _MONTHS_FALLBACK
    return [(re.compile(rf"(?<![а-яё])(?:{stem}){_MONTH_ENDINGS}(?![а-яё])", re.IGNORECASE), num) for stem, num in stems.items()]


def parse_month(word: str) -> int | None:
    """Слово — месяц? «июня» → 6, «мая» → 5; «Майор», «Маркиз», «Сенька» → None (только полная основа месяца)."""
    for rx, num in _month_table():
        if rx.fullmatch(word.strip()):
            return num
    return None


def parse_date(text: str) -> tuple[int, int] | None:
    """«12.04», «ночь 18.04», «12 июня 1995», «ночь с 12 на 13 июня» → (месяц, день); «та же ночь» → None."""
    m = DATE_NUM_RE.search(text or "")
    if m:
        return int(m.group(2)), int(m.group(1))
    for m in DATE_WORD_RE.finditer(text or ""):
        mon = parse_month(m.group(2))
        if mon:
            return mon, int(m.group(1))
    return None


def parse_year(text: str) -> int | None:
    """Год, явно названный в дате («3 января 1996», «12.06.1995»); нет — None."""
    m = YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


def _months(text: str) -> set[int]:
    """Месяцы периода («май–июнь» → {5, 6}); имена собственные с похожим началом («Майор») месяцами не считаются."""
    found = [num for _, num in sorted((m.start(), num) for rx, num in _month_table() for m in rx.finditer(text or ""))]
    if len(found) >= 2 and found[0] <= found[-1]:
        return set(range(found[0], found[-1] + 1))
    return set(found)


# ------------------------------------------------------------------ контекст прогона


_LINES_CACHE: dict[tuple[Path, int, int], list[str]] = {}


def _lines(path: Path | None) -> list[str]:
    if path is None:
        return []
    try:
        st = path.stat()
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if key not in _LINES_CACHE:
        try:
            _LINES_CACHE[key] = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            _LINES_CACHE[key] = []
        if len(_LINES_CACHE) > 500:
            for old in list(_LINES_CACHE)[:250]:
                _LINES_CACHE.pop(old, None)
    return _LINES_CACHE[key]


def marker_hit(text: str, markers: list[str]) -> str | None:
    """Маркер тайны в тексте по границам слова и основе (как стоп-лексика Э1), а не подстрокой."""
    low = text.lower().replace("ё", "е")
    for m in markers:
        if m and verifier1.item_pattern(m).search(low):
            return m
    return None


@dataclass
class LintContext:
    library: Path
    exports: Path
    root: Path
    volume: int
    types: dict
    modules: dict
    manifest: manifest_mod.Manifest
    enabled_codes: set[str]
    briefs: list[Brief] = field(default_factory=list)
    matrix: list[MatrixFact] = field(default_factory=list)
    infobans: list[InfoBan] = field(default_factory=list)
    plants: list = field(default_factory=list)
    continuity: list = field(default_factory=list)
    dossiers: list = field(default_factory=list)
    stoplists: list = field(default_factory=list)
    acts: list = field(default_factory=list)
    circles: list = field(default_factory=list)
    arcs: list = field(default_factory=list)
    doses: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    chronicle: list = field(default_factory=list)
    chronology: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    narration: list = field(default_factory=list)
    norms: dict = field(default_factory=dict)
    known: set[str] = field(default_factory=set)
    pseudo: set[str] = field(default_factory=set)

    def doc(self, тип: str) -> Path | None:
        docs = self.manifest.docs(self.library, тип, self.volume, self.types)
        return docs[0] if docs else None

    def docs(self, тип: str) -> list[Path]:
        return self.manifest.docs(self.library, тип, self.volume, self.types)

    def rel(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return path.relative_to(self.library).as_posix()
        except ValueError:
            return path.name

    def line_of(self, path: Path | None, needle: str, start: int = 0) -> int | None:
        if path is None or not needle:
            return None
        for i, line in enumerate(_lines(path)[start:], start=start + 1):
            if needle in line:
                return i
        return None

    def brief_loc(self, b: Brief) -> tuple[str, int | None]:
        path = self.doc("план_глав")
        line = b.line or self.line_of(path, f"Глава {b.chapter}") or self.line_of(path, f"| {b.chapter} |")
        return self.rel(path), line

    @property
    def hi(self) -> int:
        vol = [b.chapter for b in self.briefs if b.volume == self.volume]
        return max(vol) if vol else 0

    @property
    def year(self) -> int | None:
        return next((b.year for b in self.briefs if b.year), None)

    def dossier_of(self, name: str):
        return next((d for d in self.dossiers if d.name.lower() == name.lower()), None)


def load_context(library: Path, exports_dir: Path, volume: int, root: Path | None = None) -> LintContext:
    root = exporter.project_root_of(library, root)
    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    man = manifest_mod.effective(root, library, types)
    enabled = catalog.enabled_lint_codes(modules, man.enabled_modules(modules))
    ctx = LintContext(library=library, exports=exports_dir, root=root, volume=volume, types=types, modules=modules,
                      manifest=man, enabled_codes=enabled)
    ctx.briefs = exporter.load_briefs(exports_dir)
    ctx.matrix = exporter.load_matrix(exports_dir)
    ctx.infobans = exporter.load_infobans(exports_dir)
    ctx.plants = exporter.load_plants(exports_dir)
    ctx.continuity = exporter.load_continuity(exports_dir)
    ctx.dossiers = exporter.load_dossiers(exports_dir)
    ctx.stoplists = exporter.load_stoplists(exports_dir)
    ctx.acts = exporter.load_acts(exports_dir)
    ctx.circles = exporter.load_circles(exports_dir)
    ctx.arcs = exporter.load_arcs(exports_dir)
    ctx.doses = exporter.load_doses(exports_dir)
    ctx.documents = exporter.load_documents(exports_dir)
    ctx.chronicle = exporter.load_chronicle(exports_dir)
    ctx.chronology = exporter.load_chronology(exports_dir)
    ctx.decisions = exporter.load_decisions(exports_dir)
    ctx.narration = exporter.load_narration(exports_dir)
    ctx.norms = exporter.load_norms(exports_dir)
    ctx.pseudo = exporter.pseudo_subjects(exports_dir, root)
    ctx.known = exporter.known_names_of(exports_dir)
    if ctx.briefs:
        ctx.volume = ctx.briefs[0].volume
    return ctx


# ------------------------------------------------------------------ реестр проверок

Check = Callable[[LintContext], list[LintFinding]]
CHECKS: list[tuple[tuple[str, ...], Check]] = []


def check(*codes: str):
    def deco(fn: Check) -> Check:
        CHECKS.append((codes, fn))
        return fn
    return deco


def _f(code: str, severity: str, file: str, line: int | None, message: str, hint: str = "", **kw) -> LintFinding:
    text = message if not hint else f"{message}. Что сделать: {hint}"
    return LintFinding(code=code, severity=severity, file=file, line=line, message=text, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ хронология брифов, части, акты


@check("ХРОН-1", "ХРОН-2")
def check_chronology(ctx: LintContext) -> list[LintFinding]:
    """ХРОН-1 — дата вне календаря (день сверяется с месяцем и годом); ХРОН-2 — порядок глав против дат: при годах,
    названных в датах, сравнение полное; без года — по месяцу и дню, а «декабрь → январь» считается сменой года."""
    out: list[LintFinding] = []
    last: tuple[int | None, int, int] | None = None
    last_ch = None
    for b in sorted((x for x in ctx.briefs if x.volume == ctx.volume), key=lambda b: b.chapter):
        d = parse_date(b.date)
        if d is None:
            continue
        mon, day = d
        year = parse_year(b.date)
        file, line = ctx.brief_loc(b)
        try:
            _date(year or b.year or 2000, mon, day)  # 2000 — високосный: без года 29 февраля допустимо
        except ValueError:
            out.append(_f("ХРОН-1", "ошибка", file, line, f"гл. {b.chapter}: дата «{b.date}» вне календаря",
                          "исправьте дату главы в плане глав"))
            continue
        if last is not None:
            ly, lm, ld = last
            if year is not None and ly is not None:
                earlier = (year, mon, day) < (ly, lm, ld)
            else:
                earlier = (mon, day) < (lm, ld) and not (lm == 12 and mon == 1)
            if earlier:
                when = f"{ld:02d}.{lm:02d}" + (f".{ly}" if ly else "")
                out.append(_f("ХРОН-2", "ошибка", file, line,
                              f"гл. {b.chapter} датирована «{b.date}» — раньше гл. {last_ch} ({when}); "
                              "порядок глав нарушает хронологию тома", "переставьте главы или поправьте даты"))
        last, last_ch = (year, mon, day), b.chapter
    return out


@check("АКТ-1")
def check_ranges(ctx: LintContext) -> list[LintFinding]:
    """Акты идут подряд без разрывов и наложений и вместе покрывают ровно главы тома."""
    out: list[LintFinding] = []
    if not ctx.briefs or not ctx.acts:
        return out
    lo, hi = min(b.chapter for b in ctx.briefs), ctx.hi
    path = ctx.doc("каркасы") or ctx.doc("акты")
    expect = lo
    last = None
    for a in sorted(ctx.acts, key=lambda a: a.from_chapter):
        line = a.line or ctx.line_of(path, f"| {a.act} |") or (ctx.line_of(path, a.title[:20]) if a.title else None)
        if a.from_chapter != expect:
            out.append(_f("АКТ-1", "ошибка", ctx.rel(path), line,
                          f"акт «{a.title}» начинается с гл. {a.from_chapter}, ожидалась гл. {expect} (разрыв или наложение)",
                          "поправьте границы актов так, чтобы они шли подряд"))
        expect = a.to_chapter + 1
        last = (a, line)
    covered = expect - 1
    if covered < hi:
        out.append(_f("АКТ-1", "предупреждение", ctx.rel(path), last[1] if last else None,
                      f"акты покрывают главы до {covered}, а в томе {hi}", "добавьте главы в последний акт или заведите ещё акт"))
    elif covered > hi:
        out.append(_f("АКТ-1", "предупреждение", ctx.rel(path), last[1] if last else None,
                      f"акты покрывают главы до {covered}, а в томе {hi}",
                      "сократите последний акт до глав плана или добавьте главы в план глав"))
    return out


@check("ХРОН-3")
def check_part_period(ctx: LintContext) -> list[LintFinding]:
    """Дата главы вне периода своего акта («май–июнь» в колонке «части/период»)."""
    out: list[LintFinding] = []
    for a in ctx.acts:
        months = _months(a.parts or "")
        if not months:
            continue
        for b in ctx.briefs:
            if not (a.from_chapter <= b.chapter <= a.to_chapter):
                continue
            d = parse_date(b.date)
            if d is None or d[0] in months:
                continue
            file, line = ctx.brief_loc(b)
            out.append(_f("ХРОН-3", "ошибка", file, line, f"гл. {b.chapter} датирована «{b.date}», а акт «{a.title}» объявлен "
                          f"периодом «{a.parts}»", "поправьте дату главы или период акта"))
    return out


_CHRON_DATE_RE = re.compile(r"^\**\s*(\d{1,2})\.(\d{1,2})\s*\**$")
_CAP_WORD_RE = re.compile(r"[А-ЯЁа-яё]{4,}")


def _event_keys(event: str) -> set[str]:
    words = _CAP_WORD_RE.findall(event.replace("*", " "))
    return {w.lower()[:7] for i, w in enumerate(words) if i and w[0].isupper() and len(w) >= 6}


def _brief_text(b: Brief) -> str:
    cards = [f"{s.place} {s.time} {s.participants} {s.goal} {s.enters} {s.exits}" for s in b.scene_cards]
    return " ".join([*b.beats, b.reader_learns, *b.scenes, *cards, *b.documents])


@check("ХРОН-4")
def check_chronicle_dates(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    texts = {b.chapter: _brief_text(b).lower() for b in ctx.briefs}
    by_ch = {b.chapter: b for b in ctx.briefs}
    for ev in ctx.chronicle:
        m = _CHRON_DATE_RE.match(ev.date.strip())
        if not m or "✓" not in ev.status:
            continue
        day, month = int(m.group(1)), int(m.group(2))
        for key in sorted(_event_keys(ev.event)):
            hits = [ch for ch, text in texts.items() if key in text]
            if not hits or len(hits) > 2:
                continue
            for ch in hits:
                d = parse_date(by_ch[ch].date)
                if d is None or d == (month, day):
                    continue
                file, line = ctx.brief_loc(by_ch[ch])
                out.append(_f("ХРОН-4", "предупреждение", file, line,
                              f"гл. {ch} датирована «{by_ch[ch].date}», а названное в ней событие "
                              f"«{ev.event.replace('*', '').strip()[:60]}» хроника датирует {ev.date.strip('* ')}",
                              "сверьте дату главы с хроникой эпохи"))
    return out


_WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "четверг": 3, "пятницу": 4, "субботу": 5, "воскресенье": 6}
_WEEKDAY_RE = re.compile(r"\bво?\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@check("ХРОН-5")
def check_weekdays(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    for b in ctx.briefs:
        d = parse_date(b.date)
        if d is None or not b.year:
            continue
        try:
            actual = _date(b.year, d[0], d[1]).weekday()
        except ValueError:
            continue
        for m in _WEEKDAY_RE.finditer(_brief_text(b)):
            named = _WEEKDAYS[m.group(1).lower()]
            if named == actual:
                continue
            file, line = ctx.brief_loc(b)
            out.append(_f("ХРОН-5", "ошибка", file, line, f"гл. {b.chapter}: «{m.group(0)}», а {d[1]:02d}.{d[0]:02d}.{b.year} — "
                          f"{_WEEKDAY_NAMES[actual]}", "поправьте день недели или дату"))
    return out


# ------------------------------------------------------------------ фокалы и досье

_FOCAL_VOL_RE = re.compile(r"фокал\w*\s*(?:[:—-]\s*)?(?:с\s+)?т\.?\s*(\d+)(?:\s*[–-]\s*(\d+))?", re.IGNORECASE)


def _focal_volumes(status: str) -> set[int] | None:
    """Из «Статуса» карточки: тома, где персонаж может быть фокалом; None — не разобрано; пусто — никогда."""
    if not status:
        return None
    if re.search(r"фокала не имеет|не фокален|без фокала|никогда не фокал", status, re.IGNORECASE):
        return set()
    m = _FOCAL_VOL_RE.search(status)
    if not m:
        return None
    a = int(m.group(1))
    z = int(m.group(2)) if m.group(2) else (99 if re.search(r"\bс\s+т", status) else a)
    return set(range(a, z + 1))


@check("ФОКАЛ-1", "ФОКАЛ-2")
def check_focals(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    dossier_names = {d.name.lower() for d in ctx.dossiers}
    for b in sorted(ctx.briefs, key=lambda b: b.chapter):
        if not b.focal:
            continue
        file, line = ctx.brief_loc(b)
        if ctx.dossiers and b.focal.lower() not in dossier_names:
            out.append(_f("ФОКАЛ-1", "предупреждение", file, line, f"гл. {b.chapter}: фокал «{b.focal}» без карточки персонажа — "
                          "окно не получит его профиль", "заведите карточку в папке персонажей или исправьте имя фокала"))
            continue
        d = ctx.dossier_of(b.focal)
        vols = _focal_volumes(d.status) if d else None
        if vols is not None and b.volume not in vols:
            out.append(_f("ФОКАЛ-2", "ошибка", file, line, f"гл. {b.chapter}: фокал «{b.focal}», но по карточке ({d.file}) он не "
                          f"фокален в т.{b.volume} (разрешено: {', '.join('т.' + str(v) for v in sorted(vols)) or 'нигде'})",
                          "смените фокал главы или поправьте статус карточки"))
    return out


@check("ЭПИСТ-1")
def check_brief_epistemics(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    for b in ctx.briefs:
        text = " ".join([*b.scenes, *b.beats])
        text = textutils.narration_only(text) or text
        for ban in ctx.infobans:
            if not ban.secret or ban.known_to(b.focal, b.chapter) or not ban.markers:
                continue
            hit = marker_hit(text, ban.markers)
            if hit:
                file, line = ctx.brief_loc(b)
                out.append(_f("ЭПИСТ-1", "предупреждение", file, line,
                              f"гл. {b.chapter} (фокал {b.focal}): бриф содержит «{hit}» — маркер тайны {ban.ban_id}, которую "
                              f"{b.focal} к этой главе не знает", "проверьте, не требует ли бриф от фокала чужого знания"))
    return out


@check("ТАЙНА-3", "ТАЙНА-4", "ТАЙНА-5")
def check_secrets(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    path = ctx.doc("информрежим")
    by_ch = {b.chapter: b for b in ctx.briefs}
    for ban in ctx.infobans:
        if not ban.secret:
            continue
        line = ban.line or ctx.line_of(path, ban.ban_id)
        if not ban.markers:
            out.append(_f("ТАЙНА-3", "заметка", ctx.rel(path), line, f"{ban.ban_id}: не заданы маркеры тайны — досье и план не будут "
                          "очищаться от неё в окне Писателя", "заполните колонку маркеров (основы слов через «;»)"))
        if ban.until_chapter:
            b = by_ch.get(ban.until_chapter)
            if b and not b.documents and not ban.known_to(b.focal, ban.until_chapter):
                who = ", ".join(f"{n} (гл. {c})" if c else f"{n} (всегда)" for n, c in sorted(ban.known_by.items()))
                out.append(_f("ТАЙНА-4", "ошибка", ctx.rel(path), line, f"{ban.ban_id}: читатель узнаёт тайну в гл. {ban.until_chapter}, "
                              f"но её фокал {b.focal} тайны не знает (знают: {who or 'никто'}) — раскрывать нечем",
                              "смените главу раскрытия, фокал главы или добавьте фокала в «кто знает»"))
        for f in ctx.matrix:
            if f.fact_id == ban.ban_id and f.from_chapter is not None and f.subject not in ctx.pseudo and f.subject not in ban.known_by:
                out.append(_f("ТАЙНА-5", "предупреждение", ctx.rel(path), line, f"{ban.ban_id}: по эпистемике {f.subject} знает "
                              f"{'всегда' if f.from_chapter == 0 else 'с гл. ' + str(f.from_chapter)}, а в «кто знает» его нет",
                              "согласуйте колонку «кто знает» с матрицей"))
    return out


# ------------------------------------------------------------------ эпистемика

OFFSTAGE_RE = re.compile(r"по своим каналам|за кадром", re.IGNORECASE)
DEDUCTION_RE = re.compile(r"разгадк|улик|расчётн", re.IGNORECASE)


@check("МАТР-1", "МАТР-2", "МАТР-3")
def check_matrix(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.briefs:
        return out
    hi = ctx.hi
    path = ctx.doc("эпистемика")
    by_ch = {b.chapter: b for b in ctx.briefs}
    focal_lines = {b.focal for b in ctx.briefs if b.focal}
    by_fact: dict[str, list[MatrixFact]] = {}
    for f in ctx.matrix:
        by_fact.setdefault(f.fact_id, []).append(f)
    for f in ctx.matrix:
        line = f.line or ctx.line_of(path, f.fact_id) or ctx.line_of(path, f.fact[:30])
        if f.from_chapter is not None and f.from_chapter > hi:
            out.append(_f("МАТР-1", "ошибка", ctx.rel(path), line, f"{f.fact_id} ({f.subject}): узнаёт в гл. {f.from_chapter}, "
                          f"а в томе {hi} глав", "поправьте главу или добавьте главу в план"))
            continue
        if f.subject in ctx.pseudo or not f.from_chapter or OFFSTAGE_RE.search(f.source):
            continue
        b = by_ch.get(f.from_chapter)
        if b is None or f.subject == b.focal or f.subject in b.participants:
            continue
        same = next((x for x in by_fact[f.fact_id] if x.subject == b.focal), None)
        if same is not None and same.from_chapter == f.from_chapter:
            continue
        out.append(_f("МАТР-2", "ошибка" if f.subject in focal_lines else "предупреждение", ctx.rel(path), line,
                      f"{f.fact_id} ({f.subject}): узнаёт в гл. {f.from_chapter}, но это глава фокала {b.focal or '?'}, и "
                      f"{f.subject} не значится среди участников", "добавьте участника в план главы или пометьте источник «за кадром»"))
    for fid, rows in by_fact.items():
        reader = next((x for x in rows if x.subject in ctx.pseudo), None)
        if reader and reader.from_chapter and not DEDUCTION_RE.search(reader.note or ""):
            b = by_ch.get(reader.from_chapter)
            focal = next((x for x in rows if b and x.subject == b.focal), None)
            if b and focal is not None and not b.documents and (focal.from_chapter is None or focal.from_chapter > reader.from_chapter):
                knows = "не знает его до конца тома" if focal.from_chapter is None else f"узнаёт только в гл. {focal.from_chapter}"
                out.append(_f("МАТР-3", "предупреждение", ctx.rel(path), reader.line or ctx.line_of(path, fid),
                              f"{fid}: читатель узнаёт в гл. {reader.from_chapter}, а фокал этой главы ({b.focal}) {knows} — "
                              "читатель получает факт через голову фокала", "смените главу или фокал раскрытия"))
    return out


# ------------------------------------------------------------------ закладки и континуити

PLANT_OR_RE = re.compile(r"\d\s*(?:,\s*\d+\s*)*или\s*\d")


@check("ЗАКЛ-1", "ЗАКЛ-2", "ЗАКЛ-3", "ЗАКЛ-4", "ЗАКЛ-7")
def check_plants(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.briefs:
        return out
    hi = ctx.hi
    path = ctx.doc("закладки")
    by_ch = {b.chapter: b for b in ctx.briefs}
    in_volume = {b.focal for b in ctx.briefs if b.focal} | {n for b in ctx.briefs for n in b.participants}
    for p in ctx.plants:
        line = p.line or ctx.line_of(path, p.plant_id) or ctx.line_of(path, p.what[:30])
        chapters = list(p.chapters) or ([p.placed["ch"]] if p.placed.get("ch") else [])
        for ch in chapters:
            if ch > hi:
                out.append(_f("ЗАКЛ-1", "ошибка", ctx.rel(path), line, f"{p.plant_id}: закладка в гл. {ch}, а в томе {hi} глав",
                              "поправьте главу закладки"))
        placed_ch = p.placed.get("ch")
        placed_vol = p.placed.get("vol")
        for fire in p.fires:
            if fire.get("vol") == placed_vol and fire.get("ch") and placed_ch and fire["ch"] < placed_ch:
                out.append(_f("ЗАКЛ-2", "ошибка", ctx.rel(path), line, f"{p.plant_id}: «стреляет» в гл. {fire['ch']} раньше, чем "
                              f"положена (гл. {placed_ch})", "поменяйте главу выстрела или закладки"))
            if placed_vol and fire.get("vol") and fire["vol"] < placed_vol:
                out.append(_f("ЗАКЛ-4", "ошибка", ctx.rel(path), line, f"{p.plant_id}: «стреляет» в томе {fire['vol']}, а положена "
                              f"в томе {placed_vol}", "поправьте том выстрела"))
        bearers = [n for n in names.find_names(p.what, ctx.known, ctx.pseudo) if n in in_volume]
        for ch in chapters:
            b = by_ch.get(ch)
            if not b or not bearers or any(n == b.focal or n in b.participants for n in bearers):
                continue
            out.append(_f("ЗАКЛ-3", "ошибка", ctx.rel(path), line, f"{p.plant_id} «{p.what[:40]}»: положена в гл. {ch} (фокал {b.focal}), "
                          f"но её носитель ({', '.join(bearers)}) в этой главе не фокал и не участник",
                          "добавьте носителя в участники главы или перенесите закладку"))
        if PLANT_OR_RE.search(p.status or ""):
            out.append(_f("ЗАКЛ-7", "предупреждение", ctx.rel(path), line, f"{p.plant_id}: «{p.status[:40]}» — две главы через «или»",
                          "выберите одну главу, иначе окно положит закладку дважды"))
    return out


def _continuity_volumes(c) -> list[int]:
    """Тома записи континуити — из поля даты/источника («т.1 гл.5»); пусто — запись без тома (общесерийная)."""
    return names.volumes_listed(c.date or "")


@check("КОНТ-1")
def check_continuity(ctx: LintContext) -> list[LintFinding]:
    """Ссылка континуити на главу, которой нет в томе: континуити — общесерийный реестр, поэтому проверяются
    только записи текущего тома (или без тома)."""
    out: list[LintFinding] = []
    if not ctx.briefs:
        return out
    path = ctx.doc("континуити")
    for c in ctx.continuity:
        vols = _continuity_volumes(c)
        if vols and ctx.volume not in vols:
            continue
        for ch in re.findall(r"\d+", c.chapters or ""):
            if int(ch) > ctx.hi:
                out.append(_f("КОНТ-1", "предупреждение", ctx.rel(path), c.line or ctx.line_of(path, c.event[:30]),
                              f"континуити «{c.event[:50]}»: ссылка на гл. {ch}, а в томе {ctx.hi} глав", "поправьте главу"))
    return out


# ------------------------------------------------------------------ карточки персонажей

_ADJ = r"(?:ый|ое|ая|ой|ом|ую|ым|ые|ых|ими|ого|ому)"
_FEATURES = [
    ("сторона глухоты", re.compile(r"глух\w*|\bух[оа]\b|\bуш(?:и|ей|ах)\b|\bслух\b", re.I), "сторона"),
    ("почерк", re.compile(r"почерк\w*", re.I), "размер"),
    ("рост", re.compile(r"\bрост[аом]?\b|\bросл\w+", re.I), "рост"),
    ("цвет глаз", re.compile(r"\bглаз\w*", re.I), "цвет"),
    ("цвет волос", re.compile(r"\bволос\w*|\bшевелюр\w*", re.I), "цвет"),
    ("увечья", re.compile(r"увеч\w*|\bрубц\w*|\bшрам\w*|изуродован\w*|ожог\w*", re.I), "сторона"),
]
_VALUES = {
    "сторона": [("левое", re.compile(rf"\bлев{_ADJ}\b|\bслева\b", re.I)), ("правое", re.compile(rf"\bправ{_ADJ}\b|\bсправа\b", re.I))],
    "размер": [("мелкий", re.compile(rf"\bмелк{_ADJ}\b", re.I)), ("крупный", re.compile(rf"\bкрупн{_ADJ}\b", re.I))],
    "рост": [("высокий", re.compile(rf"\bвысок{_ADJ}\b|\bвысокоросл\w+", re.I)), ("низкий", re.compile(rf"\bнизк{_ADJ}\b|\bнизкоросл\w+|\bкоротышк\w+", re.I))],
    "цвет": [("серый", re.compile(rf"\bсер{_ADJ}\b", re.I)), ("голубой", re.compile(rf"\bголуб{_ADJ}\b", re.I)),
             ("карий", re.compile(r"\bкари[йеяю]\w*\b", re.I)), ("зелёный", re.compile(rf"\bзел[её]н{_ADJ}\b", re.I)),
             ("чёрный", re.compile(rf"\bч[её]рн{_ADJ}\b", re.I)), ("светлый", re.compile(rf"\bсветл{_ADJ}\b", re.I)),
             ("тёмный", re.compile(rf"\bт[её]мн{_ADJ}\b", re.I)), ("рыжий", re.compile(r"\bрыж[ий]\w*\b", re.I)),
             ("седой", re.compile(rf"\bсед{_ADJ}\b|\bседин\w+", re.I))],
}


def _feature_values(text: str) -> dict[str, tuple[set[str], str]]:
    out: dict[str, tuple[set[str], str]] = {}
    for clause in re.split(r"[;·()]", text.replace("**", "")):
        clause = clause.strip()
        if not clause:
            continue
        for name, feature_re, group in _FEATURES:
            if not feature_re.search(clause):
                continue
            values = {value for value, value_re in _VALUES[group] if value_re.search(clause)}
            if not values:
                continue
            seen, quote = out.get(name, (set(), clause))
            out[name] = (seen | values, quote)
    return out


@check("ДОСЬЕ-1", "ДОСЬЕ-2", "ДОСЬЕ-3", "ДОСЬЕ-6")
def check_dossiers(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    year = ctx.year
    all_names = set(ctx.known) | {d.name for d in ctx.dossiers}
    by_name: dict[str, list] = {}
    for c in ctx.continuity:
        for name in names.find_names(c.event, ctx.known, ctx.pseudo):
            by_name.setdefault(name, []).append(c)
    for d in ctx.dossiers:
        path = ctx.library / d.file if d.file else None
        head_line = d.line or ctx.line_of(path, d.name) or 1
        if d.born_year and year:
            age = d.ages.get(f"т.{ctx.volume}")
            if age is not None and abs((year - d.born_year) - age) > 1:
                needle = f"{age} (т.{ctx.volume})"
                line = ctx.line_of(path, needle) or head_line
                fix = LintFix(file=d.file, line=line, old=needle, new=f"{year - d.born_year} (т.{ctx.volume})",
                              note="пересчитанный возраст") if ctx.line_of(path, needle) else None
                out.append(_f("ДОСЬЕ-1", "предупреждение", d.file, line, f"{d.name}: рождение ≈{d.born_year}, том {ctx.volume} = "
                              f"{year} год → возраст {year - d.born_year}, в карточке {age}", "исправьте возраст или год рождения", fix=fix))
        for ref in d.refs:
            base = re.split(r"[-–\s]", ref)[0]
            if not any(base.lower().startswith(n.lower()) or n.lower().startswith(base.lower()) for n in all_names):
                out.append(_f("ДОСЬЕ-2", "заметка", d.file, ctx.line_of(path, f"[[{ref}]]") or head_line,
                              f"ссылка [[{ref}]] — такого персонажа нет ни среди карточек, ни среди известных имён",
                              "заведите карточку или исправьте ссылку"))
        if not d.physique:
            out.append(_f("ДОСЬЕ-6", "заметка", d.file, head_line, f"{d.name}: у карточки нет секции «Физика»: Писатель обязан выдумать внешность",
                          "добавьте секцию «Физика»"))
        card = _feature_values(" ".join([d.profile, d.physique, d.code]))
        for c in by_name.get(d.name, []):
            for feature, (values, _q) in _feature_values(c.event).items():
                mine = card.get(feature)
                if not mine or not values or (mine[0] & values):
                    continue
                out.append(_f("ДОСЬЕ-3", "ошибка", d.file, ctx.line_of(path, mine[1][:40]) or head_line,
                              f"{d.name}: {feature} — в карточке «{', '.join(sorted(mine[0]))}», а континуити фиксирует "
                              f"«{', '.join(sorted(values))}» ({c.event[:60]})", "согласуйте карточку с континуити", quote=mine[1][:120]))
    return out


_DEATH_VOL_RE = re.compile(r"(?:гибнет|гибель|умирает|мёртв\w*|погибает)[^.;·|]{0,40}?т\.\s*(\d+)", re.I)
_DEATH_EVENT_RE = re.compile(r"гибель|гибнут|смерть|умирает|погиб\w*", re.I)
_AGE_ABS_RE = re.compile(r"(?<![\d.,–—-])(\d{2,3})(?:\s*[–-]\s*(\d{2,3}))?\s*(?:лет|года|год)?\s*(?:\(\s*(\d{4})|в\s+(\d{4}))")


@check("ДОСЬЕ-4", "ДОСЬЕ-5")
def check_dossier_chronology(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.chronology:
        return out
    years: dict[int, list[int]] = {}
    for ev in ctx.chronology:
        if ev.volume and ev.year:
            years.setdefault(ev.volume, []).append(ev.year)
    for d in ctx.dossiers:
        path = ctx.library / d.file if d.file else None
        body = "\n".join([d.profile, d.status, d.arc])
        told = {int(m.group(1)): m.group(0).strip() for m in _DEATH_VOL_RE.finditer(body)}
        if told:
            pattern = names.name_pattern(d.name)
            for ev in ctx.chronology:
                near = [re.split(r"[.,;:—|]", ev.event[dm.end():])[0] for dm in _DEATH_EVENT_RE.finditer(ev.event)]
                if not any(pattern.search(chunk) for chunk in near):
                    continue
                vol = ev.volume or (ev.volumes[0] if ev.volumes else None)
                if vol is None:
                    continue
                for said, quote in told.items():
                    if said != vol:
                        out.append(_f("ДОСЬЕ-4", "предупреждение", d.file, ctx.line_of(path, quote[:40]), f"{d.name}: карточка говорит "
                                      f"«{quote}», а хронология ставит «{ev.event.strip()[:50]}» ({ev.event_id}) в том {vol}",
                                      "согласуйте том гибели", quote=quote))
        if d.born_year:
            for m in _AGE_ABS_RE.finditer(body):
                lo = int(m.group(1)); hi = int(m.group(2)) if m.group(2) else lo
                y = int(m.group(3) or m.group(4))
                if abs((y - d.born_year) - lo) <= 1 or abs((y - d.born_year) - hi) <= 1:
                    continue
                out.append(_f("ДОСЬЕ-5", "предупреждение", d.file, ctx.line_of(path, m.group(0)) or d.line or 1,
                              f"{d.name}: «{m.group(0).strip()}» — при рождении ≈{d.born_year} на {y} год возраст {y - d.born_year}",
                              "поправьте возраст или год рождения", quote=m.group(0).strip()))
    return out


_ADJ_END_RE = re.compile(r"ск(ий|ая|ое|ие|ой|ую|ого|ому|им|их|ими|ом)$")
_FULL_NAME_RE = re.compile(r"(?<![«\w])([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+(?:ов|ев|ин|ын|ск)[а-яё]*)(?![а-яё])")
_SCENE_SKIP = {"те же", "один", "одна", "все", "никого"}


@check("ПОГЛ-2")
def check_scene_persons(ctx: LintContext) -> list[LintFinding]:
    """Участники сцен и событий без карточки персонажа: известные имена без карточки, роли безымянных
    (список ролей — из типа «персонажи»), полные имена в событиях."""
    roles = list((ctx.types.get("персонажи").raw.get("роли_безымянных") if ctx.types.get("персонажи") else None) or [])
    role_re = re.compile(r"(?<![а-яё])(" + "|".join(re.escape(r[:-1] if r.endswith(("й", "ь")) else r) for r in roles) + r")([а-яё]*)(?![а-яё])",
                         re.IGNORECASE) if roles else None
    dossier_names = {d.name.lower() for d in ctx.dossiers}
    if not dossier_names:
        return []
    seen: dict[str, tuple[str, list[int]]] = {}

    def note(key: str, who: str, chapter: int) -> None:
        key = key.lower()
        if key not in seen:
            seen[key] = (who, [chapter])
        elif chapter not in seen[key][1]:
            seen[key][1].append(chapter)

    def has_card(name: str) -> bool:
        return any(name.lower() == d or name.lower().startswith(d + " ") for d in dossier_names)

    card_text = " ".join(d.profile.lower() for d in ctx.dossiers)

    for b in ctx.briefs:
        for name in [b.focal, *b.participants]:
            if name and name in ctx.known and not has_card(name):
                note(name, name, b.chapter)
        for text in [*b.beats, *b.scenes]:
            if role_re:
                for m in role_re.finditer(text):
                    stem = m.group(1).lower()
                    if _ADJ_END_RE.search(m.group(0).lower()) or any(n.lower().startswith(stem) for n in ctx.known):
                        continue
                    if stem in card_text:
                        continue  # роль принадлежит персонажу с карточкой («сторож» — это Имя из карточки)
                    note(stem, m.group(0), b.chapter)
            for m in _FULL_NAME_RE.finditer(text):
                prev = text[: m.start()].rstrip()
                if not prev or prev[-1] in ".;:!?—·":
                    continue
                if not names.find_names(m.group(0), ctx.known, ctx.pseudo):
                    note(m.group(2), m.group(0), b.chapter)
    path = ctx.doc("план_глав")
    return [_f("ПОГЛ-2", "заметка", ctx.rel(path), ctx.line_of(path, who), f"участник сцены без карточки: «{who}» "
               f"(гл. {', '.join(str(c) for c in sorted(chs))})", "заведите карточку или оставьте персонажа безымянным")
            for who, chs in seen.values()]


# ------------------------------------------------------------------ каркасы, арки


@check("КРУГ-1", "КРУГ-2")
def check_frames(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.circles or not ctx.briefs:
        return out
    from . import circles as circles_mod

    ws = Workspace(ctx.root, ctx.volume)
    path = ctx.doc("каркасы")
    lo, hi = min(b.chapter for b in ctx.briefs), ctx.hi
    for c in ctx.circles:
        try:
            m = circles_mod.methodic_for(ws, c.scope)
            required = m.required_steps(circles_mod.LEVEL_OF[c.scope], ctx.manifest)
            total = {s.n for s in m.steps}
        except Exception:  # noqa: BLE001 — без методики каркас не проверяется по составу
            required, total = set(), set()
        present = {st.n for st in c.steps if not circles_mod.step_unset(st)}
        head_line = c.line or ctx.line_of(path, c.title[:20])
        if required and not required <= present:
            missing = sorted(required - present)
            out.append(_f("КРУГ-1", "предупреждение", ctx.rel(path), head_line,
                          f"{c.title}: не заданы обязательные шаги {missing} (методика «{m.title}»)",
                          "заполните шаги или снимите их обязательность в манифесте"))
        if total and any(st.n not in total for st in c.steps):
            out.append(_f("КРУГ-1", "предупреждение", ctx.rel(path), head_line,
                          f"{c.title}: есть шаги вне методики «{m.title}» ({sorted(st.n for st in c.steps if st.n not in total)})",
                          "уберите лишние шаги"))
        if c.scope in ("книга", "акт"):
            if c.scope == "книга":
                a, z = lo, hi
            else:
                act = next((x for x in ctx.acts if x.act == c.key), None)
                if not act:
                    continue
                a, z = act.from_chapter, act.to_chapter
            # шаги идут по порядку и лежат внутри границ; несколько шагов на одну главу — норма короткого тома
            prev_from = a
            for st in c.steps:
                if st.from_chapter is None:
                    continue
                st_lo, st_hi = st.from_chapter, st.to_chapter or st.from_chapter
                if st_lo < a or st_hi > z:
                    out.append(_f("КРУГ-2", "предупреждение", ctx.rel(path), ctx.line_of(path, st.text[:25], max(head_line or 1, 1) - 1),
                                  f"{c.title}, шаг {st.n} «{st.name}» ({st.chapters}) выходит за границы {a}–{z}",
                                  "поправьте диапазон глав шага"))
                    continue  # шаг вне границ не сдвигает «предыдущий» — иначе каскад находок по следующим шагам
                if st_lo < prev_from:
                    out.append(_f("КРУГ-2", "предупреждение", ctx.rel(path), ctx.line_of(path, st.text[:25], max(head_line or 1, 1) - 1),
                                  f"{c.title}, шаг {st.n} «{st.name}» ({st.chapters}) начинается раньше предыдущего шага (гл. {prev_from})",
                                  "переставьте шаги или поправьте диапазоны глав"))
                prev_from = max(prev_from, st_lo)
    return out


@check("АРКА-1", "АРКА-2")
def check_arcs(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.arcs:
        return out
    path = ctx.doc("арки")
    dossier_names = {d.name for d in ctx.dossiers}
    act_numbers = {a.act for a in ctx.acts}
    seen: set[tuple[str, str]] = set()
    for arc in ctx.arcs:
        if dossier_names and arc.character not in dossier_names and ("АРКА-1", arc.character) not in seen:
            seen.add(("АРКА-1", arc.character))
            out.append(_f("АРКА-1", "заметка", ctx.rel(path), arc.line or ctx.line_of(path, f"| {arc.character} |"),
                          f"арки: персонаж «{arc.character}» без карточки", "опечатка в имени или нужна карточка"))
        if act_numbers and arc.act not in act_numbers and ("АРКА-2", str(arc.act)) not in seen:
            seen.add(("АРКА-2", str(arc.act)))
            out.append(_f("АРКА-2", "заметка", ctx.rel(path), arc.line or ctx.line_of(path, f"| {arc.character} | {arc.act} |"),
                          f"арки: акт {arc.act} («{arc.character}») вне таблицы актов ({', '.join(str(n) for n in sorted(act_numbers))})",
                          "поправьте номер акта"))
    return out


# ------------------------------------------------------------------ дозы, документы

_DOSE_REF_RE = re.compile(r"[Дд]оз[аеуы]\s*(?:\d{4}\s*)?№\s*(\d+)")
_DOC_REF_RE = re.compile(r"→\s*\**\s*[Дд]окумент\s*№\s*(\d+)")
_DOC_POG_RE = re.compile(r"№\s*(\d+)\s*\(([^)]*)\)")


@check("ДОЗА-1")
def check_doses(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.doses:
        return out
    path = ctx.doc("дозы_прошлого")
    grid: dict[int, int] = {}
    for b in ctx.briefs:
        for m in _DOSE_REF_RE.finditer(_brief_text(b)):
            grid.setdefault(int(m.group(1)), b.chapter)
    for dose in ctx.doses:
        num = re.search(r"\d+", dose.dose_id)
        if not num or int(num.group()) not in grid:
            continue
        if grid[int(num.group())] != dose.chapter:
            out.append(_f("ДОЗА-1", "ошибка", ctx.rel(path), ctx.line_of(path, f"| {dose.dose_id} |"),
                          f"доза {dose.dose_id}: таблица доз — гл. {dose.chapter}, план глав — гл. {grid[int(num.group())]}",
                          "доза прошлого — единственный канал воспоминаний, глава должна быть одна"))
    return out


@check("ДОК-1")
def check_documents(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.documents:
        return out
    path = ctx.doc("документы_вставки")
    pog: dict[int, int] = {}
    for b in ctx.briefs:
        for m in _DOC_REF_RE.finditer(" ".join([*b.beats, b.reader_learns])):
            pog.setdefault(int(m.group(1)), b.chapter)
        for item in b.documents:
            m = _DOC_POG_RE.match(item.strip())
            if m:
                after = re.search(r"гл\.?\s*(\d+)", m.group(2))
                pog.setdefault(int(m.group(1)), int(after.group(1)) if after else b.chapter)
    for spec in ctx.documents:
        if spec.number in pog and pog[spec.number] != spec.after_chapter:
            out.append(_f("ДОК-1", "ошибка", ctx.rel(path), ctx.line_of(path, f"| {spec.number} |"),
                          f"документ №{spec.number}: реестр — после гл. {spec.after_chapter}, план глав — после гл. {pog[spec.number]}",
                          "согласуйте место вставки"))
    return out


# ------------------------------------------------------------------ проза

PATRONYMIC = r"[А-ЯЁ][а-яё]*?(?:ович|евич|ьич|инична|ична|овна|евна)"
SURNAME = r"[А-ЯЁ][а-яё]+?(?:ов|ев|ин|ын|ск|цк)"
PROSE_NAME_RE = re.compile(rf"(?<![А-Яа-яЁё])([А-ЯЁ][а-яё]{{2,}})\s+({PATRONYMIC}|{SURNAME})([а-яё]{{0,3}})(?![А-Яа-яЁё])")
_STEM_END_RE = re.compile(r"(?:ами|ями|ого|ому|ыми|ими|ой|ей|ом|ем|ых|их|ий|ый|ая|ое|ые|ов|ев|ах|ях|[аяуюеиыоь])$")


def _stems(text: str) -> set[str]:
    return {s for w in re.findall(r"[а-яёa-z]{3,}", text.lower()) if len(s := _STEM_END_RE.sub("", w)) >= 3}


def _prose(ctx: LintContext) -> list[tuple[int, Path, Brief | None]]:
    by_ch = {b.chapter: b for b in ctx.briefs}
    return [(ch, p, by_ch.get(ch)) for ch, p in exporter.prose_files(ctx.library, ctx.volume, ctx.root)]


@check("ПРОЗА-1", "ПРОЗА-2")
def check_prose(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    for ch, path, b in _prose(ctx):
        if not b:
            continue
        lines = _lines(path)
        active = [ban for ban in ctx.infobans if ban.secret and ban.markers and not ban.known_to(b.focal, b.chapter)]
        rules = [r for r in ctx.stoplists if r.kind == "лексика" and verifier1._stoplist_applies(r, b)]
        narration = set(textutils.narration_only("\n\n".join(lines)).splitlines())
        for i, line in enumerate(lines, start=1):
            if line.strip() and line.strip() not in narration:
                continue
            for ban in active:
                hit = marker_hit(line, ban.markers)
                if hit:
                    out.append(_f("ПРОЗА-1", "предупреждение", ctx.rel(path), i, f"гл. {ch} (фокал {b.focal}): «{hit}» — маркер тайны "
                                  f"{ban.ban_id}, которой фокал ещё не знает", "проверьте фразу или знание фокала"))
            for r in rules:
                found = verifier1._find_items(line, list(r.items))
                if found:
                    out.append(_f("ПРОЗА-2", "заметка", ctx.rel(path), i, f"гл. {ch}: стоп-лексика линии [{r.rule_id}]: {', '.join(found[:3])}",
                                  "замените слово или снимите правило"))
    return out


@check("ПРОЗА-3")
def check_accepted_prose(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not ctx.norms:
        return out
    for ch, path, b in _prose(ctx):
        if not b:
            continue
        try:
            checks = verifier1.analyze(path.read_text(encoding="utf-8"), "", b, ctx.norms, ctx.stoplists)
        except (KeyError, ValueError):
            continue
        brak = [c for c in checks if c.status == "BRAK"]
        if brak:
            details = "; ".join(f"{c.check_id} = {c.actual} при пороге {c.threshold}" for c in brak)
            out.append(_f("ПРОЗА-3", "предупреждение", ctx.rel(path), None, f"принятая глава {ch} не проходит Э1 по текущим нормам: {details}",
                          "перекалибровать нормы по принятой прозе или править главу — решение автора"))
    return out


@check("ПРОЗА-4")
def check_prose_names(ctx: LintContext) -> list[LintFinding]:
    prose = _prose(ctx)
    if not prose:
        return []
    canon = "\n".join([*(d.profile + " " + d.physique + " " + d.speech + " " + " ".join(d.relations) for d in ctx.dossiers),
                       *(c.event for c in ctx.continuity)])
    canon_stems = _stems(canon)
    out: list[LintFinding] = []
    seen: set[str] = set()
    for _ch, path, _b in prose:
        text = path.read_text(encoding="utf-8")
        for m in PROSE_NAME_RE.finditer(text):
            if not names.find_names(m.group(1), ctx.known, ctx.pseudo):
                continue
            core = m.group(2).lower()
            if core in canon_stems or _STEM_END_RE.sub("", core) in canon_stems:
                continue
            full = re.sub(r"\s+", " ", m.group(0))
            key = f"{path.name}:{core}"
            if key in seen:
                continue
            seen.add(key)
            out.append(_f("ПРОЗА-4", "заметка", ctx.rel(path), text[: m.start()].count("\n") + 1,
                          f"«{full}» — имя из принятой прозы, которого нет ни в карточках, ни в континуити: новый факт прозы, не внесённый в канон",
                          "внесите в карточку/континуити или уберите из прозы", quote=full))
    return out


# ------------------------------------------------------------------ вопросы автору (КАНОН-1)

_INDEX_RANGE_RE = re.compile(r"Р-(\d+)\s*…\s*Р-(\d+)")


@check("КАНОН-1")
def check_canon_questions(ctx: LintContext) -> list[LintFinding]:
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in ctx.briefs}
    path = ctx.doc("хронология")
    for ev in ctx.chronology:
        if ev.volume != ctx.volume or not ev.chapters:
            continue
        d = parse_date(ev.date)
        months = {d[0]} if d else _months(ev.date)
        if not months:
            continue
        for ch in ev.chapters:
            b = by_ch.get(ch)
            bd = parse_date(b.date) if b else None
            if bd is None or bd[0] in months:
                continue
            out.append(_f("КАНОН-1", "заметка", ctx.rel(path), ev.line or ctx.line_of(path, ev.event_id),
                          f"{ev.event_id} датировано «{ev.date}», а гл. {ch}, где читатель его узнаёт, — «{b.date}»",
                          "согласуйте хронологию и план глав — решение за автором"))
    index = ctx.doc("индекс_библиотеки")
    if index is not None and ctx.decisions:
        last = max((int(re.sub(r"\D", "", d.decision_id) or 0) for d in ctx.decisions), default=0)
        for i, line in enumerate(_lines(index), start=1):
            rm = _INDEX_RANGE_RE.search(line)
            if rm and int(rm.group(2)) < last:
                out.append(_f("КАНОН-1", "заметка", ctx.rel(index), i, f"индекс библиотеки обещает «{rm.group(0)}», а журнал решений дошёл до Р-{last:03d}",
                              "обновите индекс"))
    # фокал открывается в разных томах: карточка против таблицы фокалов
    for d in ctx.dossiers:
        vols = _focal_volumes(d.status)
        if not vols:
            continue
        for n in ctx.narration:
            for line in n.focals_text.splitlines():
                if not line.startswith("|") or d.name not in line:
                    continue
                cell = line.strip("|").split("|")[0].strip()
                m = re.match(r"(\d+)", cell)
                if m and re.search(r"открыва", line) and int(m.group(1)) != min(vols):
                    out.append(_f("КАНОН-1", "заметка", d.file, d.line or 1, f"{d.name}: по карточке фокал с т.{min(vols)}, по таблице "
                                  f"фокалов открывается в т.{m.group(1)}", "решение за автором"))
    return out


# ------------------------------------------------------------------ плагины проекта


def _project_checks(ctx: LintContext) -> list[LintFinding]:
    folder = ctx.root / "линтер"
    out: list[LintFinding] = []
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"konveyer_lint_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = module  # type: ignore[union-attr]
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            fn = getattr(module, "checks", None)
            if fn:
                out.extend(f for f in fn(ctx) if f.code in ctx.enabled_codes or f.code not in catalog.all_lint_codes(ctx.modules))
        except Exception as e:  # noqa: BLE001 — плагин не должен ронять линтер
            out.append(LintFinding(code="ЛИНТ-0", severity="заметка", file=f"линтер/{path.name}",
                                   message=f"плагин линтера не выполнен: {type(e).__name__}: {e}"))
    return out


# ------------------------------------------------------------------ прогон


def run_checks(ctx: LintContext) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for codes, fn in CHECKS:
        if not any(c in ctx.enabled_codes for c in codes):
            continue
        for f in fn(ctx):
            if f.code in ctx.enabled_codes:
                findings.append(f)
    findings += _project_checks(ctx)
    return findings


def run_lint(library: Path, exports_dir: Path, logs_dir: Path, export: bool = True, volume: int = 1,
             root: Path | None = None, use_cache: bool = True) -> LintReport:
    """Машинный слой: экспорт + все проверки тома. Кэш по отпечатку канона (FR-LT-5): при том же отпечатке и той же
    конфигурации модулей возвращается прежний отчёт."""
    root = exporter.project_root_of(library, root)
    fingerprint = exporter.canon_fingerprint(library)
    key = f"{fingerprint}:{volume}"
    if use_cache:
        cached = load_report(logs_dir)
        if cached is not None and cached.fingerprint == key:
            return cached
    findings: list[LintFinding] = []
    try:
        if export:
            exporter.run_export(library, exports_dir, logs_dir, volume, root)
    except MarkupError as e:
        errors = getattr(e, "errors", [e])
        for err in errors:
            rel = ""
            try:
                rel = Path(err.path).relative_to(library).as_posix()
            except (ValueError, TypeError):
                rel = str(getattr(err, "path", ""))
            findings.append(LintFinding(code="РАЗМ-1", severity="ошибка", file=rel, line=getattr(err, "line", None),
                                        message=f"разметка расходится с типом документа: {str(err).split(': ', 1)[-1]}"))
        return _finish(findings, logs_dir, files=len(_library_docs(library)), fingerprint=key)
    ctx = load_context(library, exports_dir, volume, root)
    findings += run_checks(ctx)
    return _finish(findings, logs_dir, files=len(_library_docs(library)), fingerprint=key)


def _library_docs(library: Path) -> list[Path]:
    return [p for p in sorted(library.rglob("*.md"))] if library.is_dir() else []


def _finish(findings: list[LintFinding], logs_dir: Path, files: int, fingerprint: str = "") -> LintReport:
    order = {"ошибка": 0, "предупреждение": 1, "заметка": 2}
    findings.sort(key=lambda f: (order[f.severity], f.file, f.line or 0, f.code))
    report = LintReport(
        ts=datetime.now(timezone.utc).isoformat(), files_checked=files, findings=findings,
        errors=sum(1 for f in findings if f.severity == "ошибка"),
        warnings=sum(1 for f in findings if f.severity == "предупреждение"),
        notes=sum(1 for f in findings if f.severity == "заметка"),
        fingerprint=fingerprint,
    )
    guard.write_text(logs_dir / "линтер.json", report.model_dump_json(indent=2) + "\n")
    guard.write_text(logs_dir / "линтер.md", render_md(report))
    return report


def render_md(report: LintReport) -> str:
    lines = [f"# Проверка канона · {report.ts[:19].replace('T', ' ')}",
             f"Документов: {report.files_checked} · ошибок: {report.errors} · предупреждений: {report.warnings} · заметок: {report.notes}", ""]
    for f in report.findings:
        where = f"{f.file}:{f.line}" if f.line else f.file
        src = " (модель)" if f.source == "модель" else ""
        lines.append(f"- **{f.severity}** [{f.code}]{src} {where} — {f.message}")
        if f.fix:
            lines.append(f"  - исправление: «{f.fix.old}» → «{f.fix.new}»")
    return "\n".join(lines) + "\n"


def load_report(logs_dir: Path) -> LintReport | None:
    p = logs_dir / "линтер.json"
    if not p.exists():
        return None
    try:
        return LintReport.model_validate_json(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


# ------------------------------------------------------------------ исправления


def apply_fix(library: Path, fix: LintFix) -> Path:
    """Применяет предложенное исправление — ТОЛЬКО по подтверждению автора: вызывающий обязан открыть сессию записи."""
    path = (library / fix.file).resolve()
    if library.resolve() not in path.parents:
        raise ValueError("исправление указывает вне библиотеки")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = (fix.line or 1) - 1
    if idx < 0 or idx >= len(lines) or fix.old not in lines[idx]:
        raise ValueError(f"строка {fix.line} файла {fix.file} изменилась — исправление больше не подходит, перепроверьте канон")
    lines[idx] = lines[idx].replace(fix.old, fix.new, 1)
    guard.write_text(path, "".join(lines))
    return path


# ------------------------------------------------------------------ модельный слой (FR-LT-3)


def _template(root: Path | None = None) -> str:
    for cand in ([root / "промпты" / "линтер.md", root / "шаблоны" / "линтер_канона_система.md"] if root else []):
        if cand.exists():
            return cand.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/линтер_канона_система.md").read_text(encoding="utf-8")


def _context_slices(exports_dir: Path) -> str:
    briefs = exporter.load_briefs(exports_dir)
    infobans = exporter.load_infobans(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    lines = ["## Тайны тома (кто и когда знает)"]
    for b in infobans:
        if b.secret:
            when = f"читатель узнаёт: гл. {b.until_chapter}" if b.until_chapter is not None else "читателю не раскрывается в томе"
            who = "; ".join(f"{n} — {'всегда' if c == 0 else 'с гл. ' + str(c)}" for n, c in sorted(b.known_by.items()))
            lines.append(f"- {b.ban_id} {b.text} · {when} · знают: {who or 'никто'}")
    lines += ["", "## Главы тома (фокал, дата, событие)"]
    for b in sorted(briefs, key=lambda b: b.chapter):
        lines.append(f"- гл. {b.chapter} · {b.date} · {b.focal}: {'; '.join(b.beats)[:300]}")
    lines += ["", "## Матрица знаний (кто что знает и с какой главы)"]
    for f in matrix:
        if f.from_chapter is not None:
            lines.append(f"- {f.fact_id} {f.subject}: {'всегда' if f.from_chapter == 0 else 'гл. ' + str(f.from_chapter)}"
                         + (f" ({f.note})" if f.note else "") + f" — {f.fact}")
    return "\n".join(lines)


def resolve_library_files(library: Path, files: list[str] | None) -> list[Path]:
    if not files:
        return _library_docs(library)
    root = library.resolve()
    out: list[Path] = []
    for f in files:
        path = (library / f).resolve()
        if root not in path.parents or path.suffix != ".md":
            raise ValueError(f"«{f}»: документ модельного слоя должен быть .md внутри библиотеки")
        if not path.exists():
            raise ValueError(f"«{f}»: нет такого документа в библиотеке")
        out.append(path)
    return out


def estimate_llm_cost(cfg: Config, n_docs: int, avg_chars: int = 12_000) -> float | None:
    mc = cfg.role("линтер")
    if not (mc.price_in_per_1m or mc.price_out_per_1m):
        return None
    tokens_in = n_docs * (avg_chars / 3 + 3_000)
    tokens_out = n_docs * 800
    return tokens_in / 1e6 * mc.price_in_per_1m + tokens_out / 1e6 * mc.price_out_per_1m


def run_lint_llm(ws: Workspace, cfg: Config, library: Path, files: list[Path] | None = None,
                 max_calls: int | None = None, max_cost_usd: float | None = None) -> tuple[list[LintFinding], list[str]]:
    """Смысловые противоречия по документам (по одному вызову на документ), с лимитом вызовов и бюджетом
    стоимости по ценам конфига (FR-LT-3): превышение — отказ до первого вызова."""
    docs = files or _library_docs(library)
    if max_calls is not None and len(docs) > max_calls:
        raise ValueError(f"документов {len(docs)}, лимит вызовов модели {max_calls}: укажите --файл или поднимите --лимит")
    system = _template(ws.root)
    context = _context_slices(ws.exports)
    if max_cost_usd is not None:
        mc = cfg.role("линтер")
        total = sum(adapters.estimate_cost_before(mc, len(system) + len(context) + len(d.read_text(encoding="utf-8", errors="replace")), 800) or 0.0
                    for d in docs)
        if total > max_cost_usd:
            raise ValueError(f"оценка стоимости модельного слоя {total:.2f} $ выше бюджета {max_cost_usd:.2f} $: "
                             f"сузьте список --файл или поднимите бюджет")
    findings: list[LintFinding] = []
    prompts: list[str] = []
    for doc in docs:
        rel = doc.relative_to(library).as_posix()
        user = f"# Документ: {rel}\n\n<документ>\n{doc.read_text(encoding='utf-8')}\n</документ>\n\n# Контекст канона\n\n{context}"
        prompt_path = ws.logs / "линтер_промпты" / (re.sub(r"[^\w.\-]+", "_", rel) + ".md")
        guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
        try:
            raw = adapters.call_role(cfg, "линтер", system, user, ws.logs, role="линтер канона")
            findings += parse_llm_findings(raw, library, doc)
        except adapters.ManualModeNeeded:
            prompts.append(str(prompt_path))
        except Exception as e:  # noqa: BLE001
            findings.append(LintFinding(code="ЛИНТ-0", severity="заметка", file=rel, source="модель",
                                        message=f"модельный слой не дал результата по документу: {type(e).__name__}: {str(e)[:200]}"))
    return findings, prompts


def parse_llm_findings(raw: str, library: Path, doc: Path) -> list[LintFinding]:
    data = llmjson.extract_json(raw, list)
    out: list[LintFinding] = []
    lines = _lines(doc)
    for item in data:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "") or "")[:200]
        sev = str(item.get("severity", "предупреждение"))
        if sev not in ("ошибка", "предупреждение", "заметка"):
            sev = "предупреждение"
        line = next((i for i, ln in enumerate(lines, start=1) if quote[:40] and quote[:40] in ln), None)
        out.append(LintFinding(code="МОДЕЛЬ", severity=sev, file=doc.relative_to(library).as_posix(), line=line,
                               message=(str(item.get("problem", "")).strip() + (f" — {item['suggestion']}" if item.get("suggestion") else "")).strip(),
                               quote=quote, source="модель"))
    return out


def error_report(exc: BaseException, logs_dir: Path, files: int = 0) -> LintReport:
    hint = " — файл не в UTF-8: пересохраните его в UTF-8" if isinstance(exc, UnicodeDecodeError) else ""
    return _finish([LintFinding(code="ЛИНТ-0", severity="ошибка", file=str(getattr(exc, "path", "") or ""),
                                message=f"проверка канона не выполнена: {type(exc).__name__}: {exc}{hint}")], logs_dir, files)


def merge_llm(report: LintReport, extra: list[LintFinding], logs_dir: Path) -> LintReport:
    findings = [f for f in report.findings if f.source != "модель"] + extra
    return _finish(findings, logs_dir, report.files_checked, report.fingerprint)
