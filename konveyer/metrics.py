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

from . import lang as lang_mod
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
    part_range: tuple[int, int] | None = None
    # производные (считаются один раз)
    text: str = ""
    sentences: list[str] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        L = self.language
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


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*-{3,}\s*$", "", text, flags=re.M)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return text


# ------------------------------------------------------------------ общие помощники


def status_of(actual: float, norm: Norm) -> str:
    """PASS/FLAG/BRAK по коридору нормы. BRAK — только если задан порог брака."""
    if norm.brak is not None:
        if norm.min is not None and actual < norm.brak:
            return "BRAK"
        if norm.min is None and norm.max is not None and actual > norm.brak:
            return "BRAK"
    if norm.min is not None and actual < norm.min:
        return "FLAG"
    if norm.max is not None and actual > norm.max:
        return "FLAG"
    return "PASS"


def corridor(norm: Norm) -> str:
    parts = []
    if norm.min is not None:
        parts.append(f"мин {norm.min:g}")
    if norm.max is not None:
        parts.append(f"макс {norm.max:g}")
    if norm.brak is not None:
        parts.append(f"брак {norm.brak:g}")
    return ", ".join(parts) + (f" {norm.unit}" if norm.unit else "")


def stoplist_applies(rule: StopRule, brief: Brief) -> bool:
    """Действует ли правило в главе: линия (фокал), год, том (`volume`) и «до главы» (`until_chapter`)."""
    applies = rule.applies_to
    if "volume" in applies and int(applies["volume"]) != brief.volume:
        return False
    if "until_chapter" in applies and brief.chapter > int(applies["until_chapter"]):
        return False
    if "focal" in applies:
        return applies["focal"] == brief.focal
    if "year" in applies and brief.year is not None:
        y = applies["year"]
        if "before" in y:
            return brief.year < y["before"]
        if "from" in y:
            return y["from"] <= brief.year <= y.get("to", 9999)
    return True


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


def corpus_scope(corpus_dir: Path, volume: int, part_range: tuple[int, int] | None) -> list[Path]:
    """Файлы корпуса для TTR-окна: том брифа и, если известна часть, её главы; файлы без номера — не отсеиваются."""
    files: list[Path] = []
    for f in sorted(corpus_dir.glob("*.txt"), key=lambda p: p.name):
        m = _CORPUS_STEM_RE.search(f.stem)
        if m is not None:
            vol, ch = int(m.group(1)), int(m.group(2))
            if vol != volume or (part_range and not part_range[0] <= ch <= part_range[1]):
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


def ttr(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    return len({t.lower() for t in tokens}) / len(tokens)


def rolling_ttr(tokens: list[str], window: int) -> list[tuple[int, float]]:
    result: list[tuple[int, float]] = []
    if window <= 0 or len(tokens) < window:
        return result
    step = max(1, window // 10)
    for end in range(window, len(tokens) + 1, step):
        result.append((end, ttr(tokens[end - window: end])))
    return result


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


REGISTRY: dict[str, Metric] = {}


def metric(id: str, check_id: str, description: str, unit: str, *, scope: str = "проза", kind: str = "метрика",
           params: tuple[str, ...] = (), needs: tuple[str, ...] = ()):
    def deco(fn):
        REGISTRY[id] = Metric(id, check_id, description, unit, scope, kind, params, needs, fn)
        return fn
    return deco


def parameter(id: str, description: str, unit: str) -> None:
    REGISTRY[id] = Metric(id, "", description, unit, "проза", "параметр", (), (), None)


def available() -> list[str]:
    return list(REGISTRY)


def unknown_norms(norms: dict[str, Norm] | list[str]) -> list[str]:
    """Идентификаторы норм, для которых нет вычислителя (FR-V1-2)."""
    ids = norms if isinstance(norms, list) else list(norms)
    return [n for n in ids if n not in REGISTRY]


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


@metric("средняя_длина", "V1.2a_средняя_длина", "средняя длина предложения", "слов")
def m_avg(ctx: MetricContext) -> list[CheckResult]:
    L = ctx.lengths
    return _result(ctx, REGISTRY["средняя_длина"], round(sum(L) / len(L), 2) if L else 0.0)


@metric("доля_коротких", "V1.2b_доля_коротких", "доля предложений не длиннее порога короткой фразы", "доля",
        params=("короткая_фраза_порог",))
def m_short(ctx: MetricContext) -> list[CheckResult]:
    thr = ctx.param("короткая_фраза_порог")
    if thr is None or not ctx.lengths:
        return []
    return _result(ctx, REGISTRY["доля_коротких"], round(sum(1 for x in ctx.lengths if x <= thr) / len(ctx.lengths), 3))


@metric("доля_длинных", "V1.2c_доля_длинных", "доля предложений не короче порога длинной фразы", "доля",
        params=("длинная_фраза_порог",))
def m_long(ctx: MetricContext) -> list[CheckResult]:
    thr = ctx.param("длинная_фраза_порог")
    if thr is None or not ctx.lengths:
        return []
    return _result(ctx, REGISTRY["доля_длинных"], round(sum(1 for x in ctx.lengths if x >= thr) / len(ctx.lengths), 3))


@metric("максимум_длины", "V1.2d_максимум_длины", "самое длинное предложение", "слов")
def m_max(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.lengths:
        return []
    L = ctx.language
    longest = max(zip(ctx.lengths, [s for s in ctx.sentences if L.words(s)], strict=True))
    return _result(ctx, REGISTRY["максимум_длины"], longest[0], quotes=[longest[1]])


@metric("объём_главы", "V1.2e_объём", "объём главы (коридор мин–макс, если бриф не задаёт объём)", "слов", needs=("бриф",))
def m_volume(ctx: MetricContext) -> list[CheckResult]:
    if ctx.brief.volume_words:
        return []
    return _result(ctx, REGISTRY["объём_главы"], ctx.n_words)


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


@metric("был_на_250", "V1.3_был", "плотность лексем «был/было/были» (набор «был» языкового модуля) на 250 слов", "шт/250 слов")
def m_byl(ctx: MetricContext) -> list[CheckResult]:
    forms = ctx.language.lexemes("был")
    count = sum(1 for t in ctx.tokens if t in forms)
    return _result(ctx, REGISTRY["был_на_250"], round(count / ctx.n_words * 250, 2) if ctx.n_words else 0.0,
                   quotes=ctx.quote(forms), note=f"{count} вхождений на {ctx.n_words} слов")


def lexeme_metric(norm_id: str, lexemes: list[str], per: int = 1000, check_id: str | None = None,
                  description: str = "") -> Metric:
    """Плотность заданных лексем (FR-V1-1): норма `лексемы:<имя>` объявляется в стиле со списком слов
    (колонка «параметр»: «слово1, слово2 на 1000 слов»)."""
    forms = {lang_mod.get().normalize_word(w) for w in lexemes}
    cid = check_id or f"V1.3_{norm_id}"

    def compute(ctx: MetricContext) -> list[CheckResult]:
        count = sum(1 for t in ctx.tokens if t in forms)
        m = REGISTRY[norm_id]
        return _result(ctx, m, round(count / ctx.n_words * per, 2) if ctx.n_words else 0.0,
                       quotes=ctx.quote(forms), note=f"{count} вхождений на {ctx.n_words} слов")

    m = Metric(norm_id, cid, description or f"плотность лексем {', '.join(lexemes)} на {per} слов", f"шт/{per} слов",
               "проза", "метрика", (), (), compute)
    REGISTRY[norm_id] = m
    return m


@metric("усилители_на_1000", "V1.4_усилители", "плотность наречий-усилителей по словарю стиля на 1000 слов", "шт/1000 слов",
        needs=("стоп-листы",))
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
    out: list[CheckResult] = []
    for rule in ctx.stoplists:
        if rule.kind != "лексика" or not stoplist_applies(rule, ctx.brief):
            continue
        # стоп-лист линии фокала касается ВНУТРЕННЕЙ речи: реплики других персонажей — не флаг; лексика эпохи — весь текст
        scope_text = narration if rule.scope == "линии" else ctx.text
        found = find_items(scope_text, rule.items, L)
        if found:
            out.append(CheckResult(
                check_id="V1.5_стоп_лексика", status="FLAG", threshold=f"действие: {rule.action}", actual="; ".join(found),
                quotes=quote_sentences(ctx.sentences, {L.normalize_word(w) for w in found}, L),
                rule_source=f"{rule.rule_id} (реестр {rule.scope})",
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
        for f in corpus_scope(ctx.corpus_dir, ctx.brief.volume, None):
            if ctx.own_stem and f.stem == ctx.own_stem:
                continue
            scope_files.append(f.stem)
            part_tokens.extend(f.read_text(encoding="utf-8").split())
    part_tokens.extend(ctx.tokens)
    rolling = rolling_ttr(part_tokens, win_size)
    min_ttr = min((v for _, v in rolling), default=None)
    short_corpus = min_ttr is None and bool(part_tokens)
    if short_corpus:
        min_ttr = round(len(set(part_tokens)) / len(part_tokens), 3)
    status = ("PASS" if short_corpus or min_ttr is None or ttr_norm.min is None
              else "BRAK" if ttr_norm.brak is not None and min_ttr < ttr_norm.brak
              else "FLAG" if min_ttr < ttr_norm.min else "PASS")
    out.append(CheckResult(
        check_id="V1.8b_ttr_окно", status=status,
        threshold=f"мин {ttr_norm.min:g}" + (f", брак {ttr_norm.brak:g}" if ttr_norm.brak else "") + f" в окне {win_size} слов",
        actual=(f"{min_ttr:.3f}" if min_ttr is not None else "корпус пуст")
               + (f" (справочно: том короче окна, {len(part_tokens)} слов)" if short_corpus else ""),
        rule_source=ttr_norm.source,
        note=f"корпус: том {ctx.brief.volume}" + (f" ({', '.join(scope_files)})" if scope_files else " (корпус пуст)")))
    return out


@metric("доля_диалога", "V1.9a_доля_диалога", "доля абзацев-реплик", "доля", scope="диалог")
def m_dialogue(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.paragraphs:
        return []
    d = sum(1 for p in ctx.paragraphs if ctx.language.is_dialogue_paragraph(p))
    return _result(ctx, REGISTRY["доля_диалога"], round(d / len(ctx.paragraphs), 3),
                   note=f"{d} реплик-абзацев из {len(ctx.paragraphs)}")


@metric("фраз_в_абзаце", "V1.9b_фраз_в_абзаце", "среднее число предложений в абзаце", "предложений")
def m_para(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.paragraphs:
        return []
    L = ctx.language
    per = [len([s for s in L.split_sentences(p, ctx.extra_abbr) if L.words(s)]) for p in ctx.paragraphs]
    single = sum(1 for n in per if n <= 1)
    return _result(ctx, REGISTRY["фраз_в_абзаце"], round(sum(per) / len(per), 2),
                   note=f"однострочных абзацев: {single} из {len(ctx.paragraphs)} (приём, не норма)")


@metric("документ_вставка", "V1.11_документ_вставка", "документ-вставка из брифа оформлен блоком «→ ДОКУМЕНТ … ← КОНЕЦ ДОКУМЕНТА»",
        "да/нет", needs=("бриф",))
def m_document(ctx: MetricContext) -> list[CheckResult]:
    if not ctx.brief.documents:
        return []
    has = lang_mod.DOC_START in ctx.raw and lang_mod.DOC_END in ctx.raw
    return [CheckResult(check_id="V1.11_документ_вставка", status="PASS" if has else "BRAK",
                        threshold="блок `→ ДОКУМЕНТ` … `← КОНЕЦ ДОКУМЕНТА`", actual="есть" if has else "нет",
                        rule_source="бриф главы (реестр документов)", note="; ".join(ctx.brief.documents)[:200])]


# ------------------------------------------------------------------ прогон и документация


ALWAYS = {"стоп_лексика", "утечка_окна", "межглавные_повторы", "объём_брифа", "документ_вставка"}  # без своей нормы


def register_lexeme_norms(norms: dict[str, Norm]) -> None:
    """Нормы вида `лексемы_<имя>` с перечнем слов в единице («слово1, слово2 на 1000») — динамические метрики."""
    for nid, n in norms.items():
        if nid in REGISTRY or not nid.startswith("лексемы_"):
            continue
        m = re.match(r"^\s*(.+?)\s+на\s+(\d+)", n.unit or "")
        if not m:
            continue
        words = [w.strip() for w in re.split(r"[,;/]", m.group(1)) if w.strip()]
        lexeme_metric(nid, words, per=int(m.group(2)))


def run(ctx: MetricContext) -> list[CheckResult]:
    register_lexeme_norms(ctx.norms)
    checks: list[CheckResult] = []
    for m in REGISTRY.values():
        if m.kind == "параметр" or m.compute is None:
            continue
        if m.id not in ctx.norms and m.id not in ALWAYS:
            continue  # метрика без нормы не считается (FR-V1-2)
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
