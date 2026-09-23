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

from . import exporter, guard, lang, metrics, textutils
from .paths import Workspace
from .schemas import Brief, CheckResult, DiffReport, Edit, Norm, StopRule, Verdict

from .metrics import (  # noqa: E402 — совместимость: прежние имена помощников Э1
    MAX_QUOTES, MetricContext, corpus_scope, corridor as _corridor, find_items as _find_items,
    matching_runs as _matching_runs, quote_sentences as _quote_sentences, status_of as _status,
    stoplist_applies as _stoplist_applies, strip_prose_tail as _strip_prose_tail,
)


def _norm_value(norms: dict[str, Norm], norm_id: str) -> float | None:
    """Числовое значение нормы-параметра: только из norms.json; нет нормы — None (умолчаний в коде нет)."""
    n = norms.get(norm_id)
    if n is None:
        return None
    return n.max if n.max is not None else n.min


def word_stems(word: str) -> list[str]:
    """Основы слова (языковой модуль, FR-V1-3)."""
    return lang.get().stems(word)


def item_pattern(item: str) -> re.Pattern:
    return lang.get().item_pattern(item)


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
        project_root=ws.root,
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
    project_root: Path | None = None,
) -> list[CheckResult]:
    """Чистое ядро Э1: текст + контекст → результаты проверок по реестру метрик (FR-V1-1).
    Пороги — только из `norms` (FR-V1-2); язык — модуль проекта (FR-V1-3)."""
    ctx = MetricContext(raw=raw, brief=brief, norms=norms, stoplists=stoplists,
                        language=lang.for_project(project_root), window_raw=window_raw, corpus_dir=corpus_dir,
                        own_stem=own_stem, extra_abbr=extra_abbr, part_range=part_range)
    return metrics.run(ctx)


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
