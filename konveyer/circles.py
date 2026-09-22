"""Круги истории (восемь шагов) — несущий каркас драматургии серии (Р-020).

Три уровня вложенности: книга (том) → акты (четыре, Р-021) → главы. Круг
каждого уровня строится внутри шага уровня выше: акт работает на шаги тома,
глава — на шаг акта. Роль аналитика — модель Anthropic; результаты — черновики в
`драматургия/`; в канон (документ 2.1 `21_Круги_истории_Том1.md`) они
вносятся только по подтверждению автора (`konveyer circles --в-канон`, FR-K3),
после чего попадают в окно Писателя (секция «Драматургия») и в проверки Э2.
Без API промпты сохраняются для ручного прогона (NFR-3).

Круг главы (Р-024): шаги 1–7 обязательны, шаг 8 «Изменение» — только если материал главы
его даёт, иначе «в материале не задано» (`step_unset`); такой шаг в окно и Э2 не выводится
и линтером не считается ошибкой. Круги тома и актов — полные (Р-020, Р-021).
Материал аналитика (Р-026): тема цикла (13 §1), арки 2.5 (все колонки — внутренний инструмент
автора), события сетки с участниками сцен; промпт аналитика Писателю не выводится.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import adapters, cancel, canonchange, config as config_mod, exporter, gitops, guard, llmjson, mdparse, realcanon
from .config import Config
from .paths import Workspace
from .schemas import Act, Arc, CircleStep, StoryCircle

SCOPES = ("книга", "акты", "главы", "всё")
SCOPE_ALIASES = {"части": "акты", "часть": "акт"}
STEP_NAMES = ["Ты", "Потребность", "Переход", "Поиск", "Обретение", "Расплата", "Возвращение", "Изменение"]
REQUIRED_CHAPTER_STEPS = 7   # Р-024: у главы обязательны шаги 1–7, шаг 8 «Изменение» — по материалу
STEP_UNSET = "в материале не задано"   # так аналитик помечает шаг, которого материал не даёт
_UNSET_RE = re.compile(r"^\s*[(\[]?\s*в материале не задан[оа]?\s*[)\]]?\s*\.?\s*$", re.IGNORECASE)
CANON_DOC = "21_Круги_истории_Том1.md"  # документ 2.1 тома 1 (совместимость); по тому — canon_doc_name()


def canon_doc_name(volume: int = 1) -> str:
    """Имя документа 2.1 тома: `21_Круги_истории_Том{N}.md` (аудит 2, п. 27)."""
    return f"21_Круги_истории_Том{int(volume)}.md"
_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def step_unset(step: CircleStep) -> bool:
    """Шаг без содержания: пустой текст или пометка «в материале не задано» (Р-024)."""
    return not step.text.strip() or bool(_UNSET_RE.match(step.text))


def chapter_steps_present(circle: StoryCircle) -> list[CircleStep]:
    """Шаги круга главы, которые есть по содержанию: незаданный шаг 8 «Изменение» не выводится
    ни в окно, ни в Э2 (Р-024); для тома/акта — все шаги как есть."""
    if circle.scope != "глава":
        return list(circle.steps)
    return [st for st in circle.steps if not (st.n == 8 and step_unset(st))]


def _template(ws: Workspace) -> str:
    override = ws.templates / "круг_истории_система.md"
    if override.exists():
        return override.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/круг_истории_система.md").read_text(encoding="utf-8")


def _dir(ws: Workspace) -> Path:
    return ws.root / "драматургия"


# ------------------------------------------------------------ модель круга


def to_model(data: dict) -> StoryCircle:
    """JSON черновика (ответ модели + scope/key) → StoryCircle с разобранными диапазонами глав."""
    steps = []
    for i, st in enumerate(data.get("steps", []), start=1):
        chapters = str(st.get("chapters", "") or "").strip()
        lo, hi = realcanon.chapter_range(chapters)
        steps.append(CircleStep(
            n=int(st.get("n", i)), name=str(st.get("name", "") or STEP_NAMES[min(i, 8) - 1]).strip(),
            text=str(st.get("text", "") or "").strip(), chapters=chapters, from_chapter=lo, to_chapter=hi,
        ))
    return StoryCircle(
        scope=data.get("scope", "глава"), key=data.get("key"), title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""), weak_spot=str(data.get("weak_spot", "") or ""), steps=steps,
    )


def drafts(ws: Workspace) -> list[StoryCircle]:
    """Черновики кругов рабочей области (драматургия/*.json)."""
    return [to_model(c) for c in list_circles(ws)]


def canon_circles(ws: Workspace) -> list[StoryCircle]:
    """Круги, внесённые в канон (выгрузки/circles.json); пусто, если документа 2.1 ещё нет."""
    try:
        return exporter.load_circles(ws.exports)
    except FileNotFoundError:
        return []


def act_list(ws: Workspace) -> list[Act]:
    """Акты тома из выгрузки (таблица 2.1; без неё — части реестра); пусто, если выгрузки нет."""
    try:
        return exporter.load_acts(ws.exports)
    except FileNotFoundError:
        return []


def _pick(circles: list[StoryCircle], scope: str, key: int | None) -> StoryCircle | None:
    return next((c for c in circles if c.scope == scope and c.key == key), None)


def frame_for_chapter(circles: list[StoryCircle], acts: list[Act], chapter: int) -> dict:
    """Каркас главы: шаги тома и акта, на которые она приходится, и её собственный круг."""
    act = next((a for a in acts if a.from_chapter <= chapter <= a.to_chapter), None)
    book = _pick(circles, "книга", None)
    act_circle = _pick(circles, "акт", act.act) if act else None
    return {
        "act": act,
        "book_steps": book.steps_for_chapter(chapter) if book else [],
        "act_steps": act_circle.steps_for_chapter(chapter) if act_circle else [],
        "chapter": _pick(circles, "глава", chapter),
        "has_any": bool(book or act_circle or _pick(circles, "глава", chapter)),
    }


def frame_lines(frame: dict, with_weak_spot: bool = False) -> list[str]:
    """Текстовое представление каркаса — для промптов Писателя, аналитика и Э2."""
    lines: list[str] = []
    for st in frame["book_steps"]:
        lines.append(f"- Том: шаг {st.n} «{st.name}» ({st.chapters}) — {st.text}")
    act = frame.get("act")
    for st in frame["act_steps"]:
        label = f"Акт {act.act} «{act.title}»" if act else "Акт"
        lines.append(f"- {label}: шаг {st.n} «{st.name}» ({st.chapters}) — {st.text}")
    ch = frame.get("chapter")
    if ch:
        lines.append(f"- Круг главы: {ch.summary}" if ch.summary else "- Круг главы:")
        present = chapter_steps_present(ch)
        for st in present:
            where = f" ({st.chapters})" if st.chapters else ""
            lines.append(f"  {st.n}. {st.name}{where} — {st.text}")
        if not any(st.n == 8 for st in present):
            lines.append("  (шаг 8 «Изменение» в круге главы не задан — изменение фокала не требуется, Р-024)")
        if with_weak_spot and ch.weak_spot:
            lines.append(f"  Слабое место (по оценке аналитика): {ch.weak_spot}")
    return lines


# ------------------------------------------------------------ материалы


def _chapter_rows(briefs, lo: int | None = None, hi: int | None = None) -> list[str]:
    """События сетки по главам — с участниками сцен (Р-026): «фокал X · участники: Y, Z»."""
    rows = []
    for b in sorted(briefs, key=lambda b: b.chapter):
        if lo is not None and b.chapter < lo:
            continue
        if hi is not None and b.chapter > hi:
            continue
        beats = "; ".join(x for x in b.beats if not x.lower().startswith("кладём"))
        others = [p for p in b.participants if p and p != b.focal]
        who = f"фокал {b.focal}" + (f" · участники: {', '.join(others)}" if others else "")
        rows.append(f"- гл. {b.chapter} · {b.date} · {who}: {beats}")
    return rows


def _library_of(ws: Workspace, library: Path | None) -> Path | None:
    """Библиотека канона для материалов, которых нет в выгрузках (тема цикла 13 §1)."""
    if library is not None:
        return library
    try:
        lib = config_mod.library_dir(ws, config_mod.load_config(ws))
    except (OSError, ValueError):  # без конфиг.yaml или с битым — материал просто без темы
        return None
    return lib if lib.exists() else None


THEME_DOC_GLOB = "13_*.md"
THEME_SECTION_RE = r"Ядро цикла и тема"


def cycle_theme(library: Path | None) -> str:
    """Тема цикла — раздел «## 1. Ядро цикла и тема» документа 13 (Р-026). Тема цикла I (тома 1–6,
    «опека через ложь») в библиотеке сформулирована в первом абзаце того же раздела — отдельного
    документа концепции цикла I нет, поэтому берётся весь раздел. Без документа — пусто."""
    if library is None:
        return ""
    for path in sorted(library.glob(THEME_DOC_GLOB)):
        sec = mdparse.find_section(mdparse.parse_sections(path), THEME_SECTION_RE)
        if sec and sec.body:
            return sec.body
    return ""


def arcs_rows(arcs: list[Arc], act: int | None = None) -> list[str]:
    """Таблица арок 2.5 (все колонки — внутренний инструмент автора, тайны допустимы: промпт
    аналитика Писателю не выводится). `act` — только строки этого акта."""
    rows = [a for a in arcs if act is None or a.act == act]
    if not rows:
        return []
    dash = lambda v: v or "—"  # noqa: E731
    return [
        "| Персонаж | Акт | Ложь | Желание | Потребность | Где на арке | Что видно снаружи |",
        "|---|---|---|---|---|---|---|",
        *[f"| {a.character} | {a.act} | {dash(a.lie)} | {dash(a.want)} | {dash(a.need)} | {dash(a.position)} | {dash(a.visible)} |"
          for a in sorted(rows, key=lambda a: (a.act, a.character))],
    ]


def _known_circles(ws: Workspace) -> list[StoryCircle]:
    """Каркас для вложенности при построении: черновики поверх канона (черновик новее)."""
    merged = {(c.scope, c.key): c for c in canon_circles(ws)}
    for c in drafts(ws):
        merged[(c.scope, c.key)] = c
    return list(merged.values())


def _outer_frame(ws: Workspace, scope: str, key: int | None) -> list[str]:
    """Шаги уровня выше, внутри которых строится круг (акт — в томе, глава — в акте и томе)."""
    circles = _known_circles(ws)
    acts = act_list(ws)
    if scope == "акт":
        act = next((a for a in acts if a.act == key), None)
        book = _pick(circles, "книга", None)
        if not act or not book:
            return []
        lo, hi = act.from_chapter, act.to_chapter
        return [
            f"- Том: шаг {st.n} «{st.name}» ({st.chapters}) — {st.text}"
            for st in book.steps
            if st.from_chapter is not None and st.from_chapter <= hi and (st.to_chapter or st.from_chapter) >= lo
        ]
    if scope == "глава":
        frame = frame_for_chapter(circles, acts, int(key or 0))
        frame["chapter"] = None
        return frame_lines(frame)
    return []


def build_material(ws: Workspace, scope: str, key: int | None = None, library: Path | None = None) -> tuple[str, str]:
    """(заголовок, материал) для круга: книга / акт N / глава N. Для тома и актов (Р-026) — тема цикла
    (13 §1), арки 2.5 целиком и события сетки с участниками сцен; `library` — для темы (иначе по конфиг.yaml)."""
    ex = ws.exports
    briefs = exporter.load_briefs(ex)
    bans = exporter.load_infobans(ex)
    arcs = exporter.load_arcs(ex)
    secrets = [
        f"- {b.text} (читатель узнаёт: {'гл. ' + str(b.until_chapter) if b.until_chapter else 'не в этом томе'})"
        for b in bans if b.secret
    ]
    outer = _outer_frame(ws, scope, key)
    outer_block = ["## Каркас уровня выше (круг строится ВНУТРИ этих шагов)", *outer, ""] if outer else []
    if scope == "книга":
        parts = exporter.load_parts(ex)
        parts_lines = [f"- Часть {p['part']} «{p['title']}» — {p['period']} (гл. {p['from_chapter']}–{p['to_chapter']})" for p in parts]
        acts_lines = [
            f"- Акт {a.act} «{a.title}» — гл. {a.from_chapter}–{a.to_chapter}" + (f": шаги {a.steps}" if a.steps else "")
            for a in act_list(ws)
        ]
        theme = cycle_theme(_library_of(ws, library))
        theme_block = ["## Тема цикла (13 §1)", theme, ""] if theme else []
        arcs_block = ["## Арки тома (2.5: ложь / желание / потребность — внутренний инструмент автора)",
                      *arcs_rows(arcs), ""] if arcs else []
        material = "\n".join(
            [*theme_block, "## Акты тома (шаги круга тома должны ложиться на эти границы)", *acts_lines, "",
             "## Части тома (редакторское деление)", *parts_lines, "", *arcs_block,
             "## Главы тома (события сетки с участниками сцен)", *_chapter_rows(briefs), "",
             "## Реестр тайн (режим читателя)", *secrets]
        )
        return "Книга (том целиком)", material
    if scope == "акт":
        act = next((a for a in act_list(ws) if a.act == key), None)
        if act is None:
            raise FileNotFoundError(f"акта {key} нет в таблице актов (2.1)")
        lo, hi = act.from_chapter, act.to_chapter
        head = [f"## Акт {act.act} «{act.title}» — гл. {lo}–{hi}" + (f" (части реестра {act.parts})" if act.parts else "")]
        if act.steps:
            head.append(f"Шаги круга тома, за которые отвечает акт: {act.steps}. Круг акта раскрывает именно их.")
        act_arcs = arcs_rows(arcs, act.act)
        arcs_block = [f"## Арки акта {act.act} (2.5: ложь / желание / потребность — внутренний инструмент автора)",
                      *act_arcs, ""] if act_arcs else []
        material = "\n".join(
            [*outer_block, *head, *_chapter_rows(briefs, lo, hi), "", *arcs_block,
             "## Тайны, раскрываемые читателю в этом акте",
             *[s for b, s in zip([b for b in bans if b.secret], secrets, strict=True) if b.until_chapter and lo <= b.until_chapter <= hi]]
        )
        return f"Акт {act.act} «{act.title}»", material
    if scope == "глава":
        brief = exporter.load_brief(ex, key)
        matrix = exporter.load_matrix(ex)
        known = [f"- [{f.fact_id}] {f.fact}" for f in matrix
                 if f.subject == brief.focal and f.from_chapter is not None and f.from_chapter <= key]
        from . import compiler

        plants = [f"- [{p.plant_id}] {p.what}" for p in compiler.chapter_plants(ex, brief)]
        material = "\n".join(
            [*outer_block, f"## Глава {key} · {brief.date} · фокал {brief.focal}", "### Сцены", *[f"- {s}" for s in brief.scenes],
             "### Биты", *[f"- {b}" for b in brief.beats], "### Что знает фокал", *known, "### Закладки главы", *plants]
        )
        return f"Глава {key}", material
    raise ValueError(f"неизвестный охват: {scope}")


# ---------------------------------------------------------------- прогон


def targets(ws: Workspace, scope: str, chapter: int | None = None) -> list[tuple[str, int | None]]:
    """Список (охват, ключ) по выбору: книга | акты | главы | всё | одна глава."""
    scope = SCOPE_ALIASES.get(scope, scope)
    if scope == "книга":
        return [("книга", None)]
    if scope == "акты":
        return [("акт", a.act) for a in act_list(ws)]
    if scope == "главы":
        if chapter is not None:
            return [("глава", chapter)]
        return [("глава", b.chapter) for b in sorted(exporter.load_briefs(ws.exports), key=lambda b: b.chapter)]
    if scope == "всё":
        return targets(ws, "книга") + targets(ws, "акты") + targets(ws, "главы")
    raise ValueError(f"охват должен быть одним из {SCOPES}")


def _file_stem(scope: str, key: int | None) -> str:
    if scope == "книга":
        return "книга"
    if scope == "акт":
        return f"акт_{key}"
    return f"глава_{int(key or 0):02d}"


def render_md(circle: dict) -> str:
    lines = [f"# Круг истории · {circle.get('title', '')}", ""]
    if circle.get("summary"):
        lines += [f"**Суть:** {circle['summary']}", ""]
    for st in circle.get("steps", []):
        lines.append(f"## {st.get('n')}. {st.get('name')}" + (f" ({st['chapters']})" if st.get("chapters") else ""))
        lines.append(st.get("text", ""))
        lines.append("")
    if circle.get("weak_spot"):
        lines += ["## Слабое место", circle["weak_spot"], ""]
    return "\n".join(lines)


def save_circle(ws: Workspace, scope: str, key: int | None, circle: dict) -> Path:
    stem = _file_stem(scope, key)
    circle = {**circle, "scope": scope, "key": key, "generated": datetime.now(timezone.utc).isoformat()}
    guard.write_text(_dir(ws) / f"{stem}.json", json.dumps(circle, ensure_ascii=False, indent=2) + "\n")
    path = _dir(ws) / f"{stem}.md"
    guard.write_text(path, render_md(circle))
    return path


def run(ws: Workspace, cfg: Config, scope: str, chapter: int | None = None, only_missing: bool = True,
        library: Path | None = None) -> dict:
    """Генерация кругов сверху вниз (том → акты → главы): каждый уровень строится
    внутри уже построенного уровня выше. Возвращает {готово, промпты, ручной_режим}.
    Промпты аналитика (материал с тайнами и арками) — только в `драматургия/промпты/`, Писателю не идут."""
    done: list[str] = []
    prompts: list[str] = []
    system = _template(ws)
    manual_reason = None
    todo = [(sc, key) for sc, key in targets(ws, scope, chapter)
            if not (only_missing and (_dir(ws) / f"{_file_stem(sc, key)}.json").exists())]
    for i, (sc, key) in enumerate(todo, start=1):
        stem = _file_stem(sc, key)
        title, material = build_material(ws, sc, key, library)
        print(f"[{i}/{len(todo)}] {title}")  # прогресс для карточки задачи в панели (аудит 5.5)
        user = f"# {title}\n\n{material}"
        prompt_path = _dir(ws) / "промпты" / f"{stem}.md"
        guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
        if manual_reason:
            prompts.append(str(prompt_path))
            continue
        if done:
            cancel.check(f"круги истории: перед «{title}»")  # между вызовами модели; готовые круги остаются
        try:
            raw = adapters.call_anthropic(system, user, cfg.canonist, cfg.api, ws.logs, role="аналитик (круг истории)")
            circle = llmjson.extract_json(raw, dict)
            circle.setdefault("title", title)
            done.append(str(save_circle(ws, sc, key, circle)))
        except adapters.ManualModeNeeded as e:
            manual_reason = e.reason
            prompts.append(str(prompt_path))
    return {"готово": done, "промпты": prompts, "ручной_режим": manual_reason}


def accept_manual(ws: Workspace, scope: str, key: int | None, raw: str) -> Path:
    """Ручной режим: вставленный ответ модели → файл круга."""
    circle = llmjson.extract_json(raw, dict)
    circle.setdefault("title", build_material(ws, scope, key)[0])
    return save_circle(ws, scope, key, circle)


def list_circles(ws: Workspace) -> list[dict]:
    out = []
    for p in sorted(_dir(ws).glob("*.json")) if _dir(ws).exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    order = {"книга": 0, "акт": 1, "глава": 2}
    return sorted(out, key=lambda c: (order.get(c.get("scope"), 9), c.get("key") or 0))


# ------------------------------------------------------------ канон (2.1)


def render_canon_doc(circles: list[StoryCircle], acts: list[Act], volume: int = 1) -> str:
    """Документ 2.1 из актов и кругов — в разметке, которую читает exporter (Д-1)."""
    order = {"книга": 0, "акт": 1, "глава": 2}
    lines = [
        f"# 2.1. Круги истории — Том {volume}",
        "## Версия 1.0 · Р-020, Р-021. Несущий каркас драматургии: том → четыре акта → главы",
        "",
        "Документ генерируется конвейером из черновиков `драматургия/` по подтверждению автора "
        "(`konveyer circles --в-канон`) и правится автором как любой документ канона. Что читает машина: "
        "таблица «Акты тома» (границы актов — единственный источник актов для конвейера); "
        "заголовки «## Круг тома» / «## Круг акта N …» / «## Круг главы N», строка «**Суть:**», "
        "восемь нумерованных шагов «N. **Имя** (гл. A–B) — текст», строка «**Слабое место:**». "
        "Диапазоны глав шагов тома и актов — единственная привязка главы к её шагу. "
        "Круг главы (Р-024): шаги 1–7 обязательны, шаг 8 «Изменение» — только если материал главы его даёт, "
        "иначе «в материале не задано».",
        "",
        "## Акты тома",
        "",
        "| Акт | Название | Главы | Части | Шаги круга |",
        "|---|---|---|---|---|",
    ]
    for a in sorted(acts, key=lambda a: a.act):
        lines.append(f"| {a.act} | «{a.title}» | {a.from_chapter}–{a.to_chapter} | {a.parts} | {a.steps} |")
    lines.append("")
    for c in sorted(circles, key=lambda c: (order.get(c.scope, 9), c.key or 0)):
        if c.scope == "книга":
            lines.append("## Круг тома")
        elif c.scope == "акт":
            act = next((a for a in acts if a.act == c.key), None)
            tail = f" «{act.title}» (гл. {act.from_chapter}–{act.to_chapter})" if act else ""
            lines.append(f"## Круг акта {c.key}{tail}")
        else:
            lines.append(f"## Круг главы {c.key}")
        if c.summary:
            lines.append(f"**Суть:** {c.summary}")
        for st in c.steps:
            where = f" ({st.chapters})" if st.chapters else ""
            text = STEP_UNSET if c.scope == "глава" and st.n == 8 and step_unset(st) else st.text
            lines.append(f"{st.n}. **{st.name}**{where} — {text}")
        if c.weak_spot:
            lines.append(f"**Слабое место:** {c.weak_spot}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def canon_status(ws: Workspace) -> dict[str, str]:
    """Для панели: по каждому черновику — «в каноне» / «отличается от канона» / «не в каноне»."""
    canon = {(c.scope, c.key): c for c in canon_circles(ws)}
    status: dict[str, str] = {}
    for d in drafts(ws):
        c = canon.get((d.scope, d.key))
        if c is None:
            status[_file_stem(d.scope, d.key)] = "не в каноне"
        elif [(s.n, s.name, s.text, s.chapters) for s in c.steps] == [(s.n, s.name, s.text, s.chapters) for s in d.steps] \
                and c.summary == d.summary:
            status[_file_stem(d.scope, d.key)] = "в каноне"
        else:
            status[_file_stem(d.scope, d.key)] = "отличается от канона"
    return status


def commit_to_canon(ws: Workspace, cfg: Config, library: Path) -> tuple[Path, str]:
    """Вносит черновики кругов в документ 2.1 библиотеки (FR-K3: только по подтверждению автора).

    Черновики заменяют одноимённые круги канона; круги канона, у которых черновика нет,
    сохраняются. Затем перегенерация выгрузок и git-коммит канона.
    """
    new = drafts(ws)
    if not new:
        raise RuntimeError("черновиков кругов нет — сначала постройте их (`konveyer circles`).")
    merged = {(c.scope, c.key): c for c in canon_circles(ws)}
    for c in new:
        merged[(c.scope, c.key)] = c
    acts = act_list(ws)
    path = library / canon_doc_name(ws.volume)
    text = render_canon_doc(list(merged.values()), acts, ws.volume)
    message = f"[круги истории] внесено кругов: {len(new)} (каркас драматургии, Р-020)"
    # единый конвейер: чистый git → сессия записи → выгрузки → линт → коммит (или «не под git»)
    result = canonchange.canon_change(
        ws, cfg, library, lambda: guard.write_text(path, text), message,
        commit=True, author_confirmed=True, action="внесение кругов",
    )
    if result.commit:
        return path, result.commit
    if not gitops.is_repo(library):
        return path, "(библиотека не под git — коммит пропущен, настройте git!)"
    return path, "(изменений в каноне нет)"
