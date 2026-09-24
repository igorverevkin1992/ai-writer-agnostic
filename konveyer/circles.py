"""Каркасы драматургии по методикам-плагинам (FR-DR-1…FR-DR-6): том → акты → главы.

Роль аналитика — модель; результаты — черновики в `драматургия/`; в канон (документ типа «каркасы») они
вносятся только по подтверждению автора (`каркас --в-канон`), после чего попадают в окно Писателя (секция
«Драматургия») и в проверки Э2 (FR-DR-4, FR-DR-5). Без API промпты сохраняются для ручного прогона.

Имена шагов, их обязательность и тексты для окна/Э2 задаёт методика (`methodics`), выбранная в манифесте
для уровня; движок не знает ни одного шага (П-1). Незаданный необязательный шаг — не ошибка: в окно и Э2
не выводится, линтером не считается (FR-DR-3). Материал аналитика (тема серии, арки целиком, события с
участниками) Писателю не показывается (FR-DR-6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment

from . import adapters, cancel, canonchange, catalog, config as config_mod, dramaturgy_doc, exporter, gitops, guard
from . import llmjson, manifest as manifest_mod, methodics
from .config import Config
from .names import chapter_range
from .paths import Workspace
from .schemas import Act, Arc, CircleStep, StoryCircle

SCOPES = ("книга", "акты", "главы", "всё")
SCOPE_ALIASES = {"части": "акты", "часть": "акт"}
LEVEL_OF = {"книга": "том", "акт": "акт", "глава": "глава"}
STEP_UNSET = dramaturgy_doc.STEP_UNSET
step_unset = dramaturgy_doc.step_unset
# методика движка по умолчанию — когда в манифесте уровень не задан вовсе (совместимость со старыми проектами)
_DEFAULT = "круг_хармона"
UNSET_NOTE_DEFAULT = "(шаг {n} «{name}» в каркасе главы не задан)"


def _default_methodic() -> methodics.Methodic:
    return methodics.load_all()[_DEFAULT]


# совместимость: имена шагов методики по умолчанию
STEP_NAMES = _default_methodic().step_names()


def _manifest(ws: Workspace) -> manifest_mod.Manifest:
    lib = _library_of(ws, None) or ws.root / "Библиотека"
    return manifest_mod.effective(ws.root, lib, catalog.load_types(ws.root))


def methodic_for(ws: Workspace, scope: str) -> methodics.Methodic:
    """Методика уровня из манифеста. Уровень не задан — методика движка по умолчанию; задана, но не найдена или
    не поддерживает уровень — ValueError с понятной причиной (доктор показывает то же, `methodics.problems`)."""
    man = _manifest(ws)
    level = LEVEL_OF.get(scope, scope)
    m = methodics.primary(level, man, ws.root)
    if m is None:
        names = man.методики.for_level(level)
        if names:
            known = methodics.load_all(ws.root)
            missing = [n for n in names if n not in known]
            if missing:
                raise ValueError(f"методика «{missing[0]}» (уровень «{level}») не найдена ни в движке, ни в методики/ проекта")
            raise ValueError(f"методика «{names[0]}» не поддерживает уровень «{level}» "
                             f"(её уровни: {', '.join(known[names[0]].levels)})")
        m = methodics.load_all(ws.root).get(_DEFAULT) or _default_methodic()
    if m.name == "пустая":
        m = methodics.empty_from_manifest(man, m, level)
    return m


def _chapter_methodic(ws: Workspace) -> tuple[methodics.Methodic | None, manifest_mod.Manifest]:
    """Методика главы для окна и Э2; при ошибке манифеста — None (П-5: окно собирается без текстов методики)."""
    man = _manifest(ws)
    try:
        return methodic_for(ws, "глава"), man
    except ValueError:
        return None, man


def required_steps(ws: Workspace, scope: str) -> set[int]:
    return methodic_for(ws, scope).required_steps(LEVEL_OF.get(scope, scope), _manifest(ws))


def canon_doc_name(volume: int = 1, root: Path | None = None) -> str:
    """Имя документа каркасов тома — из каталога типов (`каркасы.имя_по_умолчанию`)."""
    spec = catalog.load_types(root).get("каркасы")
    pattern = spec.default_name if spec and spec.default_name else "21_Каркасы_Том{том}.md"
    return pattern.format(том=int(volume))


CANON_DOC = canon_doc_name(1)


def chapter_steps_present(circle: StoryCircle, required: set[int] | None = None) -> list[CircleStep]:
    """Шаги каркаса, которые есть по содержанию: незаданный НЕобязательный шаг не выводится ни в окно, ни в Э2;
    обязательные (по методике и манифесту) выводятся всегда; для тома/акта — как есть."""
    if circle.scope != "глава":
        return list(circle.steps)
    req = required or set()
    return [st for st in circle.steps if st.n in req or not step_unset(st)]


def _template(ws: Workspace, scope: str) -> str:
    """Системный промпт аналитика по методике уровня (проект может переопределить `методики/<имя>/промпт.md`)."""
    m = methodic_for(ws, scope)
    man = _manifest(ws)
    level = LEVEL_OF.get(scope, scope)
    env = Environment()
    return env.from_string(m.prompt).render(
        series=man.проект.имя, steps=m.steps, required=sorted(m.required_steps(level, man)),
        schema=json.dumps(m.schema, ensure_ascii=False, indent=2) if m.schema else "{}", level=level,
    )


def window_intro(ws: Workspace, frame: dict) -> str:
    """Вводный абзац секции «Драматургия» окна — из `в_окно.j2` методики главы (FR-DR-1)."""
    if not frame.get("has_any"):
        return ""
    m, man = _chapter_methodic(ws)
    if m is None or not m.window_template.strip():
        return ""
    return Environment().from_string(m.window_template).render(
        step_names=m.step_names(), optional=sorted(m.optional_steps("глава", man)), required=sorted(m.required_steps("глава", man)),
    ).strip()


def e2_text(ws: Workspace) -> str:
    """Текст проверки драматургии для Э2 — из `в_э2.md` методики главы."""
    m, _ = _chapter_methodic(ws)
    return m.e2_text.strip() if m is not None else ""


def frame_lines_for(ws: Workspace, frame: dict, with_weak_spot: bool = False) -> list[str]:
    """Строки каркаса главы по методике главы из манифеста: обязательность, имена шагов, заголовок и пометка
    о незаданном шаге — из методики, не из движка (П-1)."""
    m, man = _chapter_methodic(ws)
    if m is None or not frame.get("has_any"):
        return frame_lines(frame, with_weak_spot=with_weak_spot)
    return frame_lines(
        frame, with_weak_spot=with_weak_spot, required=m.required_steps("глава", man), optional=m.optional_steps("глава", man),
        step_names={st.n: st.name for st in m.steps}, unset_note=m.unset_note, heading=m.heading,
    )


def _dir(ws: Workspace) -> Path:
    return ws.root / "драматургия"


# ------------------------------------------------------------ модель каркаса


def to_model(data: dict, step_names: list[str] | None = None) -> StoryCircle:
    """JSON черновика (ответ модели + scope/key) → StoryCircle с разобранными диапазонами глав; имена шагов без
    имени в ответе — из методики (`step_names`), иначе «шаг N»."""
    names_ = step_names or []
    steps = []
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError("в ответе поле «steps» должно быть списком шагов")
    for i, st in enumerate(raw_steps, start=1):
        if not isinstance(st, dict):
            raise ValueError(f"шаг {i} ответа — не объект")
        chapters = str(st.get("chapters", "") or "").strip()
        lo, hi = chapter_range(chapters)
        default_name = names_[min(i, len(names_)) - 1] if names_ else f"шаг {i}"
        try:
            n = int(st.get("n", i))
        except (TypeError, ValueError):
            raise ValueError(f"шаг {i}: номер «{st.get('n')}» — не число") from None
        steps.append(CircleStep(
            n=n, name=str(st.get("name", "") or default_name).strip(),
            text=str(st.get("text", "") or "").strip(), chapters=chapters, from_chapter=lo, to_chapter=hi,
        ))
    return StoryCircle(
        scope=data.get("scope", "глава"), key=data.get("key"), title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""), weak_spot=str(data.get("weak_spot", "") or ""), steps=steps,
    )


def _step_names_of(ws: Workspace, scope: str) -> list[str]:
    try:
        return methodic_for(ws, scope).step_names()
    except ValueError:
        return []


def broken_drafts(ws: Workspace) -> list[str]:
    """Файлы черновиков, которые не разбираются (для доктора и панели)."""
    bad: list[str] = []
    for p in sorted(_dir(ws).glob("*.json")) if _dir(ws).exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("не объект")
            to_model(data)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            bad.append(f"{p.name}: {e}")
    return bad


def drafts(ws: Workspace) -> list[StoryCircle]:
    """Черновики каркасов рабочей области (драматургия/*.json)."""
    return [to_model(c, _step_names_of(ws, str(c.get("scope", "глава")))) for c in list_circles(ws)]


def canon_circles(ws: Workspace) -> list[StoryCircle]:
    try:
        return exporter.load_circles(ws.exports)
    except FileNotFoundError:
        return []


def act_list(ws: Workspace) -> list[Act]:
    try:
        return exporter.load_acts(ws.exports)
    except FileNotFoundError:
        return []


def _pick(circles: list[StoryCircle], scope: str, key: int | None) -> StoryCircle | None:
    return next((c for c in circles if c.scope == scope and c.key == key), None)


def frame_for_chapter(circles: list[StoryCircle], acts: list[Act], chapter: int) -> dict:
    """Каркас главы: шаги тома и акта, на которые она приходится, и её собственный каркас."""
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


def frame_lines(frame: dict, with_weak_spot: bool = False, required: set[int] | None = None,
                optional: set[int] | None = None, step_names: dict[int, str] | None = None, unset_note: str = "",
                heading: str = "Каркас") -> list[str]:
    """Текстовое представление каркаса — для промптов Писателя, аналитика и Э2. Всё, что зависит от методики
    (обязательные и необязательные шаги, имена шагов, слово заголовка, пометка о незаданном шаге), приходит
    параметрами (`frame_lines_for`); без них — только заданные шаги, без пометок."""
    lines: list[str] = []
    for st in frame["book_steps"]:
        lines.append(f"- Том: шаг {st.n} «{st.name}» ({st.chapters}) — {st.text}")
    act = frame.get("act")
    for st in frame["act_steps"]:
        label = f"Акт {act.act} «{act.title}»" if act else "Акт"
        lines.append(f"- {label}: шаг {st.n} «{st.name}» ({st.chapters}) — {st.text}")
    ch = frame.get("chapter")
    if ch:
        label = f"- {heading or 'Каркас'} главы:"
        lines.append(f"{label} {ch.summary}" if ch.summary else label)
        present = chapter_steps_present(ch, required)
        for st in present:
            where = f" ({st.chapters})" if st.chapters else ""
            lines.append(f"  {st.n}. {st.name}{where} — {st.text}")
        names_ = step_names or {}
        for n in sorted(optional or set()):
            if not any(st.n == n for st in present):
                name = names_.get(n) or next((st.name for st in ch.steps if st.n == n), f"шаг {n}")
                lines.append("  " + (unset_note or UNSET_NOTE_DEFAULT).format(n=n, name=name))
        if with_weak_spot and ch.weak_spot:
            lines.append(f"  Слабое место (по оценке аналитика): {ch.weak_spot}")
    return lines


# ------------------------------------------------------------ материалы


def _chapter_rows(briefs, lo: int | None = None, hi: int | None = None) -> list[str]:
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
    if library is not None:
        return library
    try:
        lib = config_mod.library_dir(ws, config_mod.load_config(ws))
    except (OSError, ValueError):
        return None
    return lib if lib.exists() else None


def cycle_theme(ws: Workspace) -> str:
    """Тема серии — из выгрузки замысла (документ типа «методика»); без документа — пусто."""
    try:
        notes = exporter.load_method(ws.exports)
    except FileNotFoundError:
        return ""
    return "\n\n".join(n.theme for n in notes if n.theme)


def arcs_rows(arcs: list[Arc], act: int | None = None) -> list[str]:
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
    merged = {(c.scope, c.key): c for c in canon_circles(ws)}
    for c in drafts(ws):
        merged[(c.scope, c.key)] = c
    return list(merged.values())


def _outer_frame(ws: Workspace, scope: str, key: int | None) -> list[str]:
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
    """(заголовок, материал) для каркаса: книга / акт N / глава N (FR-DR-6)."""
    ex = ws.exports
    briefs = exporter.load_briefs(ex)
    bans = exporter.load_infobans(ex)
    arcs = exporter.load_arcs(ex)
    secrets = [
        f"- {b.text} (читатель узнаёт: {'гл. ' + str(b.until_chapter) if b.until_chapter else 'не в этом томе'})"
        for b in bans if b.secret
    ]
    outer = _outer_frame(ws, scope, key)
    outer_block = ["## Каркас уровня выше (каркас строится ВНУТРИ этих шагов)", *outer, ""] if outer else []
    if scope == "книга":
        parts = exporter.load_parts(ex)
        parts_lines = [f"- Часть {p['part']} «{p['title']}» — {p['period']} (гл. {p['from_chapter']}–{p['to_chapter']})" for p in parts]
        acts_lines = [
            f"- Акт {a.act} «{a.title}» — гл. {a.from_chapter}–{a.to_chapter}" + (f": шаги {a.steps}" if a.steps else "")
            for a in act_list(ws)
        ]
        theme = cycle_theme(ws)
        theme_block = ["## Тема серии", theme, ""] if theme else []
        arcs_block = ["## Арки тома (ложь / желание / потребность — внутренний инструмент автора)",
                      *arcs_rows(arcs), ""] if arcs else []
        material = "\n".join(
            [*theme_block, "## Акты тома (шаги каркаса тома должны ложиться на эти границы)", *acts_lines, "",
             "## Части тома", *parts_lines, "", *arcs_block,
             "## Главы тома (события плана с участниками сцен)", *_chapter_rows(briefs), "",
             "## Реестр тайн (режим читателя)", *secrets]
        )
        return "Книга (том целиком)", material
    if scope == "акт":
        act = next((a for a in act_list(ws) if a.act == key), None)
        if act is None:
            raise FileNotFoundError(f"акта {key} нет в таблице актов")
        lo, hi = act.from_chapter, act.to_chapter
        head = [f"## Акт {act.act} «{act.title}» — гл. {lo}–{hi}" + (f" (части {act.parts})" if act.parts else "")]
        if act.steps:
            head.append(f"Шаги каркаса тома, за которые отвечает акт: {act.steps}. Каркас акта раскрывает именно их.")
        act_arcs = arcs_rows(arcs, act.act)
        arcs_block = [f"## Арки акта {act.act} (ложь / желание / потребность — внутренний инструмент автора)",
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
    lines = [f"# Каркас · {circle.get('title', '')}", ""]
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
    """Генерация каркасов сверху вниз (том → акты → главы). Возвращает {готово, промпты, ручной_режим}."""
    done: list[str] = []
    prompts: list[str] = []
    failed: list[str] = []
    manual_reason = None
    todo = [(sc, key) for sc, key in targets(ws, scope, chapter)
            if not (only_missing and (_dir(ws) / f"{_file_stem(sc, key)}.json").exists())]
    for i, (sc, key) in enumerate(todo, start=1):
        stem = _file_stem(sc, key)
        system = _template(ws, sc)
        title, material = build_material(ws, sc, key, library)
        print(f"[{i}/{len(todo)}] {title}")
        user = f"# {title}\n\n{material}"
        prompt_path = _dir(ws) / "промпты" / f"{stem}.md"
        guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
        if manual_reason:
            prompts.append(str(prompt_path))
            continue
        if done or failed:
            cancel.check(f"каркасы: перед «{title}»")
        try:
            raw = adapters.call_role(cfg, "аналитик", system, user, ws.logs, role="аналитик драматургии")
        except adapters.ManualModeNeeded as e:
            manual_reason = e.reason
            prompts.append(str(prompt_path))
            continue
        try:
            circle = llmjson.extract_json(raw, dict)
            circle.setdefault("title", title)
            to_model(circle)  # проверка формы ответа до записи черновика
            done.append(str(save_circle(ws, sc, key, circle)))
        except (ValueError, TypeError) as e:
            # битый ответ по одной цели: сырой ответ сохраняется, промпт остаётся для повтора, прогон продолжается (П-5)
            raw_path = _dir(ws) / "промпты" / f"{stem}.ответ_сырой.md"
            guard.write_text(raw_path, raw)
            failed.append(f"{title}: {e} (ответ сохранён: {raw_path.name})")
            prompts.append(str(prompt_path))
    return {"готово": done, "промпты": prompts, "ручной_режим": manual_reason, "сбои": failed}


def accept_manual(ws: Workspace, scope: str, key: int | None, raw: str) -> Path:
    circle = llmjson.extract_json(raw, dict)
    circle.setdefault("title", build_material(ws, scope, key)[0])
    return save_circle(ws, scope, key, circle)


def list_circles(ws: Workspace) -> list[dict]:
    """Черновики драматургия/*.json; битый файл (не JSON, не объект, шаги не список) пропускается с пометкой в журнале
    (`broken_drafts`), а не роняет окно/канон."""
    out = []
    for p in sorted(_dir(ws).glob("*.json")) if _dir(ws).exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("не объект")
            to_model(data)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        out.append(data)
    order = {"книга": 0, "акт": 1, "глава": 2}
    return sorted(out, key=lambda c: (order.get(c.get("scope"), 9), c.get("key") or 0))


# ------------------------------------------------------------ канон


def render_canon_doc(circles: list[StoryCircle], acts: list[Act], volume: int = 1, ws: Workspace | None = None) -> str:
    """Документ каркасов из актов и кругов — в разметке, которую читает экспорт (тип «каркасы»)."""
    m = methodic_for(ws, "глава") if ws is not None else _default_methodic()
    return dramaturgy_doc.render_doc(circles, acts, volume, method_name=m.title, heading=m.heading)


def canon_status(ws: Workspace) -> dict[str, str]:
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
    """Вносит черновики каркасов в документ каркасов библиотеки (только по подтверждению автора, FR-DR-4)."""
    new = drafts(ws)
    if not new:
        raise RuntimeError("черновиков каркасов нет — сначала постройте их (`konveyer каркас`).")
    merged = {(c.scope, c.key): c for c in canon_circles(ws)}
    for c in new:
        merged[(c.scope, c.key)] = c
    acts = act_list(ws)
    existing = exporter.docs_of_type(library, "каркасы", ws.volume, ws.root)
    path = existing[0] if existing else library / canon_doc_name(ws.volume, ws.root)
    text = render_canon_doc(list(merged.values()), acts, ws.volume, ws)
    message = f"[каркасы] внесено каркасов: {len(new)} (драматургия тома {ws.volume})"
    result = canonchange.canon_change(
        ws, cfg, library, lambda: guard.write_text(path, text), message,
        commit=True, author_confirmed=True, action="внесение каркасов",
    )
    if not existing:
        _register_in_manifest(ws, library, path)
    if result.commit:
        return path, result.commit
    if not gitops.is_repo(library):
        return path, "(библиотека не под git — коммит пропущен, настройте git!)"
    return path, "(изменений в каноне нет)"


def _register_in_manifest(ws: Workspace, library: Path, path: Path) -> None:
    """Новый документ каркасов — в карту библиотеки манифеста (если манифест есть на диске)."""
    man = manifest_mod.load(ws.root)
    if man is None:
        return
    rel = path.relative_to(library).as_posix()
    if man.entry_for(rel) is None:
        man.библиотека.append(manifest_mod.LibraryEntry(файл=rel, тип="каркасы", том=ws.volume))
        manifest_mod.save(ws.root, man)
