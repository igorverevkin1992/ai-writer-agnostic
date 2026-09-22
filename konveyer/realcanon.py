"""Разбор фактической структуры библиотеки «УГАР» (Д-1: парсер пишется под канон).

Реальный канон хранит нормы и правила в прозе и смешанных форматах:
- 02: числовые ориентиры — абзацем в §5 (уточнены Р-015);
- 03: лексические стоп-листы линий — в «Персональных запретах линий»;
- 04: анахронизмы — раздел Е, элементы через « · » с годами в скобках;
- 36: словарь наречий-усилителей — внутри решения Р-016(г);
- реестр информрежима: постраничная сетка (поглавник тома), реестр тайн,
  §7 — реестр дальних закладок;
- 31: широкая матрица (колонки — субъекты);
- 33: континуити — буллеты «факт · т.X гл.Y · статус»;
- досье: несколько карточек «# Досье 1.3: ИМЯ» в одном файле.

Табличные форматы демо-библиотеки остаются поддержанными в exporter;
сюда вынесены ветки реального формата.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import mdparse
from .mdparse import MarkupError, cell
from .schemas import (
    Act, Arc, Brief, ChronicleEvent, ChronologyEvent, CircleStep, ContinuityEvent, DocumentSpec, Dose, Dossier,
    InfoBan, MatrixFact,
    Norm, Plant, Scene,
    StopRule, StoryCircle,
)

CH_RE = re.compile(r"[Гг]л\.?\s*(\d+)")
# перечисление глав после одного «гл.»: «Гл. 9, 27», «Гл. 29 или 40», «гл. 34, 40»
CH_LIST_RE = re.compile(r"[Гг]л\.?\s*(\d+(?:\s*(?:,|или|и|/)\s*\d+)*)")
# том выстрела: «Том 2», «тома 9–10», «томов 2–5», «т.6»; «в томе N …» — оговорка, не выстрел
VOL_RE = re.compile(
    r"(?<!(?<![а-яё])[Вв]\s)[Тт]ом\w*\s*(\d+)(?:\s*[–-]\s*(\d+))?"
    r"|(?<!(?<![а-яё])[Вв]\s)(?<![а-яё])т\.?\s*(\d+)(?:\s*[–-]\s*(\d+))?"
)


def chapters_listed(text: str) -> list[int]:
    """Все главы из перечислений «гл. 9, 27» / «гл. 29 или 40» (аудит 3.1)."""
    nums: list[int] = []
    for m in CH_LIST_RE.finditer(text):
        nums.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    return list(dict.fromkeys(nums))


def volumes_listed(text: str) -> list[int]:
    """Тома выстрела с раскрытием диапазонов: «томов 2–5» → [2, 3, 4, 5]."""
    vols: list[int] = []
    for m in VOL_RE.finditer(text):
        lo = int(m.group(1) or m.group(3))
        hi = int(m.group(2) or m.group(4) or lo)
        vols.extend(range(lo, hi + 1) if hi >= lo else [lo])
    return list(dict.fromkeys(vols))


# --------------------------------------------------- имена в тексте канона

# падежные окончания имён: «Лемму», «Штерном», «Асю» (основа «Ас»), «куратору ОГПУ»
_NAME_ENDINGS = "ами|ями|ой|ей|ом|ем|ым|им|ою|ею|ах|ях|ов|ев|а|я|у|ю|е|и|ы"
_PSEUDO_SUBJECTS = {"Читатель"}  # субъект матрицы, не персонаж


def name_pattern(name: str) -> re.Pattern:
    """Регэксп имени в любом падеже и регистре: «Ася» → Ас(я|и|е|ю…), «Куратор ОГПУ» → куратор(у) ОГПУ."""
    first, *rest = name.split()
    stem = first[:-1] if len(first) > 2 and first[-1] in "аяь" else first
    pat = rf"(?<![А-Яа-яЁё]){re.escape(stem)}(?:{_NAME_ENDINGS})?(?![А-Яа-яЁё])"
    if rest:
        pat += r"\s+" + r"\s+".join(re.escape(r) for r in rest)
    return re.compile(pat, re.IGNORECASE)


def _name_matches(text: str, known_names: set[str]) -> list[tuple[str, re.Match]]:
    """Вхождения известных имён в тексте (по основе, без учёта регистра). Имя из нескольких слов
    («Куратор ОГПУ») находится и по одному первому слову («к куратору»), если оно единственное
    полное имя с таким началом; «Читатель» — не персонаж."""
    out: list[tuple[str, re.Match]] = []
    for n in known_names:
        if n in _PSEUDO_SUBJECTS:
            continue
        found = list(name_pattern(n).finditer(text))
        first = n.split()[0]
        if not found and " " in n and sum(1 for o in known_names if o.split()[0] == first) == 1:
            found = list(name_pattern(first).finditer(text))
        out.extend((n, m) for m in found)
    return out


def find_names(text: str, known_names: set[str]) -> list[str]:
    """Известные имена, встречающиеся в тексте (в любом падеже и регистре); из пары
    «Куратор» / «Куратор ОГПУ» остаётся более длинное."""
    result: set[str] = set()
    for n, _ in _name_matches(text, known_names):
        longer = [o for o in known_names if o != n and o.startswith(n + " ")]
        result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


# имя — участник действия, если стоит в именительном падеже, после предлога совместного действия
# («к Заварзину», «с Леммом», «на куратора», «за Леммом») или как дополнение глагола настоящего
# времени — сетка написана в настоящем («ведёт Степана», «прикрывает Лемма»); родительный при
# существительном («рапорт Степана», «стола Лемма») и дательный адресата («сказанная Степану»,
# «по спецу Лемму») — упоминание, не участие (аудит 1.11)
_ACTION_PREPS = {"к", "ко", "с", "со", "на", "за", "у", "против", "перед", "рядом", "вместе", "между", "при"}
_VERB_END_RE = re.compile(r"(?:[её]т|ит|[ую]т|[ая]т|ся|сь|ть|ти)$")


def _acts_in(text: str, m: re.Match, name: str) -> bool:
    form = m.group(0).split()[0]
    if form.lower() == name.split()[0].lower():
        return True  # именительный падеж (в том числе в перечислении «X, Y»)
    before = re.findall(r"[А-Яа-яЁё-]+", text[: m.start()])
    if not before:
        return False
    prev = before[-1].lower()
    return prev in _ACTION_PREPS or bool(_VERB_END_RE.search(prev))


def find_acting_names(text: str, known_names: set[str]) -> list[str]:
    """Имена, участвующие в действии (для события постраничной сетки): см. `_acts_in`."""
    result: set[str] = set()
    for n, m in _name_matches(text, known_names):
        if _acts_in(text, m, n):
            longer = [o for o in known_names if o != n and o.startswith(n + " ")]
            result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


# ------------------------------------------------------------------ нормы 02


def parse_norms_prose(path: Path) -> dict[str, Norm] | None:
    """Числовые ориентиры из прозы §5 стилевого регламента (Р-015)."""
    text = path.read_text(encoding="utf-8")
    if "Числовые ориентиры" not in text:
        return None
    src = f"{path.name} §5 (Р-015)"
    norms: dict[str, Norm] = {}

    m = re.search(r"средняя фраза\s*(\d+)\s*[–-]\s*(\d+)\s*слов", text)
    b = re.search(r"средняя ниже\s*(\d+)\s*[—-]+\s*брак", text)
    if not m:
        raise MarkupError(path, 1, "в «Числовых ориентирах» не найден коридор средней фразы")
    norms["средняя_длина"] = Norm(
        min=float(m.group(1)), max=float(m.group(2)),
        brak=float(b.group(1)) if b else None, unit="слов", source=src,
    )

    m = re.search(r"короткие\s*\(≤\s*(\d+)\)\s*[—-]+\s*(\d+)\s*[–-]\s*(\d+)\s*%", text)
    if not m:
        raise MarkupError(path, 1, "не найдена доля коротких фраз")
    norms["короткая_фраза_порог"] = Norm(min=float(m.group(1)), max=float(m.group(1)), unit="слов", source=src)
    norms["доля_коротких"] = Norm(min=int(m.group(2)) / 100, max=int(m.group(3)) / 100, unit="доля", source=src)

    m = re.search(r"длинные\s*\(≥\s*(\d+)\)\s*[—-]+\s*до\s*(\d+)\s*%", text)
    if not m:
        raise MarkupError(path, 1, "не найдена доля длинных фраз")
    norms["длинная_фраза_порог"] = Norm(min=float(m.group(1)), max=float(m.group(1)), unit="слов", source=src)
    norms["доля_длинных"] = Norm(max=int(m.group(2)) / 100, unit="доля", source=src)

    m = re.search(r"«был/было»\s*[—-]+\s*не чаще\s*(\d+)\s*на\s*(\d+)\s*слов", text)
    if not m:
        raise MarkupError(path, 1, "не найдена норма «был/было»")
    norms["был_на_250"] = Norm(max=float(m.group(1)) * 250 / float(m.group(2)), unit="шт/250 слов", source=src)

    m = re.search(r"TTR\s*≥\s*([\d.,]+)\s*на окне\s*(\d+)\s*тыс", text)
    if not m:
        raise MarkupError(path, 1, "не найдена норма TTR")
    brak_ttr = re.search(r"переводной уровень\s*([\d.,]+)\s*=\s*брак", text)
    norms["ttr_мин"] = Norm(
        min=float(m.group(1).replace(",", ".")),
        brak=float(brak_ttr.group(1).replace(",", ".")) if brak_ttr else None,
        unit="доля", source=src,
    )
    norms["ttr_окно_слов"] = Norm(min=float(m.group(2)) * 1000, max=float(m.group(2)) * 1000, unit="слов", source=src)

    m2 = re.search(r"диалог\s*~?\s*(\d+)\s*[–-]\s*(\d+)\s*%", text)
    if m2:  # «диалог ~15–20 % строк»
        norms["доля_диалога"] = Norm(min=int(m2.group(1)) / 100, max=int(m2.group(2)) / 100, unit="доля строк", source=src)
    m2 = re.search(r"[Аа]бзац\s*—\s*как правило,\s*(\d+)\s*[–-]\s*(\d+)\s*фраз", text)
    if m2:  # «Абзац — как правило, 2–5 фраз; однострочный абзац — приём, не норма»
        norms["фраз_в_абзаце"] = Norm(min=float(m2.group(1)), max=float(m2.group(2)), unit="фраз", source=src)

    m = re.search(r"объём главы\s*[—-]+\s*(\d+)\s*[–-]\s*(\d+)\s*слов", text)
    if m:  # необязательная норма (Р-019)
        norms["объём_главы"] = Norm(min=float(m.group(1)), max=float(m.group(2)), unit="слов", source=src)
    return norms


def parse_intensifier_norm(journal: Path) -> tuple[list[str], Norm] | None:
    """Р-016(г): словарь наречий-усилителей и порог «>N на тысячу слов»."""
    if not journal.exists():
        return None
    text = journal.read_text(encoding="utf-8")
    m = re.search(r"усилителей\s*\(([^)]*)\)", text)
    if not m:
        return None
    words = re.findall(r"«([^»]+)»", m.group(1))
    thr = re.search(r">\s*(\d+)\s*на тысячу", m.group(1))
    if not words or not thr:
        return None
    return words, Norm(max=float(thr.group(1)), unit="шт/1000 слов", source=f"{journal.name} Р-016(г)")


# --------------------------------------------------------- стоп-листы 03/04


def focal_names(path: Path) -> set[str]:
    """Имена линий из 03: заголовки «**Имя…**» персональных запретов и таблица фокалов."""
    names: set[str] = set()
    sections = mdparse.parse_sections(path)
    sec = mdparse.find_section(sections, r"[Пп]ерсональные запреты")
    if sec:
        for line in sec.body.splitlines():
            m = re.match(r"\*\*([А-ЯЁ][а-яё]+)", line.strip())
            if m:
                names.add(m.group(1))
    for table in mdparse.parse_tables(path):
        if any("Фокальные" in h for h in table.headers):
            for row in table.rows:
                for cell_text in row.values():
                    # ячейка-предложение «Британец — никогда не фокален» — оговорка, не список имён
                    if re.match(r"^\s*[А-ЯЁ][а-яё]+\s+[—–-]\s", cell_text):
                        continue
                    names.update(re.findall(r"\b([А-ЯЁ][а-яё]{2,})\b", cell_text))
    return names - {"Тома", "Без", "Открывается"}


def parse_focal_stoplists(path: Path) -> list[StopRule]:
    """«Персональные запреты линий»: строки со «стоп-лист» → слова в «кавычках»."""
    sec = mdparse.find_section(mdparse.parse_sections(path), r"[Пп]ерсональные запреты")
    if sec is None:
        return []
    rules: list[StopRule] = []
    focal = ""
    for line in sec.body.splitlines():
        header = re.match(r"\*\*([А-ЯЁ][а-яё]+)", line.strip())
        if header:
            focal = header.group(1)
        if "стоп-лист" in line.lower() and focal:
            forbidden_part = re.split(r"[Рр]азрешен", line)[0]  # после «Разрешено:» — не запреты
            words = re.findall(r"«([^»]+)»", forbidden_part)
            if words:
                rules.append(
                    StopRule(
                        scope="0.3", rule_id=f"0.3-{focal}", items=words,
                        applies_to={"focal": focal}, action="запрет",
                    )
                )
    return rules


def parse_line_prose_bans(path: Path) -> list[StopRule]:
    """«Персональные запреты линий» 03 фразами, а не словами: «канцелярит — панцирь страха»,
    «сентимент запрещён», «Степан не подозревает Штерна». Э2 без них не может проверить 4.1
    (лексический стоп-лист даёт только слова)."""
    sec = mdparse.find_section(mdparse.parse_sections(path), r"[Пп]ерсональные запреты")
    if sec is None:
        return []
    rules: list[StopRule] = []
    focal = ""
    items: list[str] = []

    def flush() -> None:
        if focal and items:
            rules.append(StopRule(scope="0.3", rule_id=f"0.3-проза-{focal}", items=list(items),
                                  applies_to={"focal": focal}, action="запрет", kind="проза"))
    for line in sec.body.splitlines():
        header = re.match(r"\*\*([А-ЯЁ][а-яё]+)", line.strip())
        if header:
            flush()
            focal, items = header.group(1), []
            continue
        text = line.strip()
        if not text.startswith("- ") or "стоп-лист" in text.lower():
            continue  # лексический стоп-лист разбирается отдельно (parse_stoplists)
        items.append(re.sub(r"\s+", " ", text[2:]).strip())
    flush()
    return rules


CHRONICLE_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _chronicle_month(date: str) -> int | None:
    m = re.search(r"\b\d{1,2}\.(\d{2})\b", date)
    if m:
        return int(m.group(1))
    low = date.lower()
    for stem, num in CHRONICLE_MONTHS.items():
        if stem in low:
            return num
    return None


def parse_chronicle(path: Path) -> list[ChronicleEvent]:
    """Историческая хроника 17: таблицы «Дата | Событие | Статус» (чек-лист 4.2, анахронизмы)."""
    out: list[ChronicleEvent] = []
    for table in mdparse.parse_tables(path):
        if "Дата" not in table.headers or "Событие" not in table.headers:
            continue
        for row in table.rows:
            date = cell(row, "Дата")
            event = cell(row, "Событие")
            if not date or not event:
                continue
            status = cell(row, "Статус") or "✓"
            out.append(ChronicleEvent(date=date, event=event, status=status, month=_chronicle_month(date)))
    return out


# ------------------------------------------------- генеральная хронология 12

# «**Ф-1926-02** · 15.04 · событие · участники · [Читатель: гл.3, 6] · Закладка → т.6»
CHRONOLOGY_RE = re.compile(r"^\*\*(Ф-(\d{4})-(\d+))\*\*\s*·\s*(.+)$")
# «## Цикл I. Том 1 — 1926 (детализация…)», «## Том 11 — 1946–1947»
CHRONOLOGY_VOL_RE = re.compile(r"Том\s*(\d+)\s*[—–-]\s*(\d{4})(?:\s*[–-]\s*(\d{4}))?")
_VIS_VOL_RE = re.compile(r"т\.\s*(\d+)(?:\s*[–-]\s*(\d+))?", re.IGNORECASE)
# «гл.3, 6», «гл.31–32», «гл. 45–46» — перечисление и диапазон после одного «гл.»
_VIS_CH_RE = re.compile(r"гл\.?\s*(\d+(?:\s*[,–-]\s*\d+)*)", re.IGNORECASE)


def _span(m: re.Match) -> list[int]:
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return list(range(lo, hi + 1)) if 0 < hi - lo < 50 else [lo, hi] if hi != lo else [lo]


def _numbers(text: str) -> list[int]:
    """«3, 6» → [3, 6]; «31–32» → [31, 32]; «45–46» → [45, 46]."""
    out: list[int] = []
    for part in text.split(","):
        edges = [int(x) for x in re.findall(r"\d+", part)]
        if len(edges) == 2 and 0 < edges[1] - edges[0] < 50:
            out.extend(range(edges[0], edges[1] + 1))
        else:
            out.extend(edges)
    return out


def parse_chronology(path: Path) -> list[ChronologyEvent]:
    """Генеральная хронология фабулы 12: строки «**Ф-ГОД-№** · дата · событие · [участники] ·
    [видимость] · [хвост]», сгруппированные разделами «## Том N — ГОД».

    Поле видимости узнаётся по «[»: всё до него после даты — событие и участники, всё после —
    хвост (закладки, «(Р-007)»). Тома и главы видимости разбираются в `volumes`/`chapters`,
    том и годы раздела — в `volume`/`section_years` (единственная в каноне карта «том → год»)."""
    out: list[ChronologyEvent] = []
    section = ""
    volume: int | None = None
    years: list[int] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("##"):
            section = line.lstrip("#").strip()
            vm = CHRONOLOGY_VOL_RE.search(section)
            volume = int(vm.group(1)) if vm else None
            years = [int(vm.group(2))] + ([int(vm.group(3))] if vm and vm.group(3) else []) if vm else []
            continue
        m = CHRONOLOGY_RE.match(line)
        if not m:
            continue
        fields = [f.strip() for f in m.group(4).split("·")]
        # у фоновых исторических строк поля даты нет вовсе: «**Ф-1927-01** · (ист.) Крах «Треста»… · [Фон]»
        dated = bool(fields) and len(fields[0]) <= 30 and not fields[0].startswith("(ист")
        date = fields[0] if dated else ""
        rest = fields[1:] if dated else fields
        vis_at = next((k for k, f in enumerate(rest) if f.startswith("[")), None)
        if vis_at is None:
            event, participants, visibility, note = " · ".join(rest), "", "", ""
        else:
            event = rest[0] if vis_at > 0 else ""
            participants = " · ".join(rest[1:vis_at])
            visibility = rest[vis_at]
            note = " · ".join(rest[vis_at + 1:])
        volumes: list[int] = []
        chapters: list[int] = []
        for vm2 in _VIS_VOL_RE.finditer(visibility):
            volumes.extend(_span(vm2))
        for cm in _VIS_CH_RE.finditer(visibility):
            chapters.extend(_numbers(cm.group(1)))
        out.append(ChronologyEvent(
            event_id=m.group(1), year=int(m.group(2)), date=date, event=event, participants=participants,
            visibility=visibility, volumes=sorted(set(volumes)), chapters=sorted(set(chapters)),
            volume=volume, section=section, section_years=years,
            historical="(ист" in line, hidden="[Скрыто" in visibility, background="[Фон" in visibility,
            open_question="⚠" in line, note=note, line=i,
        ))
    return out


def _clean_item(item: str) -> list[str]:
    """Элемент стоп-листа: убрать пояснения/маркеры, разбить перечисления."""
    item = re.sub(r"\(.*?\)", "", item)               # скобочные пояснения
    item = re.sub(r"[✓⚠🔧].*$", "", item)             # статусные маркеры и хвосты
    item = re.split(r"\s+как\s+", item)[0]            # «вредитель как штамп»
    words = []
    for piece in re.split(r"[,/]", item):
        piece = piece.strip(" .;:—-").strip()
        # описательные обороты («любые кальки телепроцедурала») — не лексические единицы
        if piece and not piece.startswith("«") and len(piece) > 1 and len(piece.split()) <= 3 \
                and not piece.lower().startswith("любые"):
            words.append(piece.strip("«»"))
    return words


def parse_anachronisms(path: Path) -> list[StopRule]:
    """Раздел Е языкового канона: «Запрещено до года» и «Запрещено навсегда»."""
    sec = mdparse.find_section(mdparse.parse_sections(path), r"[Сс]топ-лист анахронизмов")
    if sec is None:
        return []
    rules: list[StopRule] = []
    banned: set[str] = set()
    for line in sec.body.splitlines():
        line = line.strip()
        forever = line.startswith("**Запрещено навсегда")
        dated = line.startswith("**Запрещено до")
        if not (forever or dated):
            continue
        payload = line.split(":**", 1)[-1]
        for i, raw_item in enumerate(payload.split("·")):
            year_m = re.search(r"до\s+(\d{4})", raw_item)
            words = _clean_item(raw_item)
            if not words:
                continue
            applies: dict = {"all": True}
            if dated and year_m:
                applies = {"year": {"before": int(year_m.group(1))}}
            rules.append(
                StopRule(
                    scope="0.4",
                    rule_id=f"0.4-Е-{'нав' if forever else 'год'}-{i + 1}",
                    items=words, applies_to=applies, action="запрет",
                )
            )
            banned.update(w.lower() for w in words)
    # Ж-3: слово со статусом ⚠ в прозе = флаг («глухарь ⚠», «мент ⚠» — ложные друзья и датировки)
    flagged = [
        w for w in dict.fromkeys(m.group(1) for m in re.finditer(r"([а-яё][а-яё-]*)\s*⚠", sec.body))
        if w.lower() not in banned
    ]
    if flagged:
        rules.append(StopRule(scope="0.4", rule_id="0.4-Е-⚠", items=flagged, applies_to={"all": True}, action="флаг"))
    return rules


# ------------------------------------------------- поглавник (реестр + 23)


def registry_year_volume(path: Path) -> tuple[int | None, int]:
    head = path.read_text(encoding="utf-8")[:200]
    y = re.search(r"\((\d{4})\)", head)
    v = re.search(r"Том\s*(\d+)", head)
    return (int(y.group(1)) if y else None, int(v.group(1)) if v else 1)


def normalize_name(raw: str, known_names: set[str]) -> str:
    """«Лемма» (глазами Лемма) → «Лемм», «куратор ОГПУ» → «Куратор ОГПУ», «куратор» → «Куратор ОГПУ»
    (единственное полное имя с таким началом): известное имя в начале строки, в любом падеже."""
    raw = raw.strip()
    word = raw.split()[0] if raw else ""
    for name in sorted(known_names, key=len, reverse=True):
        if name_pattern(name).match(raw):
            return name
    for name in sorted(known_names, key=len, reverse=True):
        first = name.split()[0]
        if " " in name and name_pattern(first).match(word) \
                and sum(1 for o in known_names if o.split()[0] == first) == 1:
            return name
    return word


def parse_registry_briefs(path: Path, known_names: set[str] | None = None) -> list[Brief]:
    """Постраничная сетка реестра: | Гл. | Фокал | Дата | Событие | … | на весь том."""
    year, volume = registry_year_volume(path)
    known_names = known_names or set()
    briefs: list[Brief] = []
    for table in mdparse.parse_tables(path):
        headers = [h.lower() for h in table.headers]
        if not any("фокал" in h for h in headers) or not any("гл" in h for h in headers):
            continue
        for row in table.rows:
            ch = mdparse.parse_number(cell(row, "Гл"))
            if ch is None:
                continue
            focal_raw = cell(row, "Фокал")
            focal = focal_raw
            eyes = re.search(r"глазами\s+([А-ЯЁ][а-яё]+)", focal)
            if eyes:
                focal = eyes.group(1)
            focal = normalize_name(focal, known_names)
            event = cell(row, "Событие")
            # участники сцены — имена, участвующие в действии события, и «X глазами Y» (аудит 3.5, 1.11)
            participants = [n for n in find_acting_names(f"{focal_raw} · {event}", known_names) if n != focal]
            briefs.append(
                Brief(
                    chapter=int(ch), volume=volume, year=year,
                    focal=focal,
                    date=cell(row, "Дата"),
                    beats=[event] if event else [],
                    scenes=[],
                    not_knows=[],
                    bans=[],
                    participants=participants,
                    volume_words=None,
                    plants=[],
                    reader_learns=cell(row, "знает читатель"),
                )
            )
    return briefs


POGLAVNIK_HEAD_RE = re.compile(r"##\s*Гл\.?\s*(\d+)\s*·\s*([^·]+)·\s*фокал\s+([А-ЯЁ]+)", re.IGNORECASE)
SCENE_RE = re.compile(r"\*\*Сц\.\s*([\d.]*?\d)\.?\*\*\s*(.+)")
# «**→ ДОКУМЕНТ №1** (после главы): первый рапорт — …» → документ-вставка главы (аудит 3.6)
DOCUMENT_RE = re.compile(r"\*\*→\s*ДОКУМЕНТ\s*№?\s*(\d+)\*\*\s*(?:\(([^)]*)\))?\s*:?\s*(.*)")
# строки главы после сцен: «**Запреты.** …; …» и «**Не знает.** …; …» (аудит 2, 1.8)
CHAPTER_FIELD_RE = re.compile(r"\*\*(Запреты|НЕ знает|Не знает)\.?\*\*\s*(.+)", re.IGNORECASE)
# время сцены — хвост поля «место» после запятой: «…, за полночь», «…, утро»
SCENE_TIME_RE = re.compile(
    r",\s*([^,]*\b(?:утро|утром|вечер|вечером|ночь|ночью|за полночь|день|днём|рассвет|полдень|сумерки)\b[^,]*)$",
    re.IGNORECASE,
)
_PLANTS_RE = re.compile(r"(?:^|[;·])\s*кладём:\s*", re.IGNORECASE)
_ENTERS_RE = re.compile(r"(?:^|[;·])\s*входит:\s*")
_EXITS_RE = re.compile(r"(?:^|[;·])\s*выходит(?:\s+[^:;·]*)?:\s*")


def _split_items(text: str) -> list[str]:
    """«а; б (в; г); д.» → [«а», «б (в; г)», «д»] — «;» внутри скобок не делит."""
    items, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            items.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [it.strip(" .\t") for it in items if it.strip(" .\t")]


def parse_scene(number: str, text: str) -> Scene:
    """Строка сцены поглавника → карточка: место · участники · цель [· …] · входит: …; выходит: … · кладём: …

    «входит:»/«выходит:» находятся и внутри поля цели (гл. 6.1: «…; выходит: …»); всё, что стоит
    между участниками и «входит/выходит», — цель (дополнительные поля через «·» приклеиваются к ней);
    «кладём: …» — элементы через «;» (скобки не делят)."""
    # «кладём: …» — хвост строки (стоит и после «·», и после «;» — гл. 7.1); всё после него — закладки
    km = _PLANTS_RE.search(text)
    plants = _split_items(text[km.end():]) if km else []
    fields = [p.strip() for p in (text[: km.start()] if km else text).split("·")]
    fields = [f for f in fields if f]
    place = fields[0] if fields else ""
    time = ""
    tm = SCENE_TIME_RE.search(place)
    if tm:
        time, place = tm.group(1).strip(), place[: tm.start()].strip()
    participants = fields[1] if len(fields) > 1 else ""
    rest = " · ".join(fields[2:]).strip()
    m_in, m_out = _ENTERS_RE.search(rest), _EXITS_RE.search(rest)
    cut = min(m.start() for m in (m_in, m_out) if m) if (m_in or m_out) else len(rest)
    goal = rest[:cut].strip(" ·;")
    enters = exits = ""
    if m_in:
        stop = m_out.start() if m_out and m_out.start() > m_in.start() else len(rest)
        enters = rest[m_in.end(): stop].strip(" ·;.")
    if m_out:
        stop = m_in.start() if m_in and m_in.start() > m_out.start() else len(rest)
        exits = rest[m_out.end(): stop].strip(" ·;.")
    return Scene(
        number=number, place=place.rstrip(" ."), time=time, participants=participants.rstrip(" ."),
        goal=goal.rstrip(" ."), enters=enters, exits=exits, plants=plants,
    )


def enrich_from_poglavnik(briefs: list[Brief], path: Path, known_names: set[str]) -> None:
    """Обогащение брифов сценами, участниками, документами-вставками и полями «Запреты»/«Не знает»
    из рабочего поглавника (23).

    Сцена → `scene_cards` (структурно) и `scenes` (строка без «кладём: …» — совместимость);
    «кладём: …» — в `Scene.plants`, в биты НЕ идёт (аудит 2, 1.10). Строки главы
    «**Запреты.** …; …» / «**Не знает.** …; …» → `bans` / `not_knows` (аудит 2, 1.8)."""
    by_ch = {b.chapter: b for b in briefs}
    current: Brief | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = POGLAVNIK_HEAD_RE.match(line.strip())
        if m:
            current = by_ch.get(int(m.group(1)))
            continue
        if current is None:
            continue
        dm = DOCUMENT_RE.match(line.strip())
        if dm:
            position = (dm.group(2) or "после главы").strip()
            current.documents.append(f"№{dm.group(1)} ({position}): {dm.group(3).strip()}")
            continue
        fm = CHAPTER_FIELD_RE.match(line.strip())
        if fm:
            target = current.bans if fm.group(1).lower() == "запреты" else current.not_knows
            target.extend(_split_items(fm.group(2)))
            continue
        sm = SCENE_RE.match(line.strip())
        if sm:
            scene = parse_scene(sm.group(1), sm.group(2))
            current.scene_cards.append(scene)
            parts = [p.strip() for p in sm.group(2).split("·")]
            current.scenes.append(" · ".join(p for p in parts if not p.lower().startswith("кладём")))
            for name in find_names(scene.participants, known_names):
                if name not in current.participants and name != current.focal:
                    current.participants.append(name)


# --------------------------------------------- дозы прошлого (§5) и документы (§6)

_SENT_RE = re.compile(r"(?<=[.!?»])\s+(?=[А-ЯЁ«])")
_DOSE_REF_RE = re.compile(r"доз[аеуы]?\s*№\s*(\d+)", re.IGNORECASE)
_SCALE_ITEM_RE = re.compile(r"№\s*(\d+)\s*[–-]\s*(\d+)\s*[—–-]\s*(.+?)(?=;\s*№|\.\s|\.$|$)")


def _section_parts(path: Path, title_pattern: str) -> tuple[str, str, list[mdparse.Table], list[str]] | None:
    """Секция реестра по заголовку: (уточнение в скобках заголовка, вводный абзац до таблицы,
    таблицы, абзацы после таблицы)."""
    sec = mdparse.find_section(mdparse.parse_sections(path), title_pattern)
    if sec is None:
        return None
    kind_m = re.search(r"\(([^)]*)\)", sec.title)
    tables = mdparse.parse_tables(path, sec.body, start_line=sec.line + 1)
    intro: list[str] = []
    after: list[str] = []
    seen_table = False
    for line in sec.body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            seen_table = True
            continue
        if not stripped or stripped == "---":
            continue
        (after if seen_table else intro).append(stripped)
    return (kind_m.group(1).strip() if kind_m else ""), " ".join(intro), tables, after


def _sentences(text: str) -> list[str]:
    return [t.strip() for t in _SENT_RE.split(text) if t.strip()]


def parse_doses(path: Path) -> list[Dose]:
    """§5 реестра «Три дозы 1913 года»: | Доза | Глава | Триггер | Что получает читатель | Чего НЕ получает |
    и абзац «Правило доз: …» после таблицы. Фраза правила с адресом «в дозе №N» относится только к дозе N,
    остальные фразы — к каждой (так «печь в мае» не попадает в окна доз №2–3)."""
    found = _section_parts(path, r"[Дд]оз[аы]")
    if found is None:
        return []
    _, intro, tables, after = found
    _, volume = registry_year_volume(path)
    rule_text = next((a.split(":", 1)[1].strip() for a in after if a.lower().startswith("правило доз")), "")
    rule_sentences = _sentences(rule_text)
    doses: list[Dose] = []
    for table in tables:
        headers = " ".join(table.headers).lower()
        if "доза" not in headers or "триггер" not in headers:
            continue
        for row in table.rows:
            ch = mdparse.parse_number(cell(row, "Глав"))
            if ch is None:
                continue
            dose_id = cell(row, "Доза")
            num = re.search(r"\d+", dose_id)
            own = [
                st for st in rule_sentences
                if not _DOSE_REF_RE.search(st) or (num and int(num.group()) in {int(x) for x in _DOSE_REF_RE.findall(st)})
            ]
            doses.append(
                Dose(
                    dose_id=dose_id, chapter=int(ch), volume=volume,
                    trigger=cell(row, "Триггер"),
                    reader_gets=cell(row, "получает читатель"),
                    reader_not_gets=cell(row, "НЕ получает"),
                    form=intro,
                    rule=" ".join(own),
                )
            )
    return doses


def parse_documents(path: Path) -> list[DocumentSpec]:
    """§6 реестра «Реестр документов»: | № | После гл. | Стиль | Расхождение с правдой … | и абзац
    «Языковая шкала рапортов …: №1–3 — …; №4–6 — …» — документу достаётся строка шкалы своего диапазона."""
    found = _section_parts(path, r"[Рр]еестр документов")
    if found is None:
        return []
    kind, intro, tables, after = found
    _, volume = registry_year_volume(path)
    scale_text = next((a.split(":", 1)[1] for a in after if a.lower().startswith("языковая шкала")), "")
    scale_items = [
        (int(m.group(1)), int(m.group(2)), f"№{m.group(1)}–{m.group(2)} — {m.group(3).strip()}")
        for m in _SCALE_ITEM_RE.finditer(scale_text)
    ]
    docs: list[DocumentSpec] = []
    for table in tables:
        headers = " ".join(table.headers).lower()
        if "после гл" not in headers or "стиль" not in headers:
            continue
        for row in table.rows:
            num = mdparse.parse_number(cell(row, "№"))
            ch = mdparse.parse_number(cell(row, "После гл"))
            if num is None or ch is None:
                continue
            docs.append(
                DocumentSpec(
                    number=int(num), after_chapter=int(ch), volume=volume, kind=kind,
                    style=cell(row, "Стиль"),
                    divergence=cell(row, "Расхождение"),
                    form=intro,
                    scale=next((text for lo, hi, text in scale_items if lo <= int(num) <= hi), ""),
                )
            )
    return docs


# ------------------------------------------------------------- матрица 31


def parse_wide_matrix(path: Path) -> list[MatrixFact]:
    """Широкая матрица: строки — факты, колонки — субъекты."""
    for table in mdparse.parse_tables(path):
        if "Факт" not in table.headers or len(table.headers) < 5:
            continue
        subject_cols = [h for h in table.headers if h not in ("#", "Факт")]
        facts: list[MatrixFact] = []
        for row in table.rows:
            num = mdparse.parse_number(row.get("#", "")) or len(facts) + 1
            fact_text = row.get("Факт", "")
            for subj in subject_cols:
                raw = row.get(subj, "").strip()
                if not raw:
                    continue
                # курсив *…* = частичное/неверное знание; жирный **…** — просто выделение
                partial = raw.startswith("*") and raw.endswith("*") and not raw.startswith("**")
                clean = raw.strip("*").strip()
                if clean in ("—", "-", ""):
                    from_ch: int | None = None
                elif clean.lower().startswith("всегда") or clean.lower().startswith("пролог"):
                    from_ch = 0
                elif subj == "Читатель":
                    from_ch = reveal_chapter(clean)  # «улики с гл.4; расчётная разгадка ≈гл.20» → 20
                else:
                    chm = CH_RE.search(clean)
                    from_ch = int(chm.group(1)) if chm else None
                source = clean.split("/", 1)[1].strip() if "/" in clean else ""
                facts.append(
                    MatrixFact(
                        fact_id=f"М-{int(num):02d}",
                        fact=fact_text,
                        subject=subj,
                        from_chapter=from_ch,
                        source=source,
                        note=("частично/неверно: " + clean) if partial else ("" if from_ch is not None else clean),
                    )
                )
        if facts:
            return facts
    raise MarkupError(path, 1, "не найдена матрица (широкая таблица с колонкой «Факт»)")


# ---------------------------------------------------------- закладки (§7)


def parse_plants_registry(path: Path) -> list[Plant]:
    """§7 реестра: | Закладка | Где лежит | Где стреляет |."""
    _, volume = registry_year_volume(path)
    plants: list[Plant] = []
    for table in mdparse.parse_tables(path):
        headers = " ".join(table.headers).lower()
        if "закладка" not in headers or "стреляет" not in headers:
            continue
        for i, row in enumerate(table.rows, start=1):
            placed_raw = cell(row, "лежит")
            chapters = chapters_listed(placed_raw)  # «Гл. 9, 27» → обе главы (аудит 3.1)
            if re.search(r"[Пп]ролог", placed_raw):
                chapters.insert(0, 0)
            fires = [{"vol": vol} for vol in volumes_listed(cell(row, "стреляет"))]
            plants.append(
                Plant(
                    plant_id=f"З-{i:02d}",
                    what=cell(row, "Закладка"),
                    placed={"vol": volume, "ch": chapters[0]} if chapters else {"vol": volume},
                    chapters=chapters,
                    fires=fires,
                    status=placed_raw if not chapters else "",
                )
            )
    return plants


_NOT_MENTIONED_RE = re.compile(r"НЕ упомина\w*\s+в\s+томе\s*(\d+)", re.IGNORECASE)


def parse_plant_bans(path: Path) -> list[InfoBan]:
    """§7: строка «НЕ упоминается в томе N» (Красный Крест / Ватикан) — не закладка,
    а запрет информрежима до тома N включительно: в окне — «НЕ упоминать» (аудит 1.5)."""
    bans: list[InfoBan] = []
    for table in mdparse.parse_tables(path):
        headers = " ".join(table.headers).lower()
        if "закладка" not in headers or "стреляет" not in headers:
            continue
        for i, row in enumerate(table.rows, start=1):
            m = _NOT_MENTIONED_RE.search(cell(row, "лежит"))
            if m:
                bans.append(
                    InfoBan(ban_id=f"З-{i:02d}", text=cell(row, "Закладка"), until_volume=int(m.group(1)), secret=False)
                )
    return bans


# ------------------------------------------------------- континуити 33


# ссылка «т.1 гл.4», «т.1 гл.1, 4», «гл.5» — где угодно в буллете; «т.8» без главы (статус) — не ссылка
_CONT_REF_RE = re.compile(r"(?:т\.?\s*(\d+)\s*)?гл\.?\s*(\d+(?:\s*,\s*\d+)*)", re.IGNORECASE)
NO_SOURCE = "без привязки"  # факт без ссылки на главу (открытое решение)


def parse_continuity_bullets(path: Path) -> list[ContinuityEvent]:
    """Буллеты «факт · т.X гл.Y · статус» по секциям трекера (аудит 3.7).

    date — первая ссылка на источник («т.1 гл.4»; строки Э2 без пустых дат),
    chapters — номера глав через запятую, note — последнее поле (статус)."""
    events: list[ContinuityEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("·")]
        if len(parts) < 2:
            continue
        refs = list(_CONT_REF_RE.finditer(line))
        chapters = list(dict.fromkeys(int(x) for m in refs for x in re.findall(r"\d+", m.group(2))))
        if refs:
            first = refs[0]
            date = (f"т.{first.group(1)} " if first.group(1) else "") + "гл." + re.sub(r"\s*,\s*", ", ", first.group(2))
        else:
            date = NO_SOURCE
        events.append(
            ContinuityEvent(
                date=date,
                event=re.sub(r"\*\*", "", parts[0]),
                chapters=", ".join(str(c) for c in chapters),
                note=re.sub(r"\*\*", "", parts[-1]) if len(parts) > 2 else "",
            )
        )
    return events


# ------------------------------------------------------ информрежим (тайны)


def reveal_chapter(text: str) -> int | None:
    """Глава раскрытия читателю из ячейки: «гл. 32», «расчётная разгадка ≈гл.20» (число после «разгадк»);
    «улики с гл. 4» без разгадки — не раскрытие (None)."""
    m = re.search(r"разгадк[а-я]*\D{0,25}?гл\.?\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.search(r"улик", text, re.IGNORECASE):
        return None
    m = CH_RE.search(text)
    return int(m.group(1)) if m else None


# явная ссылка на факт матрицы в строке реестра: «М-12», «факт 12»
_FACT_REF_RE = re.compile(r"\bМ-(\d+)\b|\bфакт[а-я]*\s+(\d+)\b", re.IGNORECASE)
# слова, не считающиеся значимыми при сопоставлении тайны с фактом матрицы
_MATCH_STOP = {
    "года", "году", "год", "полная", "картина", "слой", "кто", "что", "как", "его", "при", "для", "все", "или",
    "это", "том", "томе", "тома", "нет", "без", "над", "под", "про", "где", "уже", "ответ", "никто",
}


def _match_words(text: str) -> set[str]:
    """Значимые основы: слова ≥3 букв (первые 5 букв — «подлог»/«подложный», «рапорт»/«рапорте») и годы."""
    stems = {w[:5] for w in re.findall(r"[а-яё]{3,}", text.lower()) if w not in _MATCH_STOP}
    return stems | set(re.findall(r"\b\d{4}\b", text))


def _match_matrix_fact(secret: str, matrix: list[MatrixFact]) -> str | None:
    """fact_id факта матрицы 3.1 для строки реестра тайн (аудит 1.4).

    Явная ссылка «М-12»/«факт 12» в тексте тайны решает всё. Иначе — факт с наибольшим числом общих
    значимых основ, не меньше 3 (или всех основ тайны, если их меньше); при равенстве лучших — None."""
    ref = _FACT_REF_RE.search(secret)
    if ref:
        num = int(ref.group(1) or ref.group(2))
        return next((f.fact_id for f in matrix if f.fact_id == f"М-{num:02d}"), None)
    target = _match_words(secret)
    if not target:
        return None
    scores: list[tuple[int, str]] = []
    seen: set[str] = set()
    for f in matrix:
        if f.subject != "Читатель" or f.fact_id in seen:
            continue
        seen.add(f.fact_id)
        scores.append((len(target & _match_words(f.fact)), f.fact_id))
    scores.sort(key=lambda x: -x[0])
    if not scores or scores[0][0] < min(3, len(target)):
        return None
    if len(scores) > 1 and scores[1][0] == scores[0][0]:
        return None  # неоднозначно — лучше без сопоставления, чем ложное знание
    return scores[0][1]


def _matrix_knowledge(fact_id: str, matrix: list[MatrixFact]) -> tuple[int | None, dict[str, int]]:
    """(глава раскрытия читателю, {персонаж: глава полного знания}) по строке матрицы."""
    reader: int | None = None
    known: dict[str, int] = {}
    for f in matrix:
        if f.fact_id != fact_id or f.from_chapter is None:
            continue
        if f.subject == "Читатель":
            reader = f.from_chapter
        elif not f.note.startswith("частично"):
            known[f.subject] = f.from_chapter
    return reader, known


def _parse_known_by(text: str, known_names: set[str]) -> dict[str, int | None]:
    """«Лемм, Штерн; больше никто», «Степан, куратор ОГПУ; Штерн — с гл. 7; Лемм — с гл. 17»,
    «Лемм; Заварзин узнает в томе 3» → {имя: глава}. Имя без главы — None («неизвестно когда»,
    аудит 1.5): главу даёт матрица 3.1, а без неё — 0 (знает всегда)."""
    known: dict[str, int | None] = {}
    for chunk in re.split(r"[;,]", text):
        chunk = chunk.strip()
        if not chunk or re.search(r"\b(никто|не узнает|не узнаёт)\b", chunk, re.IGNORECASE):
            continue
        if re.search(r"в томе\s*\d+", chunk, re.IGNORECASE):
            continue  # узнаёт в другом томе — в этом не знает
        name = normalize_name(re.sub(r"^(только|больше)\s+", "", chunk, flags=re.IGNORECASE), known_names)
        if name not in known_names:
            continue
        chm = CH_RE.search(chunk)
        known[name] = int(chm.group(1)) if chm else None
    return known


def _merge_known(registry: dict[str, int | None], matrix: dict[str, int]) -> dict[str, int]:
    """Знание из реестра + матрицы: обе главы есть — ранняя; в реестре имя без главы (None) —
    глава матрицы, а без матрицы — 0 (всегда)."""
    out: dict[str, int] = {}
    for name in [*registry, *(n for n in matrix if n not in registry)]:
        reg, mat = registry.get(name), matrix.get(name)
        if reg is None:
            out[name] = mat if mat is not None else 0
        elif mat is None:
            out[name] = reg
        else:
            out[name] = min(reg, mat)
    return out


def parse_secret_markers(path: Path) -> dict[str, list[str]]:
    """Таблица «Маркеры фильтра окна» (Р-022): | Т-№ | Маркеры | → {Т-01: [слова…]}."""
    markers: dict[str, list[str]] = {}
    for table in mdparse.parse_tables(path):
        if not any("Маркер" in h for h in table.headers):
            continue
        for row in table.rows:
            key = next((v.strip() for h, v in row.items() if h.startswith("Т")), "")
            raw = next((v for h, v in row.items() if "Маркер" in h), "")
            if key:
                markers[key] = [m.strip().strip("«»") for m in raw.split(";") if m.strip()]
    return markers


def parse_secrets(path: Path, known_names: set[str] | None = None, matrix: list[MatrixFact] | None = None) -> list[InfoBan]:
    """Реестр тайн: тайна → «НЕ упоминать» до главы, где читатель узнаёт; кто из персонажей знает (FR-C3)."""
    _, volume = registry_year_volume(path)
    known_names = known_names or set()
    parts = parse_parts(path)
    markers = parse_secret_markers(path)
    bans: list[InfoBan] = []
    for table in mdparse.parse_tables(path):
        headers = " ".join(table.headers).lower()
        if "тайна" not in headers:
            continue
        for i, row in enumerate(table.rows, start=1):
            reveal = cell(row, "узнаёт")
            secret_text = cell(row, "Тайна")
            # знание персонажей — из реестра И из матрицы 3.1 (в реестре список часто неполон)
            matrix_reader, matrix_known = None, {}
            fid = _match_matrix_fact(secret_text, matrix) if matrix else None
            if fid:
                matrix_reader, matrix_known = _matrix_knowledge(fid, matrix)
            if re.search(r"НЕ раскрыва", reveal):
                until_ch: int | None = None
            elif re.search(r"[Пп]ролог", reveal):
                until_ch = 0
            else:
                until_ch = reveal_chapter(reveal)
                if until_ch is None and matrix_reader is not None:
                    until_ch = matrix_reader
                if until_ch is None:
                    pm = re.search(r"[Чч]аст[ьи]\s+([IVX\d]+)", reveal)
                    if pm:
                        num = ROMAN.get(pm.group(1), int(pm.group(1)) if pm.group(1).isdigit() else 0)
                        part = next((p for p in parts if p["part"] == num), None)
                        if part:
                            until_ch = part["to_chapter"]  # до конца части — безопасная граница
            ban_id = f"Т-{i:02d}"
            bans.append(
                InfoBan(
                    ban_id=ban_id,
                    text=secret_text,
                    until_volume=None if until_ch is not None else volume + 1,
                    until_chapter=until_ch,
                    secret=True,
                    known_by=_merge_known(_parse_known_by(cell(row, "знают"), known_names), matrix_known),
                    markers=markers.get(ban_id, []),
                )
            )
    return bans


# --------------------------------------------------------------- досье 1.3


DOSSIER_HEAD_RE = re.compile(r"^#\s*Досье[^:]*:\s*(.+)$", re.MULTILINE)


def _dossier_cards(paths: list[Path]) -> list[tuple[Path, re.Match, str]]:
    """(файл, заголовок «# Досье 1.3: ИМЯ…», тело карточки) по всем карточкам библиотеки."""
    cards: list[tuple[Path, re.Match, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        heads = list(DOSSIER_HEAD_RE.finditer(text))
        for i, head in enumerate(heads):
            body = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            cards.append((path, head, body))
    return cards


def dossier_card_name(title: str, known_names: set[str]) -> str:
    """Короткое имя карточки: известное имя, стоящее в заголовке раньше других («ОЛЬГА ЛЕММ» → Ольга),
    иначе последнее слово заголовка — фамилия («АРТУР МЕРЕДИТ (рабочее имя, Р-013)» → Мередит)."""
    main_title = re.sub(r"\(.*?\)", "", title)  # скобки — псевдонимы/пояснения
    found = [
        (m.start(), n) for n in known_names
        if (m := re.search(rf"(?<![А-Яа-яЁё]){re.escape(n)}(?![А-Яа-яЁё])", main_title, re.IGNORECASE))
    ]
    return min(found)[1] if found else main_title.split()[-1].capitalize()


def dossier_names(paths: list[Path], known_names: set[str]) -> set[str]:
    """Имена всех карточек досье (источник известных имён наряду с субъектами матрицы, аудит 1.6)."""
    return {dossier_card_name(head.group(1), known_names) for _, head, _ in _dossier_cards(paths)}


def _dossier_section(body: str, pattern: str) -> str:
    m = re.search(rf"##\s*{pattern}[^\n]*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_dossiers_real(paths: list[Path], known_names: set[str]) -> list[Dossier]:
    """Карточки «# Досье 1.3: ИМЯ» (по нескольку в файле); отношения — проза [[Имя]];
    «## Опознавательный код» — приметы, по которым персонажа опознают (перстень, перчатка…)."""
    dossiers: list[Dossier] = []
    for _, head, body in _dossier_cards(paths):
        name = dossier_card_name(head.group(1), known_names)
        relations: dict[str, str] = {}
        rel_body = _dossier_section(body, r"Отношени")
        # «[[Лемм]] — описание.» и без тире: «[[Ася]], [[Бугаев]] («своя, из выдвиженок»).» (аудит 3.7)
        for rm in re.finditer(r"\[\[([^\]]+)\]\]\s*(?:[—-]+\s*)?([^\[]*)", rel_body):
            descr = rm.group(2).strip().strip(",;. ").strip()
            if descr.startswith("(") and descr.endswith(")"):
                descr = descr[1:-1].strip()
            relations[rm.group(1).strip()] = descr.rstrip(". ") or "(связь отмечена без пояснения)"
        dossiers.append(
            Dossier(
                name=name,
                profile=_dossier_section(body, r"Профил"),
                physique=_dossier_section(body, r"Физик"),
                speech=_dossier_section(body, r"Речевой"),
                code=_dossier_section(body, r"Опознавательн"),
                relations=relations,
            )
        )
    return dossiers


_ARC_VOLUME_RE = re.compile(r"(?<![А-Яа-яЁё])[Тт]\.\s*(\d+)\s*:")


def dossier_presence(paths: list[Path], volume: int, known_names: set[str]) -> dict[str, list[int]]:
    """Главы тома, где персонаж в кадре по секции «## Арка» его карточки: во фрагменте «Т.N: …»
    текущего тома номера «гл. K» — присутствие («Т.1: две немые сцены (пролог; кабаре, гл. 41)»).
    Так участник попадает в главу, если сетка называет его описательно («британец»), а не по имени."""
    presence: dict[str, list[int]] = {}
    for _, head, body in _dossier_cards(paths):
        arc = _dossier_section(body, r"Арк")
        if not arc:
            continue
        marks = list(_ARC_VOLUME_RE.finditer(arc))
        for i, m in enumerate(marks):
            if int(m.group(1)) != volume:
                continue
            segment = arc[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(arc)]
            chapters = chapters_listed(segment)
            if chapters:
                name = dossier_card_name(head.group(1), known_names)
                seen = presence.setdefault(name, [])
                seen.extend(c for c in chapters if c not in seen)
    return presence


def enrich_from_dossiers(briefs: list[Brief], paths: list[Path], known_names: set[str]) -> None:
    """Участники глав по аркам досье (см. `dossier_presence`)."""
    by_vol: dict[int, dict[str, list[int]]] = {}
    for b in briefs:
        if b.volume not in by_vol:
            by_vol[b.volume] = dossier_presence(paths, b.volume, known_names)
        for name, chapters in by_vol[b.volume].items():
            if b.chapter in chapters and name != b.focal and name not in b.participants:
                b.participants.append(name)


# ------------------------------------------------------------- части тома


PART_RE = re.compile(r"###\s*ЧАСТЬ\s+([IVX\d]+)\.\s*«([^»]+)»\s*[—-]+\s*(.*?)\s*\(гл\.\s*(\d+)\s*[–-]\s*(\d+)\)")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def parse_parts(path: Path) -> list[dict]:
    """Части тома из заголовков реестра: «### ЧАСТЬ I. «МОКРОЕ ДЕЛО» — апрель 1926 (гл. 1–9)»."""
    parts = []
    for m in PART_RE.finditer(path.read_text(encoding="utf-8")):
        num = m.group(1)
        parts.append(
            {
                "part": ROMAN.get(num, int(num) if num.isdigit() else len(parts) + 1),
                "title": m.group(2),
                "period": m.group(3),
                "from_chapter": int(m.group(4)),
                "to_chapter": int(m.group(5)),
            }
        )
    return parts


# ------------------------------------------- закладки из контроля поглавника


def parse_poglavnik_plants(path: Path, volume: int, existing: list[Plant]) -> list[Plant]:
    """Строка «Закладки положены: знак/зола (6.2), часы (гл. 4), …» → закладки глав,
    которых нет в реестре §7 (дедупликация по главе и первому слову)."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"Закладки положены:\s*(.+)", text)
    if not m:
        return []
    new: list[Plant] = []
    for i, item in enumerate(re.split(r",\s*(?![^(]*\))", m.group(1).rstrip(". ")), start=1):
        loc = re.search(r"\(([^)]*)\)", item)
        name = re.sub(r"\(.*?\)", "", item).strip()
        if not loc or not name:
            continue
        ch_m = re.search(r"(\d+)", loc.group(1))
        if not ch_m:
            continue
        chapter = int(ch_m.group(1))
        stem = re.split(r"[\s/]", name.lower())[0][:4]
        if any(chapter in p.chapters and stem in p.what.lower() for p in existing + new):
            continue
        new.append(
            Plant(
                plant_id=f"П-{i:02d}",
                what=f"{name} (по контролю поглавника)",
                placed={"vol": volume, "ch": chapter},
                chapters=[chapter],
                fires=[],
                status="🔧",
            )
        )
    return new


# ------------------------------------------------ круги истории (2.1, Р-020)

CIRCLE_HEAD_RE = re.compile(r"^##\s*Круг\s+(тома|акта\s+([IVX\d]+)|главы\s+(\d+))\b(.*)$", re.MULTILINE)
CIRCLE_STEP_RE = re.compile(r"^(\d)\.\s+\*\*(.+?)\*\*\s*(?:\(([^)]*)\))?\s*(?:[—–-]+\s*)?(.*)$")
_CH_RANGE_RE = re.compile(r"гл\.?\s*([\d\s,–\-]+)")


def chapter_range(text: str) -> tuple[int | None, int | None]:
    """«гл. 1–3» / «гл. 5» / «гл. 1, 3» → (1, 3); «сц. 5.1» → (None, None)."""
    m = _CH_RANGE_RE.search(text)
    if not m:
        return None, None
    nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
    if not nums:
        return None, None
    return min(nums), max(nums)


def parse_circles(path: Path) -> list[StoryCircle]:
    """Документ 21_Круги_истории: «## Круг тома» / «## Круг акта 3 …» / «## Круг главы 5»,
    внутри — «**Суть:**», нумерованные шаги «1. **Ты** (гл. 1–3) — …», «**Слабое место:**»."""
    text = path.read_text(encoding="utf-8")
    heads = list(CIRCLE_HEAD_RE.finditer(text))
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
            sm = CIRCLE_STEP_RE.match(stripped)
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


def parse_acts(path: Path) -> list[Act]:
    """Таблица «Акты тома» документа 2.1 (Р-021): | Акт | Название | Главы | Части | Шаги круга |."""
    acts: list[Act] = []
    for table in mdparse.parse_tables(path):
        if "Акт" not in table.headers or "Главы" not in table.headers:
            continue
        for row in table.rows:
            nums = [int(x) for x in re.findall(r"\d+", row["Главы"])]
            if not nums or not row["Акт"].strip().isdigit():
                continue
            acts.append(Act(
                act=int(row["Акт"]), title=row.get("Название", "").strip("«» "),
                from_chapter=min(nums), to_chapter=max(nums),
                parts=row.get("Части", "").strip(), steps=row.get("Шаги круга", "").strip(),
            ))
    return sorted(acts, key=lambda a: a.act)


# ---------------------------------------------------------------- арки тома (2.5, Р-025)

ARC_COLUMNS = ("Персонаж", "Акт", "Ложь", "Желание", "Потребность", "Где на арке", "Что видно снаружи")
_ARC_EMPTY = {"", "—", "–", "-"}


def _arc_cell(row: dict[str, str], name: str) -> str:
    value = cell(row, name).strip()
    return "" if value in _ARC_EMPTY else value


def parse_arcs(path: Path) -> list[Arc]:
    """Документ 2.5 `22_Арки_Том{N}.md` (Р-025): таблица «персонаж × акт»
    `| Персонаж | Акт | Ложь | Желание | Потребность | Где на арке | Что видно снаружи |`.
    Пустые ячейки («—») → «»; «⚠ заполнить» остаётся как есть (скелет; в окно такие строки не идут).
    Строки без имени или без номера акта пропускаются; таблиц может быть несколько (по актам)."""
    arcs: list[Arc] = []
    for table in mdparse.parse_tables(path):
        if not all(any(col in h for h in table.headers) for col in ("Персонаж", "Акт")):
            continue
        for row in table.rows:
            name = _arc_cell(row, "Персонаж").strip("*_ ")
            m = re.search(r"\d+", cell(row, "Акт"))
            if not name or not m:
                continue
            arcs.append(Arc(
                character=name, act=int(m.group()),
                lie=_arc_cell(row, "Ложь"), want=_arc_cell(row, "Желание"), need=_arc_cell(row, "Потребность"),
                position=_arc_cell(row, "Где на арке"), visible=_arc_cell(row, "Что видно снаружи"),
            ))
    return arcs

