"""Имена персонажей в тексте канона: падежные формы, участие в действии, перечисления глав и томов.

Общий для всех проектов языковой инструмент (русский, FR-V1-3): ни одного имени конкретной серии здесь нет (П-1) —
известные имена приходят из выгрузок проекта (субъекты эпистемики, карточки персонажей, линии повествования).
"""

from __future__ import annotations

import re

from . import lang as lang_mod

CH_RE = re.compile(r"[Гг]л\.?\s*(\d+)")
# перечисление глав после одного «гл.»: «Гл. 9, 27», «Гл. 29 или 40», «гл. 34, 40», «гл. 5–7» (диапазон)
CH_LIST_RE = re.compile(r"[Гг]л\.?\s*(\d+(?:\s*(?:,|или|и|/|[–-])\s*\d+)*)")
# том: «Том 2», «тома 9–10», «томов 2–5», «т.6»; «в томе N …» — оговорка, не ссылка на выстрел
VOL_RE = re.compile(
    r"(?<!(?<![а-яё])[Вв]\s)[Тт]ом\w*\s*(\d+)(?:\s*[–-]\s*(\d+))?"
    r"|(?<!(?<![а-яё])[Вв]\s)(?<![а-яё])т\.?\s*(\d+)(?:\s*[–-]\s*(\d+))?"
)
# «гл. 1–3», «Гл. 1, 3», «главы 1–3», «глава 5»
_CH_RANGE_RE = re.compile(r"гл(?:ав[аы]?)?\.?\s*([\d\s,–\-]+)", re.IGNORECASE)
_RANGE_ITEM_RE = re.compile(r"(\d+)\s*[–-]\s*(\d+)|(\d+)")

# падежные окончания имён: «Иванову», «Петровым», «Асю» (основа «Ас»), «Игорем» (основа «Игор»),
# «Андрея» (основа «Андре»), «куратору отдела»; фамильные суффиксы («Иванов» ≠ «Иван») окончаниями не считаются.
# Таблицы живут в языковом слое (`языки/ru.yaml`, раздел `имена`); значения здесь — запас на случай их отсутствия.
_DEFAULT_RULES = {
    "окончания": ["ами", "ями", "ой", "ей", "ом", "ем", "ым", "им", "ою", "ею", "ах", "ях", "ью", "а", "я", "у", "ю",
                  "е", "и", "ы", "ь", "й"],
    "усечение_основы": ["а", "я", "ь", "й"],
    "предлоги_действия": ["к", "ко", "с", "со", "на", "за", "у", "против", "перед", "рядом", "вместе", "между", "при"],
    "окончания_глагола": r"(?:[её]т|ит|[ую]т|[ая]т|ся|сь|ть|ти)$",
}
_SHORT_STEM = 4  # основа короче — только полная форма имени или основа с непустым окончанием («Над» ≠ «Надя»)


def _rules() -> dict:
    """Правила склонения имён языка по умолчанию (движок + переопределения проекта берутся через `lang`)."""
    try:
        rules = lang_mod.get().name_rules
    except (ValueError, OSError):
        rules = {}
    return {**_DEFAULT_RULES, **{k: v for k, v in rules.items() if v}}


def _endings() -> str:
    return "|".join(re.escape(str(e)) for e in _rules()["окончания"])


def _range_numbers(text: str) -> list[int]:
    """Числа перечисления с раскрытием диапазонов: «1–3, 7» → [1, 2, 3, 7]."""
    nums: list[int] = []
    for m in _RANGE_ITEM_RE.finditer(text):
        if m.group(3):
            nums.append(int(m.group(3)))
        else:
            lo, hi = int(m.group(1)), int(m.group(2))
            nums.extend(range(lo, hi + 1) if hi >= lo else [lo, hi])
    return nums


def chapters_listed(text: str) -> list[int]:
    """Все главы из перечислений «гл. 9, 27» / «гл. 29 или 40» / «гл. 5–7» (диапазон раскрывается)."""
    nums: list[int] = []
    for m in CH_LIST_RE.finditer(text):
        nums.extend(_range_numbers(m.group(1)))
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
    """«гл. 1–3» / «Гл. 5» / «главы 1, 3» → (1, 3); «сц. 5.1» → (None, None)."""
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


def _stem(word: str) -> str:
    """Основа имени: «Ася» → «Ас», «Игорь» → «Игор», «Андрей» → «Андре», «Иван» → «Иван»."""
    if len(word) > 2 and word[-1] in "".join(str(x) for x in _rules()["усечение_основы"]):
        return word[:-1]
    return word


def name_pattern(name: str, *, strict_case: bool = True) -> re.Pattern:
    """Регэксп имени в любом падеже: «Ася» → Ас(я|и|е|ю…), «Игорь» → Игор(ь|я|ю|ем…), «Куратор отдела» →
    Куратор(у) отдела. Первая буква сохраняет регистр имени (имя в прозе пишется с заглавной: «над столом» — не
    «Надя»), остальное — без учёта регистра; для короткой основы («Над», «Ад», «Люб») нулевое окончание
    допускается только в полной форме имени («Надя», но не «Над»). `strict_case=False` — первая буква в любом
    регистре (для ячеек канона: «куратор» → «Куратор отдела»)."""
    first, *rest = name.split()
    stem = _stem(first)
    lead = stem[0]
    # имя с заглавной требует заглавную в тексте; имя-роль со строчной («куратор») и имя из нескольких слов
    # («Куратор отдела» — второе слово само отсекает случайные совпадения) находятся в обоих регистрах
    head = (re.escape(lead) if lead.isupper() and strict_case and not rest
            else f"[{re.escape(lead.lower())}{re.escape(lead.upper())}]")
    head += f"(?i:{re.escape(stem[1:])})" if len(stem) > 1 else ""
    endings = _endings()
    if len(stem) < _SHORT_STEM and stem != first:
        tail = rf"(?:{endings})"      # окончание обязательно: «Надя», «Наде», но не «Над»
    else:
        tail = rf"(?:{endings})?"
    pat = rf"(?<![А-Яа-яЁё]){head}(?i:{tail})(?![А-Яа-яЁё])"
    if rest:
        pat += r"(?i:\s+" + r"\s+".join(re.escape(r) for r in rest) + ")"
    return re.compile(pat)


def _name_matches(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[tuple[str, re.Match]]:
    """Вхождения известных имён (по основе). Имя из нескольких слов находится и по одному первому слову, если оно
    единственное полное имя с таким началом; `pseudo` — субъекты, не являющиеся персонажами (например «Читатель»
    из эпистемики)."""
    out: list[tuple[str, re.Match]] = []
    for n in sorted(known_names):
        if n in pseudo or not n.strip():
            continue
        found = list(name_pattern(n).finditer(text))
        first = n.split()[0]
        if not found and " " in n and sum(1 for o in known_names if o.split()[0] == first) == 1:
            found = list(name_pattern(first).finditer(text))
        out.extend((n, m) for m in found)
    return out


def find_names(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[str]:
    """Известные имена, встречающиеся в тексте (в любом падеже); из пары
    «Куратор» / «Куратор отдела» остаётся более длинное."""
    result: set[str] = set()
    for n, _ in _name_matches(text, known_names, pseudo):
        longer = [o for o in known_names if o != n and o.startswith(n + " ")]
        result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


# имя — участник действия, если стоит в именительном падеже, после предлога совместного действия
# («к …», «с …», «на …», «за …») или как дополнение глагола настоящего времени; родительный при
# существительном («рапорт …») и дательный адресата — упоминание, не участие
def _acts_in(text: str, m: re.Match, name: str) -> bool:
    form = m.group(0).split()[0]
    if form.lower() == name.split()[0].lower():
        return True
    before = re.findall(r"[А-Яа-яЁё-]+", text[: m.start()])
    if not before:
        return False
    prev = before[-1].lower()
    rules = _rules()
    return prev in {str(p) for p in rules["предлоги_действия"]} or bool(re.search(str(rules["окончания_глагола"]), prev))


def find_acting_names(text: str, known_names: set[str], pseudo: set[str] = frozenset()) -> list[str]:
    """Имена, участвующие в действии (для события плана глав)."""
    result: set[str] = set()
    for n, m in _name_matches(text, known_names, pseudo):
        if _acts_in(text, m, n):
            longer = [o for o in known_names if o != n and o.startswith(n + " ")]
            result.add(longer[0] if len(longer) == 1 else n)
    return sorted(result)


def normalize_name(raw: str, known_names: set[str]) -> str:
    """«Иванова» → «Иванов», «Куратор» → «Куратор отдела» (единственное полное имя с таким началом): известное
    имя в начале строки, в любом падеже. Ни одно известное имя не подошло — строка возвращается целиком
    («Мария Петровна» без досье остаётся «Мария Петровна»)."""
    raw = raw.strip()
    word = raw.split()[0] if raw else ""
    if raw in known_names:
        return raw
    ordered = sorted(known_names, key=lambda n: (-len(n), n))  # детерминированно при равной длине (П-6)
    for name in ordered:
        if name.strip() and name_pattern(name, strict_case=False).match(raw):
            return name
    for name in ordered:
        first = name.split()[0]
        if " " in name and name_pattern(first, strict_case=False).match(word) \
                and sum(1 for o in known_names if o.split()[0] == first) == 1:
            return name
    return raw
