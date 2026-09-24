"""Документ каркасов драматургии: формат, который конвейер сам пишет и читает (тип «каркасы»).

Разметка: таблица «## Акты тома» (`| Акт | Название | Главы | Части | Шаги |`), заголовки «## Круг тома» /
«## Круг акта N …» / «## Круг главы N» (слово «Круг» может быть «Каркас» — для методик без круга), внутри —
строка `**Суть:**`, нумерованные шаги `N. **Имя** (гл. A–B) — текст`, строка `**Слабое место:**`.
Имена шагов задаёт методика проекта (FR-DR-1); движок их не знает.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import mdparse
from .names import chapter_range
from .schemas import Act, CircleStep, StoryCircle

HEAD_RE = re.compile(r"^##\s*(?:Круг|Каркас)\s+(тома|акта\s+([IVX\d]+)|главы\s+(\d+))\b(.*)$", re.MULTILINE)
STEP_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(?:\(([^)]*)\))?\s*(?:[—–-]+\s*)?(.*)$")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
ROMAN_LIST = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
STEP_UNSET = "в материале не задано"
_UNSET_RE = re.compile(r"^\s*[(\[]?\s*в материале не задан[оа]?\s*[)\]]?\s*\.?\s*$", re.IGNORECASE)


def step_unset(step: CircleStep) -> bool:
    return not step.text.strip() or bool(_UNSET_RE.match(step.text))


def parse_frames(path: Path) -> list[StoryCircle]:
    """Каркасы документа: том / акты / главы с шагами."""
    text = mdparse.read_text(path)
    heads = list(HEAD_RE.finditer(text))
    circles: list[StoryCircle] = []
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        kind = h.group(1)
        if kind == "тома":
            scope, key, title = "книга", None, "Книга (том целиком)"
        elif kind.startswith("акта"):
            num = h.group(2)
            key = ROMAN.get(num, int(num) if num.isdigit() else 0)
            tm = re.search(r"«([^»]+)»", h.group(4) or "")
            scope, title = "акт", f"Акт {key}" + (f" «{tm.group(1)}»" if tm else "")
        else:
            scope, key = "глава", int(h.group(3))
            title = f"Глава {key}"
        circle = StoryCircle(scope=scope, key=key, title=title)
        current: CircleStep | None = None
        for line in body.splitlines():
            stripped = line.strip()
            sm = STEP_RE.match(stripped)
            if sm:
                lo, hi = chapter_range(sm.group(3) or "")
                current = CircleStep(
                    n=int(sm.group(1)), name=sm.group(2).strip(), chapters=(sm.group(3) or "").strip(),
                    text=sm.group(4).strip(), from_chapter=lo, to_chapter=hi,
                )
                circle.steps.append(current)
                continue
            if stripped.startswith("**Суть:**"):
                circle.summary = stripped[len("**Суть:**"):].strip(); current = None
            elif stripped.startswith("**Слабое место:**"):
                circle.weak_spot = stripped[len("**Слабое место:**"):].strip(); current = None
            elif stripped and current is not None and not stripped.startswith("#"):
                current.text = (current.text + " " + stripped).strip()
        if circle.steps:
            circles.append(circle)
    return circles


def acts_from_rows(rows: list[dict]) -> list[Act]:
    """Строки таблицы актов (chapters_text «1–9») → Act с границами глав; строки без глав пропускаются."""
    acts: list[Act] = []
    for r in rows:
        text = str(r.get("chapters_text") or "")
        nums = [int(x) for x in re.findall(r"\d+", text)]
        if not nums or r.get("act") is None:
            continue
        acts.append(Act(act=int(r["act"]), title=str(r.get("title") or "").strip("«» "), from_chapter=min(nums),
                        to_chapter=max(nums), parts=str(r.get("parts") or ""), steps=str(r.get("steps") or ""),
                        chapters_text=text))
    return sorted(acts, key=lambda a: a.act)


def render_doc(circles: list[StoryCircle], acts: list[Act], volume: int, *, method_name: str = "круг истории",
               heading: str = "Круг", intro: str = "") -> str:
    """Документ каркасов из актов и кругов — в разметке, которую читает `parse_frames`."""
    order = {"книга": 0, "акт": 1, "глава": 2}
    lines = [
        f"# Каркасы драматургии — Том {volume}",
        f"## Методика: {method_name}",
        "",
        intro or ("Документ генерируется конвейером из черновиков `драматургия/` по подтверждению автора и правится "
                  "автором как любой документ канона. Что читает машина: таблица «Акты тома»; заголовки "
                  f"«## {heading} тома» / «## {heading} акта N …» / «## {heading} главы N», строка «**Суть:**», "
                  "нумерованные шаги «N. **Имя** (гл. A–B) — текст», строка «**Слабое место:**». "
                  "Диапазоны глав шагов тома и актов — единственная привязка главы к её шагу."),
        "",
        "## Акты тома",
        "",
        "| Акт | Название | Главы | Части | Шаги |",
        "|---|---|---|---|---|",
    ]
    for a in sorted(acts, key=lambda a: a.act):
        lines.append(f"| {a.act} | «{a.title}» | {a.from_chapter}–{a.to_chapter} | {a.parts} | {a.steps} |")
    lines.append("")
    for c in sorted(circles, key=lambda c: (order.get(c.scope, 9), c.key or 0)):
        if c.scope == "книга":
            lines.append(f"## {heading} тома")
        elif c.scope == "акт":
            act = next((a for a in acts if a.act == c.key), None)
            tail = f" «{act.title}» (гл. {act.from_chapter}–{act.to_chapter})" if act else ""
            lines.append(f"## {heading} акта {c.key}{tail}")
        else:
            lines.append(f"## {heading} главы {c.key}")
        if c.summary:
            lines.append(f"**Суть:** {c.summary}")
        for st in c.steps:
            where = f" ({st.chapters})" if st.chapters else ""
            text = STEP_UNSET if step_unset(st) else st.text
            lines.append(f"{st.n}. **{st.name}**{where} — {text}")
        if c.weak_spot:
            lines.append(f"**Слабое место:** {c.weak_spot}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
