"""Верификатор-2 (Э2): смысловые проверки моделью (FR-V2-1…FR-V2-7).

Вход — текст + только релевантные срезы выгрузок (не полные документы). Чек-листы определяются включёнными
модулями (`модули/*.yaml: э2` → `модули/э2/<имя>.md`; методика драматургии даёт свой текст). Текст главы
огорожен маркерами (FR-V2-2, FR-SC-8). Выход — `флаги.json`. Без API промпт сохраняется для ручного прогона.
"""

from __future__ import annotations

import json
import re
from importlib import resources

from jinja2 import Environment
from pydantic import ValidationError

from . import adapters, catalog, circles, compiler, exporter, guard, llmjson, manifest as manifest_mod, mdparse
from .config import Config
from .paths import Workspace
from .schemas import Flag

FENCE_OPEN = "<текст_главы>"
FENCE_CLOSE = "</текст_главы>"


def _template(ws: Workspace, name: str) -> str:
    """Шаблон роли: `промпты/` проекта → `шаблоны/` проекта → движок (FR-RL-2, FR-AD-8)."""
    for cand in (ws.root / "промпты" / name, ws.templates / name):
        if cand.exists():
            return cand.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath(f"шаблоны/{name}").read_text(encoding="utf-8")


def _checklist_text(ws: Workspace, name: str) -> str:
    for cand in (ws.root / "модули" / "э2" / f"{name}.md",):
        if cand.exists():
            return cand.read_text(encoding="utf-8").strip()
    path = resources.files("konveyer").joinpath(f"модули/э2/{name}.md")
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _manifest(ws: Workspace) -> manifest_mod.Manifest:
    from .config import library_dir, load_config

    lib = guard._library() or library_dir(ws, load_config(ws))
    return manifest_mod.effective(ws.root, lib, catalog.load_types(ws.root))


def checklists(ws: Workspace) -> list[str]:
    """Пункты проверок по включённым модулям (FR-V2-3): базовые всегда; методика — свой текст."""
    man = _manifest(ws)
    mods = catalog.load_modules(ws.root)
    items: list[str] = []
    seen: set[str] = set()
    for m in sorted(mods.values(), key=lambda m: (not m.base, m.name)):
        if not (m.base or man.module_enabled(m.name, mods)):
            continue
        for name in m.e2_checks:
            if name in seen:
                continue
            seen.add(name)
            text = _checklist_text(ws, name)
            if name == "драматургия":
                extra = circles.e2_text(ws)
                if extra:
                    text = f"**Драматургия.** {extra} Тип флага — \"драматургия\"."
            if text:
                items.append(text)
    return items


def _chapter_month(brief) -> int | None:
    from .lint import parse_date

    d = parse_date(brief.date or "")
    return d[0] if d else None


def chronicle_slice(events: list, brief) -> list[str]:
    month = _chapter_month(brief)
    out = []
    for e in events:
        if month is not None and e.month is not None and min(abs(e.month - month), 12 - abs(e.month - month)) > 1:
            continue
        mark = "" if e.status == "✓" else f" [{e.status} — на этот факт опираться нельзя]"
        out.append(f"- {e.date}: {e.event}{mark}")
    return out


def system_prompt(ws: Workspace, cfg: Config) -> str:
    man = _manifest(ws)
    project_checks = "\n\n".join(c.text for c in exporter.load_checklists(ws.exports) if c.text)
    return Environment().from_string(_template(ws, "верификатор2_система.md")).render(
        series=man.проект.имя, checks=checklists(ws), project_checklists=project_checks,
        quote_words=cfg.e2_quote_words, max_flags=cfg.e2_max_flags,
    )


def build_prompt(ws: Workspace, chapter: int, draft: int, cfg: Config | None = None) -> tuple[str, str]:
    """(system, user): срезы по включённым модулям (FR-V2-1), текст в ограждении (FR-V2-2)."""
    cfg = cfg or Config()
    exports_dir = ws.exports
    man = _manifest(ws)
    mods = catalog.load_modules(ws.root)
    on = {m: man.module_enabled(m, mods) for m in mods}
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
    compiler.configure_markers(ws.root)
    try:
        drama = circles.frame_for_chapter(exporter.load_circles(exports_dir), exporter.load_acts(exports_dir), chapter)
    except FileNotFoundError:
        drama = circles.frame_for_chapter([], [], chapter)
    drama_lines = circles.frame_lines_for(ws, drama, with_weak_spot=True) or [
        "- (каркас в канон не внесён — проверка драматургии ограничивается собственным движением главы)"
    ]
    participants = sorted(set([brief.focal, *brief.participants]) - {""})
    line_rules = [
        f"- [{r.rule_id}] {r.applies_to.get('focal', 'все линии')}: {'; '.join(sorted(r.items))} ({r.action})"
        for r in stoplists
        if r.kind == "лексика" and r.scope == "0.3" and ("focal" not in r.applies_to or r.applies_to["focal"] in participants)
    ]
    prose_rules = [
        f"- [{r.rule_id}] {r.applies_to.get('focal', 'все линии')}: {item}"
        for r in stoplists
        if r.kind == "проза" and ("focal" not in r.applies_to or r.applies_to["focal"] in participants)
        for item in r.items
    ]
    dossier_slice: list[str] = []
    for d in sorted((compiler.safe_dossier(d, brief, infobans, participants) for d in dossiers if d.name in participants), key=lambda d: d.name):
        parts = [f"физика: {d.physique}" if d.physique else "", f"речевой паспорт: {d.speech}" if d.speech else "",
                 f"опознавательный код: {d.code}" if d.code else ""]
        body = "; ".join(x for x in parts if x)
        dossier_slice.append(f"- {d.name}: {body}" if body else f"- {d.name}: (карточка без физики и речевого паспорта)")
    matrix_slice = [
        f"- [{f.fact_id}] {f.subject}: {f.fact} "
        + ("(знает всегда)" if f.from_chapter == 0 else f"(узнаёт в гл. {f.from_chapter})" if f.from_chapter is not None else "(НЕ знает)")
        + (f" — {f.note}" if f.note.startswith("частично") else "")
        for f in matrix if f.subject in participants
    ]
    dose_lines = [f"- Доза {d.dose_id} (гл. {d.chapter}): триггер — {d.trigger}; читатель получает: {d.reader_gets}; "
                  f"НЕ получает: {d.reader_not_gets}. {d.rule}" for d in doses]
    document_lines = [f"- Документ №{d['number']} ({d['style']}): расхождение с правдой — {d['divergence']}. Языковая шкала: {d['scale']}"
                      for d in documents]
    blocks: list[list[str]] = [[f"# Проверка главы {chapter} (том {brief.volume}, фокал: {brief.focal}, дата: {brief.date})", ""]]
    if on.get("эпистемика") and matrix_slice:
        blocks.append(["## Срез знаний (участники сцены)", *matrix_slice, ""])
    if dossier_slice:
        blocks.append(["## Карточки участников сцены (что обязано совпасть)", *dossier_slice, ""])
    blocks.append(["## Бриф главы", f"- Сцены: {'; '.join(brief.scenes)}", f"- Биты: {'; '.join(brief.beats)}",
                   f"- Запреты: {'; '.join(brief.bans)}", f"- Фокал НЕ знает: {'; '.join(brief.not_knows)}",
                   *([f"- Что нового должен узнать читатель: {brief.reader_learns}"] if brief.reader_learns else []), ""])
    if on.get("закладки"):
        blocks.append(["## Закладки, назначенные главе", *[f"- [{p.plant_id}] {p.what}" for p in plants], ""])
    if on.get("дозы_прошлого") and dose_lines:
        blocks.append(["## Доза прошлого этой главы", *dose_lines, ""])
    if on.get("документы_вставки") and document_lines:
        blocks.append(["## Документ-вставка этой главы — блок `→ ДОКУМЕНТ` … `← КОНЕЦ ДОКУМЕНТА` обязателен", *document_lines, ""])
    if on.get("информрежим"):
        blocks.append(["## Запреты информрежима (резервы будущих томов)",
                       *[f"- [{b.ban_id}] {b.text}" for b in infobans if compiler.ban_active(b, brief)], ""])
    if on.get("фокализация"):
        blocks.append(["## Стоп-листы линий (фокализация)", *line_rules, ""])
        if prose_rules:
            blocks.append(["## Прозаические запреты линий", *prose_rules, ""])
    if on.get("драматургия"):
        blocks.append(["## Драматургия: каркас", *drama_lines, ""])
    if on.get("континуити"):
        blocks.append(["## Континуити (детали, которые обязаны совпасть)",
                       *[f"- {c}" for c in compiler.prior_continuity(continuity, brief, infobans, participants, briefs)], ""])
    if on.get("хроника_эпохи") and chronicle:
        blocks.append(["## Хроника эпохи (анахронизмы) — месяц главы ± 1", *chronicle_slice(chronicle, brief), ""])
    blocks.append(["## ТЕКСТ ГЛАВЫ", "", FENCE_OPEN, text, FENCE_CLOSE])
    user = "\n".join(line for block in blocks for line in block)
    return system_prompt(ws, cfg), user


def parse_flags(raw: str, max_words: int | None = None) -> list[Flag]:
    try:
        data = llmjson.extract_json(raw, list)
    except ValueError as e:
        raise ValueError(f"Ответ Верификатора-2: {e}") from e
    flags = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item.get("flag_id"), str) or not re.fullmatch(r"[\w.\-]+", item["flag_id"]):
            item["flag_id"] = f"F-{i:03d}"
        if max_words and isinstance(item.get("quote"), str):
            words = item["quote"].split()
            if len(words) > max_words:
                item["quote"] = " ".join(words[:max_words])
        try:
            flags.append(Flag.model_validate(item))
        except ValidationError as e:
            raise ValueError(f"Невалидный флаг №{i}: {e}") from e
    return flags


def _call_and_parse(ws: Workspace, cfg: Config, chapter: int, system: str, user: str, *, role: str, raw_name: str) -> list[Flag]:
    raw = adapters.call_role(cfg, "верификатор2", system, user, ws.logs, role=role, chapter=chapter)
    try:
        return parse_flags(raw, cfg.e2_quote_words)
    except ValueError:
        # FR-AD-3: неразбираемый ответ сохраняется целиком и предъявляется автору
        guard.write_text(ws.chapter_dir(chapter) / raw_name, raw)
        raise ValueError(f"ответ модели не разобран — сохранён целиком в {ws.chapter_rel(chapter)}/{raw_name}") from None


def run_verify2(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    system, user = build_prompt(ws, chapter, draft, cfg)
    guard.write_text(ws.chapter_dir(chapter) / "промпт_э2.md", f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    flags = _call_and_parse(ws, cfg, chapter, system, user, role="верификатор-2", raw_name="ответ_э2_сырой.md")
    save_flags(ws, chapter, flags)
    return flags


AGAIN_FLAGS = "флаги_повторно.json"
AGAIN_PROMPT = "промпт_э2_повторно.md"


def run_verify2_again(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    system, user = build_prompt(ws, chapter, draft, cfg)
    guard.write_text(ws.chapter_dir(chapter) / AGAIN_PROMPT, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    flags = _call_and_parse(ws, cfg, chapter, system, user, role="верификатор-2 (повторно)", raw_name="ответ_э2_повторно_сырой.md")
    save_flags_again(ws, chapter, flags, draft)
    return flags


def save_flags_again(ws: Workspace, chapter: int, flags: list[Flag], draft: int) -> None:
    guard.write_text(ws.chapter_dir(chapter) / AGAIN_FLAGS,
                     json.dumps({"черновик": draft, "флаги": [f.model_dump() for f in flags]}, ensure_ascii=False, indent=2) + "\n")


def load_flags_again(ws: Workspace, chapter: int) -> tuple[int | None, list[Flag]]:
    path = ws.chapter_dir(chapter) / AGAIN_FLAGS
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return None, [Flag.model_validate(r) for r in data]
    return data.get("черновик"), [Flag.model_validate(r) for r in data.get("флаги", [])]


def taste_rules(ws: Workspace) -> str:
    """Правила вкуса автора — секции документа стиля по образцу типа (`окно.секции_вкуса`)."""
    from .config import library_dir, load_config

    library = guard._library() or library_dir(ws, load_config(ws))
    spec = catalog.load_types(ws.root).get("стиль")
    pattern = (spec.window.get("секции_вкуса") if spec else None) or r"^(?:§\s*)?6\.[12]\.?\s|[Вв]кус"
    rx = re.compile(pattern)
    wanted: list[str] = []
    for path in exporter.docs_of_type(library, "стиль", None, ws.root):
        for s in mdparse.parse_sections(path):
            if s.level and rx.search(s.title + " "):
                wanted.append(f"### {s.title}\n{s.body}")
    return "\n\n".join(wanted) or "(правила вкуса в документе стиля ещё не заполнены)"


def build_taste_prompt(ws: Workspace, chapter: int, draft: int, cfg: Config | None = None) -> tuple[str, str]:
    cfg = cfg or Config()
    man = _manifest(ws)
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    user = (f"# Вкус: глава {chapter}\n\n## Правила вкуса автора\n\n{taste_rules(ws)}\n\n## ТЕКСТ ГЛАВЫ\n\n"
            f"{FENCE_OPEN}\n{text}\n{FENCE_CLOSE}\n")
    system = Environment().from_string(_template(ws, "верификатор2_вкус_система.md")).render(
        series=man.проект.имя, quote_words=cfg.e2_quote_words)
    return system, user


def run_taste(ws: Workspace, cfg: Config, chapter: int, draft: int) -> list[Flag]:
    system, user = build_taste_prompt(ws, chapter, draft, cfg)
    guard.write_text(ws.chapter_dir(chapter) / "промпт_вкуса.md", f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    raw = adapters.call_role(cfg, "верификатор2", system, user, ws.logs, role="вкус", chapter=chapter)
    flags = [f.model_copy(update={"severity": "мелочь", "type": "вкус", "kind": "violation"}) for f in parse_flags(raw)]
    guard.write_text(ws.chapter_dir(chapter) / "вкус.json", json.dumps([f.model_dump() for f in flags], ensure_ascii=False, indent=2) + "\n")
    return flags


def load_taste(ws: Workspace, chapter: int) -> list[Flag]:
    path = ws.chapter_dir(chapter) / "вкус.json"
    if not path.exists():
        return []
    return [Flag.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def save_flags(ws: Workspace, chapter: int, flags: list[Flag]) -> None:
    guard.write_text(ws.chapter_dir(chapter) / "флаги.json", json.dumps([f.model_dump() for f in flags], ensure_ascii=False, indent=2) + "\n")


def load_flags(ws: Workspace, chapter: int) -> list[Flag]:
    path = ws.chapter_dir(chapter) / "флаги.json"
    if not path.exists():
        return []
    return [Flag.model_validate(f) for f in json.loads(path.read_text(encoding="utf-8"))]
