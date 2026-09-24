"""Реестр метрик Э1 (FR-V1-1): каждый вычислитель объявлен один раз — идентификатор, что считает, единица,
параметры, применимость (проза | повествование | диалог). Пороги — только из документа стиля (FR-V1-2):
метрика без нормы не считается; норма с неизвестным идентификатором — ошибка валидации с перечнем доступных.
Языковые правила (предложения, лексемы, основы) — из языкового модуля (`lang.py`, FR-V1-3).

Реестр обходится по порядку объявления; каждая метрика получает `MetricContext` и возвращает результаты проверок
(`CheckResult`) — те же идентификаторы `V1.*`, что и в эталоне.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import lang as lang_mod, textutils
from .schemas import Brief, CheckResult, Norm, StopRule

MAX_QUOTES = 10
_CORPUS_STEM_RE = re.compile(r"Том0*(\d+)_Глава0*(\d+)")
_TAIL_BLOCK_RE = re.compile(r"<!-- ХВОСТ ПРОЗЫ[^>]*-->.*?<!-- КОНЕЦ ХВОСТА -->", re.DOTALL)


# ------------------------------------------------------------------ контекст


@dataclass
class MetricContext:
    raw: str
    brief: Brief
    norms: dict[str, Norm]
    stoplists: list[StopRule]
    language: lang_mod.Language
    window_raw: str = ""
    corpus_dir: Path | None = None
    own_stem: str | None = None
    extra_abbr: Path | None = None
    documents: list[str] | None = None   # документы-вставки главы (реестр + бриф); None — только из брифа
    # производные (считаются один раз)
    text: str = ""
    sentences: list[str] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        L = self.language
        if self.documents is None:
            self.documents = list(self.brief.documents)
        self.text = strip_markdown(L.strip_document_inserts(self.raw))
        self.sentences = L.split_sentences(self.text, self.extra_abbr)
        self.lengths = [len(L.words(s)) for s in self.sentences if L.words(s)]
        self.tokens = L.normalize(self.text)
        self.paragraphs = [p for p in L.paragraphs(self.text) if p.strip()]

    @property
    def n_words(self) -> int:
        return len(self.tokens)

    def norm(self, norm_id: str) -> Norm | None:
        return self.norms.get(norm_id)

    def param(self, norm_id: str) -> float | None:
        """Числовое значение нормы-параметра (макс, иначе мин); нет нормы — None (умолчаний в коде нет)."""
        n = self.norms.get(norm_id)
        if n is None:
            return None
        return n.max if n.max is not None else n.min

    def quote(self, items: set[str]) -> list[str]:
        return quote_sentences(self.sentences, items, self.language)


strip_markdown = textutils.strip_markdown
ttr = textutils.ttr
rolling_ttr = textutils.rolling_ttr


# ------------------------------------------------------------------ общие помощники


def brak_side(norm: Norm) -> str | None:
    """Сторона порога брака по его положению относительно коридора: «низ» (брак ≤ мин или задан только мин),
    «верх» (брак ≥ макс или задан только макс); None — брак не задан или его сторона неопределима
    (брак без мин/макс, брак строго внутри коридора) — такую норму отвергает экспорт (`norm_problems`)."""
    if norm.brak is None:
        return None
    if norm.min is not None and norm.brak <= norm.min:
        return "низ"
    if norm.max is not None and norm.brak >= norm.max:
        return "верх"
    if norm.min is not None and norm.max is None:
        return "низ"
    if norm.max is not None and norm.min is None:
        return "верх"
    return None


def status_of(actual: float, norm: Norm) -> str:
    """PASS/FLAG/BRAK по коридору нормы. BRAK — только если задан порог брака и его сторона определима."""
    side = brak_side(norm)
    if side == "низ" and actual < norm.brak:
        return "BRAK"
    if side == "верх" and actual > norm.brak:
        return "BRAK"
    if norm.min is not None and actual < norm.min:
        return "FLAG"
    if norm.max is not None and actual > norm.max:
        return "FLAG"
    return "PASS"


def norm_problems(norms: dict[str, Norm]) -> list[tuple[str, str]]:
    """Нормы, которые приняты таблицей, но проверяться не будут или будут проверяться неверно (FR-V1-2):
    брак без мин/макс, брак внутри коридора, норма метрики без нормы-параметра. Возвращает [(id, что не так)]."""
    out: list[tuple[str, str]] = []
    for nid, n in norms.items():
        if n.brak is not None and brak_side(n) is None:
            if n.min is None and n.max is None:
                out.append((nid, f"задан брак {n.brak:g} без мин/макс — сторона порога неизвестна; задайте мин или макс"))
            else:
                out.append((nid, f"брак {n.brak:g} внутри коридора {n.min:g}–{n.max:g} — брак должен лежать "
                                 "не выше мин (нижний порог) или не ниже макс (верхний порог)"))
        m = REGISTRY.get(nid)
        if m is None or m.kind == "параметр":
            continue
        missing = [p for p in m.params if p not in norms]
        if missing:
            out.append((nid, f"метрика не будет проверяться: нет нормы-параметра {', '.join(missing)}"))
    return out


def corridor(norm: Norm) -> str:
    parts = []
    if norm.min is not None:
        parts.append(f"мин {norm.min:g}")
    if norm.max is not None:
        parts.append(f"макс {norm.max:g}")
    if norm.brak is not None:
        parts.append(f"брак {norm.brak:g}")
    return ", ".join(parts) + (f" {norm.unit}" if norm.unit else "")


def year_applies(applies: dict, year: int | None) -> bool:
    """Ограничение правила годом («до 1999», «1990–1999»); год главы неизвестен — правило действует."""
    if "year" not in applies or year is None:
        return True
    y = applies["year"]
    if "before" in y:
        return year < y["before"]
    if "from" in y:
        return y["from"] <= year <= y.get("to", 9999)
    return True


def volume_applies(applies: dict, volume: int | None) -> bool:
    """Ограничение правила томом: диапазон «2», «1–2», «с 3» ({from, to}) или один том числом; том главы неизвестен —
    правило действует (FR-V1-4)."""
    if "volume" not in applies or volume is None:
        return True
    v = applies["volume"]
    if isinstance(v, dict):
        return v.get("from", 0) <= volume <= v.get("to", 10**6)
    return int(v) == volume


def chapter_applies(applies: dict, brief: Brief) -> bool:
    """Правило действует в главе брифа по году, тому и «до главы» (`until_chapter`, включительно); линию не проверяет —
    окно и Э2 берут правила всех участников сцены, не только фокала."""
    if "until_chapter" in applies and brief.chapter > int(applies["until_chapter"]):
        return False
    return year_applies(applies, brief.year) and volume_applies(applies, brief.volume)


def stoplist_applies(rule: StopRule, brief: Brief) -> bool:
    """Правило действует для главы (FR-V1-4): линия фокала, год, том и «до главы»."""
    applies = rule.applies_to
    if "focal" in applies and applies["focal"] != brief.focal:
        return False
    return chapter_applies(applies, brief)


def find_items(text: str, items: list[str], language: lang_mod.Language | None = None) -> list[str]:
    L = language or lang_mod.get()
    return [it for it in items if it and L.item_pattern(it).search(text)]


def quote_sentences(sentences: list[str], items: set[str], language: lang_mod.Language | None = None) -> list[str]:
    """Предложения, в которых встречаются слова (по основам) — цитаты для вердикта."""
    L = language or lang_mod.get()
    patterns = [L.item_pattern(it) for it in items if it]
    out: list[str] = []
    for s in sentences:
        if any(p.search(s) for p in patterns):
            out.append(s)
            if len(out) >= MAX_QUOTES:
                break
    return out


def corpus_scope(corpus_dir: Path, volume: int) -> list[Path]:
    """Файлы корпуса для TTR-окна: главы тома брифа; файлы без номера тома — не отсеиваются."""
    files: list[Path] = []
    for f in sorted(corpus_dir.glob("*.txt"), key=lambda p: p.name):
        m = _CORPUS_STEM_RE.search(f.stem)
        if m is not None and int(m.group(1)) != volume:
            continue
        files.append(f)
    return files


def matching_runs(text_tokens: list[str], target_ngrams: set[tuple], n: int) -> list[str]:
    """Максимальные дословные совпадения длиной ≥ n токенов."""
    runs: list[str] = []
    i = 0
    while i <= len(text_tokens) - n:
        if tuple(text_tokens[i: i + n]) in target_ngrams:
            j = i + n
            while j <= len(text_tokens) - 1 and tuple(text_tokens[j - n + 1: j + 1]) in target_ngrams:
                j += 1
            runs.append(" ".join(text_tokens[i:j]))
            i = j
        else:
            i += 1
    return runs[:MAX_QUOTES]


def strip_prose_tail(window: str) -> str:
    """Хвост прозы предыдущей главы в окне — цитата канона, не промпт: из проверки утечки исключается (FR-WN-5)."""
    return _TAIL_BLOCK_RE.sub("", window)


# ------------------------------------------------------------------ реестр


@dataclass(frozen=True)
class Metric:
    id: str                       # идентификатор нормы в документе стиля
    check_id: str                 # идентификатор проверки в вердикте (V1.*)
    description: str
    unit: str
    scope: str = "проза"          # проза | повествование | диалог
    kind: str = "метрика"         # метрика | параметр (используется другими метриками, сам не проверяется)
    params: tuple[str, ...] = ()  # нормы-параметры, от которых зависит
    needs: tuple[str, ...] = ()   # бриф | окно | корпус | стоп-листы
    compute: Callable[[MetricContext], list[CheckResult]] | None = None
    # калибровка (FR-V1-7): (нужна нижняя граница, нужна верхняя); None — по образцам не калибруется
    calibrate: tuple[bool, bool] | None = None
    decimals: int = 0             # знаков после запятой в предлагаемых коридорах


REGISTRY: dict[str, Metric] = {}


def metric(id: str, check_id: str, description: str, unit: str, *, scope: str = "проза", kind: str = "метрика",
           params: tuple[str, ...] = (), needs: tuple[str, ...] = (), calibrate: tuple[bool, bool] | None = None,
           decimals: int = 0):
    def deco(fn):
        REGISTRY[id] = Metric(id, check_id, description, unit, scope, kind, params, needs, fn, calibrate, decimals)
        return fn
    return deco


def parameter(id: str, description: str, unit: str) -> None:
    REGISTRY[id] = Metric(id, "", description, unit, "проза", "параметр", (), (), None)


def available() -> list[str]:
    return list(REGISTRY)


def unknown_norms(norms: dict[str, Norm] | list[str]) -> list[str]:
    """Идентификаторы норм, для которых нет вычислителя (FR-V1-2); лексемная норма известна, если её единица
    разобрана («слово1, слово2 на 1000 слов»)."""
    if isinstance(norms, list):
        return [n for n in norms if n not in REGISTRY]
    return [n for n, norm in norms.items() if n not in REGISTRY and lexeme_norm(n, norm) is None]


def _result(ctx: MetricContext, m: Metric, actual: float, quotes: list[str] | None = None, note: str = "") -> list[CheckResult]:
    norm = ctx.norm(m.id)
    if norm is None:
        return []
    return [CheckResult(check_id=m.check_id, status=status_of(actual, norm), threshold=corridor(norm),
                        actual=f"{actual:g}", quotes=quotes or [], rule_source=norm.source, note=note)]


# --- параметры (пороги, которыми пользуются метрики)
parameter("короткая_фраза_порог", "порог «короткой» фразы (для доли коротких)", "слов")
parameter("длинная_фраза_порог", "порог «длинной» фразы (для доли длинных)", "слов")
parameter("объём_допуск", "допуск отклонения объёма от брифа", "доля")
parameter("ttr_окно_слов", "окно расчёта TTR по тому", "слов")
parameter("утечка_нграмма", "длина совпадения с окном («утечка промпта»)", "слов")
parameter("повтор_нграмма", "длина межглавного повтора", "слов")


@metric("средняя_длина", "V1.2a_средняя_длина", "средняя длина предложения", "слов", calibrate=(True, True), decimals=1)
def m_avg(ctx: MetricContext) -> list[CheckResult]:
    L = ctx.lengths
    return _result(ctx, REGISTRY["средняя_длина"], round(sum(L) / len(L), 2) if L else 0.0)


@metric("доля_коротких", "V1.2b_доля_коротких", "доля предложений не длиннее порога короткой фразы", "доля",
        params=("короткая_фраза_порог",), calibrate=(True, True), decimals=2)
def m_short(ctx: MetricContext) -> list[CheckResult]:
    thr = ctx.param("короткая_фраза_порог")
    if thr is None or not ctx.lengths:
        return []
    return _result(ctx, REGISTRY["доля_коротких"], round(sum(1 for x in ctx.lengths if x <= thr) / len(ctx.lengths), 3))


@metric("доля_длинных", "V1.2c_доля_длинных", "доля предложений не короче порога длинной фразы", "доля",
        params=("длинная_фраза_порог",), calibrate=(False, True), decimals=2)
def m_long(ctx: MetricContext) -> list[CheckResult]:
    thr = ctx.param("длинная_фраза_порог")
    if thr is None or not ctx.lengths:
        return []
    return _result(ctx, REGISTRY["доля_длинных"], round(sum(1 for x in ctx.lengths if x >= thr) / len(ctx.lengths), 3))


@metric("максимум_длины", "V1.2d_максимум_длины", "самое длинное предложение", "слов", calibrate=(False, True))
def m_max(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.lengths:
        return []
    L = ctx.language
    longest = max(zip(ctx.lengths, [s for s in ctx.sentences if L.words(s)], strict=True))
    return _result(ctx, REGISTRY["максимум_длины"], longest[0], quotes=[longest[1]])


@metric("объём_главы", "V1.2e_объём", "объём главы (коридор мин–макс, если бриф не задаёт объём или нет нормы «объём_допуск»)",
        "слов", needs=("бриф",), calibrate=(True, True))
def m_volume(ctx: MetricContext) -> list[CheckResult]:
    if ctx.brief.volume_words and ctx.norm("объём_допуск") is not None:
        return []  # объём брифа проверяет «объём_брифа»
    note = f"бриф задаёт {ctx.brief.volume_words} слов, но нормы «объём_допуск» нет — проверен коридор стиля" if ctx.brief.volume_words else ""
    return _result(ctx, REGISTRY["объём_главы"], ctx.n_words, note=note)


@metric("объём_брифа", "V1.2e_объём", "соответствие объёму брифа (допуск — параметр «объём_допуск»)", "слов",
        params=("объём_допуск",), needs=("бриф",))
def m_brief_volume(ctx: MetricContext) -> list[CheckResult]:
    norm = ctx.norm("объём_допуск")
    if not ctx.brief.volume_words or norm is None:
        return []
    tolerance = ctx.param("объём_допуск") or 0.0
    n = ctx.n_words
    deviation = abs(n - ctx.brief.volume_words) / ctx.brief.volume_words
    return [CheckResult(check_id="V1.2e_объём", status="BRAK" if deviation > tolerance else "PASS",
                        threshold=f"{ctx.brief.volume_words} слов ± {tolerance:.0%}",
                        actual=f"{n} слов (отклонение {deviation:.0%})", rule_source=norm.source)]


@metric("был_на_250", "V1.3_был", "плотность лексем «был/было/были» (набор «был» языкового модуля) на 250 слов", "шт/250 слов",
        calibrate=(False, True), decimals=1)
def m_byl(ctx: MetricContext) -> list[CheckResult]:
    forms = ctx.language.lexemes("был")
    count = sum(1 for t in ctx.tokens if t in forms)
    return _result(ctx, REGISTRY["был_на_250"], round(count / ctx.n_words * 250, 2) if ctx.n_words else 0.0,
                   quotes=ctx.quote(forms), note=f"{count} вхождений на {ctx.n_words} слов")


LEXEME_PREFIX = "лексемы_"
_LEXEME_UNIT_RE = re.compile(r"^\s*(.+?)\s+на\s+(\d+)")


def lexeme_norm(norm_id: str, norm: Norm) -> tuple[list[str], int] | None:
    """Динамическая метрика плотности заданных лексем (FR-V1-1): норма `лексемы_<имя>` в документе стиля, перечень
    слов и база — в единице («слово1, слово2 на 1000 слов»). Возвращает (слова, база) или None, если единица
    не разобрана. Слова живут в норме, не в реестре: правка документа стиля действует с первого прогона."""
    if not norm_id.startswith(LEXEME_PREFIX):
        return None
    m = _LEXEME_UNIT_RE.match(norm.unit or "")
    if not m:
        return None
    words = [w.strip() for w in re.split(r"[,;/]", m.group(1)) if w.strip()]
    return (words, int(m.group(2))) if words else None


def lexeme_metric(norm_id: str, norm: Norm) -> Metric | None:
    """Вычислитель лексемной нормы (не хранится в реестре — строится на каждый прогон из нормы)."""
    parsed = lexeme_norm(norm_id, norm)
    if parsed is None:
        return None
    lexemes, per = parsed

    def compute(ctx: MetricContext) -> list[CheckResult]:
        forms = {ctx.language.normalize_word(w) for w in lexemes}
        count = sum(1 for t in ctx.tokens if t in forms)
        return _result(ctx, m, round(count / ctx.n_words * per, 2) if ctx.n_words else 0.0,
                       quotes=ctx.quote(forms), note=f"{count} вхождений на {ctx.n_words} слов")

    m = Metric(norm_id, f"V1.3_{norm_id}", f"плотность лексем {', '.join(lexemes)} на {per} слов", f"шт/{per} слов",
               "проза", "метрика", (), (), compute, (False, True), 1)
    return m


def describe(norm_id: str, norm: Norm | None = None) -> str | None:
    """Описание метрики для нормы: из реестра или из самой нормы (лексемная); None — метрика неизвестна."""
    m = REGISTRY.get(norm_id) or (lexeme_metric(norm_id, norm) if norm is not None else None)
    return m.description if m else None


@metric("усилители_на_1000", "V1.4_усилители", "плотность наречий-усилителей по словарю стиля на 1000 слов", "шт/1000 слов",
        needs=("стоп-листы",), calibrate=(False, True), decimals=1)
def m_intensifiers(ctx: MetricContext) -> list[CheckResult]:
    L = ctx.language
    forms = {L.normalize_word(w) for r in ctx.stoplists if r.kind == "усилитель" for w in r.items}
    if not forms:
        return []
    count = sum(1 for t in ctx.tokens if t in forms)
    return _result(ctx, REGISTRY["усилители_на_1000"], round(count / ctx.n_words * 1000, 2) if ctx.n_words else 0.0,
                   quotes=ctx.quote(forms), note=f"{count} вхождений")


@metric("стоп_лексика", "V1.5_стоп_лексика", "запрещённая лексика по стоп-листам (линия, год, том; реплики персонажей отделены)",
        "вхождений", scope="повествование", needs=("стоп-листы", "бриф"))
def m_stoplists(ctx: MetricContext) -> list[CheckResult]:
    L = ctx.language
    narration = L.narration_only(ctx.text)
    narration_sentences = L.split_sentences(narration, ctx.extra_abbr)
    out: list[CheckResult] = []
    for rule in ctx.stoplists:
        if rule.kind != "лексика" or not stoplist_applies(rule, ctx.brief):
            continue
        # стоп-лист линии фокала касается ВНУТРЕННЕЙ речи: реплики других персонажей — не флаг; лексика эпохи — весь текст
        line_rule = rule.narrator_only
        scope_text = narration if line_rule else ctx.text
        found = find_items(scope_text, rule.items, L)
        if found:
            out.append(CheckResult(
                check_id="V1.5_стоп_лексика", status="FLAG", threshold=f"действие: {rule.action}", actual="; ".join(found),
                # цитаты — оттуда же, где искали: реплика персонажа нарушением линии не считается и в цитаты не идёт
                quotes=quote_sentences(narration_sentences if line_rule else ctx.sentences, {L.normalize_word(w) for w in found}, L),
                rule_source=f"{rule.rule_id} ({rule.scope_label})",
                note="проверьте значение: прямое значение эпохи допустимо" if rule.action == "флаг" else ""))
    if not out:
        out.append(CheckResult(check_id="V1.5_стоп_лексика", status="PASS", threshold="0 вхождений", actual="0",
                               rule_source="stoplists.json"))
    return out


@metric("утечка_окна", "V1.6_утечка_окна", "дословные совпадения с окном («утечка промпта»)", "совпадений",
        params=("утечка_нграмма",), needs=("окно",))
def m_leak(ctx: MetricContext) -> list[CheckResult]:
    n = int(ctx.param("утечка_нграмма") or 0)
    if not n:
        return []
    L = ctx.language
    win_tokens = L.normalize(strip_prose_tail(ctx.window_raw))
    leaks = matching_runs(ctx.tokens, set(L.ngrams(win_tokens, n)), n) if win_tokens else []
    return [CheckResult(check_id="V1.6_утечка_окна", status="FLAG" if leaks else "PASS",
                        threshold=f"совпадения ≥ {n} слов с окном", actual=str(len(leaks)), quotes=leaks,
                        rule_source=ctx.norms["утечка_нграмма"].source)]


@metric("межглавные_повторы", "V1.7_межглавные_повторы", "дословные n-граммы против корпуса принятых глав", "совпадений",
        params=("повтор_нграмма",), needs=("корпус",))
def m_repeats(ctx: MetricContext) -> list[CheckResult]:
    n = int(ctx.param("повтор_нграмма") or 0)
    if not n:
        return []
    L = ctx.language
    repeats: list[str] = []
    sources: list[str] = []
    if ctx.corpus_dir is not None and ctx.corpus_dir.exists():
        text_ngrams = set(L.ngrams(ctx.tokens, n))
        for f in sorted(ctx.corpus_dir.glob("*.txt")):
            if ctx.own_stem and f.stem == ctx.own_stem:
                continue
            hits = matching_runs(f.read_text(encoding="utf-8").split(), text_ngrams, n)
            if hits:
                sources.append(f.stem)
                repeats.extend(f"[{f.stem}] {h}" for h in hits[:3])
    return [CheckResult(check_id="V1.7_межглавные_повторы", status="FLAG" if repeats else "PASS",
                        threshold=f"n-граммы ≥ {n} слов против корпуса", actual=str(len(repeats)),
                        quotes=repeats[:MAX_QUOTES], rule_source=ctx.norms["повтор_нграмма"].source,
                        note="главы-источники: " + ", ".join(sources) if sources else "")]


@metric("ttr_мин", "V1.8b_ttr_окно", "лексическое разнообразие: TTR главы (справочно) и минимум скользящим окном по тому",
        "доля", params=("ttr_окно_слов",), needs=("корпус",))
def m_ttr(ctx: MetricContext) -> list[CheckResult]:
    ttr_norm = ctx.norm("ttr_мин")
    if ttr_norm is None or ctx.norm("ttr_окно_слов") is None:
        return []
    out = [CheckResult(check_id="V1.8a_ttr_главы", status="PASS", threshold="справочно", actual=f"{ttr(ctx.tokens):.3f}",
                       rule_source=ttr_norm.source)]
    win_size = int(ctx.param("ttr_окно_слов") or 0)
    part_tokens: list[str] = []
    scope_files: list[str] = []
    if ctx.corpus_dir is not None and ctx.corpus_dir.exists():
        for f in corpus_scope(ctx.corpus_dir, ctx.brief.volume):
            if ctx.own_stem and f.stem == ctx.own_stem:
                continue
            scope_files.append(f.stem)
            part_tokens.extend(f.read_text(encoding="utf-8").split())
    corpus_len = len(part_tokens)
    part_tokens.extend(ctx.tokens)
    rolling = rolling_ttr(part_tokens, win_size)
    # вердикт — только по окнам, захватывающим текст главы: разнообразие уже принятых глав черновик не исправит
    min_ttr = min((v for end, v in rolling if end > corpus_len), default=None)
    corpus_min = min((v for end, v in rolling if end <= corpus_len), default=None)
    short_corpus = min_ttr is None and bool(part_tokens)
    if short_corpus:
        min_ttr = round(len(set(part_tokens)) / len(part_tokens), 3)
    status = "PASS" if short_corpus or min_ttr is None else status_of(min_ttr, ttr_norm)
    note = f"корпус: том {ctx.brief.volume}" + (f" ({', '.join(scope_files)})" if scope_files else " (корпус пуст)")
    if corpus_min is not None:
        note += f"; справочно: минимум по окнам принятых глав {corpus_min:.3f}"
    out.append(CheckResult(
        check_id="V1.8b_ttr_окно", status=status,
        threshold=f"{corridor(ttr_norm)} в окне {win_size} слов",
        actual=(f"{min_ttr:.3f}" if min_ttr is not None else "корпус пуст")
               + (f" (справочно: том короче окна, {len(part_tokens)} слов)" if short_corpus else ""),
        rule_source=ttr_norm.source, note=note))
    return out


@metric("доля_диалога", "V1.9a_доля_диалога", "доля абзацев-реплик", "доля", scope="диалог", calibrate=(True, True), decimals=2)
def m_dialogue(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.paragraphs:
        return []
    d = sum(1 for p in ctx.paragraphs if ctx.language.is_dialogue_paragraph(p))
    return _result(ctx, REGISTRY["доля_диалога"], round(d / len(ctx.paragraphs), 3),
                   note=f"{d} реплик-абзацев из {len(ctx.paragraphs)}")


@metric("фраз_в_абзаце", "V1.9b_фраз_в_абзаце", "среднее число предложений в абзаце", "предложений", calibrate=(True, True), decimals=1)
def m_para(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.paragraphs:
        return []
    L = ctx.language
    per = [len([s for s in L.split_sentences(p, ctx.extra_abbr) if L.words(s)]) for p in ctx.paragraphs]
    single = sum(1 for n in per if n <= 1)
    return _result(ctx, REGISTRY["фраз_в_абзаце"], round(sum(per) / len(per), 2),
                   note=f"однострочных абзацев: {single} из {len(ctx.paragraphs)} (приём, не норма)")


@metric("документ_вставка", "V1.11_документ_вставка", "документ-вставка главы (реестр документов или бриф) оформлен блоком "
        "«→ ДОКУМЕНТ … ← КОНЕЦ ДОКУМЕНТА»", "да/нет", needs=("бриф",))
def m_document(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.documents:
        return []
    L = ctx.language
    has = L.has_document_insert(ctx.raw)
    return [CheckResult(check_id="V1.11_документ_вставка", status="PASS" if has else "BRAK",
                        threshold=f"блок `{L.doc_start}` … `{L.doc_end}`", actual="есть" if has else "нет",
                        rule_source="реестр документов / бриф главы", note="; ".join(ctx.documents)[:200])]


# ------------------------------------------------------------------ прогон и документация


ALWAYS = {"стоп_лексика", "утечка_окна", "межглавные_повторы", "объём_брифа", "документ_вставка"}  # без своей нормы


def active_metrics(norms: dict[str, Norm]) -> list[Metric]:
    """Вычислители прогона: метрики реестра с нормой (или без своей нормы — `ALWAYS`) и лексемные метрики из норм."""
    out: list[Metric] = []
    for m in REGISTRY.values():
        if m.kind == "параметр" or m.compute is None:
            continue
        if m.id not in norms and m.id not in ALWAYS:
            continue  # метрика без нормы не считается (FR-V1-2)
        out.append(m)
    for nid in sorted(norms):
        if nid not in REGISTRY:
            lm = lexeme_metric(nid, norms[nid])
            if lm is not None:
                out.append(lm)
    return out


def run(ctx: MetricContext) -> list[CheckResult]:
    checks: list[CheckResult] = []
    for m in active_metrics(ctx.norms):
        checks.extend(m.compute(ctx))
    return checks


def documentation() -> str:
    lines = ["# Реестр метрик Э1", "", "Пороги — только из документа стиля (таблица норм: id, мин, макс, брак); "
             "метрика без нормы не считается. Параметры — нормы, которыми пользуются другие метрики.", "",
             "| id нормы | проверка | что считает | единица | применимость | зависит от |", "|---|---|---|---|---|---|"]
    for m in REGISTRY.values():
        kind = "параметр" if m.kind == "параметр" else m.check_id
        lines.append(f"| {m.id} | {kind} | {m.description} | {m.unit} | {m.scope} | {', '.join(m.params) or '—'} |")
    return "\n".join(lines) + "\n"
