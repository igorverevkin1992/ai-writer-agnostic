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

import difflib
import json
import re
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
    pattern = spec.default_name if spec and spec.default_name else "Каркасы_Том{том}.md"
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


def _level_methodics(ws: Workspace) -> list[methodics.Methodic]:
    """Методики уровней глава → акт → том без повторов (одна и та же методика на нескольких уровнях — один раз);
    уровень с ошибкой манифеста пропускается (П-5)."""
    out: list[methodics.Methodic] = []
    for scope in ("глава", "акт", "книга"):
        try:
            m = methodic_for(ws, scope)
        except ValueError:
            continue
        if all(x.name != m.name for x in out):
            out.append(m)
    return out


def e2_text(ws: Workspace) -> str:
    """Текст проверки драматургии для Э2 — из `в_э2.md` методик уровней (глава, акт, том), каждая один раз."""
    return " ".join(t for m in _level_methodics(ws) if (t := m.e2_text.strip()))


def arcs_intro(ws: Workspace) -> str:
    """Вводный абзац секции арок окна — из `в_окно.j2` методики вида «арки», выбранной для уровня акта;
    иначе пусто (шаблон окна подставляет свой текст)."""
    try:
        m = methodic_for(ws, "акт")
    except ValueError:
        return ""
    if m.result_kind != "арки" or not m.window_template.strip():
        return ""
    return Environment().from_string(m.window_template).render(step_names=m.step_names()).strip()


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


def is_arcs_draft(data: dict) -> bool:
    return isinstance(data, dict) and "rows" in data and "steps" not in data


def arcs_from_answer(data: dict, act: int | None = None) -> list[Arc]:
    """Ответ методики вида «арки» ({"rows": [{character, act, lie, want, need, position, visible}]}) → строки арок;
    акт строки — из ответа либо ключ цели."""
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("в ответе поле «rows» должно быть списком строк арок")
    out: list[Arc] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"строка {i} ответа — не объект")
        name = str(row.get("character", "") or "").strip()
        if not name:
            raise ValueError(f"строка {i}: нет имени персонажа (character)")
        raw_act = row.get("act", act if act is not None else data.get("key"))
        try:
            act_n = int(raw_act)
        except (TypeError, ValueError):
            raise ValueError(f"строка {i} ({name}): акт «{raw_act}» — не число") from None
        out.append(Arc(character=name, act=act_n, **{k: str(row.get(k, "") or "").strip() for k in ("lie", "want", "need", "position", "visible")}))
    return out


def validate_draft(data: dict) -> None:
    """Черновик (ответ модели + scope/key) разбирается либо как шаги, либо как арки — иначе ValueError."""
    if is_arcs_draft(data):
        arcs_from_answer(data, data.get("key"))
    else:
        to_model(data)


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
            validate_draft(data)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            bad.append(f"{p.name}: {e}")
    return bad


def drafts(ws: Workspace) -> list[StoryCircle]:
    """Черновики каркасов из шагов (драматургия/*.json); черновики арок — `arc_drafts`."""
    return [to_model(c, _step_names_of(ws, str(c.get("scope", "глава")))) for c in list_circles(ws) if not is_arcs_draft(c)]


def arc_drafts(ws: Workspace) -> list[Arc]:
    """Черновики методики вида «арки»: строки персонаж × акт из драматургия/акт_N.json."""
    out: list[Arc] = []
    for c in list_circles(ws):
        if is_arcs_draft(c):
            out.extend(arcs_from_answer(c, c.get("key")))
    return out


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


# Секции материала аналитика (FR-DR-1 «требуемый материал», FR-DR-6): состав по умолчанию для уровня;
# методика может задать свой список ключом `материал:` (список или словарь уровень → список).
DEFAULT_MATERIAL: dict[str, list[str]] = {
    "том": ["тема", "акты", "части", "арки", "главы", "тайны"],
    "акт": ["каркас_выше", "акт", "главы", "арки", "тайны"],
    "глава": ["каркас_выше", "глава", "сцены", "биты", "знание", "закладки"],
}
MATERIAL_SECTIONS = ("тема", "акты", "части", "арки", "главы", "тайны", "каркас_выше", "акт", "глава", "сцены", "биты",
                     "знание", "закладки", "континуити", "хроника", "хронология")


class _Material:
    """Поставщики секций материала: каждая секция — список строк (пусто — секция не выводится)."""

    def __init__(self, ws: Workspace, scope: str, key: int | None):
        self.ws, self.scope, self.key = ws, scope, key
        ex = ws.exports
        self.briefs = exporter.load_briefs(ex)
        self.bans = exporter.load_infobans(ex)
        self.arcs = exporter.load_arcs(ex)
        self.act = next((a for a in act_list(ws) if a.act == key), None) if scope == "акт" else None
        if scope == "акт" and self.act is None:
            raise FileNotFoundError(f"акта {key} нет в таблице актов")
        self.brief = exporter.load_brief(ex, key) if scope == "глава" else None
        self.lo, self.hi = self._bounds()

    def _bounds(self) -> tuple[int | None, int | None]:
        if self.scope == "акт":
            return self.act.from_chapter, self.act.to_chapter
        if self.scope == "глава":
            return self.key, self.key
        return None, None

    def title(self) -> str:
        if self.scope == "книга":
            return "Книга (том целиком)"
        if self.scope == "акт":
            return f"Акт {self.act.act} «{self.act.title}»"
        return f"Глава {self.key}"

    def _secret_line(self, b) -> str:
        return f"- {b.text} (читатель узнаёт: {'гл. ' + str(b.until_chapter) if b.until_chapter else 'не в этом томе'})"

    # --- секции
    def тема(self) -> list[str]:
        theme = cycle_theme(self.ws)
        return ["## Тема серии", theme, ""] if theme else []

    def акты(self) -> list[str]:
        acts = act_list(self.ws)
        return ["## Акты тома (шаги каркаса тома должны ложиться на эти границы)",
                *[f"- Акт {a.act} «{a.title}» — гл. {a.from_chapter}–{a.to_chapter}" + (f": шаги {a.steps}" if a.steps else "") for a in acts],
                ""] if acts else []

    def части(self) -> list[str]:
        parts = exporter.load_parts(self.ws.exports)
        return ["## Части тома", *[f"- Часть {p['part']} «{p['title']}» — {p['period']} (гл. {p['from_chapter']}–{p['to_chapter']})" for p in parts],
                ""] if parts else []

    def арки(self) -> list[str]:
        if self.scope == "акт":
            rows = arcs_rows(self.arcs, self.act.act)
            return [f"## Арки акта {self.act.act} (ложь / желание / потребность — внутренний инструмент автора)", *rows, ""] if rows else []
        if self.scope == "глава":
            act = next((a for a in act_list(self.ws) if a.from_chapter <= self.key <= a.to_chapter), None)
            rows = arcs_rows(self.arcs, act.act) if act else []
            return [f"## Арки акта {act.act}", *rows, ""] if rows else []
        rows = arcs_rows(self.arcs)
        return ["## Арки тома (ложь / желание / потребность — внутренний инструмент автора)", *rows, ""] if rows else []

    def главы(self) -> list[str]:
        head = "## Главы тома (события плана с участниками сцен)" if self.scope == "книга" else "## Главы (события плана с участниками сцен)"
        rows = _chapter_rows(self.briefs, self.lo, self.hi)
        return [head, *rows, ""] if rows else []

    def тайны(self) -> list[str]:
        secrets = [b for b in self.bans if b.secret]
        if self.scope == "акт":
            secrets = [b for b in secrets if b.until_chapter and self.lo <= b.until_chapter <= self.hi]
            head = "## Тайны, раскрываемые читателю в этом акте"
        elif self.scope == "глава":
            secrets = [b for b in secrets if b.until_chapter == self.key]
            head = "## Тайны, раскрываемые читателю в этой главе"
        else:
            head = "## Реестр тайн (режим читателя)"
        return [head, *[self._secret_line(b) for b in secrets], ""] if secrets or self.scope == "книга" else []

    def каркас_выше(self) -> list[str]:
        outer = _outer_frame(self.ws, self.scope, self.key)
        return ["## Каркас уровня выше (каркас строится ВНУТРИ этих шагов)", *outer, ""] if outer else []

    def акт(self) -> list[str]:
        if self.act is None:
            return []
        head = [f"## Акт {self.act.act} «{self.act.title}» — гл. {self.lo}–{self.hi}" + (f" (части {self.act.parts})" if self.act.parts else "")]
        if self.act.steps:
            head.append(f"Шаги каркаса тома, за которые отвечает акт: {self.act.steps}. Каркас акта раскрывает именно их.")
        return head

    def глава(self) -> list[str]:
        return [f"## Глава {self.key} · {self.brief.date} · фокал {self.brief.focal}"] if self.brief else []

    def сцены(self) -> list[str]:
        return ["### Сцены", *[f"- {x}" for x in self.brief.scenes]] if self.brief else []

    def биты(self) -> list[str]:
        return ["### Биты", *[f"- {x}" for x in self.brief.beats]] if self.brief else []

    def знание(self) -> list[str]:
        if not self.brief:
            return []
        matrix = exporter.load_matrix(self.ws.exports)
        return ["### Что знает фокал", *[f"- [{f.fact_id}] {f.fact}" for f in matrix
                                         if f.subject == self.brief.focal and f.from_chapter is not None and f.from_chapter <= self.key]]

    def закладки(self) -> list[str]:
        if not self.brief:
            return []
        from . import compiler

        return ["### Закладки главы", *[f"- [{p.plant_id}] {p.what}" for p in compiler.chapter_plants(self.ws.exports, self.brief)]]

    def континуити(self) -> list[str]:
        try:
            events = exporter.load_continuity(self.ws.exports)
        except FileNotFoundError:
            return []
        rows = []
        for e in events:
            chs = [int(x) for x in re.findall(r"\d+", e.chapters or "")]
            if self.lo is not None and chs and not any(self.lo <= c <= self.hi for c in chs):
                continue
            rows.append(f"- {e.event}" + (f" ({e.date})" if e.date else ""))
        return ["## Континуити (закреплённые детали)", *rows, ""] if rows else []

    def хроника(self) -> list[str]:
        events = exporter.load_chronicle(self.ws.exports)
        return ["## Хроника эпохи", *[f"- {e.date}: {e.event}" for e in events], ""] if events else []

    def хронология(self) -> list[str]:
        events = [e for e in exporter.load_chronology(self.ws.exports) if not e.volume or e.volume == self.ws.volume]
        return ["## Хронология фабулы", *[f"- {e.event_id} · {e.date} · {e.event}" for e in events], ""] if events else []


def material_sections(ws: Workspace, scope: str) -> list[str]:
    """Состав материала для охвата: из методики уровня (`материал:`) либо по умолчанию."""
    level = LEVEL_OF.get(scope, scope)
    try:
        wanted = methodic_for(ws, scope).material_for(level)
    except ValueError:
        wanted = None
    wanted = wanted or DEFAULT_MATERIAL[level]
    unknown = [w for w in wanted if w not in MATERIAL_SECTIONS]
    if unknown:
        raise ValueError(f"методика запрашивает неизвестный материал {unknown}; доступно: {', '.join(MATERIAL_SECTIONS)}")
    return wanted


def build_material(ws: Workspace, scope: str, key: int | None = None, library: Path | None = None) -> tuple[str, str]:
    """(заголовок, материал) для каркаса: книга / акт N / глава N — из секций, которые запросила методика (FR-DR-1),
    иначе состав по умолчанию (FR-DR-6: тема серии, события с участниками, арки, реестры уровня)."""
    if scope not in LEVEL_OF:
        raise ValueError(f"неизвестный охват: {scope}")
    src = _Material(ws, scope, key)
    lines: list[str] = []
    for name in material_sections(ws, scope):
        lines.extend(getattr(src, name)())
    return src.title(), "\n".join(lines).rstrip("\n")


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
    if is_arcs_draft(circle):
        try:
            rows = arcs_from_answer(circle, circle.get("key"))
        except ValueError:
            rows = []
        lines += [*arcs_rows(rows), ""]
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
            validate_draft({**circle, "scope": sc, "key": key})  # проверка формы ответа до записи черновика
            done.append(str(save_circle(ws, sc, key, circle)))
        except (ValueError, TypeError) as e:
            # FR-AD-3, П-5: оплаченный, но битый (неразбираемый или не по форме) ответ по одной цели сохраняется
            # целиком в драматургия/ответы/ (не рядом с промптами: панель показывает промпты/ как список промптов),
            # промпт остаётся для повтора или ручного разбора, прогон продолжается
            raw_path = _dir(ws) / "ответы" / f"{stem}_сырой.md"
            guard.write_text(raw_path, raw)
            failed.append(f"{title}: {e} (ответ сохранён: {raw_path})")
            prompts.append(str(prompt_path))
    return {"готово": done, "промпты": prompts, "ручной_режим": manual_reason, "сбои": failed}


def accept_manual(ws: Workspace, scope: str, key: int | None, raw: str) -> Path:
    circle = llmjson.extract_json(raw, dict)
    circle.setdefault("title", build_material(ws, scope, key)[0])
    validate_draft({**circle, "scope": scope, "key": key})
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
            validate_draft(data)
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


def canon_arcs(ws: Workspace) -> list[Arc]:
    try:
        return exporter.load_arcs(ws.exports)
    except FileNotFoundError:
        return []


def _arc_key(a: Arc) -> tuple[str, int]:
    return a.character, a.act


def _arc_fields(a: Arc) -> tuple:
    """Поля строки арки для сравнения черновика с каноном: пустая ячейка и прочерк — одно и то же."""
    return tuple("" if v.strip() in ("", "—", "–", "-") else v.strip() for v in (a.lie, a.want, a.need, a.position, a.visible))


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
    arcs = {_arc_key(a): a for a in canon_arcs(ws)}
    for c in list_circles(ws):
        if not is_arcs_draft(c):
            continue
        rows = arcs_from_answer(c, c.get("key"))
        stem = _file_stem(str(c.get("scope", "акт")), c.get("key"))
        if not rows or all(_arc_key(r) not in arcs for r in rows):
            status[stem] = "не в каноне"
        elif all(_arc_key(r) in arcs and _arc_fields(arcs[_arc_key(r)]) == _arc_fields(r) for r in rows):
            status[stem] = "в каноне"
        else:
            status[stem] = "отличается от канона"
    return status


def arcs_doc_name(volume: int = 1, root: Path | None = None) -> str:
    """Имя документа арок тома — из каталога типов (`арки.имя_по_умолчанию`)."""
    spec = catalog.load_types(root).get("арки")
    pattern = spec.default_name if spec and spec.default_name else "Арки_Том{том}.md"
    return pattern.format(том=int(volume))


def _planned_writes(ws: Workspace, library: Path) -> tuple[list[tuple[Path, str, str]], list[StoryCircle], list[Arc]]:
    """Что внесение черновиков запишет в библиотеку, без записи: список (путь, текст, тип). Каркасы из шагов —
    в документ каркасов; строки арок (методика вида «арки») — в документ арок тома, где строка персонаж × акт
    заменяет прежнюю, остальные строки сохраняются. Возвращает (записи, черновики каркасов, черновики арок)."""
    new = drafts(ws)
    new_arcs = arc_drafts(ws)
    if not new and not new_arcs:
        raise RuntimeError("черновиков каркасов нет — сначала постройте их (`konveyer каркас`).")
    writes: list[tuple[Path, str, str]] = []
    if new:
        merged = {(c.scope, c.key): c for c in canon_circles(ws)}
        for c in new:
            merged[(c.scope, c.key)] = c
        existing = exporter.docs_of_type(library, "каркасы", ws.volume, ws.root)
        path = existing[0] if existing else library / canon_doc_name(ws.volume, ws.root)
        writes.append((path, render_canon_doc(list(merged.values()), act_list(ws), ws.volume, ws), "каркасы"))
    if new_arcs:
        rows = {_arc_key(a): a for a in canon_arcs(ws)}
        for a in new_arcs:
            rows[_arc_key(a)] = a
        existing = exporter.docs_of_type(library, "арки", ws.volume, ws.root)
        path = existing[0] if existing else library / arcs_doc_name(ws.volume, ws.root)
        try:
            method_name = methodic_for(ws, "акт").title
        except ValueError:
            method_name = "арки персонажей"
        writes.append((path, dramaturgy_doc.render_arcs_doc(list(rows.values()), ws.volume, method_name=method_name), "арки"))
    return writes, new, new_arcs


def _doc_diff(library: Path, path: Path, text: str) -> str:
    """Унифицированный дифф документа библиотеки с его будущим текстом (пустой канон — весь текст добавлением)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    rel = path.relative_to(library).as_posix() if path.is_relative_to(library) else path.name
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), text.splitlines(keepends=True),
                                        fromfile=f"{rel} (канон)", tofile=f"{rel} (после внесения)"))


def canon_preview(ws: Workspace, library: Path) -> dict:
    """Предпросмотр внесения черновиков в канон (FR-DR-4): будущие документы (каркасов и, при арках, арок тома),
    дифф с текущими и статусы черновиков — до подтверждения автора, без записи.
    {path, exists, text, diff, status, n, документы}: `path`/`exists`/`text` — первый документ (каркасов, если
    он есть), `diff` — по всем документам подряд, `n` — число черновиков (каркасов и арок),
    `документы` — [{path, exists, text, diff, тип}] по каждому документу."""
    writes, new, new_arcs = _planned_writes(ws, library)
    docs = [{"path": str(p), "exists": p.exists(), "text": text, "diff": _doc_diff(library, p, text), "тип": kind}
            for p, text, kind in writes]
    first = docs[0]
    return {"path": first["path"], "exists": first["exists"], "text": first["text"],
            "diff": "".join(d["diff"] for d in docs), "status": canon_status(ws),
            "n": len(new) + len(new_arcs), "документы": docs}


def commit_to_canon(ws: Workspace, cfg: Config, library: Path) -> tuple[Path, str]:
    """Вносит черновики в канон одной сменой канона (только по подтверждению автора, FR-DR-4): каркасы из шагов —
    в документ каркасов, строки арок (методика вида «арки») — в документ арок тома (то же, что показывает
    `canon_preview`). Возвращает (документ каркасов или арок, коммит)."""
    writes, new, new_arcs = _planned_writes(ws, library)
    parts = ([f"каркасов: {len(new)}"] if new else []) + ([f"арок: {len(new_arcs)}"] if new_arcs else [])
    message = f"[каркасы] внесено {', '.join(parts)} (драматургия тома {ws.volume})"

    def write_all() -> None:
        for path, text, _ in writes:
            guard.write_text(path, text)

    result = canonchange.canon_change(
        ws, cfg, library, write_all, message, commit=True, author_confirmed=True, action="внесение каркасов",
    )
    for path, _, kind in writes:
        _register_in_manifest(ws, library, path, kind)
    main_path = writes[0][0]
    if result.commit:
        return main_path, result.commit
    if not gitops.is_repo(library):
        return main_path, "(библиотека не под git — коммит пропущен, настройте git!)"
    return main_path, "(изменений в каноне нет)"


def _register_in_manifest(ws: Workspace, library: Path, path: Path, kind: str = "каркасы") -> None:
    """Новый документ каркасов/арок — в карту библиотеки манифеста (если манифест есть на диске)."""
    man = manifest_mod.load(ws.root)
    if man is None:
        return
    rel = path.relative_to(library).as_posix()
    if man.entry_for(rel) is None:
        man.библиотека.append(manifest_mod.LibraryEntry(файл=rel, тип=kind, том=ws.volume))
        manifest_mod.save(ws.root, man)
