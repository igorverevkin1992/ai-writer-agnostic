"""Верификатор-2 (Э2): смысловые проверки LLM (FR-V2.1…FR-V2.5).

Вход — текст + релевантные срезы выгрузок (не полные файлы канона).
Выход — флаги.json. При недоступности API промпт сохраняется в
главы/N/промпт_э2.md для ручного прогона (NFR-3).
"""

from __future__ import annotations

import json
import re
from importlib import resources

from pydantic import ValidationError

from . import adapters, circles, compiler, exporter, guard, llmjson
from .config import Config
from .paths import Workspace
from .schemas import Flag


def _template(ws: Workspace, name: str) -> str:
    override = ws.templates / name
    if override.exists():
        return override.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath(f"шаблоны/{name}").read_text(encoding="utf-8")


def _chapter_month(brief) -> int | None:
    """Месяц главы из даты брифа («12.04», «ночь 18.04», «12 июня 1995») — для среза хроники."""
    from .lint import parse_date

    d = parse_date(brief.date or "")
    return d[0] if d else None


def chronicle_slice(events: list, brief) -> list[str]:
    """Хроника 1926 за месяц главы ± 1 (чек-лист 4.2: анахронизмы). Без даты главы — весь год."""
    month = _chapter_month(brief)
    out = []
    for e in events:
        if month is not None and e.month is not None and min(abs(e.month - month), 12 - abs(e.month - month)) > 1:
            continue
        mark = "" if e.status == "✓" else f" [{e.status} — на этот факт опираться нельзя]"
        out.append(f"- {e.date}: {e.event}{mark}")
    return out


def build_prompt(ws: Workspace, chapter: int, draft: int) -> tuple[str, str]:
    """(system, user): срезы матрицы участников, досье, бриф, закладки, информрежим, доза/документ,
    континуити, хроника (FR-V2.1, чек-листы 4.1–4.3)."""
    exports_dir = ws.exports
    brief = exporter.load_brief(exports_dir, chapter)
    briefs = exporter.load_briefs(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    infobans = exporter.load_infobans(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    continuity = exporter.load_continuity(exports_dir)
    dossiers = exporter.load_dossiers(exports_dir)
    chronicle = exporter.load_chronicle(exports_dir)
    plants = compiler.chapter_plants(exports_dir, brief)
    doses = compiler.chapter_doses(exports_dir, brief)
    documents = compiler.chapter_documents(exports_dir, brief)
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    try:
        drama = circles.frame_for_chapter(exporter.load_circles(exports_dir), exporter.load_acts(exports_dir), chapter)
    except FileNotFoundError:
        drama = circles.frame_for_chapter([], [], chapter)
    drama_lines = circles.frame_lines(drama, with_weak_spot=True) or [
        "- (каркас в канон не внесён — проверка драматургии ограничивается собственным движением главы)"
    ]

    participants = sorted(set([brief.focal, *brief.participants]) - {""})
    # запреты линий участников: и словарные (0.3), и прозаические («канцелярит — панцирь страха»)
    line_rules = [
        f"- [{r.rule_id}] {r.applies_to.get('focal', 'все линии')}: {'; '.join(sorted(r.items))} ({r.action})"
        for r in stoplists
        if r.kind == "лексика" and r.scope == "0.3"
        and ("focal" not in r.applies_to or r.applies_to["focal"] in participants)
    ]
    prose_rules = [
        f"- [{r.rule_id}] {r.applies_to.get('focal', 'все линии')}: {item}"
        for r in stoplists
        if r.kind == "проза" and ("focal" not in r.applies_to or r.applies_to["focal"] in participants)
        for item in r.items
    ]
    # досье участников — та же проекция, что в окне Писателя (FR-C3): без тайн, недоступных фокалу
    dossier_slice: list[str] = []
    for d in sorted((compiler.safe_dossier(d, brief, infobans, participants)
                     for d in dossiers if d.name in participants), key=lambda d: d.name):
        parts = [f"физика: {d.physique}" if d.physique else "", f"речевой паспорт: {d.speech}" if d.speech else "",
                 f"опознавательный код: {d.code}" if getattr(d, "code", "") else ""]
        body = "; ".join(x for x in parts if x)
        dossier_slice.append(f"- {d.name}: {body}" if body else f"- {d.name}: (карточка без физики и речевого паспорта)")
    matrix_slice = [
        f"- [{f.fact_id}] {f.subject}: {f.fact} "
        + ("(знает всегда)" if f.from_chapter == 0 else
           f"(узнаёт в гл. {f.from_chapter})" if f.from_chapter is not None else "(НЕ знает)")
        + (f" — {f.note}" if f.note.startswith("частично") else "")
        for f in matrix
        if f.subject in participants
    ]
    dose_lines = [
        f"- Доза {d.dose_id} (гл. {d.chapter}): триггер — {d.trigger}; читатель получает: {d.reader_gets}; "
        f"НЕ получает: {d.reader_not_gets}. {d.rule}" for d in doses
    ]
    document_lines = [
        f"- Документ №{d.number} ({d.style}): расхождение с правдой — {d.divergence}. Языковая шкала: {d.scale}"
        for d in documents
    ]
    user = "\n".join(
        [
            f"# Проверка главы {chapter} (том {brief.volume}, фокал: {brief.focal}, дата: {brief.date})",
            "",
            "## Срез матрицы знаний (участники сцены)",
            *matrix_slice,
            "",
            "## Досье участников сцены (что обязано совпасть)",
            *dossier_slice,
            "",
            "## Бриф главы",
            f"- Сцены: {'; '.join(brief.scenes)}",
            f"- Биты: {'; '.join(brief.beats)}",
            f"- Запреты: {'; '.join(brief.bans)}",
            f"- Фокал НЕ знает: {'; '.join(brief.not_knows)}",
            *([f"- Что нового должен узнать читатель (реестр 2.2): {brief.reader_learns}"] if brief.reader_learns else []),
            "",
            "## Закладки, назначенные главе",
            *[f"- [{p.plant_id}] {p.what}" for p in plants],
            "",
            *(["## Доза прошлого этой главы (реестр §5)", *dose_lines, ""] if dose_lines else []),
            *(["## Документ-вставка этой главы (реестр §6) — блок `→ ДОКУМЕНТ` … `← КОНЕЦ ДОКУМЕНТА` обязателен",
               *document_lines, ""] if document_lines else []),
            "## Запреты информрежима (резервы будущих томов)",
            *[
                f"- [{b.ban_id}] {b.text}"
                for b in infobans
                if compiler.ban_active(b, brief)  # тот же фильтр, что у компилятора: раскрытое — не нарушение
            ],
            "",
            "## Стоп-листы линий (фокализация, 0.3)",
            *line_rules,
            "",
            "## Прозаические запреты линий (03 «Персональные запреты линий»)",
            *prose_rules,
            "",
            "## Драматургия: каркас круга истории (2.1, Р-020)",
            *drama_lines,
            "",
            "## Континуити 3.3 (детали, которые обязаны совпасть)",
            *[f"- {c}" for c in compiler.prior_continuity(continuity, brief, infobans, participants, briefs)],
            "",
            "## Хроника 1926 (анахронизмы, 4.2) — месяц главы ± 1",
            *chronicle_slice(chronicle, brief),
            "",
            "## ТЕКСТ ГЛАВЫ",
            "",
            "<текст_главы>",
            text,
            "</текст_главы>",
        ]
    )
    return _template(ws, "верификатор2_система.md"), user


def parse_flags(raw: str) -> list[Flag]:
    try:
        data = llmjson.extract_json(raw, list)
    except ValueError as e:
        raise ValueError(f"Ответ Верификатора-2: {e}") from e
    flags = []
    for i, item in enumerate(data, start=1):
        # идентификатор попадает в разметку (id/href): всё, что не [\w.-], заменяется порядковым
        if not isinstance(item.get("flag_id"), str) or not re.fullmatch(r"[\w.\-]+", item["flag_id"]):
            item["flag_id"] = f"F-{i:03d}"
        try:
            flags.append(Flag.model_validate(item))
        except ValidationError as e:
            raise ValueError(f"Невалидный флаг №{i}: {e}") from e
    return flags


def run_verify2(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    system, user = build_prompt(ws, chapter, draft)
    prompt_path = ws.chapter_dir(chapter) / "промпт_э2.md"
    guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    raw = adapters.call_anthropic(
        system, user, cfg.verifier2, cfg.api, ws.logs, role="верификатор-2", chapter=chapter
    )
    flags = parse_flags(raw)
    save_flags(ws, chapter, flags)
    return flags


AGAIN_FLAGS = "флаги_повторно.json"
AGAIN_PROMPT = "промпт_э2_повторно.md"


def run_verify2_again(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    """Повторный Э2 после правок (аудит 2, п. 24а) — совещательный: тот же промпт по текущему
    черновику, результат в флаги_повторно.json; флаги.json, решения.json и FSM не трогает."""
    system, user = build_prompt(ws, chapter, draft)
    guard.write_text(ws.chapter_dir(chapter) / AGAIN_PROMPT, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    raw = adapters.call_anthropic(
        system, user, cfg.verifier2, cfg.api, ws.logs, role="верификатор-2 (повторно)", chapter=chapter
    )
    flags = parse_flags(raw)
    save_flags_again(ws, chapter, flags, draft)
    return flags


def save_flags_again(ws: Workspace, chapter: int, flags: list[Flag], draft: int) -> None:
    guard.write_text(
        ws.chapter_dir(chapter) / AGAIN_FLAGS,
        json.dumps({"черновик": draft, "флаги": [f.model_dump() for f in flags]}, ensure_ascii=False, indent=2) + "\n",
    )


def load_flags_again(ws: Workspace, chapter: int) -> tuple[int | None, list[Flag]]:
    """(черновик, флаги) повторного Э2; файл может быть и голым списком (ручной режим)."""
    path = ws.chapter_dir(chapter) / AGAIN_FLAGS
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return None, [Flag.model_validate(r) for r in data]
    return data.get("черновик"), [Flag.model_validate(r) for r in data.get("флаги", [])]


def build_taste_prompt(ws: Workspace, chapter: int, draft: int) -> tuple[str, str]:
    """Совещательный проход «вкус» (02 §6.1–6.2): правила вкуса автора + текст главы."""
    from . import mdparse

    from .config import library_dir, load_config

    library = guard._library() or library_dir(ws, load_config(ws))
    sections = mdparse.parse_sections(sorted(library.glob("02_*.md"))[0])
    wanted = [s for s in sections if re.match(r"(?:§\s*)?6\.[12]\.?\s", s.title + " ")]
    rules = "\n\n".join(f"### {s.title}\n{s.body}" for s in wanted) or "(правила вкуса в 02 §6.1–6.2 ещё не заполнены)"
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    user = f"# Вкус: глава {chapter}\n\n## Правила вкуса автора (02 §6.1–6.2)\n\n{rules}\n\n## ТЕКСТ ГЛАВЫ\n\n<текст_главы>\n{text}\n</текст_главы>\n"
    return _template(ws, "верификатор2_вкус_система.md"), user


def run_taste(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    """Советы по вкусу — отдельный файл вкус.json: приёмку не блокируют, в флаги.json не попадают."""
    system, user = build_taste_prompt(ws, chapter, draft)
    prompt_path = ws.chapter_dir(chapter) / "промпт_вкуса.md"
    guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    raw = adapters.call_anthropic(system, user, cfg.verifier2, cfg.api, ws.logs, role="вкус", chapter=chapter)
    flags = [f.model_copy(update={"severity": "мелочь", "type": "вкус", "kind": "violation"}) for f in parse_flags(raw)]
    guard.write_text(
        ws.chapter_dir(chapter) / "вкус.json",
        json.dumps([f.model_dump() for f in flags], ensure_ascii=False, indent=2) + "\n",
    )
    return flags


def load_taste(ws: Workspace, chapter: int) -> list[Flag]:
    path = ws.chapter_dir(chapter) / "вкус.json"
    if not path.exists():
        return []
    return [Flag.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def save_flags(ws: Workspace, chapter: int, flags: list[Flag]) -> None:
    guard.write_text(
        ws.chapter_dir(chapter) / "флаги.json",
        json.dumps([f.model_dump() for f in flags], ensure_ascii=False, indent=2) + "\n",
    )


def load_flags(ws: Workspace, chapter: int) -> list[Flag]:
    path = ws.chapter_dir(chapter) / "флаги.json"
    if not path.exists():
        return []
    return [Flag.model_validate(f) for f in json.loads(path.read_text(encoding="utf-8"))]
