"""Review: пакет приёмки автора (FR-RV-1), решения по флагам (FR-RV-2) и разбор правок (FR-RV-3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import guard, verifier2, writer
from .paths import Workspace
from .schemas import CheckResult, Edit, Flag, Resolution, Verdict

NOT_FOUND_NOTE = "цитата не найдена в тексте — проверьте вручную"
DECISIONS = ("принять", "вычеркнуть", "канонизировать", "отклонить")


def quote_found(text: str, quote: str) -> bool:
    """Цитата флага есть в тексте — с той же терпимостью к пробелам и переносам, что у применения правок."""
    return bool(writer.find_quote(text, quote))


def _anchor(text: str, quote: str, marker: str) -> str:
    """Якорь флага в тексте (FR-RV-1): маркер после первого вхождения цитаты (пробелы и переносы — не в счёт)."""
    hits = writer.find_quote(text, quote)
    if hits and marker not in text:
        end = hits[0][1]
        return text[:end] + marker + text[end:]
    return text


def build_review_pack(ws: Workspace, chapter: int, draft: int) -> Path:
    """FR-RV-1: текст с якорями флагов Э1/Э2 + форма правок + форма решений по самоволкам (приёмка.md);
    автономный HTML того же пакета строит `htmlreview.build_review_html`."""
    chdir = ws.chapter_dir(chapter)
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    raw = text

    verdict_path = chdir / "вердикт.json"
    checks: list[CheckResult] = []
    if verdict_path.exists():
        checks = Verdict.model_validate(json.loads(verdict_path.read_text(encoding="utf-8"))).flags
    flags = verifier2.load_flags(ws, chapter)

    # якоря в тексте: 【check_id】 для Э1, 【flag_id】 для Э2
    for c in checks:
        for q in c.quotes[:3]:
            text = _anchor(text, q, f"【{c.check_id}】")
    for f in flags:
        text = _anchor(text, f.quote, f"【{f.flag_id}】")

    lines = [f"# Приёмка · Глава {chapter} · черновик {draft}", ""]
    lines += ["## Флаги Э1 (формальные)", ""]
    if checks:
        for c in checks:
            lines.append(f"- **[{c.status}] {c.check_id}** — порог: {c.threshold}; факт: {c.actual} ({c.rule_source})")
            for q in c.quotes[:5]:
                lines.append(f"  > {q}")
    else:
        lines.append("- нет")
    lines += ["", "## Флаги Э2 (смысловые)", ""]
    violations = [f for f in flags if f.kind == "violation"]
    samovolki = [f for f in flags if f.kind == "samovolka"]
    def missing(f: Flag) -> str:
        return "" if not f.quote.strip() or quote_found(raw, f.quote) else f" ⚠ {NOT_FOUND_NOTE}"

    if violations:
        for f in violations:
            lines.append(f"- **[{f.severity}] {f.flag_id} · {f.type}** — {f.rule}; рекомендация: {f.recommendation}{missing(f)}")
            lines.append(f"  > {f.quote}")
    else:
        lines.append("- нет")
    lines += ["", "## Самоволки (требуют решения автора: «вычеркнуть» или «канонизировать»)", ""]
    if samovolki:
        for f in samovolki:
            lines.append(f"- **{f.flag_id}** — {f.rule}{missing(f)}")
            lines.append(f"  > {f.quote}")
    else:
        lines.append("- нет")
    taste = verifier2.load_taste(ws, chapter)
    if taste:
        lines += ["", "## Вкус (советы, не блокируют приёмку; 02 §6.1)", ""]
        for f in taste:
            lines.append(f"- **{f.flag_id}** — {f.rule}; {f.recommendation}{missing(f)}")
            lines.append(f"  > {f.quote}")
    lines += ["", "---", "", "## ТЕКСТ", "", text]
    guard.write_text(chdir / "приёмка.md", "\n".join(lines) + "\n")

    # форма правок
    if not (chdir / "правки.md").exists():
        guard.write_text(
            chdir / "правки.md",
            "\n".join(
                [
                    f"# Правки автора · Глава {chapter}",
                    "",
                    "Формат пары — две строки, маркеры в начале строки (значение может занимать несколько строк):",
                    "",
                    "```",
                    "БЫЛО: точная цитата из текста",
                    "СТАЛО: новая формулировка",
                    "```",
                    "",
                    "Пустое `СТАЛО:` — удалить цитату. Свободные указания — строками, начинающимися с `УКАЗАНИЕ:`.",
                    "Правки разделяйте пустой строкой.",
                    "",
                ]
            )
            + "\n",
        )

    # форма решений (FR-RV-2): пересобирается по ТЕКУЩЕМУ флаги.json при каждом review —
    # решения по флагам, которые остались, сохраняются; исчезнувшие флаги
    # («фантомные самоволки» прошлого прогона Э2) не блокируют приёмку
    rebuild_resolutions(ws, chapter, samovolki, flags)
    return chdir / "приёмка.md"


SECOND_PASS_HEADER = "## Повторный Э2 после правок"


def append_second_pass(ws: Workspace, chapter: int, draft: int, flags: list[Flag]) -> Path | None:
    """Раздел «Повторный Э2 после правок» в приёмка.md (совещательно, приёмку не блокирует);
    прежний раздел заменяется. Без приёмка.md — ничего не пишет."""
    path = ws.chapter_dir(chapter) / "приёмка.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    lines = [SECOND_PASS_HEADER, "", f"Черновик {draft}; совещательно — флаги.json и решения по самоволкам не меняет.", ""]
    if flags:
        for f in flags:
            kind = "самоволка" if f.kind == "samovolka" else f.severity
            lines.append(f"- **[{kind}] {f.flag_id} · {f.type}** — {f.rule}; рекомендация: {f.recommendation}")
            lines.append(f"  > {f.quote}")
    else:
        lines.append("- флагов нет")
    section = "\n".join(lines) + "\n"
    if SECOND_PASS_HEADER in text:
        head, _, tail = text.partition(SECOND_PASS_HEADER)
        # хвост — до следующего раздела верхнего уровня (или до «---» перед текстом)
        m = re.search(r"\n(?=## |---\n)", tail)
        text = head + section + (tail[m.start() + 1:] if m else "")
    else:
        marker = "\n---\n"
        if marker in text:
            head, _, tail = text.partition(marker)
            text = head + "\n" + section + marker + tail
        else:
            text = text.rstrip("\n") + "\n\n" + section
    guard.write_text(path, text)
    return path


def rebuild_resolutions(ws: Workspace, chapter: int, samovolki: list[Flag], flags: list[Flag] | None = None) -> list[Resolution]:
    """Форма решений: запись на каждую самоволку (пустая — «без решения») плюс уже принятые решения
    по остальным флагам текущего флаги.json («отклонить»/«принять» по нарушению не пропадают)."""
    res_path = ws.chapter_dir(chapter) / "решения.json"
    existing: dict[str, Resolution] = {}
    if res_path.exists():
        existing = {r.flag_id: r for r in load_resolutions(ws, chapter)}
    merged = [existing.get(f.flag_id, Resolution(flag_id=f.flag_id)) for f in samovolki]
    sam_ids = {f.flag_id for f in samovolki}
    for f in flags or []:
        if f.flag_id not in sam_ids and f.flag_id in existing and existing[f.flag_id].decision:
            merged.append(existing[f.flag_id])
    save_resolutions(ws, chapter, merged)
    return merged


def decide(ws: Workspace, chapter: int, flag_id: str, decision: str, registry: str | None = None,
           reason: str = "") -> list[Resolution]:
    """Решение автора по флагу (FR-RV-2) — общий путь CLI и панели: самоволку — вычеркнуть или канонизировать
    (с реестром), любой флаг — отклонить с причиной (в журнал) или принять рекомендацию (указание в правки.md).
    Возвращает решения главы; ValueError — недопустимое решение или нет такого флага."""
    reason = reason.strip()
    if decision not in DECISIONS:
        raise ValueError("решение должно быть «принять», «вычеркнуть», «канонизировать» или «отклонить» (с --причина).")
    if decision == "отклонить" and not reason:
        raise ValueError("отклонение флага требует причины: --причина «…» (FR-RV-2).")
    flags = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
    resolutions = load_resolutions(ws, chapter)
    flag = flags.get(flag_id)
    if flag is None and flag_id not in {r.flag_id for r in resolutions}:
        raise ValueError(f"флаг {flag_id} не найден (см. `konveyer resolve {chapter}`).")
    is_samovolka = (flag.kind == "samovolka") if flag else True
    if decision in ("вычеркнуть", "канонизировать") and not is_samovolka:
        raise ValueError(f"{flag_id} — не самоволка: нарушение можно принять (рекомендацию) или отклонить с причиной.")
    if decision == "принять" and is_samovolka:
        raise ValueError(f"{flag_id} — самоволка: её можно только вычеркнуть или канонизировать (FR-RV-2).")
    if flag_id not in {r.flag_id for r in resolutions}:
        resolutions.append(Resolution(flag_id=flag_id))
    for r in resolutions:
        if r.flag_id != flag_id:
            continue
        r.decision = decision  # type: ignore[assignment]
        r.target_registry = registry if decision == "канонизировать" else None
        r.reason = reason if decision == "отклонить" else ""
    save_resolutions(ws, chapter, resolutions)
    if decision == "отклонить":
        log_rejected(ws, chapter, flag, flag_id, reason)
    elif decision == "принять" and flag is not None:
        _accept_recommendation(ws, chapter, flag)
    return resolutions


def _accept_recommendation(ws: Workspace, chapter: int, flag: Flag) -> None:
    """«Принять правку»: рекомендация флага — указанием в правки.md (один раз на флаг)."""
    path = ws.chapter_dir(chapter) / "правки.md"
    text = path.read_text(encoding="utf-8") if path.exists() else f"# Правки автора · Глава {chapter}\n\n"
    marker = f"(по флагу {flag.flag_id})"
    if marker in text:
        return
    what = flag.recommendation.strip() or flag.rule.strip()
    quote = f" — цитата: «{' '.join(flag.quote.split())}»" if flag.quote.strip() else ""
    guard.write_text(path, text.rstrip("\n") + f"\n\nУКАЗАНИЕ: {what}{quote} {marker}\n")


# ------------------------------------------------------------ разбор правки.md


class EditsFormatError(ValueError):
    """Ошибка формата правки.md с номером строки (FR-RV-3)."""

    def __init__(self, path: Path, line: int, message: str):
        super().__init__(f"{path.name}:{line}: {message}")
        self.path = path
        self.line = line


_MARKER_RE = re.compile(r"^\s*(?P<kind>БЫЛО|СТАЛО|УКАЗАНИЕ)\s*:\s?(?P<rest>.*)$", re.IGNORECASE)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def parse_edits_text(text: str, chapter: int, path: Path | None = None) -> list[Edit]:
    """Построчный автомат (FR-RV-3): маркеры `БЫЛО:` / `СТАЛО:` / `УКАЗАНИЕ:` в начале строки (регистр
    не важен: «Было:» тоже маркер); значение — до следующего маркера или пустой строки (многострочные
    цитаты допустимы); пустое «СТАЛО:» = удаление цитаты, правка на этом закончена — следующая строка
    в неё не втягивается; «БЫЛО» без «СТАЛО» — ошибка с номером строки;
    «УКАЗАНИЕ» сразу после пары — отдельная правка, а не хвост «СТАЛО».
    """
    src = path or Path("правки.md")
    # примеры формата в ограждённых код-блоках — не правки; номера строк сохраняем
    text = _FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    edits: list[Edit] = []
    state = "idle"  # idle | before | before_done | after | note
    before_lines: list[str] = []
    value: list[str] = []
    before_line = 0

    def emit_pair() -> None:
        edits.append(Edit(chapter=chapter, seq=len(edits) + 1, before="\n".join(before_lines).strip(),
                          after="\n".join(value).strip()))

    def emit_note() -> None:
        note = "\n".join(value).strip()
        if note:
            edits.append(Edit(chapter=chapter, seq=len(edits) + 1, before="", after=note, note="свободное указание"))

    def flush() -> None:
        nonlocal state
        if state == "after":
            emit_pair()
        elif state == "note":
            emit_note()
        state = "idle"

    def require_after(n: int, what: str) -> None:
        if state in ("before", "before_done"):
            raise EditsFormatError(src, n, f"{what}: у «БЫЛО:» (строка {before_line}) нет своего «СТАЛО:»")

    for n, line in enumerate(text.splitlines(), start=1):
        m = _MARKER_RE.match(line)
        if m:
            kind, rest = m.group("kind").upper(), m.group("rest")
            if kind == "БЫЛО":
                require_after(n, f"строка {n}: новое «БЫЛО:»")
                flush()
                if not rest.strip():
                    raise EditsFormatError(src, n, "«БЫЛО:» пустое — нужна точная цитата из черновика")
                before_lines, before_line, state = [rest], n, "before"
            elif kind == "СТАЛО":
                if state not in ("before", "before_done"):
                    raise EditsFormatError(src, n, "«СТАЛО:» без предшествующего «БЫЛО:»")
                value, state = [rest], "after"
                if not rest.strip():
                    flush()  # пустое «СТАЛО:» — удаление: правка завершена, пояснение ниже — не замена
            else:  # УКАЗАНИЕ
                require_after(n, f"строка {n}: «УКАЗАНИЕ:»")
                flush()
                value, state = [rest], "note"
            continue
        if not line.strip():
            if state == "before":
                state = "before_done"
            elif state in ("after", "note"):
                flush()
            continue
        if state == "before":
            before_lines.append(line)
        elif state in ("after", "note"):
            value.append(line)
        elif state == "before_done":
            raise EditsFormatError(src, n, f"у «БЫЛО:» (строка {before_line}) нет своего «СТАЛО:»")
        # idle: свободный текст (заголовки, пояснения) — не правка
    if state in ("before", "before_done"):
        raise EditsFormatError(src, before_line, "у «БЫЛО:» нет своего «СТАЛО:» до конца файла")
    flush()
    return edits


def parse_edits_md(ws: Workspace, chapter: int) -> list[Edit]:
    """FR-RV-3: правки.md (пары «было → стало» и/или свободные указания) → правки.jsonl."""
    path = ws.chapter_dir(chapter) / "правки.md"
    if not path.exists():
        raise FileNotFoundError(f"Нет файла правок {ws.chapter_rel(chapter)}/правки.md. Сначала `konveyer review {chapter}`.")
    edits = parse_edits_text(path.read_text(encoding="utf-8"), chapter, path)
    save_edits(ws, chapter, edits)
    return edits


def save_edits(ws: Workspace, chapter: int, edits: list[Edit]) -> None:
    guard.write_text(
        ws.chapter_dir(chapter) / "правки.jsonl",
        "".join(json.dumps(e.model_dump(by_alias=True), ensure_ascii=False) + "\n" for e in edits),
    )


def load_edits(ws: Workspace, chapter: int) -> list[Edit]:
    path = ws.chapter_dir(chapter) / "правки.jsonl"
    if not path.exists():
        return []
    return [
        Edit.model_validate(json.loads(ln))
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def save_resolutions(ws: Workspace, chapter: int, resolutions: list[Resolution]) -> None:
    guard.write_text(
        ws.chapter_dir(chapter) / "решения.json",
        json.dumps([r.model_dump() for r in resolutions], ensure_ascii=False, indent=2) + "\n",
    )


def load_resolutions(ws: Workspace, chapter: int) -> list[Resolution]:
    path = ws.chapter_dir(chapter) / "решения.json"
    if not path.exists():
        return []
    return [Resolution.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def unresolved_samovolki(ws: Workspace, chapter: int) -> list[str]:
    """Самоволки без решения: записи решения.json с пустым decision плюс самоволки из флаги.json, у которых
    записи вообще нет (флаги.json дополнен после review — руками или ручным режимом Э2)."""
    resolutions = {r.flag_id: r for r in load_resolutions(ws, chapter)}
    out = [fid for fid, r in resolutions.items() if r.decision is None]
    for f in verifier2.load_flags(ws, chapter):
        if f.kind == "samovolka" and f.flag_id not in resolutions:
            out.append(f.flag_id)
    return out


# ------------------------------------------------------------ журнал отклонённых флагов (FR-RV-2)


REJECTED_LOG = "отклонённые_флаги.jsonl"


def log_rejected(ws: Workspace, chapter: int, flag: Flag | None, flag_id: str, reason: str) -> None:
    """Отклонённый флаг — в журнал для настройки промптов (FR-RV-2)."""
    from datetime import datetime, timezone

    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "chapter": chapter, "flag_id": flag_id,
             "reason": reason, "type": flag.type if flag else None, "kind": flag.kind if flag else None,
             "rule": flag.rule if flag else None, "quote": flag.quote if flag else None}
    guard.append_text(ws.logs / REJECTED_LOG, json.dumps(entry, ensure_ascii=False) + "\n")
