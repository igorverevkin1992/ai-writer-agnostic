"""Тома (аудит 2, п. 27): сводка тома, закрытие тома, переключение текущего тома.

Рабочая область ведёт один текущий том (`конфиг.yaml: volume`, `Workspace.volume`):
главы тома 1 — `главы/001`, тома N ≥ 2 — `главы/ТN/001`; выгрузки `выгрузки/` —
всегда текущего тома (`exporter.run_export(volume=…)`); документы канона по тому выбирает
`exporter.volume_docs` (маркер `Том{N}`/`_Т{N}` в имени).

`close_volume` — закрытие тома: все главы «зафиксировано» → снапшот 3.5 в библиотеку
(`35_Снапшот_ТомN.md`, через единый конвейер `canonchange.canon_change`) → тег `том-N` →
рукопись `рукопись/ТомN.md` (+ `.docx`, если установлен python-docx) → статистика тома.
Переключение `config.volume` на следующий том — только по подтверждению автора (в CLI).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import canonchange, exporter, gitops, guard, snapshot
from .apilog import read_log
from .config import Config
from .fsm import ChapterState, StatusFileError, all_states
from .paths import Workspace
from .schemas import Act, Brief, Verdict

SNAPSHOT_DOC = "35_Снапшот_Том{volume}.md"
TAG = "том-{volume}"
_PROSE_RE = re.compile(r"Том0*(\d+)_Глава0*(\d+)")
# метрики Э1, у которых «actual» — число (усредняются по актам в статистике тома)
_NUMERIC_CHECKS = {
    "V1.2a_средняя_длина": "средняя длина фразы",
    "V1.2b_доля_коротких": "доля коротких",
    "V1.2c_доля_длинных": "доля длинных",
    "V1.2e_объём": "слов",
    "V1.8a_ttr_главы": "TTR главы",
    "V1.9a_доля_диалога": "доля диалога",
}


def snapshot_doc_name(volume: int) -> str:
    return SNAPSHOT_DOC.format(volume=int(volume))


def tag_name(volume: int) -> str:
    return TAG.format(volume=int(volume))


# ------------------------------------------------------------------ рукопись и статистика


def prose_files(library: Path, volume: int) -> list[tuple[int, Path]]:
    """Принятые главы тома в `Проза/` по возрастанию номера: [(N, путь)]. Макеты («_МАКЕТ») не берутся."""
    out: list[tuple[int, Path]] = []
    prose = library / "Проза"
    for p in sorted(prose.glob("*.md")) if prose.exists() else []:
        m = _PROSE_RE.search(p.stem)
        if not m or int(m.group(1)) != volume or "МАКЕТ" in p.stem.upper():
            continue
        out.append((int(m.group(2)), p))
    return sorted(out)


def _chapter_body(text: str) -> tuple[str, list[str]]:
    """Текст главы без служебной шапки: заголовки `#`, линейки `---` и курсивные пометки в начале файла."""
    paragraphs = [pg.strip() for pg in re.split(r"\n\s*\n", text) if pg.strip()]
    head: list[str] = []
    while paragraphs and (paragraphs[0].startswith("#") or paragraphs[0].startswith(("---", "***"))
                          or re.fullmatch(r"\*[^*]+\*", paragraphs[0])):
        head.append(paragraphs.pop(0))
    return "\n\n".join(paragraphs), head


def _word_count(text: str) -> int:
    return len(re.findall(r"[А-Яа-яЁёA-Za-z0-9]+(?:-[А-Яа-яЁёA-Za-z0-9]+)*", text))


def build_manuscript(ws: Workspace, library: Path, volume: int) -> tuple[Path, Path | None, str | None]:
    """Собирает `рукопись/ТомN.md` (главы по порядку с заголовками) и, если установлен python-docx,
    `рукопись/ТомN.docx`. Возвращает (md, docx | None, подсказка | None)."""
    chapters = prose_files(library, volume)
    if not chapters:
        raise FileNotFoundError(f"в библиотеке нет принятых глав тома {volume} (Проза/Том{volume}_ГлаваNN.md).")
    briefs = {b.chapter: b for b in _briefs_of(ws, volume)}
    lines = [f"# Том {volume}", ""]
    sections: list[tuple[str, list[str]]] = []
    for n, path in chapters:
        body, _ = _chapter_body(path.read_text(encoding="utf-8"))
        title = f"Глава {n}"
        brief = briefs.get(n)
        if brief is not None and brief.date:
            title += f". {brief.date}"
        lines += [f"## {title}", "", body, ""]
        sections.append((title, [pg for pg in re.split(r"\n\s*\n", body) if pg.strip()]))
    md = ws.manuscript / f"Том{volume}.md"
    guard.write_text(md, "\n".join(lines).rstrip("\n") + "\n")
    docx_path = ws.manuscript / f"Том{volume}.docx"
    try:
        import docx  # type: ignore  # python-docx — опциональная зависимость [docx]
    except ImportError:
        return md, None, "python-docx не установлен — собран только .md; `pip install 'konveyer[docx]'` даст и .docx"
    document = docx.Document()
    document.add_heading(f"Том {volume}", level=0)
    for title, paragraphs in sections:
        document.add_heading(title, level=1)
        for pg in paragraphs:
            document.add_paragraph(pg)
    guard.check_write_allowed(docx_path)
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(docx_path))
    return md, docx_path, None


def _briefs_of(ws: Workspace, volume: int) -> list[Brief]:
    try:
        return [b for b in exporter.load_briefs(ws.exports) if b.volume == volume]
    except FileNotFoundError:
        return []


def _acts_of(ws: Workspace) -> list[Act]:
    try:
        return exporter.load_acts(ws.exports)
    except FileNotFoundError:
        return []


def _verdict(ws: Workspace, chapter: int) -> Verdict | None:
    path = ws.chapter_dir(chapter) / "вердикт.json"
    if not path.exists():
        return None
    try:
        return Verdict.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError):
        return None


def _num(text: str) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group().replace(",", ".")) if m else None


@dataclass
class VolumeStats:
    volume: int
    chapters_total: int            # глав в поглавнике тома
    fixed: list[int]               # «зафиксировано»
    in_work: dict[int, str]        # глава → состояние (не «зафиксировано», не «не-начато»)
    words: dict[int, int]          # слова принятых глав (по Проза/)
    acts: list[Act]
    act_metrics: dict[int, dict[str, float]]   # акт → {метрика: среднее по главам акта}
    total_metrics: dict[str, float]            # то же по всем главам тома
    cost: float                    # $ по журналы/api.jsonl за главы тома
    calls: int
    missing_docs: list[str] = field(default_factory=list)

    @property
    def words_total(self) -> int:
        return sum(self.words.values())


def volume_stats(ws: Workspace, library: Path, volume: int | None = None) -> VolumeStats:
    """Сводка тома: главы по состояниям, слова принятых глав, средние метрики Э1 по актам, стоимость."""
    volume = ws.volume if volume is None else int(volume)
    vws = ws.for_volume(volume)
    briefs = _briefs_of(ws, volume)
    fixed: list[int] = []
    in_work: dict[int, str] = {}
    verdicts: dict[int, Verdict] = {}
    for st in all_states(vws):
        if st.state == "зафиксировано":
            fixed.append(st.chapter)
        elif st.state != "не-начато":
            in_work[st.chapter] = st.state
        v = _verdict(vws, st.chapter)
        if v is not None:
            verdicts[st.chapter] = v
    words = {n: _word_count(_chapter_body(p.read_text(encoding="utf-8"))[0]) for n, p in prose_files(library, volume)}
    acts = _acts_of(ws) if volume == ws.volume else []

    def averages(lo: int, hi: int) -> dict[str, float]:
        sums: dict[str, list[float]] = {}
        for ch, v in verdicts.items():
            if not lo <= ch <= hi:
                continue
            for c in v.checks:
                label = _NUMERIC_CHECKS.get(c.check_id)
                val = _num(c.actual) if label else None
                if label and val is not None:
                    sums.setdefault(label, []).append(val)
        return {k: round(sum(v) / len(v), 3) for k, v in sums.items()}

    act_metrics = {a.act: m for a in acts if (m := averages(a.from_chapter, a.to_chapter))}
    cost, calls = _cost_of_volume(ws, volume, {b.chapter for b in briefs} | set(words) | set(in_work) | set(fixed))
    return VolumeStats(
        volume=volume, chapters_total=len(briefs), fixed=sorted(fixed), in_work=dict(sorted(in_work.items())),
        words=words, acts=acts, act_metrics=act_metrics, total_metrics=averages(0, 10**6),
        cost=round(cost, 2), calls=calls, missing_docs=exporter.missing_volume_docs(library, volume),
    )


def _cost_of_volume(ws: Workspace, volume: int, chapters: set[int]) -> tuple[float, int]:
    """Стоимость по `журналы/api.jsonl`: строки с `volume == N`; старые строки без поля «volume» — том 1."""
    cost, calls = 0.0, 0
    for row in read_log(ws.logs):
        row_vol = row.get("volume")
        if row_vol is None:
            row_vol = 1
        if int(row_vol) != volume:
            continue
        if row.get("chapter") is not None and chapters and int(row["chapter"]) not in chapters:
            continue
        calls += 1
        cost += float(row.get("cost_est") or 0.0)
    return cost, calls


def render_stats(stats: VolumeStats) -> str:
    lines = [f"# Том {stats.volume} — статистика", ""]
    lines.append(f"- Глав в поглавнике: {stats.chapters_total}; зафиксировано: {len(stats.fixed)}; в работе: {len(stats.in_work)}")
    lines.append(f"- Слов в принятых главах: {stats.words_total}")
    lines.append(f"- Вызовов моделей: {stats.calls}; оценка стоимости: ${stats.cost:.2f}")
    if stats.in_work:
        lines += ["", "## В работе", ""]
        lines += [f"- Глава {n}: {s}" for n, s in stats.in_work.items()]
    if stats.words:
        lines += ["", "## Объём глав", "", "| Глава | Слов |", "|---|---|"]
        lines += [f"| {n} | {w} |" for n, w in sorted(stats.words.items())]
    if stats.total_metrics:
        lines += ["", "## Метрики Э1 (средние по вердиктам глав)", ""]
        lines.append("- Весь том: " + "; ".join(f"{k}: {v:g}" for k, v in stats.total_metrics.items()))
        for a in stats.acts:
            m = stats.act_metrics.get(a.act, {})
            words = sum(w for n, w in stats.words.items() if a.from_chapter <= n <= a.to_chapter)
            desc = "; ".join(f"{k}: {v:g}" for k, v in m.items()) or "вердиктов нет"
            lines.append(f"- Акт {a.act} «{a.title}» (гл. {a.from_chapter}–{a.to_chapter}, {words} слов): {desc}")
    return "\n".join(lines) + "\n"


def write_stats(ws: Workspace, stats: VolumeStats) -> Path:
    path = ws.manuscript / f"Том{stats.volume}_статистика.md"
    guard.write_text(path, render_stats(stats))
    return path


# ------------------------------------------------------------------ закрытие и переключение


@dataclass
class CloseResult:
    volume: int
    snapshot_doc: Path
    commit: str | None
    tag: str | None
    manuscript_md: Path
    manuscript_docx: Path | None
    docx_hint: str | None
    stats_path: Path
    messages: list[str] = field(default_factory=list)


def unfixed_chapters(ws: Workspace, volume: int) -> list[str]:
    """Главы тома, которые ещё не «зафиксировано» (по поглавнику тома и папкам глав): «гл. N (состояние)»."""
    vws = ws.for_volume(volume)
    states: dict[int, str] = {}
    for n, _ in vws.chapter_dirs():
        try:
            states[n] = ChapterState(vws, n).state
        except StatusFileError:
            states[n] = "повреждено"
    for b in _briefs_of(ws, volume):
        states.setdefault(b.chapter, "не-начато")
    return [f"гл. {n} ({s})" for n, s in sorted(states.items()) if s != "зафиксировано"]


def close_volume(ws: Workspace, cfg: Config, library: Path, volume: int, *, again: bool = False,
                 author_confirmed: bool) -> CloseResult:
    """Закрывает том (аудит 2, п. 27): проверка приёмки всех глав → снапшот 3.5 в библиотеку (через
    `canonchange.canon_change`, коммит) → тег `том-N` → рукопись .md/.docx → статистика.
    Выгрузки должны быть тома `volume` (закрывается текущий том; другой — сначала `konveyer volume open N`)."""
    if not author_confirmed:
        raise PermissionError("закрытие тома без подтверждения автора запрещено (FR-K2, Д-8).")
    if volume != ws.volume:
        raise RuntimeError(
            f"закрывается только текущий том рабочей области (сейчас том {ws.volume}); "
            f"переключитесь: `konveyer volume open {volume}`."
        )
    exporter.run_export(library, ws.exports, ws.logs, volume)
    pending = unfixed_chapters(ws, volume)
    if pending:
        raise RuntimeError(
            f"том {volume} нельзя закрыть: не зафиксированы {', '.join(pending)}. "
            "Доведите главы до «зафиксировано» (`konveyer canonize N --apply`)."
        )
    if not _briefs_of(ws, volume):
        raise RuntimeError(f"в поглавнике нет глав тома {volume} — закрывать нечего.")
    doc = library / snapshot_doc_name(volume)
    messages: list[str] = []
    if doc.exists() and not again:
        raise RuntimeError(
            f"снапшот {doc.name} уже есть в библиотеке; чтобы переписать его — `konveyer volume close {volume} --заново`."
        )
    draft = snapshot.build_snapshot(ws, volume)
    text = draft.read_text(encoding="utf-8").replace(
        "Черновик сгенерирован конвейером; вносится в канон правкой библиотеки + `konveyer canon-commit`.",
        f"Внесён в канон командой `konveyer volume close {volume}` (реестр 3.5, срез мира на конец тома).",
    )
    result = canonchange.canon_change(
        ws, cfg, library, lambda: guard.write_text(doc, text),
        f"[том {volume}] снапшот 3.5: {doc.name} (закрытие тома)",
        commit=True, author_confirmed=True, action=f"закрытие тома {volume}",
    )
    messages.append(result.message)
    tag: str | None = None
    if gitops.is_repo(library):
        sha = result.commit or gitops.head(library)
        name = tag_name(volume)
        try:
            existing = gitops.tags(library, name)
            if existing and not again:
                messages.append(f"тег {name} уже стоит — оставлен как есть.")
                tag = name
            else:
                if existing:
                    gitops.delete_tag(library, name)
                tag = gitops.tag(library, name, sha)
        except (RuntimeError, FileNotFoundError) as e:
            messages.append(f"тег {name} не поставлен: {e}")
    else:
        messages.append("библиотека не под git — тег тома не поставлен.")
    md, docx_path, hint = build_manuscript(ws, library, volume)
    stats_path = write_stats(ws, volume_stats(ws, library, volume))
    return CloseResult(
        volume=volume, snapshot_doc=doc, commit=result.commit, tag=tag, manuscript_md=md,
        manuscript_docx=docx_path, docx_hint=hint, stats_path=stats_path, messages=messages,
    )


def open_volume(ws: Workspace, library: Path, volume: int) -> list[str]:
    """Проверяет, что документы тома есть в библиотеке; возвращает список недостающих (пусто — можно
    переключать `config.volume`, это делает CLI через `config.set_volume`)."""
    if int(volume) < 1:
        raise ValueError(f"номер тома должен быть ≥ 1, получено: {volume}.")
    return exporter.missing_volume_docs(library, int(volume))
