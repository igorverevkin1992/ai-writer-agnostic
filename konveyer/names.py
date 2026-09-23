"""Имена персонажей в тексте канона: падежные формы, участие в действии, перечисления глав и томов.

Общий для всех проектов языковой инструмент (русский, Д-13): ни одного имени конкретной серии здесь нет (П-1) —
известные имена приходят из выгрузок проекта (субъекты эпистемики, карточки персонажей, линии повествования).
"""

from __future__ import annotations

import re

CH_RE = re.compile(r"[Гг]л\.?\s*(\d+)")
# перечисление глав после одного «гл.»: «Гл. 9, 27», «Гл. 29 или 40», «гл. 34, 40»
CH_LIST_RE = re.compile(r"[Гг]л\.?\s*(\d+(?:\s*(?:,|или|и|/)\s*\d+)*)")
# том: «Том 2», «тома 9–10», «томов 2–5», «т.6»; «в томе N …» — оговорка, не ссылка на выстрел
VOL_RE = re.compile(
    r"(?<!(?<![а-яё])[Вв]\s)[Тт]ом\w*\s*(\d+)(?:\s*[–-]\s*(\d+))?"
    r"|(?<!(?<![а-яё])[Вв]\s)(?<![а-яё])т\.?\s*(\d+)(?:\s*[–-]\s*(\d+))?"
)
_CH_RANGE_RE = re.compile(r"гл\.?\s*([\d\s,–\-]+)")

# падежные окончания имён: «Иванову», «Петровым», «Асю» (основа «Ас»), «куратору отдела»
_NAME_ENDINGS = "ами|ями|ой|ей|ом|ем|ым|им|ою|ею|ах|ях|ов|ев|а|я|у|ю|е|и|ы"


def chapters_listed(text: str) -> list[int]:
    """Все главы из перечислений «гл. 9, 27» / «гл. 29 или 40»."""
    nums: list[int] = []
    for m in CH_LIST_RE.finditer(text):
        nums.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    return list(dict.fromkeys(nums))


def volumes_listed(text: str) -> list[int]:
    """Тома с раскрытием диапазонов: «томов 2–5» → [2, 3, 4, 5]."""
    vols: list[int] = []
    for m in VOL_RE.finditer(text):
        lo = int(m.group(1) or m.group(3))
        hi = int(m.group(2) or m.group(4) or lo)
        vols.extend(range(lo, hi + 1) if hi >= lo else [lo])
    return list(dict.fromkeys(vols))


def chapter_range(text: str) -> tuple[int | None, int | None]:
    """«гл. 1–3» / «гл. 5» / «гл. 1, 3» → (1, 3); «сц. 5.1» → (None, None)."""
    m = _CH_RANGE_RE.search(text)
    if not m:
        return None, None
    nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
    if not nums:
        return None, None
    return min(nums), max(nums)


def split_items(text: str) -> list[str]:
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


def name_pattern(name: str) -> re.Pattern:
    """Регэксп имени в любом падеже и регистре: «Ася» → Ас(я|и|е|ю…), «Куратор отдела» → куратор(у) отдела."""
    first, *rest = name.split()
    stem = first[:-1] if len(first) > 2 and first[-1] in "аяь" else first
    pat = rf"(?<![А-Яа-яЁё]){re.escape(stem)}(?:{_NAME_ENDINGS})?(?![А-Яа-яЁё])"
    if rest:
        pat += r"\s+" + r"\s+".join(re.escape(r) for r in rest)
    return re.compile(pat, re.IGNORECASE)


def _name_matches(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[tuple[str, re.Match]]:
    """Вхождения известных имён (по основе, без учёта регистра). Имя из нескольких слов находится и по одному
    первому слову, если оно единственное полное имя с таким началом; `pseudo` — субъекты, не являющиеся
    персонажами (например «Читатель» из эпистемики)."""
    out: list[tuple[str, re.Match]] = []
    for n in known_names:
        if n in pseudo:
            continue
        found = list(name_pattern(n).finditer(text))
        first = n.split()[0]
        if not found and " " in n and sum(1 for o in known_names if o.split()[0] == first) == 1:
            found = list(name_pattern(first).finditer(text))
        out.extend((n, m) for m in found)
    return out


def find_names(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[str]:
    """Известные имена, встречающиеся в тексте (в любом падеже и регистре); из пары
    «Куратор» / «Куратор отдела» остаётся более длинное."""
    result: set[str] = set()
    for n, _ in _name_matches(text, known_names, pseudo):
        longer = [o for o in known_names if o != n and o.startswith(n + " ")]
        result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


# имя — участник действия, если стоит в именительном падеже, после предлога совместного действия
# («к …», «с …», «на …», «за …») или как дополнение глагола настоящего времени; родительный при
# существительном («рапорт …») и дательный адресата — упоминание, не участие
_ACTION_PREPS = {"к", "ко", "с", "со", "на", "за", "у", "против", "перед", "рядом", "вместе", "между", "при"}
_VERB_END_RE = re.compile(r"(?:[её]т|ит|[ую]т|[ая]т|ся|сь|ть|ти)$")


def _acts_in(text: str, m: re.Match, name: str) -> bool:
    form = m.group(0).split()[0]
    if form.lower() == name.split()[0].lower():
        return True
    before = re.findall(r"[А-Яа-яЁё-]+", text[: m.start()])
    if not before:
        return False
    prev = before[-1].lower()
    return prev in _ACTION_PREPS or bool(_VERB_END_RE.search(prev))


def find_acting_names(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[str]:
    """Имена, участвующие в действии (для события плана глав)."""
    result: set[str] = set()
    for n, m in _name_matches(text, known_names, pseudo):
        if _acts_in(text, m, n):
            longer = [o for o in known_names if o != n and o.startswith(n + " ")]
            result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


def normalize_name(raw: str, known_names: set[str]) -> str:
    """«Иванова» → «Иванов», «куратор отдела» → «Куратор отдела», «куратор» → «Куратор отдела» (единственное полное
    имя с таким началом): известное имя в начале строки, в любом падеже."""
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
