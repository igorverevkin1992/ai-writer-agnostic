"""Линтер канона: эпистемика, закладки, поглавник против сетки (аудит 2, находки 3.3–3.5, 3.7, 3.8).

Модуль отвечает на один вопрос: сходится ли то, КТО и КОГДА что знает и куда что положено,
между четырьмя документами — матрицей 3.1, реестром информационного режима (§1, §2, §5, §7),
поглавником 2.3 и таблицей актов 2.1.

- МАТР-2/3 — эпистемическая матрица: субъект узнаёт факт в главе, где его нет; читатель узнаёт
  факт через фокала раньше самого фокала; кто-то знает раньше события; автор действия узнаёт
  позже читателя.
- ТАЙНА-4/5 — реестр тайн: раскрытие читателю идёт через фокала, который тайну знает;
  колонка «Персонажи знают» против матрицы там, где реестр глав не называет.
- ЗАКЛ-3…7 — реестр закладок §7: носитель закладки присутствует в главе; выстрел не раньше тома
  закладки (ЗАКЛ-2 на реальном каноне мёртв — выстрелы заданы томами, не главами); §7 против строки
  «Закладки положены» сквозного контроля поглавника; правило доз §5 («печь в мае» — только доза и
  гл. 46); «гл. 29 или 40» — выбрать одну главу.
- ПОГЛ-1, АКТ-2, ФОКАЛ-3 — заголовок главы поглавника против сетки; колонка «Части» таблицы актов
  против диапазонов частей; пропорция фокалов §1.6 и сквозного контроля.
- ПРОЗА-4 — имя с отчеством или фамилией, появившееся в принятой прозе и не внесённое в досье или
  континуити 3.3 (принцип П6).

Проверки читают ТОЛЬКО выгрузки и документы библиотеки; ничего не пишут (записи — `lint.apply_fix`).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import exporter, mdparse, realcanon
from .schemas import Act, Brief, InfoBan, LintFinding, MatrixFact, Plant

# ячейка «Читатель» матрицы/реестра, где раскрытие — расчёт читателя, а не показ через фокала
DEDUCTION_RE = re.compile(r"разгадк|улик|расчётн", re.IGNORECASE)
# «/ по своим каналам», «/ за кадром» — знание, полученное вне показанных сцен (Р-033): присутствие
# субъекта в главе не проверяется
OFFSTAGE_RE = re.compile(r"по своим каналам|за кадром", re.IGNORECASE)
# «Гл. 29 или 40» — открытое решение автора, а не две главы
PLANT_OR_RE = re.compile(r"\d\s*(?:,\s*\d+\s*)*или\s*\d")
# «Пропорция фокалов: Лемм 4 гл. / Степан 3 / Штерн 2»
SHARE_RE = re.compile(r"([А-ЯЁ][а-яё]+)\s*(\d{1,3})\s*%")
COUNT_RE = re.compile(r"([А-ЯЁ][а-яё]+)\s*(\d{1,2})(?!\s*%)")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
# имя + отчество либо фамилия в прозе: «Степан Ильич», «Веры Холодовой», «Андрей Карлович»
PATRONYMIC = r"[А-ЯЁ][а-яё]*?(?:ович|евич|ьич|инична|ична|овна|евна)"
SURNAME = r"[А-ЯЁ][а-яё]+?(?:ов|ев|ин|ын|ск|цк)"
PROSE_NAME_RE = re.compile(
    rf"(?<![А-Яа-яЁё])([А-ЯЁ][а-яё]{{2,}})\s+({PATRONYMIC}|{SURNAME})([а-яё]{{0,3}})(?![А-Яа-яЁё])"
)
_STEM_END_RE = re.compile(r"(?:ами|ями|ого|ому|ыми|ими|ой|ей|ом|ем|ых|их|ий|ый|ая|ое|ые|ов|ев|ах|ях|[аяуюеиыоь])$")


def _stems(text: str) -> set[str]:
    """Основы значимых слов: «печной золе» → {печн, зол}. Для сопоставления названий закладок."""
    return {s for w in re.findall(r"[а-яёa-z]{3,}", text.lower()) if len(s := _STEM_END_RE.sub("", w)) >= 3}


class _Doc:
    """Документ канона, прочитанный один раз: номер строки по фрагменту (аудит 3.11)."""

    def __init__(self, library: Path, path: Path | None) -> None:
        self.path = path
        self.file = ""
        self.lines: list[str] = []
        if path is not None and path.exists():
            try:
                self.file = str(path.relative_to(library)).replace("\\", "/")
            except ValueError:
                self.file = path.name
            self.lines = path.read_text(encoding="utf-8").splitlines()

    def line_of(self, needle: str) -> int | None:
        if not needle:
            return None
        for i, line in enumerate(self.lines, start=1):
            if needle in line:
                return i
        return None


# ------------------------------------------------------- матрица 3.1 (МАТР-2/3)


def _matrix_reader_cells(path: Path | None) -> dict[str, str]:
    """{М-01: сырая ячейка «Читатель»} — из неё видно, раскрытие это или расчёт читателя."""
    out: dict[str, str] = {}
    if path is None or not path.exists():
        return out
    for table in mdparse.parse_tables(path):
        if "Факт" not in table.headers or "Читатель" not in table.headers:
            continue
        for i, row in enumerate(table.rows, start=1):
            num = mdparse.parse_number(row.get("#", "")) or i
            out[f"М-{int(num):02d}"] = row.get("Читатель", "")
    return out


def check_matrix_presence(matrix: list[MatrixFact], briefs: list[Brief], doc: _Doc) -> list[LintFinding]:
    """МАТР-2: субъект узнаёт факт в главе, где его нет ни фокалом, ни участником.

    Исключения — субъект узнаёт вместе с фокалом главы (общая сцена: фокал получает тот же факт
    в той же главе) и знание «за кадром» (источник «/ по своим каналам», «/ за кадром» — Р-033).
    Для фокальных линий это ошибка конструкции, для второстепенных персонажей
    (их присутствие сетка называет не всегда) — предупреждение."""
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    focal_lines = {b.focal for b in briefs if b.focal}
    by_fact: dict[str, list[MatrixFact]] = {}
    for f in matrix:
        by_fact.setdefault(f.fact_id, []).append(f)
    for f in matrix:
        if f.subject == "Читатель" or not f.from_chapter or OFFSTAGE_RE.search(f.source):
            continue
        b = by_ch.get(f.from_chapter)
        if b is None:
            continue  # глава вне тома — это МАТР-1
        if f.subject == b.focal or f.subject in b.participants:
            continue
        same = next((x for x in by_fact[f.fact_id] if x.subject == b.focal), None)
        if same is not None and same.from_chapter == f.from_chapter:
            continue  # узнаёт в одной сцене с фокалом главы
        out.append(LintFinding(
            code="МАТР-2", severity="ошибка" if f.subject in focal_lines else "предупреждение",
            file=doc.file, line=doc.line_of(f.fact[:30]),
            message=f"{f.fact_id} ({f.subject}): узнаёт в гл. {f.from_chapter}, но это глава фокала "
                    f"{b.focal or '?'}, и {f.subject} не значится среди участников — где он это узнаёт?",
        ))
    return out


def check_matrix_order(matrix: list[MatrixFact], briefs: list[Brief], doc: _Doc,
                       reader_cells: dict[str, str]) -> list[LintFinding]:
    """МАТР-3: порядок знания.

    (а) читатель узнаёт факт в главе, фокал которой его ещё не знает (кроме расчётных разгадок
        и глав с документом-вставкой) — предупреждение;
    (б) персонаж знает факт раньше события (событие = минимальная глава читателя и участников
        события — тех, кто назван в формулировке факта или помечен источником «/ автор»);
    (в) автор действия узнаёт о нём позже читателя."""
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    by_fact: dict[str, list[MatrixFact]] = {}
    for f in matrix:
        by_fact.setdefault(f.fact_id, []).append(f)
    for fid, rows in by_fact.items():
        fact = rows[0].fact
        reader = next((x for x in rows if x.subject == "Читатель"), None)
        actors = {x.subject for x in rows if x.subject != "Читатель"
                  and (x.subject in fact or "автор" in x.source)}
        # (а)
        if reader and reader.from_chapter and not DEDUCTION_RE.search(reader_cells.get(fid, "")):
            b = by_ch.get(reader.from_chapter)
            focal = next((x for x in rows if b and x.subject == b.focal), None)
            if b and focal is not None and not b.documents and (
                    focal.from_chapter is None or focal.from_chapter > reader.from_chapter):
                knows = "не знает его до конца тома" if focal.from_chapter is None else f"узнаёт только в гл. {focal.from_chapter}"
                out.append(LintFinding(
                    code="МАТР-3", severity="предупреждение", file=doc.file, line=doc.line_of(fact[:30]),
                    message=f"{fid}: читатель узнаёт в гл. {reader.from_chapter}, а фокал этой главы "
                            f"({b.focal}) {knows} — читатель получает факт через голову фокала",
                ))
        # (б)
        event = [x.from_chapter for x in rows if x.subject in actors and x.from_chapter]
        if reader and reader.from_chapter:
            event.append(reader.from_chapter)
        if event:
            first = min(event)
            for x in rows:
                if x.subject in actors or x.subject == "Читатель" or not x.from_chapter:
                    continue
                if x.from_chapter < first:
                    out.append(LintFinding(
                        code="МАТР-3", severity="ошибка", file=doc.file, line=doc.line_of(fact[:30]),
                        message=f"{fid} ({x.subject}): знает с гл. {x.from_chapter}, а само событие происходит "
                                f"не раньше гл. {first} — знание раньше события",
                    ))
        # (в)
        if reader and reader.from_chapter:
            for x in rows:
                if "автор" in x.source and x.from_chapter and x.from_chapter > reader.from_chapter:
                    out.append(LintFinding(
                        code="МАТР-3", severity="ошибка", file=doc.file, line=doc.line_of(fact[:30]),
                        message=f"{fid}: {x.subject} — автор действия, но узнаёт о нём в гл. {x.from_chapter}, "
                                f"позже читателя (гл. {reader.from_chapter})",
                    ))
    return out


# --------------------------------------------------------- реестр тайн (ТАЙНА-4/5)


def _secret_rows(reg: _Doc) -> dict[str, dict[str, str]]:
    """{Т-01: строка таблицы тайн} в том же порядке, в каком нумерует `realcanon.parse_secrets`."""
    out: dict[str, dict[str, str]] = {}
    if reg.path is None:
        return out
    for table in mdparse.parse_tables(reg.path):
        if "тайна" not in " ".join(table.headers).lower():
            continue
        for i, row in enumerate(table.rows, start=1):
            out[f"Т-{i:02d}"] = row
    return out


def check_secret_reveal(infobans: list[InfoBan], briefs: list[Brief], reg: _Doc,
                        rows: dict[str, dict[str, str]]) -> list[LintFinding]:
    """ТАЙНА-4: глава раскрытия тайны читателю — глава фокала, который тайну знает.

    Иначе читатель узнаёт то, чего не знает голова, из которой ведётся сцена. Исключения: раскрытие
    документом-вставкой (первое лицо помимо фокала) и расчётная разгадка («улики с гл. 4»)."""
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    for ban in infobans:
        if not ban.secret or not ban.until_chapter:
            continue
        b = by_ch.get(ban.until_chapter)
        if b is None or b.documents:
            continue
        row = rows.get(ban.ban_id, {})
        if DEDUCTION_RE.search(realcanon.cell(row, "узнаёт") if row else ""):
            continue
        if ban.known_to(b.focal, ban.until_chapter):
            continue
        who = ", ".join(f"{n} (гл. {c})" if c else f"{n} (всегда)" for n, c in sorted(ban.known_by.items()))
        out.append(LintFinding(
            code="ТАЙНА-4", severity="ошибка", file=reg.file, line=reg.line_of(ban.text[:40]),
            message=f"{ban.ban_id}: читатель узнаёт тайну в гл. {ban.until_chapter}, но её фокал {b.focal} "
                    f"тайны не знает (знают: {who or 'никто'}) — раскрывать нечем",
        ))
    return out


def check_secret_knowers(infobans: list[InfoBan], matrix: list[MatrixFact], known: set[str],
                         reg: _Doc, rows: dict[str, dict[str, str]]) -> list[LintFinding]:
    """ТАЙНА-5: колонка «Персонажи знают» реестра против матрицы там, где реестр не называет глав.

    Два расхождения: (а) матрица даёт знание персонажу, которого в реестре нет вовсе
    («Никто из героев» при «всегда» в матрице); (б) в одной ячейке часть имён с главами, часть без —
    а матрица для «без» даёт не «всегда», а конкретную главу."""
    out: list[LintFinding] = []
    for ban in infobans:
        row = rows.get(ban.ban_id)
        if not ban.secret or row is None:
            continue
        raw = realcanon.cell(row, "знают")
        registry = realcanon._parse_known_by(raw, known)
        fid = realcanon._match_matrix_fact(ban.text, matrix)
        if not fid:
            continue
        _, matrix_known = realcanon._matrix_knowledge(fid, matrix)
        line = reg.line_of(ban.text[:40])
        for name, ch in sorted(matrix_known.items()):
            if name in registry:
                continue
            when = "всегда" if ch == 0 else f"с гл. {ch}"
            out.append(LintFinding(
                code="ТАЙНА-5", severity="предупреждение", file=reg.file, line=line,
                message=f"{ban.ban_id}: по матрице ({fid}) {name} знает {when}, а в колонке «Персонажи знают» "
                        f"его нет («{raw[:60]}») — окно Писателя чистит досье не тому",
            ))
        if any(v is not None for v in registry.values()):
            for name, ch in sorted(registry.items()):
                mat = matrix_known.get(name)
                if ch is None and mat:
                    out.append(LintFinding(
                        code="ТАЙНА-5", severity="предупреждение", file=reg.file, line=line,
                        message=f"{ban.ban_id}: в реестре {name} назван без главы (рядом с другими — с главой), "
                                f"по матрице ({fid}) он узнаёт в гл. {mat}: с какой главы он знает?",
                    ))
    return out


# ------------------------------------------------------------ закладки §7 (ЗАКЛ-3…7)


def _plant_rows(reg: _Doc) -> dict[str, str]:
    """{З-01: ячейка «Где лежит»} в нумерации `realcanon.parse_plants_registry`."""
    out: dict[str, str] = {}
    if reg.path is None:
        return out
    for table in mdparse.parse_tables(reg.path):
        headers = " ".join(table.headers).lower()
        if "закладка" not in headers or "стреляет" not in headers:
            continue
        for i, row in enumerate(table.rows, start=1):
            out[f"З-{i:02d}"] = realcanon.cell(row, "лежит")
    return out


def _control_plants(pog: _Doc) -> list[tuple[str, int]]:
    """Строка «Закладки положены: знак/зола (6.2), часы (гл. 4)» → [(«знак/зола», 6), («часы», 4)]."""
    m = re.search(r"Закладки положены:\s*(.+)", "\n".join(pog.lines))
    if not m:
        return []
    out: list[tuple[str, int]] = []
    for item in re.split(r",\s*(?![^(]*\))", m.group(1).rstrip(". ")):
        loc = re.search(r"\(([^)]*)\)", item)
        name = re.sub(r"\(.*?\)", "", item).strip()
        ch = re.search(r"(\d+)", loc.group(1)) if loc else None
        if name and ch:
            out.append((name, int(ch.group(1))))
    return out


def check_plants(plants: list[Plant], briefs: list[Brief], doses, known: set[str],
                 reg: _Doc, pog: _Doc, rows: dict[str, str]) -> list[LintFinding]:
    """ЗАКЛ-3: носитель закладки — фокал или участник главы, где она положена.
    ЗАКЛ-4: закладка «стреляет» в томе раньше того, где положена (ЗАКЛ-2 сравнивает главы и на
        реальном каноне молчит: выстрелы §7 заданы томами).
    ЗАКЛ-5: §7 против строки «Закладки положены» сквозного контроля поглавника.
    ЗАКЛ-6: закладка вне глав, разрешённых правилом доз §5 («печь в мае» — только доза №1 и гл. 46).
    ЗАКЛ-7: «Гл. 29 или 40» — открытое решение, одну главу выбрать."""
    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    # персонажи, которые вообще появляются в томе: только они могут быть носителями
    in_volume = {b.focal for b in briefs if b.focal} | {n for b in briefs for n in b.participants}

    for p in plants:
        from_registry = p.plant_id.startswith("З-")
        doc = reg if from_registry else pog
        line = reg.line_of(p.what[:30]) if from_registry else pog.line_of("Закладки положены")
        bearers = [n for n in realcanon.find_names(p.what, known) if n in in_volume]
        for ch in p.chapters:
            b = by_ch.get(ch)
            if not b or not bearers:
                continue
            if any(n == b.focal or n in b.participants for n in bearers):
                continue
            out.append(LintFinding(
                code="ЗАКЛ-3", severity="ошибка", file=doc.file, line=line,
                message=f"{p.plant_id} «{p.what[:40]}»: положена в гл. {ch} (фокал {b.focal}), "
                        f"но её носитель ({', '.join(bearers)}) в этой главе не фокал и не участник",
            ))
        placed_vol = p.placed.get("vol")
        for fire in p.fires:
            if placed_vol and fire.get("vol") and fire["vol"] < placed_vol:
                out.append(LintFinding(
                    code="ЗАКЛ-4", severity="ошибка", file=doc.file, line=line,
                    message=f"{p.plant_id}: «стреляет» в томе {fire['vol']}, а положена в томе {placed_vol}",
                ))
        raw = rows.get(p.plant_id, "")
        if PLANT_OR_RE.search(raw):
            out.append(LintFinding(
                code="ЗАКЛ-7", severity="предупреждение", file=reg.file, line=line,
                message=f"{p.plant_id}: «{raw[:40]}» — две главы через «или»; выберите одну, иначе окно Писателя "
                        f"положит закладку дважды",
            ))
        if not from_registry:
            out.append(LintFinding(
                code="ЗАКЛ-5", severity="заметка", file=pog.file, line=line,
                message=f"закладка «{p.what.split(' (')[0]}» (гл. {p.chapters[0] if p.chapters else '?'}) есть "
                        f"в сквозном контроле поглавника, но её нет в реестре закладок §7",
            ))

    registry_plants = [p for p in plants if p.plant_id.startswith("З-")]
    for name, ch in _control_plants(pog):
        stems = _stems(name)
        scored = sorted(((len(stems & _stems(p.what)), p) for p in registry_plants), key=lambda x: -x[0])
        if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
            continue  # закладки §7 не нашлось (заметка ЗАКЛ-5 выше) либо совпадение неоднозначно
        p = scored[0][1]
        if ch not in p.chapters:
            b, other = by_ch.get(ch), by_ch.get(p.chapters[0]) if p.chapters else None
            out.append(LintFinding(
                code="ЗАКЛ-5", severity="ошибка", file=reg.file, line=reg.line_of(p.what[:30]),
                message=f"{p.plant_id} «{p.what[:40]}»: по реестру §7 лежит в гл. "
                        f"{', '.join(str(c) for c in p.chapters) or '?'}"
                        f"{f' (фокал {other.focal})' if other else ''}, а сквозной контроль поглавника кладёт "
                        f"её в гл. {ch}{f' (фокал {b.focal})' if b else ''}",
            ))

    for dose in doses:
        # «в гл. 46» — точка внутри сокращения: границы фраз только перед заглавной или кавычкой
        for sentence in realcanon._SENT_RE.split(dose.rule):
            m = re.search(r"«([^»]+)»", sentence)
            if not m or "только" not in sentence.lower():
                continue
            allowed = {int(x) for x in re.findall(r"гл\.?\s*(\d+)", sentence)}
            for dn in re.findall(r"доз[а-яё]*\s*№\s*(\d+)", sentence, re.IGNORECASE):
                allowed |= {d.chapter for d in doses if d.dose_id.strip("№ ") == dn}
            target = next((p for p in registry_plants if _stems(m.group(1)) & _stems(p.what)), None)
            if not target or not allowed:
                continue
            extra = sorted(set(target.chapters) - allowed - {0})
            if extra:
                out.append(LintFinding(
                    code="ЗАКЛ-6", severity="ошибка", file=reg.file, line=reg.line_of(target.what[:30]),
                    message=f"{target.plant_id} «{m.group(1)}»: реестр §7 кладёт её в гл. "
                            f"{', '.join(str(c) for c in extra)}, а правило доз §5 разрешает только гл. "
                            f"{', '.join(str(c) for c in sorted(allowed))}",
                ))
    return out


# ------------------------------------------ поглавник, акты, пропорция фокалов


def check_poglavnik_head(briefs: list[Brief], known: set[str], pog: _Doc) -> list[LintFinding]:
    """ПОГЛ-1: «## Гл. 3 · 17 апреля · фокал ШТЕРН» против строки сетки (фокал и дата)."""
    from .lint import parse_date

    out: list[LintFinding] = []
    by_ch = {b.chapter: b for b in briefs}
    for i, line in enumerate(pog.lines, start=1):
        m = realcanon.POGLAVNIK_HEAD_RE.match(line.strip())
        if not m:
            continue
        ch = int(m.group(1))
        b = by_ch.get(ch)
        if b is None:
            out.append(LintFinding(code="ПОГЛ-1", severity="ошибка", file=pog.file, line=i,
                                   message=f"гл. {ch} расписана в поглавнике, но её нет в постраничной сетке реестра"))
            continue
        focal = realcanon.normalize_name(m.group(3).strip().capitalize(), known)
        if b.focal and focal.lower() != b.focal.lower():
            out.append(LintFinding(
                code="ПОГЛ-1", severity="ошибка", file=pog.file, line=i,
                message=f"гл. {ch}: в поглавнике фокал {m.group(3).strip()}, в сетке реестра — {b.focal}",
            ))
        head_date, grid_date = parse_date(m.group(2)), parse_date(b.date)
        if head_date and grid_date and head_date != grid_date:
            out.append(LintFinding(
                code="ПОГЛ-1", severity="ошибка", file=pog.file, line=i,
                message=f"гл. {ch}: в поглавнике дата «{m.group(2).strip()}», в сетке реестра — «{b.date}»",
            ))
    return out


def check_act_parts(acts: list[Act], parts: list[dict], doc: _Doc) -> list[LintFinding]:
    """АКТ-2: колонка «Части» таблицы актов 2.1 = объединение диапазонов этих частей в реестре."""
    out: list[LintFinding] = []
    by_num = {p["part"]: p for p in parts}
    for a in acts:
        nums = [ROMAN.get(x.upper(), int(x) if x.isdigit() else 0) for x in re.findall(r"[IVXivx]+|\d+", a.parts)]
        nums = [n for n in nums if n in by_num]
        if not nums:
            continue
        if len(nums) == 2 and re.search(r"[–—-]", a.parts):
            nums = [n for n in range(min(nums), max(nums) + 1) if n in by_num]
        lo = min(by_num[n]["from_chapter"] for n in nums)
        hi = max(by_num[n]["to_chapter"] for n in nums)
        if (lo, hi) != (a.from_chapter, a.to_chapter):
            out.append(LintFinding(
                code="АКТ-2", severity="ошибка", file=doc.file, line=doc.line_of(a.title[:20]),
                message=f"акт {a.act} «{a.title}»: главы {a.from_chapter}–{a.to_chapter}, а части «{a.parts}» "
                        f"занимают в реестре главы {lo}–{hi}",
            ))
    return out


def check_focal_share(briefs: list[Brief], parts: list[dict], known: set[str],
                      reg: _Doc, pog: _Doc) -> list[LintFinding]:
    """ФОКАЛ-3: пропорция фокалов §1.6 реестра (том и каждая часть, допуск ±1 глава) и точные числа
    строки «Пропорция фокалов» сквозного контроля поглавника."""
    out: list[LintFinding] = []

    def count(lo: int, hi: int) -> dict[str, int]:
        c: dict[str, int] = {}
        for b in briefs:
            if b.focal and lo <= b.chapter <= hi:
                c[b.focal] = c.get(b.focal, 0) + 1
        return c

    share_line = next((line for line in reg.lines if "%" in line and len(SHARE_RE.findall(line)) >= 2), "")
    shares = {realcanon.normalize_name(n, known): int(v) for n, v in SHARE_RE.findall(share_line)}
    shares = {n: v for n, v in shares.items() if n in known}
    if shares and briefs:
        line = reg.line_of(share_line[:40])
        lo, hi = min(b.chapter for b in briefs), max(b.chapter for b in briefs)
        spans = [("том", lo, hi)] + [(f"часть {p['part']}", p["from_chapter"], p["to_chapter"]) for p in parts]
        for label, a, z in spans:
            actual = count(a, z)
            total = sum(actual.values())
            for name, pct in sorted(shares.items()):
                expect = round(total * pct / 100)
                if abs(actual.get(name, 0) - expect) > 1:
                    out.append(LintFinding(
                        code="ФОКАЛ-3", severity="предупреждение", file=reg.file, line=line,
                        message=f"{label} (гл. {a}–{z}): {name} — фокал {actual.get(name, 0)} глав из {total}, "
                                f"а по пропорции §1.6 ({pct} %) ожидается около {expect}",
                    ))

    control = next((line for line in pog.lines if "Пропорция фокалов" in line), "")
    covered = sorted({int(m.group(1)) for line in pog.lines
                      if (m := realcanon.POGLAVNIK_HEAD_RE.match(line.strip()))})
    if control and covered:
        actual = count(covered[0], covered[-1])
        for name, num in COUNT_RE.findall(control.split(":", 1)[-1]):
            name = realcanon.normalize_name(name, known)
            if name in known and actual.get(name, 0) != int(num):
                out.append(LintFinding(
                    code="ФОКАЛ-3", severity="предупреждение", file=pog.file, line=pog.line_of("Пропорция фокалов"),
                    message=f"сквозной контроль поглавника: {name} — {num} гл., а в сетке реестра "
                            f"по главам {covered[0]}–{covered[-1]} у него {actual.get(name, 0)}",
                ))
    return out


# --------------------------------------------------------------- проза (ПРОЗА-4)


def check_prose_names(library: Path, known: set[str]) -> list[LintFinding]:
    """ПРОЗА-4: имя с отчеством или фамилией, которого нет ни в досье, ни в континуити 3.3.

    Проза родила новый факт (отчество «Ильич», фамилия) — по П6 он вносится в канон той же сессией.
    Проверяются только имена: всё остальное — работа Э2 и модельного слоя."""
    prose = sorted((library / "Проза").glob("*.md")) if (library / "Проза").exists() else []
    if not prose:
        return []
    canon = "\n".join(p.read_text(encoding="utf-8")
                      for p in [*sorted((library / "Досье").glob("*.md")), *sorted(library.glob("33_*.md"))]
                      if p.exists())
    canon_stems = _stems(canon)
    out: list[LintFinding] = []
    seen: set[str] = set()
    for path in prose:
        text = path.read_text(encoding="utf-8")
        for m in PROSE_NAME_RE.finditer(text):
            if not realcanon.find_names(m.group(1), known):
                continue  # первое слово — не известное имя («Сторож Клюев», «Большого Гнездниковского»)
            core = m.group(2).lower()
            if core in canon_stems or _STEM_END_RE.sub("", core) in canon_stems:
                continue
            full = re.sub(r"\s+", " ", m.group(0))
            key = f"{path.name}:{core}"
            if key in seen:
                continue
            seen.add(key)
            out.append(LintFinding(
                code="ПРОЗА-4", severity="заметка",
                file=str(path.relative_to(library)).replace("\\", "/"),
                line=text[: m.start()].count("\n") + 1, quote=full,
                message=f"«{full}» — имя из принятой прозы, которого нет ни в досье 1.3, ни в континуити 3.3: "
                        f"новый факт прозы, не внесённый в канон по П6",
            ))
    return out


# ------------------------------------------------------------------------ прогон


def run_checks(library: Path, exports_dir: Path, briefs: list[Brief], matrix: list[MatrixFact],
               infobans: list[InfoBan], plants: list[Plant], parts: list[dict], acts: list[Act],
               known: set[str], reg_path: Path | None) -> list[LintFinding]:
    """Все проверки модуля по уже загруженным выгрузкам. Ничего не пишет в библиотеку."""
    volume = briefs[0].volume if briefs else 1
    reg = _Doc(library, reg_path)
    pog = _Doc(library, next(iter(exporter.volume_docs(library, "23_*.md", volume)), None))
    mtx_path = next(iter(exporter.volume_docs(library, "31_*.md", volume)), None)
    mtx = _Doc(library, mtx_path)
    acts_doc = _Doc(library, next(iter(exporter.volume_docs(library, "21_*.md", volume)), None))
    secret_rows = _secret_rows(reg)
    findings = check_matrix_presence(matrix, briefs, mtx)
    findings += check_matrix_order(matrix, briefs, mtx, _matrix_reader_cells(mtx_path))
    findings += check_secret_reveal(infobans, briefs, reg, secret_rows)
    findings += check_secret_knowers(infobans, matrix, known, reg, secret_rows)
    findings += check_plants(plants, briefs, exporter.load_doses(exports_dir), known, reg, pog, _plant_rows(reg))
    findings += check_poglavnik_head(briefs, known, pog)
    findings += check_act_parts(acts, parts, acts_doc)
    findings += check_focal_share(briefs, parts, known, reg, pog)
    findings += check_prose_names(library, known)
    return findings
