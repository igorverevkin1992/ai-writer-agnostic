"""Линтер канона: противоречия и ошибки логики повествования в библиотеке.

Машинный слой — детерминированные проверки по выгрузкам и документам: хронология брифов,
границы частей/актов, допустимость фокала, эпистемика брифов и принятой прозы (тайны,
которых фокал не знает), реестр тайн ↔ матрица 3.1, диапазоны глав в матрице/закладках/
континуити, возраст в досье, ссылки на неизвестные имена, участники сцен без карточки досье и
карточки без «Физики», круги истории, маркеры тайн.
Каждая находка — файл, строка, объяснение; где правка механическая — предложение
исправления (LintFix), которое автор применяет одним действием. Модельный слой
(`run_lint_llm`) ищет смысловые противоречия, которые машине не видны.

Линтер ничего не пишет в библиотеку: исправления применяет автор (`apply_fix`, только
внутри canon_write_session по подтверждению — FR-K3).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import adapters, exporter, guard, lint_canon, lint_epist, llmjson, realcanon, textutils, verifier1
from .config import Config
from .mdparse import MarkupError
from .paths import Workspace
from .schemas import Brief, InfoBan, LintFinding, LintFix, LintReport, MatrixFact

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
DATE_NUM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")
DATE_WORD_RE = re.compile(r"\b(\d{1,2})\s+([а-яё]+)", re.IGNORECASE)
FUTURE_REF_RE = re.compile(r"\bт\.\s*(\d+)")


# ------------------------------------------------------------------ утилиты


def _rel(library: Path, path: Path) -> str:
    try:
        return str(path.relative_to(library)).replace("\\", "/")
    except ValueError:
        return str(path)


def marker_hit(text: str, markers: list[str]) -> str | None:
    """Маркер тайны в тексте по границам слова и основе (как стоп-лексика Э1), а не подстрокой:
    «сынок» ≠ «сын», «активно» ≠ «актив» (аудит 2, находка 3.9). Обороты из нескольких слов —
    дословно. Возвращает найденный маркер или None."""
    low = text.lower().replace("ё", "е")
    for m in markers:
        if not m:
            continue
        if verifier1.item_pattern(m).search(low):
            return m
    return None


_LINES_CACHE: dict[tuple[Path, int, int], list[str]] = {}


def _lines_cache(path: Path) -> list[str]:
    """Строки документа: линтер обращается к одному файлу десятки раз (находки, _find_line).
    Ключ включает время изменения и размер — наблюдатель перепроверяет канон после правок, и устаревший
    кэш давал бы находки по старому содержимому (размер — страховка от грубого таймера Windows:
    две записи подряд получают одно mtime)."""
    try:
        st = path.stat()
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if key not in _LINES_CACHE:
        try:
            _LINES_CACHE[key] = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            _LINES_CACHE[key] = []
        if len(_LINES_CACHE) > 500:  # прогон линтера — десятки документов; страховка от роста
            for old_key in list(_LINES_CACHE)[:250]:
                _LINES_CACHE.pop(old_key, None)
    return _LINES_CACHE[key]


def _find_line(path: Path, needle: str, start: int = 0) -> int | None:
    """Номер строки (1-based) первого вхождения фрагмента в файле."""
    if not needle:
        return None
    lines = _lines_cache(path)
    for i, line in enumerate(lines[start:], start=start + 1):
        if needle in line:
            return i
    return None


def _library_docs(library: Path) -> list[Path]:
    """Документы канона: все .md, кроме инструментальных и текстов отбора."""
    docs = []
    for p in sorted(library.rglob("*.md")):
        rel = _rel(library, p)
        if rel.startswith(("ИНСТРУМЕНТ_", "ТЗ_", "Тест_Писателя/")):
            continue
        docs.append(p)
    return docs


def parse_date(text: str) -> tuple[int, int] | None:
    """«12.04», «ночь 18.04», «12 июня 1995» → (месяц, день); «та же ночь» → None (наследует)."""
    m = DATE_NUM_RE.search(text)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = DATE_WORD_RE.search(text)
    if m:
        mon = MONTHS.get(m.group(2).lower()[:3])
        if mon:
            return mon, int(m.group(1))
    return None


# ------------------------------------------------------------------ проверки


def check_chronology(briefs: list[Brief], reg_path: Path | None) -> list[LintFinding]:
    out: list[LintFinding] = []
    last: tuple[int, int] | None = None
    last_ch = None
    for b in sorted(briefs, key=lambda b: b.chapter):
        d = parse_date(b.date)
        if d is None:
            continue
        mon, day = d
        if not (1 <= mon <= 12 and 1 <= day <= 31):
            out.append(LintFinding(
                code="ХРОН-1", severity="ошибка", file=_rel_or("", reg_path), line=_find_line(reg_path, b.date) if reg_path else None,
                message=f"гл. {b.chapter}: дата «{b.date}» вне календаря",
            ))
            continue
        if last is not None and d < last and not (last[0] == 12 and mon == 1):
            # стык года без явного года («30 декабря» → «2 января») — не нарушение
            out.append(LintFinding(
                code="ХРОН-2", severity="ошибка", file=_rel_or("", reg_path),
                line=_find_line(reg_path, f"| {b.chapter} |") if reg_path else None,
                message=f"гл. {b.chapter} датирована «{b.date}» — раньше гл. {last_ch} ({last[1]:02d}.{last[0]:02d}); "
                        "порядок глав нарушает хронологию тома",
            ))
        last, last_ch = d, b.chapter
    return out


def _rel_or(default: str, path: Path | None) -> str:
    return default if path is None else path.name


def check_ranges(parts: list[dict], acts, briefs: list[Brief], reg_path: Path | None, acts_path: Path | None) -> list[LintFinding]:
    """Части и акты покрывают главы тома без разрывов и наложений."""
    out: list[LintFinding] = []
    if not briefs:
        return out
    lo, hi = min(b.chapter for b in briefs), max(b.chapter for b in briefs)

    def contiguous(label: str, spans: list[tuple[int, int, str]], path: Path | None, code: str) -> None:
        expect = lo
        for a, z, name in sorted(spans):
            if a != expect:
                out.append(LintFinding(
                    code=code, severity="ошибка", file=_rel_or("", path), line=_find_line(path, name) if path else None,
                    message=f"{label} «{name}» начинается с гл. {a}, ожидалась гл. {expect} (разрыв или наложение)",
                ))
            expect = z + 1
        if spans and expect - 1 != hi:
            out.append(LintFinding(
                code=code, severity="предупреждение", file=_rel_or("", path), line=None,
                message=f"{label}: покрыты главы до {expect - 1}, а в томе {hi}",
            ))

    contiguous("часть", [(p["from_chapter"], p["to_chapter"], p["title"]) for p in parts], reg_path, "ЧАСТЬ-1")
    contiguous("акт", [(a.from_chapter, a.to_chapter, a.title) for a in acts], acts_path, "АКТ-1")
    return out


FOCAL_RE = re.compile(r"Фокал[а-я]*\s*[:：]?\s*([^\n·.]*)", re.IGNORECASE)
VOL_RANGE_RE = re.compile(r"т\.\s*(\d+)\s*(?:[–-]\s*(\d+))?")


def parse_focal_volumes(paths: list[Path], known_names: set[str]) -> dict[str, tuple[Path, set[int] | None]]:
    """Из «## Статус…» карточки: {имя: (файл, тома, где персонаж может быть фокалом)}; None = не разобрано."""
    result: dict[str, tuple[Path, set[int] | None]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        heads = list(realcanon.DOSSIER_HEAD_RE.finditer(text))
        for i, head in enumerate(heads):
            body = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            title = re.sub(r"\(.*?\)", "", head.group(1))
            found = [(m.start(), n) for n in known_names if (m := re.search(n, title, re.IGNORECASE))]
            if not found:
                continue
            name = min(found)[1]
            sm = re.search(r"^##\s*Статус[^\n]*\n?(.*?)(?=^##\s|\Z)", body, re.M | re.S)
            status = (sm.group(0) if sm else "")
            fm = FOCAL_RE.search(status)
            if not fm or "⚠" in status:
                result[name] = (path, None)
                continue
            seg = fm.group(1)
            if re.search(r"не имеет|без фокала|нет", seg, re.IGNORECASE) and not VOL_RANGE_RE.search(seg):
                result[name] = (path, set())
                continue
            vols: set[int] = set()
            if re.search(r"\bс\s+т\.", seg):
                m = VOL_RANGE_RE.search(seg)
                if m:
                    vols.update(range(int(m.group(1)), 100))
            for m in VOL_RANGE_RE.finditer(seg):
                a = int(m.group(1)); z = int(m.group(2) or a)
                vols.update(range(a, z + 1))
            result[name] = (path, vols if vols else None)
    return result


def check_focals(briefs: list[Brief], focal_vols: dict[str, tuple[Path, set[int] | None]], library: Path,
                 dossier_names: set[str], reg_path: Path | None) -> list[LintFinding]:
    out: list[LintFinding] = []
    for b in sorted(briefs, key=lambda b: b.chapter):
        if not b.focal:
            continue
        if b.focal not in dossier_names:
            out.append(LintFinding(
                code="ФОКАЛ-1", severity="предупреждение", file=_rel_or("", reg_path),
                line=_find_line(reg_path, f"| {b.chapter} |") if reg_path else None,
                message=f"гл. {b.chapter}: фокал «{b.focal}» без досье 1.3 — окно не получит его профиль",
            ))
            continue
        entry = focal_vols.get(b.focal)
        if entry and entry[1] is not None and b.volume not in entry[1]:
            out.append(LintFinding(
                code="ФОКАЛ-2", severity="ошибка", file=_rel_or("", reg_path),
                line=_find_line(reg_path, f"| {b.chapter} |") if reg_path else None,
                message=f"гл. {b.chapter}: фокал «{b.focal}», но по досье ({_rel(library, entry[0])}) он не фокален в т.{b.volume} "
                        f"(разрешено: {', '.join('т.' + str(v) for v in sorted(entry[1])) or 'нигде'})",
            ))
    return out


def check_brief_epistemics(briefs: list[Brief], infobans: list[InfoBan], reg_path: Path | None) -> list[LintFinding]:
    """Бриф главы описывает событие словами тайны, которой фокал в этой главе ещё не знает."""
    out: list[LintFinding] = []
    for b in briefs:
        text = " ".join([*b.scenes, *b.beats])
        # реплики персонажей в брифе («Бугаев: «молодец, сынок»») — не знание фокала
        text = textutils.narration_only(text) or text
        for ban in infobans:
            if not ban.secret or ban.known_to(b.focal, b.chapter) or not ban.markers:
                continue
            hit = marker_hit(text, ban.markers)
            if hit:
                out.append(LintFinding(
                    code="ЭПИСТ-1", severity="предупреждение", file=_rel_or("", reg_path),
                    line=_find_line(reg_path, f"| {b.chapter} |") if reg_path else None,
                    message=f"гл. {b.chapter} (фокал {b.focal}): бриф содержит «{hit}» — маркер тайны {ban.ban_id}, "
                            f"которую {b.focal} к этой главе не знает; проверьте, не требует ли бриф от фокала чужого знания",
                ))
    return out


def check_secrets_vs_matrix(infobans: list[InfoBan], matrix: list[MatrixFact], reg_path: Path | None) -> list[LintFinding]:
    out: list[LintFinding] = []
    if reg_path is None:
        return out
    text = reg_path.read_text(encoding="utf-8")
    for ban in infobans:
        if not ban.secret:
            continue
        line = _find_line(reg_path, ban.text[:40])
        row = text.splitlines()[line - 1] if line else ""
        # явная «гл. N» в реестре против «Читатель» матрицы
        cells = [c.strip() for c in row.strip("|").split("|")] if row else []
        if len(cells) >= 2:
            explicit = realcanon.reveal_chapter(cells[1])
            fid = realcanon._match_matrix_fact(ban.text, matrix)
            if fid and explicit is not None:
                reader = next((f.from_chapter for f in matrix if f.fact_id == fid and f.subject == "Читатель"), None)
                if reader is not None and reader != explicit:
                    out.append(LintFinding(
                        code="ТАЙНА-1", severity="предупреждение", file=reg_path.name, line=line,
                        message=f"{ban.ban_id}: реестр говорит «читатель узнаёт в гл. {explicit}», матрица 3.1 ({fid}, «Читатель») — гл. {reader}",
                    ))
            if fid and len(cells) >= 3:
                for name, ch in ban.known_by.items():
                    mfact = next((f for f in matrix if f.fact_id == fid and f.subject == name), None)
                    if mfact and mfact.from_chapter is not None and not mfact.note.startswith("частично"):
                        reg_explicit = re.search(rf"{re.escape(name)}[^;,]*гл\.?\s*(\d+)", cells[2])
                        if reg_explicit and int(reg_explicit.group(1)) != mfact.from_chapter:
                            out.append(LintFinding(
                                code="ТАЙНА-2", severity="предупреждение", file=reg_path.name, line=line,
                                message=f"{ban.ban_id}: по реестру {name} узнаёт в гл. {reg_explicit.group(1)}, по матрице ({fid}) — в гл. {mfact.from_chapter}",
                            ))
        if not ban.markers:
            out.append(LintFinding(
                code="ТАЙНА-3", severity="заметка", file=reg_path.name, line=line,
                message=f"{ban.ban_id}: нет строки в таблице «Маркеры тайн для фильтра окна» — досье не будут очищаться от этой тайны",
            ))
    return out


def check_chapter_refs(matrix: list[MatrixFact], plants, continuity, briefs: list[Brief], library: Path) -> list[LintFinding]:
    out: list[LintFinding] = []
    if not briefs:
        return out
    volume = briefs[0].volume
    hi = max(b.chapter for b in briefs if b.volume == volume)
    mpath = next(iter(exporter.volume_docs(library, "31_*.md", volume)), None)
    for f in matrix:
        if f.from_chapter is not None and f.from_chapter > hi:
            out.append(LintFinding(
                code="МАТР-1", severity="ошибка", file=_rel_or("", mpath), line=_find_line(mpath, f.fact[:30]) if mpath else None,
                message=f"{f.fact_id} ({f.subject}): узнаёт в гл. {f.from_chapter}, а в томе {hi} глав",
            ))
    reg = exporter._registry(library, volume) or next(iter(exporter.volume_docs(library, "32_*.md", volume)), None)
    for p in plants:
        for ch in p.chapters:
            if ch > hi:
                out.append(LintFinding(
                    code="ЗАКЛ-1", severity="ошибка", file=_rel_or("", reg), line=_find_line(reg, p.what[:30]) if reg else None,
                    message=f"{p.plant_id}: закладка в гл. {ch}, а в томе {hi} глав",
                ))
        placed_ch = p.placed.get("ch")
        for fire in p.fires:
            if fire.get("vol") == volume and fire.get("ch") and placed_ch and fire["ch"] < placed_ch:
                out.append(LintFinding(
                    code="ЗАКЛ-2", severity="ошибка", file=_rel_or("", reg), line=_find_line(reg, p.what[:30]) if reg else None,
                    message=f"{p.plant_id}: «стреляет» в гл. {fire['ch']} раньше, чем положена (гл. {placed_ch})",
                ))
    cpath = next(iter(exporter.volume_docs(library, "33_*.md", volume)), None)
    for c in continuity:
        for ch in re.findall(r"\d+", c.chapters or ""):
            if int(ch) > hi:
                out.append(LintFinding(
                    code="КОНТ-1", severity="предупреждение", file=_rel_or("", cpath), line=_find_line(cpath, c.event[:30]) if cpath else None,
                    message=f"континуити «{c.event[:50]}…»: ссылка на гл. {ch}, а в томе {hi} глав",
                ))
    return out


AGE_RE = re.compile(r"Рожд\.\s*≈?\s*(\d{4})")
# «Возраст по томам: 24 (т.1) · 32 (т.7)» либо «55 лет в томе 1»; «гл. 41 т.1» — не возраст
AGE_VOL_RE = re.compile(r"(\d{2,3})\s*\(\s*т\.\s*(\d+)\s*\)|(\d{2,3})\s*(?:лет|года)\s+в\s+томе\s+(\d+)")


def check_dossiers(library: Path, known_names: set[str], year: int | None, volume: int) -> list[LintFinding]:
    """Возраст против года тома; ссылки [[Имя]] на неизвестных персонажей."""
    out: list[LintFinding] = []
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    # имена из заголовков карточек (включая рабочие имена и имена в скобках) — допустимые ссылки [[Имя]]
    all_names = set(known_names)
    for path in paths:
        for head in realcanon.DOSSIER_HEAD_RE.finditer(path.read_text(encoding="utf-8")):
            all_names.update(w.capitalize() for w in re.findall(r"[А-ЯЁ][А-ЯЁа-яё-]{2,}", head.group(1)))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        heads = list(realcanon.DOSSIER_HEAD_RE.finditer(text))
        for i, head in enumerate(heads):
            body = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            offset = text[: head.end()].count("\n")
            bm = AGE_RE.search(body)
            if bm and year:
                born = int(bm.group(1))
                for am in AGE_VOL_RE.finditer(body):
                    age = int(am.group(1) or am.group(3)); vol = int(am.group(2) or am.group(4))
                    if vol != volume:
                        continue
                    expected = year - born
                    if abs(expected - age) > 1:
                        line = offset + body[: am.start()].count("\n") + 1
                        old = am.group(0)
                        new = old.replace(str(age), str(expected), 1)
                        out.append(LintFinding(
                            code="ДОСЬЕ-1", severity="предупреждение", file=_rel(library, path), line=line,
                            message=f"{head.group(1).strip()}: рождение ≈{born}, том {vol} = {year} год → возраст {expected}, в досье {age}",
                            fix=LintFix(file=_rel(library, path), line=line, old=old, new=new, note="пересчитанный возраст"),
                        ))
            for rm in re.finditer(r"\[\[([^\]]+)\]\]", body):
                ref = rm.group(1).strip()
                base = re.split(r"[-–\s]", ref)[0]
                if not any(base.lower().startswith(n.lower()) or n.lower().startswith(base.lower()) for n in all_names):
                    line = offset + body[: rm.start()].count("\n") + 1
                    out.append(LintFinding(
                        code="ДОСЬЕ-2", severity="заметка", file=_rel(library, path), line=line,
                        message=f"ссылка [[{ref}]] — такого персонажа нет ни среди досье, ни среди известных имён",
                    ))
            if not re.search(r"^##\s*Физик", body, re.M):
                out.append(LintFinding(
                    code="ДОСЬЕ-6", severity="заметка", file=_rel(library, path), line=offset + 1,
                    message=f"{head.group(1).strip()}: у карточки нет секции «Физика»: Писатель обязан выдумать внешность",
                ))
    return out


# роли безымянных персонажей сцен: основа → именительный падеж; такой участник события или сцены
# не имеет карточки досье («поляк», «посредник», «оперативник ОГПУ»)
_ROLES = {
    "поляк": "поляк", "посредник": "посредник", "оперативник": "оперативник", "сторож": "сторож",
    "милиционер": "милиционер", "посыльн": "посыльный", "писар": "писарь", "гастролёр": "гастролёр",
    "медвежатник": "медвежатник", "куратор": "куратор", "чекист": "чекист", "следовател": "следователь",
}
_ROLE_RE = re.compile(r"(?<![а-яё])(" + "|".join(_ROLES) + r")([а-яё]*)(?![а-яё])", re.IGNORECASE)
_ADJ_END_RE = re.compile(r"ск(ий|ая|ое|ие|ой|ую|ого|ому|им|их|ими|ом)$")  # «чекистской», «писарского» — прилагательные
# «Веры Холодовой»: имя и фамилия подряд — персонаж, названный полностью
_FULL_NAME_RE = re.compile(r"(?<![«\w])([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+(?:ов|ев|ин|ын|ск)[а-яё]*)(?![а-яё])")
_SCENE_SKIP = {"те же", "один", "одна", "все", "никого"}


def check_scene_persons(briefs: list[Brief], dossier_names: set[str], known_names: set[str],
                        reg_path: Path | None, p23_path: Path | None) -> list[LintFinding]:
    """ПОГЛ-2: участники сцен и событий сетки без карточки досье — известные имена без карточки
    («Куратор ОГПУ»), позиции поля «участники» сцены поглавника без известного имени («милиционер»,
    «тело Клюева у сейфа»), роли безымянных персонажей и полные имена в событиях («поляк»,
    «посредник», «Веры Холодовой»). Одна заметка на персонажа со списком глав (аудит 7.6, 3.10)."""
    seen: dict[str, tuple[str, list[int], Path | None, str]] = {}

    def note(key: str, who: str, chapter: int, path: Path | None, anchor: str) -> None:
        key = key.lower()
        if key not in seen:
            seen[key] = (who, [chapter], path, anchor)
        elif chapter not in seen[key][1]:
            seen[key][1].append(chapter)

    def has_card(name: str) -> bool:
        return any(name.lower() == d.lower() or name.lower().startswith(d.lower() + " ") for d in dossier_names)

    for b in briefs:
        # событие сетки — первый бит брифа из реестра (в демо-формате биты — не события)
        event = b.beats[0] if reg_path is not None and b.beats and not b.beats[0].lower().startswith("кладём") else ""
        for name in [b.focal, *b.participants]:
            if name and name in known_names and not has_card(name):
                note(name, name, b.chapter, reg_path, event[:30])
        texts = [(event, reg_path, event[:30])]
        for scene in b.scenes:
            parts = [x.strip() for x in scene.split("·")]
            texts.append((scene, p23_path, scene[:30]))
            if len(parts) > 1 and p23_path is not None:
                for item in re.split(r"[,;]", re.sub(r"\(.*?\)", "", parts[1])):
                    item = item.strip(" .")
                    if not item or item.lower() in _SCENE_SKIP or realcanon.find_names(item, known_names):
                        continue
                    rm = _ROLE_RE.fullmatch(item)
                    if rm:
                        note(rm.group(1), _ROLES[rm.group(1).lower()], b.chapter, p23_path, scene[:30])
                    else:
                        note(item, item, b.chapter, p23_path, scene[:30])
        for text, path, anchor in texts:
            if not text:
                continue
            for m in _ROLE_RE.finditer(text):
                stem = m.group(1).lower()
                if _ADJ_END_RE.search(m.group(0).lower()) or any(n.lower().startswith(stem) for n in known_names):
                    continue  # прилагательное («чекистской») или известное имя («куратор» = «Куратор ОГПУ»)
                note(stem, _ROLES[stem], b.chapter, path, anchor)
            for m in _FULL_NAME_RE.finditer(text):
                prev = text[: m.start()].rstrip()
                if not prev or prev[-1] in ".;:!?—·":
                    continue  # начало фразы: «Смерть Дзержинского» — не «Имя Фамилия»
                if not realcanon.find_names(m.group(0), known_names):
                    note(m.group(2), m.group(0), b.chapter, path, anchor)
    out: list[LintFinding] = []
    for who, chapters, path, anchor in seen.values():
        chs = ", ".join(str(c) for c in sorted(chapters))
        out.append(LintFinding(
            code="ПОГЛ-2", severity="заметка", file=_rel_or("", path), line=_find_line(path, anchor) if path else None,
            message=f"участник сцены без досье: «{who}» (гл. {chs})",
        ))
    return out


def check_circles(circles, acts, briefs: list[Brief], library: Path) -> list[LintFinding]:
    out: list[LintFinding] = []
    path = next(iter(exporter.volume_docs(library, "21_*.md", briefs[0].volume if briefs else 1)), None)
    if not circles or not briefs:
        return out
    lo, hi = min(b.chapter for b in briefs), max(b.chapter for b in briefs)
    for c in circles:
        # Р-024: у главы обязательны шаги 1–7, шаг 8 «Изменение» — по материалу (пустой или «в материале не задано» — норма)
        n_steps = len(c.steps)
        expected = "восемь" if c.scope != "глава" else "семь обязательных (1–7) и необязательный восьмой (Р-024)"
        bad = n_steps != 8 if c.scope != "глава" else not (7 <= n_steps <= 8) or {st.n for st in c.steps} < set(range(1, 8))
        if bad:
            out.append(LintFinding(code="КРУГ-1", severity="предупреждение", file=_rel_or("", path), line=_find_line(path, c.title[:20]) if path else None,
                                   message=f"{c.title}: шагов {n_steps}, а в круге истории {expected}"))
        if c.scope in ("книга", "акт"):
            if c.scope == "книга":
                a, z = lo, hi
            else:
                act = next((x for x in acts if x.act == c.key), None)
                if not act:
                    continue
                a, z = act.from_chapter, act.to_chapter
            expect = a
            for st in c.steps:
                if st.from_chapter is None:
                    continue
                if st.from_chapter < expect or (st.to_chapter or st.from_chapter) > z:
                    out.append(LintFinding(code="КРУГ-2", severity="предупреждение", file=_rel_or("", path), line=_find_line(path, st.text[:25]) if path else None,
                                           message=f"{c.title}, шаг {st.n} «{st.name}» ({st.chapters}) выходит за границы {a}–{z} или наезжает на предыдущий шаг"))
                expect = (st.to_chapter or st.from_chapter) + 1
    return out


def check_accepted_prose(library: Path, exports_dir: Path, briefs: list[Brief]) -> list[LintFinding]:
    """ПРОЗА-3: принятая в канон глава не проходит Э1 по текущим нормам 02 §5.

    Такое расхождение — не ошибка конвейера, а противоречие внутри канона (Р-015 задал коридор,
    Р-018 принял главу вне коридора). Решение — за автором: перекалибровать нормы или править главу."""
    out: list[LintFinding] = []
    norms = exporter.load_norms(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    by_ch = {b.chapter: b for b in briefs}
    for path in sorted((library / "Проза").glob("*.md")) if (library / "Проза").exists() else []:
        m = re.search(r"Глава(\d+)", path.name)
        if not m or "МАКЕТ" in path.name:
            continue
        brief = by_ch.get(int(m.group(1)))
        if brief is None:
            continue
        try:
            checks = verifier1.analyze(path.read_text(encoding="utf-8"), "", brief, norms, stoplists)
        except (KeyError, ValueError):
            continue  # нормы неполны — это ловит РАЗМ-1/ЛИНТ-0
        brak = [c for c in checks if c.status == "BRAK"]
        if brak:
            details = "; ".join(f"{c.check_id} = {c.actual} при пороге {c.threshold}" for c in brak)
            out.append(LintFinding(
                code="ПРОЗА-3", severity="предупреждение", file=_rel(library, path),
                message=f"принятая глава {brief.chapter} не проходит Э1 по текущим нормам: {details}. "
                        "Либо нормы 02 §5 перекалибровать по принятой прозе, либо главу править — решение автора",
            ))
    return out


def check_prose(library: Path, briefs: list[Brief], infobans: list[InfoBan], stoplists) -> list[LintFinding]:
    """Принятая проза: маркеры тайн, которых фокал главы не знает; стоп-лексика линии фокала."""
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    for path in sorted((library / "Проза").glob("*.md")) if (library / "Проза").exists() else []:
        m = re.search(r"Глава(\d+)", path.name)
        if not m or "МАКЕТ" in path.name:
            continue
        b = by_ch.get(int(m.group(1)))
        if not b:
            continue
        lines = _lines_cache(path)
        active = [ban for ban in infobans if ban.secret and ban.markers and not ban.known_to(b.focal, b.chapter)]
        rules = [r for r in stoplists if r.kind == "лексика" and verifier1._stoplist_applies(r, b)]
        # маркеры тайн и стоп-лексика линии — по внутренней речи фокала: реплика чужого персонажа
        # («— Сынок, — сказал Бугаев») знанием фокала не является (03, аудит 2, находка 3.9)
        narration = set(textutils.narration_only("\n\n".join(lines)).splitlines())
        for i, line in enumerate(lines, start=1):
            if line.strip() and line.strip() not in narration:
                continue
            for ban in active:
                hit = marker_hit(line, ban.markers)
                if hit:
                    out.append(LintFinding(
                        code="ПРОЗА-1", severity="предупреждение", file=_rel(library, path), line=i,
                        message=f"гл. {b.chapter} (фокал {b.focal}): «{hit}» — маркер тайны {ban.ban_id}, которой фокал ещё не знает",
                    ))
            for r in rules:
                found = verifier1._find_items(line, list(r.items))
                if found:
                    out.append(LintFinding(
                        code="ПРОЗА-2", severity="заметка", file=_rel(library, path), line=i,
                        message=f"гл. {b.chapter}: стоп-лексика линии [{r.rule_id}]: {', '.join(found[:3])}",
                    ))
    return out


# ------------------------------------------------------------------ прогон


def run_lint(library: Path, exports_dir: Path, logs_dir: Path, export: bool = True, volume: int = 1) -> LintReport:
    """Машинный слой: экспорт (валидация Д-1) + все проверки ТОМА `volume` (текущий том рабочей области).
    Ничего не пишет в библиотеку. `export=False` — выгрузки уже актуальны (вызывающий только что сделал
    экспорт того же тома): без второго прогона."""
    findings: list[LintFinding] = []
    try:
        if export:
            exporter.run_export(library, exports_dir, logs_dir, volume)
    except MarkupError as e:
        rel = _rel(library, Path(e.path)) if getattr(e, "path", None) else ""
        findings.append(LintFinding(code="РАЗМ-1", severity="ошибка", file=rel, line=getattr(e, "line", None),
                                    message=f"разметка расходится с соглашениями Д-1: {e}"))
        return _finish(findings, logs_dir, files=len(_library_docs(library)))
    briefs = exporter.load_briefs(exports_dir)
    infobans = exporter.load_infobans(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    plants = exporter.load_plants(exports_dir)
    continuity = exporter.load_continuity(exports_dir)
    dossiers = exporter.load_dossiers(exports_dir)
    stoplists = exporter.load_stoplists(exports_dir)
    parts = exporter.load_parts(exports_dir)
    acts = exporter.load_acts(exports_dir)
    circles = exporter.load_circles(exports_dir)
    known = exporter._known_names(library)
    volume = briefs[0].volume if briefs else volume  # выгрузки — одного тома (run_export(volume=…))
    reg = exporter._registry(library, volume)
    if reg is None:
        reg = next(iter(exporter.volume_docs(library, "23_*.md", volume)), None)
    acts_path = next(iter(exporter.volume_docs(library, "21_*.md", volume)), None)
    year = briefs[0].year if briefs else None
    focal_vols = parse_focal_volumes(sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else [], known)

    findings += check_chronology(briefs, reg)
    findings += check_ranges(parts, acts, briefs, reg, acts_path)
    findings += check_focals(briefs, focal_vols, library, {d.name for d in dossiers}, reg)
    findings += check_brief_epistemics(briefs, infobans, reg)
    findings += check_secrets_vs_matrix(infobans, matrix, exporter._registry(library, volume))
    findings += check_chapter_refs(matrix, plants, continuity, briefs, library)
    findings += check_dossiers(library, known, year, volume)
    findings += check_scene_persons(briefs, {d.name for d in dossiers}, known, exporter._registry(library, volume),
                                    next(iter(exporter.volume_docs(library, "23_*.md", volume)), None))
    findings += check_circles(circles, acts, briefs, library)
    findings += check_prose(library, briefs, infobans, stoplists)
    findings += check_accepted_prose(library, exports_dir, briefs)
    findings += lint_epist.run_checks(library, exports_dir, briefs, matrix, infobans, plants, parts, acts, known, reg)
    findings += lint_canon.run_checks(library, exports_dir, briefs, parts, continuity, known, reg, volume)
    return _finish(findings, logs_dir, files=len(_library_docs(library)))


def _finish(findings: list[LintFinding], logs_dir: Path, files: int) -> LintReport:
    order = {"ошибка": 0, "предупреждение": 1, "заметка": 2}
    findings.sort(key=lambda f: (order[f.severity], f.file, f.line or 0))
    report = LintReport(
        ts=datetime.now(timezone.utc).isoformat(), files_checked=files, findings=findings,
        errors=sum(1 for f in findings if f.severity == "ошибка"),
        warnings=sum(1 for f in findings if f.severity == "предупреждение"),
        notes=sum(1 for f in findings if f.severity == "заметка"),
    )
    guard.write_text(logs_dir / "линтер.json", report.model_dump_json(indent=2) + "\n")
    guard.write_text(logs_dir / "линтер.md", render_md(report))
    return report


def render_md(report: LintReport) -> str:
    lines = [f"# Проверка канона · {report.ts[:19].replace('T', ' ')}",
             f"Документов: {report.files_checked} · ошибок: {report.errors} · предупреждений: {report.warnings} · заметок: {report.notes}", ""]
    for f in report.findings:
        where = f"{f.file}:{f.line}" if f.line else f.file
        src = " (модель)" if f.source == "модель" else ""
        lines.append(f"- **{f.severity}** [{f.code}]{src} {where} — {f.message}")
        if f.fix:
            lines.append(f"  - исправление: «{f.fix.old}» → «{f.fix.new}»")
    return "\n".join(lines) + "\n"


def load_report(logs_dir: Path) -> LintReport | None:
    p = logs_dir / "линтер.json"
    if not p.exists():
        return None
    try:
        return LintReport.model_validate_json(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


# ------------------------------------------------------------------ исправления


def apply_fix(library: Path, fix: LintFix) -> Path:
    """Применяет предложенное исправление к документу канона — ТОЛЬКО по подтверждению автора:
    вызывающий обязан открыть guard.canon_write_session()."""
    path = (library / fix.file).resolve()
    if library.resolve() not in path.parents:
        raise ValueError("исправление указывает вне библиотеки")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = (fix.line or 1) - 1
    if idx < 0 or idx >= len(lines) or fix.old not in lines[idx]:
        raise ValueError(f"строка {fix.line} файла {fix.file} изменилась — исправление больше не подходит, перепроверьте канон")
    lines[idx] = lines[idx].replace(fix.old, fix.new, 1)
    guard.write_text(path, "".join(lines))
    return path


# ------------------------------------------------------------------ модельный слой


def _template() -> str:
    return resources.files("konveyer").joinpath("шаблоны/линтер_канона_система.md").read_text(encoding="utf-8")


def _context_slices(exports_dir: Path) -> str:
    briefs = exporter.load_briefs(exports_dir)
    infobans = exporter.load_infobans(exports_dir)
    matrix = exporter.load_matrix(exports_dir)
    lines = ["## Тайны тома (кто и когда знает)"]
    for b in infobans:
        if b.secret:
            when = f"читатель узнаёт: гл. {b.until_chapter}" if b.until_chapter is not None else "читателю не раскрывается в томе"
            who = "; ".join(f"{n} — {'всегда' if c == 0 else 'с гл. ' + str(c)}" for n, c in sorted(b.known_by.items()))
            lines.append(f"- {b.ban_id} {b.text} · {when} · знают: {who or 'никто'}")
    lines += ["", "## Главы тома (фокал, дата, событие)"]
    for b in sorted(briefs, key=lambda b: b.chapter):
        lines.append(f"- гл. {b.chapter} · {b.date} · {b.focal}: {'; '.join(b.beats)[:300]}")
    lines += ["", "## Матрица знаний (кто что знает и с какой главы)"]
    for f in matrix:
        if f.from_chapter is not None:
            lines.append(f"- {f.fact_id} {f.subject}: {'всегда' if f.from_chapter == 0 else 'гл. ' + str(f.from_chapter)}"
                         + (f" ({f.note})" if f.note else "") + f" — {f.fact}")
    return "\n".join(lines)


def resolve_library_files(library: Path, files: list[str] | None) -> list[Path]:
    """Документы для модельного слоя: только .md ВНУТРИ библиотеки (иначе содержимое произвольного
    файла ушло бы в API и в журналы/линтер_промпты/)."""
    if not files:
        return _library_docs(library)
    root = library.resolve()
    out: list[Path] = []
    for f in files:
        path = (library / f).resolve()
        if root not in path.parents or path.suffix != ".md":
            raise ValueError(f"«{f}»: документ модельного слоя должен быть .md внутри библиотеки")
        if not path.exists():
            raise ValueError(f"«{f}»: нет такого документа в библиотеке")
        out.append(path)
    return out


def estimate_llm_cost(cfg: Config, n_docs: int, avg_chars: int = 12_000) -> float | None:
    """Грубая смета модельного слоя по ценам из конфиг.yaml (0 = цены не заданы → None)."""
    mc = cfg.canonist
    if not (mc.price_in_per_1m or mc.price_out_per_1m):
        return None
    tokens_in = n_docs * (avg_chars / 3 + 3_000)   # документ + контекст канона, ~3 символа на токен
    tokens_out = n_docs * 800
    return tokens_in / 1e6 * mc.price_in_per_1m + tokens_out / 1e6 * mc.price_out_per_1m


def run_lint_llm(ws: Workspace, cfg: Config, library: Path, files: list[Path] | None = None,
                 max_calls: int | None = None) -> tuple[list[LintFinding], list[str]]:
    """Смысловые противоречия по документам (по одному вызову на документ). Возвращает (находки, промпты
    ручного режима, если API недоступен). Сбой на одном документе (нет JSON в ответе, ошибка API) не
    теряет находки уже проверенных: он становится находкой ЛИНТ-0 по этому документу. `max_calls`
    ограничивает число оплачиваемых вызовов за прогон."""
    docs = files or _library_docs(library)
    if max_calls is not None and len(docs) > max_calls:
        raise ValueError(f"документов {len(docs)}, лимит вызовов модели {max_calls}: укажите --файл или поднимите --лимит")
    system = _template()
    context = _context_slices(ws.exports)
    findings: list[LintFinding] = []
    prompts: list[str] = []
    for doc in docs:
        rel = _rel(library, doc)
        user = f"# Документ: {rel}\n\n{doc.read_text(encoding='utf-8')}\n\n# Контекст канона\n\n{context}"
        prompt_path = ws.logs / "линтер_промпты" / (re.sub(r"[^\w.\-]+", "_", rel) + ".md")
        guard.write_text(prompt_path, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
        try:
            raw = adapters.call_anthropic(system, user, cfg.canonist, cfg.api, ws.logs, role="линтер канона")
            findings += parse_llm_findings(raw, library, doc)
        except adapters.ManualModeNeeded:
            prompts.append(str(prompt_path))
        except Exception as e:  # noqa: BLE001 — один документ не должен отменять весь прогон
            findings.append(LintFinding(
                code="ЛИНТ-0", severity="заметка", file=rel, source="модель",
                message=f"модельный слой не дал результата по документу: {type(e).__name__}: {str(e)[:200]}",
            ))
    return findings, prompts


def parse_llm_findings(raw: str, library: Path, doc: Path) -> list[LintFinding]:
    data = llmjson.extract_json(raw, list)
    out: list[LintFinding] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "") or "")[:200]
        sev = str(item.get("severity", "предупреждение"))
        if sev not in ("ошибка", "предупреждение", "заметка"):
            sev = "предупреждение"
        out.append(LintFinding(
            code="МОДЕЛЬ", severity=sev, file=_rel(library, doc), line=_find_line(doc, quote[:40]),
            message=(str(item.get("problem", "")).strip() + (f" — {item['suggestion']}" if item.get("suggestion") else "")).strip(),
            quote=quote, source="модель",
        ))
    return out


def error_report(exc: BaseException, logs_dir: Path, files: int = 0) -> LintReport:
    """Сбой самого линтера (нечитаемый файл, ошибка программы) — не исчезает молча, а становится
    находкой ЛИНТ-0 уровня «ошибка»: автор видит причину в панели и в журналы/линтер.md."""
    hint = ""
    if isinstance(exc, UnicodeDecodeError):
        hint = " — файл не в UTF-8 (NFR-8): пересохраните его в UTF-8"
    return _finish([LintFinding(code="ЛИНТ-0", severity="ошибка", file=getattr(exc, "path", "") or "",
                                message=f"проверка канона не выполнена: {type(exc).__name__}: {exc}{hint}")],
                   logs_dir, files)


def merge_llm(report: LintReport, extra: list[LintFinding], logs_dir: Path) -> LintReport:
    findings = [f for f in report.findings if f.source != "модель"] + extra
    return _finish(findings, logs_dir, report.files_checked)
