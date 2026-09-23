"""Верификатор-1 (Э1): формальные проверки текста (FR-V1.1…FR-V1.10).

Все пороги — ТОЛЬКО из norms.json (02 §5); в коде констант нет
(критерий приёмки 6). Метрики повествователя считаются без документов-вставок
(Д-7); деление на предложения — по Д-2.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import exporter, guard, textutils
from .paths import Workspace
from .schemas import Brief, CheckResult, DiffReport, Edit, Norm, StopRule, Verdict

MAX_QUOTES = 10


def _norm_value(norms: dict[str, Norm], norm_id: str) -> float | None:
    """Числовое значение нормы-параметра. Пороги берутся ТОЛЬКО из norms.json
    (критерий приёмки 6): нормы нет или она без числа — метрика пропускается,
    зашитого в код умолчания нет (FR-MT-3)."""
    n = norms.get(norm_id)
    if n is None:
        return None
    return n.max if n.max is not None else n.min


def _status(actual: float, norm: Norm) -> str:
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


def _corridor(norm: Norm) -> str:
    parts = []
    if norm.min is not None:
        parts.append(f"мин {norm.min:g}")
    if norm.max is not None:
        parts.append(f"макс {norm.max:g}")
    if norm.brak is not None:
        parts.append(f"брак {norm.brak:g}")
    return ", ".join(parts) + (f" {norm.unit}" if norm.unit else "")


def _stoplist_applies(rule: StopRule, brief: Brief) -> bool:
    applies = rule.applies_to
    if "focal" in applies:
        return applies["focal"] == brief.focal
    if "year" in applies and brief.year is not None:
        y = applies["year"]
        if "before" in y:
            return brief.year < y["before"]
        if "from" in y:
            return y["from"] <= brief.year <= y.get("to", 9999)
    return True


# --- стоп-лексика по основам слов (FR-V1.5, аудит 3.3): «отца», «сыну» ловятся стоп-словами «отец», «сын»
# окончания, отбрасываемые у элемента стоп-листа, чтобы получить основу
_STEM_ENDINGS = (
    "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ей", "ью",
    "а", "я", "о", "е", "ы", "и", "у", "ю",
)
# окончания словоформ, допустимые после основы в тексте (падеж, число, род)
_INFLECTIONS = (
    "ами|ями|ого|его|ому|ему|ыми|ими|ый|ий|ой|ей|ая|яя|ое|ее|ые|ие|ым|им|ых|их|ую|юю"
    "|ом|ем|ам|ям|ах|ях|ов|ев|ою|ею|ью|а|я|о|е|ы|и|у|ю"
)
MIN_STEM = 3  # короче — не основа (иначе «сын» → «с»)
_FLEETING_RE = re.compile(r"^(.+[^аеиоуыэюя])[ео]([йцкнлрхв])$")  # беглая гласная: отец → отц


def word_stems(word: str) -> list[str]:
    """Основы слова: «отец» → [«отец», «отц»], «семья» → [«семь»], «сын» → [«сын»]."""
    w = word.lower().replace("ё", "е")
    stem = w
    for end in sorted(_STEM_ENDINGS, key=len, reverse=True):
        if w.endswith(end) and len(w) - len(end) >= MIN_STEM:
            stem = w[: -len(end)]
            break
    stems = [stem]
    m = _FLEETING_RE.match(stem)
    if m and len(m.group(1)) + 1 >= MIN_STEM:
        stems.append(m.group(1) + m.group(2))
    return stems


def item_pattern(item: str) -> re.Pattern:
    """Регэксп элемента стоп-листа в нормализованном тексте (нижний регистр, ё→е):
    одно слово — сама словоформа либо основа + окончание; оборот из нескольких слов — дословно."""
    needle = item.lower().replace("ё", "е").strip()
    if len(needle.split()) > 1:
        return re.compile(r"(?<![а-яa-z])" + re.escape(needle) + r"(?![а-яa-z])")
    alts = [re.escape(needle)]
    for stem in word_stems(needle):
        # основа на «ь» («семь» от «семья») без окончания — другое слово, окончание обязательно
        alts.append(re.escape(stem) + (f"(?:{_INFLECTIONS})" if stem.endswith("ь") else f"(?:{_INFLECTIONS})?"))
    return re.compile(r"(?<![а-яa-z])(?:" + "|".join(alts) + r")(?![а-яa-z])")


def _find_items(text: str, items: list[str]) -> list[str]:
    low = text.lower().replace("ё", "е")
    return [item for item in items if item_pattern(item).search(low)]


def _quote_sentences(sentences: list[str], items: set[str]) -> list[str]:
    """Предложения-цитаты, содержащие любой из элементов (слово в любой форме или оборот)."""
    patterns = [item_pattern(i) for i in items]
    quotes = []
    for s in sentences:
        low = s.lower().replace("ё", "е")
        if any(p.search(low) for p in patterns):
            quotes.append(s)
        if len(quotes) >= MAX_QUOTES:
            break
    return quotes


_CORPUS_STEM_RE = re.compile(r"Том0*(\d+)_Глава0*(\d+)")


def corpus_scope(corpus_dir: Path, volume: int, part_range: tuple[int, int] | None) -> list[Path]:
    """Файлы корпуса для TTR-окна (аудит 3.7): том брифа и, если известна часть, её главы;
    файлы без номера тома/главы в имени не отсеиваются."""
    files: list[Path] = []
    for f in sorted(corpus_dir.glob("*.txt"), key=lambda p: p.name):  # по имени: на Windows Path сравнивается без регистра
        m = _CORPUS_STEM_RE.search(f.stem)
        if m is not None:
            vol, ch = int(m.group(1)), int(m.group(2))
            if vol != volume or (part_range and not part_range[0] <= ch <= part_range[1]):
                continue
        files.append(f)
    return files


def _matching_runs(text_tokens: list[str], target_ngrams: set[tuple], n: int) -> list[str]:
    """Максимальные дословные совпадения длиной ≥ n токенов."""
    runs: list[str] = []
    i = 0
    while i <= len(text_tokens) - n:
        if tuple(text_tokens[i : i + n]) in target_ngrams:
            j = i + n
            while j <= len(text_tokens) - 1 and tuple(text_tokens[j - n + 1 : j + 1]) in target_ngrams:
                j += 1
            runs.append(" ".join(text_tokens[i:j]))
            i = j
        else:
            i += 1
    return runs[:MAX_QUOTES]


def analyze_text(ws: Workspace, chapter: int, raw: str) -> list[CheckResult]:
    """Проверки Э1 для произвольного текста главы в контексте рабочей области (без записи вердикта):
    нормы и стоп-листы из выгрузок, окно главы, корпус части — как в run_verify1."""
    exports_dir = ws.exports
    norms = exporter.load_norms(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    brief = exporter.load_brief(exports_dir, chapter)
    window_path = ws.window_path(chapter)
    window_raw = window_path.read_text(encoding="utf-8") if window_path.exists() else ""
    own = exporter.find_corpus_file(ws.corpus, chapter, brief.volume)
    return analyze(
        raw,
        window_raw,
        brief,
        norms,
        stoplists,
        corpus_dir=ws.corpus,
        own_stem=own.stem if own else None,
        extra_abbr=ws.root / "сокращения.txt",  # пополняемый словарь (Д-2)
        part_range=part_range_for(ws.exports, chapter),
    )


def variants_summary(ws: Workspace, chapter: int, draft: int, labels: list[str] | None = None) -> dict:
    """A/B (аудит 2, п. 24б): метрики Э1 по каждому варианту черновика draft (черновик_k.md, черновик_k.alt1.md …)
    → главы/N/варианты.json. FSM и вердикт.json не трогает."""
    from . import writer

    labels = labels or writer.existing_variants(ws, chapter, draft)
    rows = []
    for label in labels:
        path = writer.variant_path(ws, chapter, draft, label)
        if not path.exists():
            continue
        checks = analyze_text(ws, chapter, path.read_text(encoding="utf-8"))
        rows.append(
            {
                "вариант": label,
                "файл": path.name,
                "слов": len(textutils.words(textutils.narrator_text(path.read_text(encoding="utf-8")))),
                "брак": sum(1 for c in checks if c.status == "BRAK"),
                "флагов": sum(1 for c in checks if c.status == "FLAG"),
                "метрики": {c.check_id: {"status": c.status, "actual": c.actual, "threshold": c.threshold} for c in checks},
            }
        )
    summary = {"глава": chapter, "черновик": draft, "варианты": rows}
    guard.write_text(
        ws.chapter_dir(chapter) / "варианты.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary


def run_verify1(ws: Workspace, chapter: int, draft: int) -> Verdict:
    raw = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    checks = analyze_text(ws, chapter, raw)
    verdict = Verdict(chapter=chapter, draft=draft, checks=checks)
    guard.write_text(
        ws.chapter_dir(chapter) / "вердикт.json",
        json.dumps(verdict.model_dump(), ensure_ascii=False, indent=2) + "\n",
    )
    return verdict


def part_range_for(exports_dir: Path, chapter: int) -> tuple[int, int] | None:
    """Главы части (акта) реестра, в которую входит глава — границы корпуса для TTR-окна."""
    try:
        parts = exporter.load_parts(exports_dir)
    except FileNotFoundError:
        return None
    for p in parts:
        if p["from_chapter"] <= chapter <= p["to_chapter"]:
            return (p["from_chapter"], p["to_chapter"])
    return None


_TAIL_BLOCK_RE = re.compile(r"<!-- ХВОСТ ПРОЗЫ[^>]*-->.*?<!-- КОНЕЦ ХВОСТА -->", re.DOTALL)


def _strip_prose_tail(window: str) -> str:
    return _TAIL_BLOCK_RE.sub("", window)


def analyze(
    raw: str,
    window_raw: str,
    brief: Brief,
    norms: dict[str, Norm],
    stoplists: list[StopRule],
    *,
    corpus_dir: Path | None = None,
    own_stem: str | None = None,
    extra_abbr: Path | None = None,
    part_range: tuple[int, int] | None = None,
) -> list[CheckResult]:
    """Чистое ядро Э1: текст + контекст → список результатов проверок.

    part_range — главы части, по корпусу которой считается TTR-окно (без него — весь том)."""
    text = textutils.narrator_text(raw)
    sentences = textutils.split_sentences(text, extra_abbr)
    lengths = [len(textutils.words(s)) for s in sentences if textutils.words(s)]
    tokens = textutils.normalize(text)
    n_words = len(tokens)
    checks: list[CheckResult] = []

    def add(check_id: str, norm_id: str, actual: float, quotes: list[str] | None = None, note: str = "") -> None:
        norm = norms.get(norm_id)
        if norm is None:  # нормы в каноне нет — метрика не считается (FR-MT-3)
            return
        checks.append(
            CheckResult(
                check_id=check_id,
                status=_status(actual, norm),
                threshold=_corridor(norm),
                actual=f"{actual:g}",
                quotes=quotes or [],
                rule_source=norm.source,
                note=note,
            )
        )

    # FR-V1.2 — длины фраз и объём
    avg = sum(lengths) / len(lengths) if lengths else 0.0
    add("V1.2a_средняя_длина", "средняя_длина", round(avg, 2))

    short_thr = _norm_value(norms, "короткая_фраза_порог")
    long_thr = _norm_value(norms, "длинная_фраза_порог")
    if lengths:
        if short_thr is not None:
            add("V1.2b_доля_коротких", "доля_коротких", round(sum(1 for x in lengths if x <= short_thr) / len(lengths), 3))
        if long_thr is not None:
            add("V1.2c_доля_длинных", "доля_длинных", round(sum(1 for x in lengths if x >= long_thr) / len(lengths), 3))
        if "максимум_длины" in norms:  # опциональная норма
            longest = max(zip(lengths, [s for s in sentences if textutils.words(s)], strict=True))
            add("V1.2d_максимум_длины", "максимум_длины", longest[0], quotes=[longest[1]])

    if not brief.volume_words and "объём_главы" in norms:
        # норма объёма из канона (Р-019): коридор мин–макс, выход — флаг
        add("V1.2e_объём", "объём_главы", n_words)
    elif brief.volume_words and "объём_допуск" in norms:
        deviation = abs(n_words - brief.volume_words) / brief.volume_words
        tolerance = _norm_value(norms, "объём_допуск") or 0.0
        checks.append(
            CheckResult(
                check_id="V1.2e_объём",
                status="BRAK" if deviation > tolerance else "PASS",
                threshold=f"{brief.volume_words} слов ± {tolerance:.0%}",
                actual=f"{n_words} слов (отклонение {deviation:.0%})",
                rule_source=norms["объём_допуск"].source,
            )
        )

    # FR-V1.3 — плотность «был/было/были». Формы намеренно зашиты: канон (02 §5) задаёт норму
    # словами «был/было», перечень форм — решение канона, не порог; не менять без Р-№ (аудит 3.7).
    byl_forms = {"был", "было", "были", "была"}
    byl_count = sum(1 for t in tokens if t in byl_forms)
    add(
        "V1.3_был",
        "был_на_250",
        round(byl_count / n_words * 250, 2) if n_words else 0.0,
        quotes=_quote_sentences(sentences, byl_forms),
        note=f"{byl_count} вхождений на {n_words} слов",
    )

    # FR-V1.4 — наречия-усилители
    intensifiers = {
        w.lower().replace("ё", "е") for r in stoplists if r.kind == "усилитель" for w in r.items
    }
    if intensifiers and "усилители_на_1000" in norms:
        int_count = sum(1 for t in tokens if t in intensifiers)
        add(
            "V1.4_усилители",
            "усилители_на_1000",
            round(int_count / n_words * 1000, 2) if n_words else 0.0,
            quotes=_quote_sentences(sentences, intensifiers),
            note=f"{int_count} вхождений",
        )

    # FR-V1.5 — запрещённая лексика (год главы и фокал)
    narration = textutils.narration_only(text)
    for rule in stoplists:
        if rule.kind != "лексика" or not _stoplist_applies(rule, brief):
            continue
        # стоп-лист линии фокала (0.3) касается ВНУТРЕННЕЙ речи: реплики других персонажей
        # («— Сынок, — сказал он») ложным флагом быть не должны. Лексика эпохи (0.4) — весь текст.
        scope_text = narration if rule.scope == "0.3" else text
        found = _find_items(scope_text, rule.items)
        if found:
            checks.append(
                CheckResult(
                    check_id="V1.5_стоп_лексика",
                    status="FLAG",
                    threshold=f"действие: {rule.action}",
                    actual="; ".join(found),
                    quotes=_quote_sentences(sentences, {w.lower().replace("ё", "е") for w in found}),
                    rule_source=f"{rule.rule_id} (реестр {rule.scope})",
                    note="проверьте значение: прямое значение эпохи допустимо" if rule.action == "флаг" else "",
                )
            )
    if not any(c.check_id == "V1.5_стоп_лексика" for c in checks):
        checks.append(
            CheckResult(
                check_id="V1.5_стоп_лексика", status="PASS", threshold="0 вхождений", actual="0",
                rule_source="stoplists.json",
            )
        )

    # FR-V1.6 — вставка окна («утечка промпта»)
    leak_n = int(_norm_value(norms, "утечка_нграмма") or 0)
    if leak_n:
        # хвост предыдущей главы в окне — цитата канона для сцепки голоса, не промпт: из проверки
        # утечки исключается (повтор канона ловит V1.7 по корпусу)
        win_tokens = textutils.normalize(_strip_prose_tail(window_raw))
        leaks = (
            _matching_runs(tokens, set(textutils.ngrams(win_tokens, leak_n)), leak_n) if win_tokens else []
        )
        checks.append(
            CheckResult(
                check_id="V1.6_утечка_окна",
                status="FLAG" if leaks else "PASS",
                threshold=f"совпадения ≥ {leak_n} слов с окном",
                actual=str(len(leaks)),
                quotes=leaks,
                rule_source=norms["утечка_нграмма"].source,
            )
        )

    # FR-V1.7 — межглавные повторы против корпус/
    rep_n = int(_norm_value(norms, "повтор_нграмма") or 0)
    if rep_n:
        repeats: list[str] = []
        sources: list[str] = []
        if corpus_dir is not None and corpus_dir.exists():
            text_ngrams = set(textutils.ngrams(tokens, rep_n))
            for f in sorted(corpus_dir.glob("*.txt")):
                if own_stem and f.stem == own_stem:
                    continue
                other = f.read_text(encoding="utf-8").split()
                hits = _matching_runs(other, text_ngrams, rep_n)
                if hits:
                    sources.append(f.stem)
                    repeats.extend(f"[{f.stem}] {h}" for h in hits[:3])
        checks.append(
            CheckResult(
                check_id="V1.7_межглавные_повторы",
                status="FLAG" if repeats else "PASS",
                threshold=f"n-граммы ≥ {rep_n} слов против корпуса",
                actual=str(len(repeats)),
                quotes=repeats[:MAX_QUOTES],
                rule_source=norms["повтор_нграмма"].source,
                note="главы-источники: " + ", ".join(sources) if sources else "",
            )
        )

    # FR-V1.8 — TTR
    ttr_val = textutils.ttr(tokens)
    if "ttr_мин" in norms and "ttr_окно_слов" in norms:  # норм TTR в каноне нет — метрика не считается
        checks.append(
            CheckResult(
                check_id="V1.8a_ttr_главы",
                status="PASS",  # по главе — справочно
                threshold="справочно",
                actual=f"{ttr_val:.3f}",
                rule_source=norms["ttr_мин"].source,
            )
        )
        win_size = int(_norm_value(norms, "ttr_окно_слов") or 0)
        # корпус окна — принятые главы тома брифа (и части, если она известна), не весь корпус/ (аудит 3.7)
        part_tokens: list[str] = []
        scope_files: list[str] = []
        if corpus_dir is not None and corpus_dir.exists():
            # окно скользит по ТОМУ: часть тома 1 (10 × 800 слов) короче окна 10 000, и проверка
            # лексической бедности не срабатывала бы никогда (аудит 2, находка 2.2)
            for f in corpus_scope(corpus_dir, brief.volume, None):
                if own_stem and f.stem == own_stem:
                    continue
                scope_files.append(f.stem)
                part_tokens.extend(f.read_text(encoding="utf-8").split())
        part_tokens.extend(tokens)
        rolling = textutils.rolling_ttr(part_tokens, win_size)
        min_ttr = min((v for _, v in rolling), default=None)
        short_corpus = min_ttr is None and part_tokens
        if short_corpus:  # тома пока меньше окна — считаем по имеющемуся объёму, справочно
            uniq = len(set(part_tokens))
            min_ttr = round(uniq / len(part_tokens), 3)
        ttr_norm = norms["ttr_мин"]
        checks.append(
            CheckResult(
                check_id="V1.8b_ttr_окно",
                status=(
                    "PASS" if short_corpus or min_ttr is None or ttr_norm.min is None
                    else "BRAK" if ttr_norm.brak is not None and min_ttr < ttr_norm.brak
                    else "FLAG" if min_ttr < ttr_norm.min else "PASS"
                ),
                threshold=f"мин {ttr_norm.min:g}" + (f", брак {ttr_norm.brak:g}" if ttr_norm.brak else "")
                          + f" в окне {win_size} слов",
                actual=(f"{min_ttr:.3f}" if min_ttr is not None else "корпус пуст")
                       + (f" (справочно: том короче окна, {len(part_tokens)} слов)" if short_corpus else ""),
                rule_source=ttr_norm.source,
                note=f"корпус: том {brief.volume}" + (f" ({', '.join(scope_files)})" if scope_files else " (корпус пуст)"),
            )
        )

    # FR-V1.9 — доля диалога и однострочные абзацы (02 §5): нормы справочные, порог — из канона
    paras = [p for p in textutils.paragraphs(text) if p.strip()]
    if paras:
        dialogue = sum(1 for p in paras if p.lstrip().startswith(("—", "–")))
        if "доля_диалога" in norms:
            add("V1.9a_доля_диалога", "доля_диалога", round(dialogue / len(paras), 3),
                note=f"{dialogue} реплик-абзацев из {len(paras)}")
        if "фраз_в_абзаце" in norms:
            per_para = [len([s for s in textutils.split_sentences(p, extra_abbr) if textutils.words(s)]) for p in paras]
            single = sum(1 for n in per_para if n <= 1)
            add("V1.9b_фраз_в_абзаце", "фраз_в_абзаце",
                round(sum(per_para) / len(per_para), 2),
                note=f"однострочных абзацев: {single} из {len(paras)} (Р-015: приём, не норма)")

    # FR-V1.10 — документ-вставка, назначенная брифом, обязана быть оформлена маркерами
    if brief.documents:
        has_block = "→ ДОКУМЕНТ" in raw and "← КОНЕЦ ДОКУМЕНТА" in raw
        checks.append(
            CheckResult(
                check_id="V1.11_документ_вставка",
                status="PASS" if has_block else "BRAK",
                threshold="блок `→ ДОКУМЕНТ` … `← КОНЕЦ ДОКУМЕНТА`",
                actual="есть" if has_block else "нет",
                rule_source="бриф главы (реестр §6)",
                note="; ".join(brief.documents)[:200],
            )
        )

    return checks



# ------------------------------------------------------- FR-V1.10 дифф-контроль


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def _edit_text(s: str) -> str:
    return _norm_ws(textutils.strip_markdown(s))


def _expected_sentences(old_sents: list[str], edits: list[Edit]) -> tuple[set[str], set[str], list[str], list[str]]:
    """Что должно получиться из старых предложений после правок:
    (ожидаемые новые предложения, объяснённые старые, тексты «стало», тексты «было»)."""
    expected_new: set[str] = set()
    explained_old: set[str] = set()
    afters = [_edit_text(e.after) for e in edits if e.after.strip()]
    befores = [_edit_text(e.before) for e in edits if e.before.strip()]
    for e in edits:
        b, a = _edit_text(e.before), _edit_text(e.after)
        if not b:
            continue
        for s in old_sents:
            sn = _norm_ws(s)
            if b in sn:
                explained_old.add(sn)
                # «стало» может быть пустым (удаление) или из нескольких предложений
                expected_new.update(_norm_ws(x) for x in textutils.split_sentences(sn.replace(b, a)))
    return expected_new, explained_old, afters, befores


def waive_unauthorized(ws: Workspace, chapter: int, report: DiffReport, fragments: list[str]) -> tuple[list[str], list[str]]:
    """Авторская правка (`diff-check --авторская-правка`): снимает самоволия и ПИШЕТ в дифф.json,
    что именно снято. Без перечня — снимаются все (как прежде); с перечнем `--фрагмент` — только
    совпавшие (номер в списке самоволий или подстрока текста). Возвращает (снято, не найдено)."""
    waived: list[str] = []
    missing: list[str] = []
    if not fragments:
        waived = list(report.unauthorized)
        report.unauthorized = []
    else:
        for frag in fragments:
            frag = frag.strip()
            hit = None
            if frag.isdigit() and 1 <= int(frag) <= len(report.unauthorized):
                hit = report.unauthorized[int(frag) - 1]
            else:
                hit = next((u for u in report.unauthorized if frag and _norm_ws(frag) in _norm_ws(u)), None)
            if hit is None:
                missing.append(frag)
            elif hit not in waived:
                waived.append(hit)
        report.unauthorized = [u for u in report.unauthorized if u not in waived]
    guard.write_text(
        ws.chapter_dir(chapter) / "дифф.json",
        json.dumps(
            {
                **report.model_dump(),
                "примечание": "ручная правка автора",
                "авторская_правка": {"снято": waived, "фрагменты": list(fragments), "не_найдено": missing},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return waived, missing


def diff_check(ws: Workspace, chapter: int, draft_before: int, draft_after: int, edits: list[Edit]) -> DiffReport:
    """Сопоставление черновиков до/после правок: внесено / не внесено / самоволия.

    Свободные указания (пустое «было») механически не проверяемы: текст указания
    не обязан появиться в прозе. Они выносятся в unverifiable и приёмку не
    блокируют — их результат автор оценивает глазами.
    """
    old = ws.draft_path(chapter, draft_before).read_text(encoding="utf-8")
    new = ws.draft_path(chapter, draft_after).read_text(encoding="utf-8")
    old_n, new_n = _norm_ws(old), _norm_ws(new)

    verifiable = [e for e in edits if e.before.strip()]
    unverifiable = [e.seq for e in edits if not e.before.strip()]
    applied = 0
    not_applied: list[int] = []
    for e in verifiable:
        before, after = e.before.strip(), e.after.strip()
        before_n, after_n = _norm_ws(before), _norm_ws(after)
        # по счётчикам вхождений (2.5): цитата, встречающаяся в тексте дважды, после правки
        # одного места встречается на один раз меньше — это «внесено», а не «не внесено»
        if after and before_n in after_n:
            # «стало» содержит «было» (дописано продолжение): число «было» не меняется —
            # считаем появление самого «стало»
            ok = (new.count(after) - old.count(after) >= 1) or (new_n.count(after_n) - old_n.count(after_n) >= 1)
        else:
            ok_removed = (old.count(before) - new.count(before) >= 1) or (old_n.count(before_n) - new_n.count(before_n) >= 1)
            ok_added = (not after) or (after in new) or (after_n in new_n)
            ok = ok_removed and ok_added
        if ok:
            applied += 1
        else:
            not_applied.append(e.seq)

    # самовольные изменения — по КАЖДОМУ изменённому предложению (2.5), а не по блоку difflib:
    # блок из двух предложений, где правкой объяснено одно, второе не «отмывает»
    old_sents = textutils.split_sentences(textutils.strip_markdown(old))
    new_sents = textutils.split_sentences(textutils.strip_markdown(new))
    expected_new, explained_old, afters, befores = _expected_sentences(old_sents, edits)

    def new_explained(s: str) -> bool:
        sn = _norm_ws(s)
        return sn in expected_new or any(a in sn or sn in a for a in afters)

    def old_explained(s: str) -> bool:
        sn = _norm_ws(s)
        return sn in explained_old or any(sn in b for b in befores)

    unauthorized: list[str] = []
    sm = difflib.SequenceMatcher(a=old_sents, b=new_sents, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        rogue = [s for s in new_sents[j1:j2] if not new_explained(s)]
        unauthorized.extend(rogue)
        if not rogue:
            # новые предложения объяснены — но не исчезло ли старое без правки?
            kept = {_norm_ws(s) for s in new_sents[j1:j2]}
            unauthorized.extend(
                f"[удалено]: {s}" for s in old_sents[i1:i2] if not old_explained(s) and _norm_ws(s) not in kept
            )

    report = DiffReport(
        chapter=chapter,
        draft_before=draft_before,
        draft_after=draft_after,
        applied_share=round(applied / len(verifiable), 3) if verifiable else 1.0,
        not_applied=not_applied,
        unauthorized=unauthorized[:MAX_QUOTES * 2],
        unverifiable=unverifiable,
    )
    guard.write_text(
        ws.chapter_dir(chapter) / "дифф.json",
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2) + "\n",
    )
    return report
