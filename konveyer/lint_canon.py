"""Линтер канона: сверка реестров тома между собой и с генеральной хронологией 12.

Второй машинный слой линтера (этап 3, п. 17 плана аудита 2). Классы находок:

- `ДОЗА-1` — глава дозы прошлого: таблица §5 реестра ↔ сетка 2.2 («Доза 1913 №1») ↔ §7 (закладки);
- `ДОК-1` — глава документа-вставки: таблица §6 ↔ сетка («→ Документ №N») ↔ поглавник 2.3;
- `ХРОН-3` — дата главы вне периода своей части («ЧАСТЬ II … — май–июнь 1926 (гл. 10–18)»);
- `ХРОН-4` — историческое событие в брифе против ✓-даты хроники 17;
- `ХРОН-5` — названный день недели против календаря года тома;
- `ДОСЬЕ-3` — физика досье против континуити 3.3 по словарю признаков (ухо, почерк, рост, цвет, увечья);
- `ДОСЬЕ-4` — статус досье («гибнет т.N») против хронологии 12 («Гибель …»);
- `ДОСЬЕ-5` — возраст в абсолютном году или в томе, отличном от текущего (расширение `ДОСЬЕ-1`);
- `КАНОН-1` — вопросы к решениям автора (заметки): где открывается фокал, объём тома, ссылки на
  события 12, индекс библиотеки против журнала решений;
- `АРКА-1` / `АРКА-2` — таблица арок 2.5 (Р-025): персонаж без карточки досье / акт вне таблицы актов 2.1
  (заметки; на скелете и без документа — тишина).

Модуль ничего не пишет: он только возвращает находки, которые собирает `lint.run_lint`.
Пороги и словари, относящиеся к прозе, сюда не переносятся — они остаются в каноне и в выгрузках.
"""

from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path

from . import exporter, realcanon
from .schemas import Brief, ContinuityEvent, LintFinding

# ------------------------------------------------------------------ утилиты


class _Docs:
    """Кэш строк документов: одна читка файла на прогон (аудит 3.11)."""

    def __init__(self, library: Path) -> None:
        self.library = library
        self._lines: dict[Path, list[str]] = {}

    def lines(self, path: Path) -> list[str]:
        if path not in self._lines:
            try:
                self._lines[path] = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                self._lines[path] = []
        return self._lines[path]

    def find(self, path: Path | None, needle: str) -> int | None:
        if path is None or not needle:
            return None
        for i, line in enumerate(self.lines(path), start=1):
            if needle in line:
                return i
        return None

    def rel(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.relative_to(self.library)).replace("\\", "/")
        except ValueError:
            return str(path)


def _parse_date(text: str):
    """Дата брифа → (месяц, день). Отложенный импорт: `lint` импортирует этот модуль."""
    from .lint import parse_date

    return parse_date(text)


def _months(text: str) -> set[int]:
    """Месяцы, названные в тексте периода: «май–июнь 1926» → {5, 6}, «апрель 1926» → {4}."""
    from .lint import MONTHS

    found = [MONTHS[w.lower()[:3]] for w in re.findall(r"[А-Яа-яЁё]{3,}", text) if w.lower()[:3] in MONTHS]
    if len(found) >= 2:
        lo, hi = found[0], found[-1]
        if lo <= hi:
            return set(range(lo, hi + 1))
    return set(found)


def _brief_text(b: Brief) -> str:
    """Всё, что автор написал о главе: событие сетки, колонка читателя, сцены поглавника."""
    cards = [f"{s.place} {s.time} {s.participants} {s.goal} {s.enters} {s.exits}" for s in b.scene_cards]
    return " ".join([*b.beats, b.reader_learns, *b.scenes, *cards, *b.documents])


def _first(pattern: re.Pattern, text: str) -> int | None:
    m = pattern.search(text)
    return int(m.group(1)) if m else None


# --------------------------------------------------- ДОЗА-1: дозы прошлого

# «**Доза 1913 №1** — см. раздел 5» в колонке сетки
_DOSE_GRID_RE = re.compile(r"[Дд]оз[аеуы]\s*(?:1913\s*)?№\s*(\d+)")
# «**«Печь в мае»** | Доза №1 (гл. 12), гл. 46 | …» — §7 реестра
_DOSE_PLANT_RE = re.compile(r"[Дд]оз[аеы]\s*№\s*(\d+)\s*\(гл\.\s*(\d+)\)")


def check_doses(doses, briefs: list[Brief], reg_path: Path | None, docs: _Docs) -> list[LintFinding]:
    """ДОЗА-1: глава дозы прошлого названа в трёх местах канона — расхождение ломает единственный
    разрешённый канал прошлого (§1 п. 4 реестра)."""
    if not doses or reg_path is None:
        return []
    grid: dict[int, int] = {}
    for b in briefs:
        for m in _DOSE_GRID_RE.finditer(_brief_text(b)):
            grid.setdefault(int(m.group(1)), b.chapter)
    plants: dict[int, int] = {
        int(m.group(1)): int(m.group(2))
        for m in _DOSE_PLANT_RE.finditer("\n".join(docs.lines(reg_path)))
    }
    out: list[LintFinding] = []
    for dose in doses:
        num = _first(re.compile(r"(\d+)"), dose.dose_id)
        if num is None:
            continue
        named = {"таблица §5": dose.chapter}
        if num in grid:
            named["сетка 2.2"] = grid[num]
        if num in plants:
            named["§7 закладок"] = plants[num]
        if len(set(named.values())) > 1:
            where = "; ".join(f"{k} — гл. {v}" for k, v in named.items())
            out.append(LintFinding(
                code="ДОЗА-1", severity="ошибка", file=docs.rel(reg_path),
                line=docs.find(reg_path, f"| {dose.dose_id} |"),
                message=f"доза {dose.dose_id}: главы расходятся ({where}); "
                        "доза прошлого — единственный разрешённый канал воспоминаний, глава должна быть одна",
            ))
    return out


# ----------------------------------------------- ДОК-1: документы-вставки

# «**→ Документ №1** после главы» — сетка 2.2; «Подготовка документа №6» назначением не считается
_DOC_GRID_RE = re.compile(r"→\s*\**\s*[Дд]окумент\s*№\s*(\d+)")
# «№1 (после главы): …» / «№2 (после гл. 8): …» — поглавник 2.3 (Brief.documents)
_DOC_POG_RE = re.compile(r"№\s*(\d+)\s*\(([^)]*)\)")


def check_documents(documents, briefs: list[Brief], reg_path: Path | None, p23_path: Path | None,
                    docs: _Docs) -> list[LintFinding]:
    """ДОК-1: глава документа-вставки в §6 реестра, в сетке 2.2 и в поглавнике."""
    if not documents or reg_path is None:
        return []
    grid: dict[int, int] = {}
    poglavnik: dict[int, int] = {}
    for b in briefs:
        for m in _DOC_GRID_RE.finditer(" ".join([*b.beats, b.reader_learns])):
            grid.setdefault(int(m.group(1)), b.chapter)
        for item in b.documents:
            m = _DOC_POG_RE.match(item.strip())
            if m:
                after = _first(re.compile(r"гл\.?\s*(\d+)"), m.group(2))
                poglavnik.setdefault(int(m.group(1)), after if after is not None else b.chapter)
    out: list[LintFinding] = []
    for spec in documents:
        named = {"таблица §6": spec.after_chapter}
        if spec.number in grid:
            named["сетка 2.2"] = grid[spec.number]
        if spec.number in poglavnik:
            named["поглавник 2.3"] = poglavnik[spec.number]
        if len(set(named.values())) > 1:
            where = "; ".join(f"{k} — после гл. {v}" for k, v in named.items())
            out.append(LintFinding(
                code="ДОК-1", severity="ошибка", file=docs.rel(reg_path),
                line=docs.find(reg_path, f"| {spec.number} | {spec.after_chapter} |"),
                message=f"документ №{spec.number}: место вставки расходится ({where})",
            ))
    return out


# ------------------------------------------------------- ХРОН-3/4/5: время


def check_part_period(briefs: list[Brief], parts: list[dict], reg_path: Path | None,
                      docs: _Docs) -> list[LintFinding]:
    """ХРОН-3: дата главы вне периода своей части («ЧАСТЬ II … — май–июнь 1926 (гл. 10–18)»)."""
    out: list[LintFinding] = []
    for part in parts:
        months = _months(str(part.get("period", "")))
        if not months:
            continue
        for b in briefs:
            if not (part["from_chapter"] <= b.chapter <= part["to_chapter"]):
                continue
            d = _parse_date(b.date)
            if d is None or d[0] in months:
                continue
            out.append(LintFinding(
                code="ХРОН-3", severity="ошибка", file=docs.rel(reg_path),
                line=docs.find(reg_path, f"| {b.chapter} |"),
                message=f"гл. {b.chapter} датирована «{b.date}», а часть {part.get('title', '')} "
                        f"объявлена периодом «{part.get('period', '')}»",
            ))
    return out


_CHRON_DATE_RE = re.compile(r"^\**\s*(\d{1,2})\.(\d{1,2})\s*\**$")
_CAP_WORD_RE = re.compile(r"[А-ЯЁа-яё]{4,}")


def _event_keys(event: str) -> set[str]:
    """Опознавательные основы исторического события: имена собственные (не первое слово фразы).

    «**Смерть Дзержинского** (после выступления…)» → {«дзержин»}; «Всесоюзная перепись населения…
    Переписчики ходят по квартирам» → {«москва», «перепис»}. Первое слово отбрасывается: оно
    заглавное по позиции, а не по существу («Смерть», «Берлинский»)."""
    words = _CAP_WORD_RE.findall(event.replace("*", " "))
    return {w.lower()[:7] for i, w in enumerate(words) if i and w[0].isupper() and len(w) >= 6}


def check_chronicle_dates(briefs: list[Brief], chronicle, reg_path: Path | None,
                          docs: _Docs) -> list[LintFinding]:
    """ХРОН-4: историческое событие названо в брифе главы, дата которой расходится с ✓-датой хроники 17.

    Опознаётся по именам собственным события; основа, встречающаяся больше чем в двух главах тома,
    считается общим словом и не опознаёт событие."""
    out: list[LintFinding] = []
    texts = {b.chapter: _brief_text(b).lower() for b in briefs}
    by_ch = {b.chapter: b for b in briefs}
    for ev in chronicle:
        m = _CHRON_DATE_RE.match(ev.date.strip())
        if not m or "✓" not in ev.status:
            continue
        day, month = int(m.group(1)), int(m.group(2))
        for key in sorted(_event_keys(ev.event)):
            hits = [ch for ch, text in texts.items() if key in text]
            if not hits or len(hits) > 2:
                continue
            for ch in hits:
                d = _parse_date(by_ch[ch].date)
                if d is None or d == (month, day):
                    continue
                out.append(LintFinding(
                    code="ХРОН-4", severity="предупреждение", file=docs.rel(reg_path),
                    line=docs.find(reg_path, f"| {ch} |"),
                    message=f"гл. {ch} датирована «{by_ch[ch].date}», а названное в ней событие "
                            f"«{ev.event.replace('*', '').strip()[:60]}» хроника 17 датирует {ev.date.strip('* ')}",
                ))
    return out


_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среду": 2, "четверг": 3,
    "пятницу": 4, "субботу": 5, "воскресенье": 6,
}
_WEEKDAY_RE = re.compile(r"\bво?\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def check_weekdays(briefs: list[Brief], reg_path: Path | None, p23_path: Path | None,
                   docs: _Docs) -> list[LintFinding]:
    """ХРОН-5: день недели, названный в брифе или поглавнике, против календаря года тома."""
    out: list[LintFinding] = []
    for b in briefs:
        d = _parse_date(b.date)
        if d is None or not b.year:
            continue
        try:
            actual = _date(b.year, d[0], d[1]).weekday()
        except ValueError:
            continue  # дату вне календаря ловит ХРОН-1
        for m in _WEEKDAY_RE.finditer(_brief_text(b)):
            named = _WEEKDAYS[m.group(1).lower()]
            if named == actual:
                continue
            path = reg_path if m.group(0).lower() in " ".join([*b.beats, b.reader_learns]).lower() else p23_path
            out.append(LintFinding(
                code="ХРОН-5", severity="ошибка", file=docs.rel(path or reg_path),
                line=docs.find(path or reg_path, m.group(0)),
                message=f"гл. {b.chapter}: «{m.group(0)}», а {d[1]:02d}.{d[0]:02d}.{b.year} — "
                        f"{_WEEKDAY_NAMES[actual]}",
            ))
    return out


# ------------------------------------------------ ДОСЬЕ-3/4/5: карточки 1.3

# словарь признаков физики: (признак, где о нём говорят, группа значений)
_FEATURES = [
    ("сторона глухоты", re.compile(r"глух\w*|\bух[оа]\b|\bуш(?:и|ей|ах)\b|\bслух\b", re.I), "сторона"),
    ("почерк", re.compile(r"почерк\w*", re.I), "размер"),
    ("рост", re.compile(r"\bрост[аом]?\b|\bросл\w+", re.I), "рост"),
    ("цвет глаз", re.compile(r"\bглаз\w*", re.I), "цвет"),
    ("цвет волос", re.compile(r"\bволос\w*|\bшевелюр\w*", re.I), "цвет"),
    ("увечья", re.compile(r"увеч\w*|\bрубц\w*|\bшрам\w*|изуродован\w*", re.I), "сторона"),
]
_ADJ = r"(?:ый|ое|ая|ой|ом|ую|ым|ые|ых|ими|ого|ому)"
_VALUES = {
    "сторона": [
        ("левое", re.compile(rf"\bлев{_ADJ}\b|\bслева\b", re.I)),
        ("правое", re.compile(rf"\bправ{_ADJ}\b|\bсправа\b", re.I)),
    ],
    "размер": [
        ("мелкий", re.compile(rf"\bмелк{_ADJ}\b", re.I)),
        ("крупный", re.compile(rf"\bкрупн{_ADJ}\b", re.I)),
    ],
    "рост": [
        ("высокий", re.compile(rf"\bвысок{_ADJ}\b|\bвысокоросл\w+", re.I)),
        ("низкий", re.compile(rf"\bнизк{_ADJ}\b|\bнизкоросл\w+|\bкоротышк\w+", re.I)),
    ],
    "цвет": [
        ("серый", re.compile(rf"\bсер{_ADJ}\b", re.I)),
        ("голубой", re.compile(rf"\bголуб{_ADJ}\b", re.I)),
        ("карий", re.compile(r"\bкари[йеяю]\w*\b", re.I)),
        ("зелёный", re.compile(rf"\bзел[её]н{_ADJ}\b", re.I)),
        ("чёрный", re.compile(rf"\bч[её]рн{_ADJ}\b", re.I)),
        ("светлый", re.compile(rf"\bсветл{_ADJ}\b", re.I)),
        ("тёмный", re.compile(rf"\bт[её]мн{_ADJ}\b", re.I)),
        ("рыжий", re.compile(r"\bрыж[ий]\w*\b", re.I)),
        ("седой", re.compile(rf"\bсед{_ADJ}\b|\bседин\w+", re.I)),
    ],
}


def _feature_values(text: str) -> dict[str, tuple[set[str], str]]:
    """Признак → (названные значения, клауза, в которой они названы).

    Клауза — часть фразы между «;», «·» и скобками: пояснение в скобках («наклоняется правым»)
    относится к своему признаку, а не к соседнему."""
    out: dict[str, tuple[set[str], str]] = {}
    for clause in re.split(r"[;·()]", text.replace("**", "")):
        clause = clause.strip()
        if not clause:
            continue
        for name, feature_re, group in _FEATURES:
            if not feature_re.search(clause):
                continue
            values = {value for value, value_re in _VALUES[group] if value_re.search(clause)}
            if not values:
                continue
            seen, quote = out.get(name, (set(), clause))
            out[name] = (seen | values, quote)
    return out


def check_dossier_physique(library: Path, continuity: list[ContinuityEvent], known_names: set[str],
                           docs: _Docs) -> list[LintFinding]:
    """ДОСЬЕ-3: физика карточки против континуити-трекера 3.3 по словарю признаков."""
    out: list[LintFinding] = []
    by_name: dict[str, list[ContinuityEvent]] = {}
    for c in continuity:
        for name in realcanon.find_names(c.event, known_names):
            by_name.setdefault(name, []).append(c)
    if not by_name:
        return out
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    for path, head, body in realcanon._dossier_cards(paths):
        name = realcanon.dossier_card_name(head.group(1), known_names)
        events = by_name.get(name)
        if not events:
            continue
        text = " ".join(realcanon._dossier_section(body, p) for p in (r"Профил", r"Физик", r"Опознавательный"))
        card = _feature_values(text)
        for c in events:
            for feature, (values, quote) in _feature_values(c.event).items():
                mine = card.get(feature)
                if not mine or not values or (mine[0] & values):
                    continue
                out.append(LintFinding(
                    code="ДОСЬЕ-3", severity="ошибка", file=docs.rel(path),
                    line=docs.find(path, mine[1][:40]),
                    message=f"{name}: {feature} — в досье «{', '.join(sorted(mine[0]))}», "
                            f"а континуити 3.3 фиксирует «{', '.join(sorted(values))}» ({c.event[:60]})",
                    quote=mine[1][:120],
                ))
    return out


# «гибнет т.6», «гибнет в т.6», «Мёртв с финала т.6», «Умирает в финале т.11», «гибель … т.6»
_DEATH_VOL_RE = re.compile(r"(?:гибнет|гибель|умирает|мёртв\w*|погибает)[^.;·|]{0,40}?т\.\s*(\d+)", re.I)
_DEATH_EVENT_RE = re.compile(r"гибель|гибнут|смерть|умирает|погиб\w*", re.I)
# имя должно стоять при слове смерти: «Гибель Заварзина и Бугаева», «Смерть Лемма»,
# а не где-нибудь в той же строке («Смерть Дзержинского … Заварзин теряет протекцию»)
_DEATH_TAIL_RE = re.compile(r"[.,;:—|]")


def check_dossier_status(library: Path, chronology, known_names: set[str], docs: _Docs) -> list[LintFinding]:
    """ДОСЬЕ-4: «гибнет т.N» карточки против тома события «Гибель …» в хронологии 12."""
    out: list[LintFinding] = []
    if not chronology:
        return out
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    for path, head, body in realcanon._dossier_cards(paths):
        name = realcanon.dossier_card_name(head.group(1), known_names)
        # карточка называет том гибели в нескольких местах (профиль, арка, статус) — сверяем каждое
        told = {int(m.group(1)): m.group(0).strip() for m in _DEATH_VOL_RE.finditer(body)}
        if not told:
            continue
        pattern = realcanon.name_pattern(name)
        for ev in chronology:
            near = [_DEATH_TAIL_RE.split(ev.event[dm.end():])[0] for dm in _DEATH_EVENT_RE.finditer(ev.event)]
            if not any(pattern.search(chunk) for chunk in near):
                continue
            volume = ev.volume or (ev.volumes[0] if ev.volumes else None)
            if volume is None:
                continue
            for said, quote in told.items():
                if said == volume:
                    continue
                out.append(LintFinding(
                    code="ДОСЬЕ-4", severity="предупреждение", file=docs.rel(path),
                    line=docs.find(path, quote[:40]),
                    message=f"{name}: досье говорит «{quote}», а хронология 12 ставит "
                            f"«{ev.event.strip()[:50]}» ({ev.event_id}) в том {volume}",
                    quote=quote,
                ))
    return out


_BORN_RE = re.compile(r"Рожд\.\s*≈?\s*(\d{4})")
# «17 лет в 1913-м», «39 (1926, кабаре)», «44–45 (т.11)», «71 год в т.11», «30 (т.1)»
_AGE_RE = re.compile(
    r"(?<![\d.,–—-])(?<!т\.)(?<!т\. )(\d{2,3})(?:\s*[–-]\s*(\d{2,3}))?\s*(?:лет|года|год)?\s*"
    r"(?:\(\s*(?:(\d{4})|т\.\s*(\d+))|в\s+(?:(\d{4})|т(?:оме)?\.?\s*(\d+)))"
)


def check_dossier_ages(library: Path, chronology, volume: int, docs: _Docs) -> list[LintFinding]:
    """ДОСЬЕ-5: возраст, названный абсолютным годом («17 лет в 1913-м», «39 (1926, кабаре)»)
    или томом, отличным от текущего («71 год в т.11»), против года рождения.

    Год тома берётся из разделов хронологии 12 («## Том 11 — 1946–1947») — единственной карты
    «том → год» в каноне. Возраст текущего тома проверяет `ДОСЬЕ-1` (не дублируем)."""
    out: list[LintFinding] = []
    years: dict[int, list[int]] = {}
    for ev in chronology:
        if ev.volume and ev.section_years:
            years.setdefault(ev.volume, ev.section_years)
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    for path, head, body in realcanon._dossier_cards(paths):
        bm = _BORN_RE.search(body)
        if not bm:
            continue
        born = int(bm.group(1))
        for m in _AGE_RE.finditer(body):
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else lo
            year_txt = m.group(3) or m.group(5)
            vol_txt = m.group(4) or m.group(6)
            if year_txt:
                span = [int(year_txt)]
                where = f"{year_txt} год"
            else:
                vol = int(vol_txt)
                if vol == volume:
                    continue  # это работа ДОСЬЕ-1
                span = years.get(vol, [])
                where = f"том {vol}"
                if not span:
                    continue
            expected = sorted({y - born for y in span})
            if any(abs(e - a) <= 1 for e in expected for a in (lo, hi)):
                continue
            line = docs.find(path, m.group(0)) or docs.find(path, head.group(1).strip())
            shown = "–".join(str(e) for e in expected) if len(expected) > 1 else str(expected[0])
            out.append(LintFinding(
                code="ДОСЬЕ-5", severity="предупреждение", file=docs.rel(path), line=line,
                message=f"{head.group(1).strip()}: «{m.group(0).strip()}» — при рождении ≈{born} "
                        f"на {where} возраст {shown}",
                quote=m.group(0).strip(),
            ))
    return out


# ------------------------------------------------- КАНОН-1: вопросы автору

_OPEN_FOCAL_RE = re.compile(r"откр[ыи]ва\w*\s+(?:в\s+)?том\w*\s*(\d+)(?:\s*[–-]\s*(\d+))?", re.I)
_DOSSIER_FOCAL_RE = re.compile(r"Фокал(?:ен|ьн\w*)?\s*:?\s*(?:с\s+)?т\.\s*(\d+)", re.I)
_VOL_RANGE_RE = re.compile(r"(\d+)(?:\s*[–-]\s*(\d+))?")


def _focal_opening(library: Path, known_names: set[str], reg_path: Path | None) -> dict[str, dict[str, int]]:
    """Имя → {источник: том, в котором открывается фокал}: §1 реестра, таблица 03, статус досье."""
    found: dict[str, dict[str, int]] = {}

    def note(name: str, source: str, volume: int) -> None:
        found.setdefault(name, {}).setdefault(source, volume)

    if reg_path is not None:
        for sentence in reg_path.read_text(encoding="utf-8").splitlines():
            m = _OPEN_FOCAL_RE.search(sentence)
            if not m:
                continue
            for name in realcanon.find_names(sentence, known_names):
                note(name, "реестр §1", int(m.group(1)))
    focal_doc = next(iter(sorted(library.glob("03_*.md"))), None)
    if focal_doc is not None:
        for line in focal_doc.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|") or "открыва" not in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            vm = _VOL_RANGE_RE.match(cells[0]) if cells else None
            if vm is None:
                continue
            for chunk in re.split(r"открыва\w+ся", line)[1:]:
                for name in realcanon.find_names(chunk.split("|")[0], known_names):
                    note(name, "таблица 03", int(vm.group(1)))
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    for _path, head, body in realcanon._dossier_cards(paths):
        status = realcanon._dossier_section(body, r"Статус")
        m = _DOSSIER_FOCAL_RE.search(status) or _DOSSIER_FOCAL_RE.search(body)
        if m:
            note(realcanon.dossier_card_name(head.group(1), known_names), "досье", int(m.group(1)))
    return found


_VOLUME_PAGES_RE = re.compile(r"(\d+)\s*глав[а-я]*,\s*~?\s*(\d+)\s*[–-]\s*(\d+)\s*стр")
# страница книжной вёрстки — 250–400 слов повествования; оценка нужна только для порядка величины
PAGE_WORDS = (250, 400)
_INDEX_RANGE_RE = re.compile(r"Р-(\d+)\s*…\s*Р-(\d+)")
_DECISION_RE = re.compile(r"\bР-(\d{3})\b")
_DOSSIER_F_REF_RE = re.compile(r"Ф-(\d{4})-(\d+)")


def check_canon_questions(library: Path, briefs: list[Brief], chronology, norms, known_names: set[str],
                          reg_path: Path | None, docs: _Docs) -> list[LintFinding]:
    """КАНОН-1: расхождения решений автора между документами — заметки, а не ошибки конвейера.

    Где открывается фокал (реестр §1 ↔ 03 ↔ досье); объём тома против нормы объёма главы;
    ссылки на события хронологии 12 (том ссылки в досье, даты глав предъявления); индекс
    библиотеки против последнего решения журнала."""
    out: list[LintFinding] = []

    for name, sources in sorted(_focal_opening(library, known_names, reg_path).items()):
        if len(sources) > 1 and len(set(sources.values())) > 1:
            where = "; ".join(f"{k} — т. {v}" for k, v in sources.items())
            out.append(LintFinding(
                code="КАНОН-1", severity="заметка", file=docs.rel(reg_path),
                line=docs.find(reg_path, "фокала не имеют"),
                message=f"{name}: фокал открывается в разных томах ({where}) — решение за автором",
            ))

    chapter_norm = norms.get("объём_главы") if norms else None
    if reg_path is not None and chapter_norm is not None and chapter_norm.min and chapter_norm.max:
        m = _VOLUME_PAGES_RE.search(reg_path.read_text(encoding="utf-8"))
        if m:
            chapters, lo, hi = int(m.group(1)), int(m.group(2)), int(m.group(3))
            words = (chapters * chapter_norm.min, chapters * chapter_norm.max)
            pages = (words[0] / PAGE_WORDS[1], words[1] / PAGE_WORDS[0])
            if not (pages[0] <= hi and lo <= pages[1]):
                out.append(LintFinding(
                    code="КАНОН-1", severity="заметка", file=docs.rel(reg_path),
                    line=docs.find(reg_path, m.group(0)),
                    message=f"объём тома: {chapters} глав × {chapter_norm.min:.0f}–{chapter_norm.max:.0f} слов "
                            f"= {words[0]:.0f}–{words[1]:.0f} слов ≈ {pages[0]:.0f}–{pages[1]:.0f} стр., "
                            f"а реестр §3 обещает {lo}–{hi} стр.",
                ))

    by_id = {ev.event_id: ev for ev in chronology}
    paths = sorted((library / "Досье").glob("*.md")) if (library / "Досье").exists() else []
    for path, _head, body in realcanon._dossier_cards(paths):
        for sentence in re.split(r"(?<=[.;])\s+|\n", body):
            for m in _DOSSIER_F_REF_RE.finditer(sentence):
                ev = by_id.get(m.group(0))
                if ev is None or ev.volume is None:
                    continue
                told = {int(v) for v in re.findall(r"(?<![А-Яа-яЁё0-9])[Тт]\.\s*(\d+)", sentence)}
                if told and ev.volume not in told:
                    out.append(LintFinding(
                        code="КАНОН-1", severity="заметка", file=docs.rel(path),
                        line=docs.find(path, m.group(0)),
                        message=f"ссылка {m.group(0)}: рядом названы тома "
                                f"{', '.join(str(t) for t in sorted(told))}, "
                                f"а хронология 12 ставит это событие в том {ev.volume}",
                    ))

    by_ch = {b.chapter: b for b in briefs}
    volume = briefs[0].volume if briefs else 1
    for ev in chronology:
        if ev.volume != volume or not ev.chapters:
            continue
        months = _months(ev.date)
        d = _parse_date(ev.date)
        if d:
            months = {d[0]}
        if not months:
            continue
        for ch in ev.chapters:
            b = by_ch.get(ch)
            bd = _parse_date(b.date) if b else None
            if bd is None or bd[0] in months:
                continue
            out.append(LintFinding(
                code="КАНОН-1", severity="заметка", file=docs.rel(reg_path),
                line=docs.find(reg_path, f"| {ch} |"),
                message=f"{ev.event_id} датировано «{ev.date}», а гл. {ch}, где читатель его узнаёт, — "
                        f"«{b.date}» (хронология 12 и сетка 2.2 расходятся)",
            ))

    index = next(iter(sorted(library.glob("00_*.md"))), None)
    journal = next(iter(sorted(library.glob("36_*.md"))), None)
    if index is not None and journal is not None:
        last = max((int(x) for x in _DECISION_RE.findall(journal.read_text(encoding="utf-8"))), default=0)
        for i, line in enumerate(docs.lines(index), start=1):
            rm = _INDEX_RANGE_RE.search(line)
            if rm and int(rm.group(2)) < last:
                out.append(LintFinding(
                    code="КАНОН-1", severity="заметка", file=docs.rel(index), line=i,
                    message=f"индекс библиотеки обещает «{rm.group(0)}», а журнал решений дошёл до Р-{last:03d}",
                ))
    return out


# ------------------------------------------------------------------ прогон


def check_arcs(arcs, acts, dossier_names: set[str], arcs_path: Path | None, docs: _Docs) -> list[LintFinding]:
    """АРКА-1: персонаж таблицы арок 2.5 без карточки досье 1.3; АРКА-2: акт строки вне таблицы актов 2.1.
    Обе — заметки автору; содержание ячеек не проверяется (скелет «⚠ заполнить» — норма)."""
    out: list[LintFinding] = []
    if not arcs:
        return out
    act_numbers = {a.act for a in acts}
    seen: set[tuple[str, str]] = set()
    for arc in arcs:
        if dossier_names and arc.character not in dossier_names and ("АРКА-1", arc.character) not in seen:
            seen.add(("АРКА-1", arc.character))
            out.append(LintFinding(
                code="АРКА-1", severity="заметка", file=docs.rel(arcs_path), line=docs.find(arcs_path, f"| {arc.character} |"),
                message=f"арки 2.5: персонаж «{arc.character}» без карточки досье 1.3 — опечатка в имени или нужна карточка",
            ))
        if act_numbers and arc.act not in act_numbers and ("АРКА-2", str(arc.act)) not in seen:
            seen.add(("АРКА-2", str(arc.act)))
            out.append(LintFinding(
                code="АРКА-2", severity="заметка", file=docs.rel(arcs_path), line=docs.find(arcs_path, f"| {arc.character} | {arc.act} |"),
                message=f"арки 2.5: акт {arc.act} («{arc.character}») вне таблицы актов 2.1 ({', '.join(str(n) for n in sorted(act_numbers))})",
            ))
    return out


def run_checks(library: Path, exports_dir: Path, briefs: list[Brief], parts: list[dict],
               continuity: list[ContinuityEvent], known_names: set[str], reg_path: Path | None,
               volume: int) -> list[LintFinding]:
    """Все проверки модуля по уже сделанным выгрузкам. Ничего не пишет."""
    docs = _Docs(library)
    p23 = next(iter(exporter.volume_docs(library, "23_*.md", volume)), None)
    doses = exporter.load_doses(exports_dir)
    documents = exporter.load_documents(exports_dir)
    chronicle = exporter.load_chronicle(exports_dir)
    chronology = exporter.load_chronology(exports_dir)
    norms = exporter.load_norms(exports_dir)
    findings: list[LintFinding] = []
    findings += check_doses(doses, briefs, reg_path, docs)
    findings += check_documents(documents, briefs, reg_path, p23, docs)
    findings += check_part_period(briefs, parts, reg_path, docs)
    findings += check_chronicle_dates(briefs, chronicle, reg_path, docs)
    findings += check_weekdays(briefs, reg_path, p23, docs)
    findings += check_dossier_physique(library, continuity, known_names, docs)
    findings += check_dossier_status(library, chronology, known_names, docs)
    findings += check_dossier_ages(library, chronology, volume, docs)
    findings += check_canon_questions(library, briefs, chronology, norms, known_names, reg_path, docs)
    findings += check_arcs(exporter.load_arcs(exports_dir), exporter.load_acts(exports_dir),
                           {d.name for d in exporter.load_dossiers(exports_dir)},
                           next(iter(exporter.volume_docs(library, exporter.ARCS_DOC_GLOB, volume)), None), docs)
    return findings
