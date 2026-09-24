"""Языковой модуль (FR-V1-3): деление на предложения, токенизация, сокращения, прямая речь, основы слов,
лексемы для метрик — из `языки/<код>.yaml` движка, дополненного `языки/<код>.yaml` проекта (списки складываются,
скаляры переопределяются) и старым словарём `<проект>/сокращения.txt`. Проверки Э1 и линтер зовут только методы
`Language`; второй язык — новый YAML (и, при нужде, код-плагин `языки/<код>.py` с теми же методами).
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

ABBR_MASK = "\x01"   # непечатаемый маркер точки внутри сокращения
DOC_START = "→ ДОКУМЕНТ"          # маркеры документа-вставки по умолчанию (язык переопределяет: `документ_начало/конец`)
DOC_END = "← КОНЕЦ ДОКУМЕНТА"
DEFAULT = "ru"
LETTER = "[^\\W\\d_]"                    # буква любого алфавита (язык переопределяет: `буква`)


@dataclass
class Language:
    code: str
    name: str
    word_re: re.Pattern
    sent_end_re: re.Pattern
    initial_re: re.Pattern | None
    heading_re: re.Pattern | None
    abbreviations: list[tuple[str, bool]]        # (сокращение, контекстное), длинные раньше коротких
    spaced_abbr_re: re.Pattern | None
    dash_marks: tuple[str, ...]
    speech_attr_re: re.Pattern | None
    normalization: dict[str, str]
    lexeme_sets: dict[str, list[str]]
    stem_endings: tuple[str, ...]
    inflections: str
    min_stem: int
    fleeting_re: re.Pattern | None
    letter: str = LETTER                          # класс букв (границы слов и сокращений)
    abbr_context: str = r"[0-9а-яёa-z]"          # перед чем точка контекстного сокращения не завершает фразу
    continuation_re: re.Pattern | None = None     # что после терминатора означает продолжение фразы
    initial_not_after_re: re.Pattern | None = None  # начало слова перед одиночной заглавной с точкой — это не инициал
    doc_start: str = DOC_START
    doc_end: str = DOC_END
    paragraph_per_line: bool = True               # текст без пустых строк: каждая строка — абзац
    raw: dict = field(default_factory=dict)
    months: dict[str, int] = field(default_factory=dict)   # основа названия месяца → номер («январ» → 1)
    name_rules: dict = field(default_factory=dict)         # склонение имён: окончания, предлоги действия, глаголы

    def month_of(self, text: str) -> int | None:
        """Номер месяца по названию в тексте («май 1996» → 5); None — названия месяца нет."""
        low = text.lower()
        for stem, num in self.months.items():
            if stem and stem in low:
                return num
        return None

    @property
    def not_names(self) -> list[str]:
        """Слова с заглавной буквы, которые не являются именами (заголовки и служебные слова таблиц): не попадают
        в известные имена при разборе таблицы фокалов."""
        return [str(x) for x in (self.raw.get("не_имена") or [])]

    @property
    def unnamed_roles(self) -> list[str]:
        """Роли безымянных участников сцен («сторож», «врач»): участник без карточки — не находка ПОГЛ-2."""
        return [str(x) for x in (self.raw.get("роли_безымянных") or [])]

    # ---------------------------------------------------------------- слова

    def words(self, text: str) -> list[str]:
        return self.word_re.findall(text)

    def normalize_word(self, w: str) -> str:
        w = w.lower()
        for a, b in self.normalization.items():
            w = w.replace(a, b)
        return w

    def normalize(self, text: str) -> list[str]:
        """Токены для n-грамм и TTR: нижний регистр, без пунктуации, с нормализацией букв (Д-14: по словоформам)."""
        return [self.normalize_word(w) for w in self.words(text)]

    def ngrams(self, tokens: list[str], n: int) -> Counter:
        return Counter(tuple(tokens[i: i + n]) for i in range(len(tokens) - n + 1))

    # ---------------------------------------------------------------- абзацы и предложения

    def paragraphs(self, text: str) -> list[str]:
        """Абзацы: блоки строк, разделённые пустыми строками; переносы внутри блока — пробел. Строка, начинающаяся
        тире реплики, всегда открывает абзац; текст без единой пустой строки (обычный вывод редакторов и моделей)
        читается построчно (`абзац_по_строке`)."""
        lines = [ln.strip() for ln in text.splitlines()]
        per_line = self.paragraph_per_line and len([ln for ln in lines if ln]) > 1 and all(lines)
        result: list[str] = []
        block: list[str] = []
        for stripped in lines + [""]:
            if stripped and (per_line or stripped.startswith(self.dash_marks)) and block:
                result.append(" ".join(block))
                block = []
            if stripped:
                block.append(stripped)
            elif block:
                result.append(" ".join(block))
                block = []
        return result

    def _mask_abbreviations(self, text: str, extra: list[tuple[str, bool]] | None = None) -> str:
        abbrs = sorted({**dict(self.abbreviations), **dict(extra or [])}.items(), key=lambda kv: len(kv[0]), reverse=True)
        for abbr, contextual in abbrs:
            # первая буква — в любом регистре («ул. Ленина» и «Ул. Ленина» в начале фразы)
            body = (f"[{abbr[0].upper()}{abbr[0].lower()}]" if abbr[0].isalpha() else re.escape(abbr[0])) + re.escape(abbr[1:])
            pattern = rf"(?<!{self.letter})" + body
            if contextual:
                # контекстное («г.», «с.», «им.»): точка не завершает фразу перед цифрой/строчной («1995 г. в мае») либо
                # когда сокращение не идёт за числом («в г. Москве», «завод им. Ленина»); «1995 г. Москва» — конец фразы
                pattern = rf"(?<!{self.letter})(?:(?<!\d)(?<!\d\s){body}|{body}(?=\s*{self.abbr_context}))"
            text = re.sub(pattern, lambda m: m.group(0).replace(".", ABBR_MASK), text)
        if self.spaced_abbr_re is not None:
            text = self.spaced_abbr_re.sub(lambda m: m.group(0).replace(".", ABBR_MASK), text)
        if self.initial_re is not None:
            text = self.initial_re.sub(self._mask_initial, text)
        return text

    def _mask_initial(self, m: re.Match) -> str:
        """Одиночная заглавная с точкой — инициал, если за ней идёт ещё инициал («А. Х.») или перед ней нет слова
        со строчной («группа А. Потом…» — конец фразы; «Петров А. Х.», «А. К. Иванов» — инициалы)."""
        text = m.string
        if self.initial_not_after_re is not None and self.initial_re is not None:
            nxt = m.end()
            while nxt < len(text) and text[nxt].isspace():
                nxt += 1
            if not self.initial_re.match(text, nxt):
                prev = re.search(r"(\S+)\s*$", text[: m.start()])
                if prev and self.initial_not_after_re.match(prev.group(1)):
                    return m.group(0)
        return m.group(1) + ABBR_MASK

    def split_sentences(self, text: str, extra_abbr: Path | list[tuple[str, bool]] | None = None) -> list[str]:
        """Деление на предложения (Д-13): терминатор + закрывающие кавычки; сокращения, инициалы и одинокие
        заголовки внутри прозы («Глава пятая») границей не считаются."""
        extra = load_abbreviations(extra_abbr) if isinstance(extra_abbr, Path) else (extra_abbr or [])
        sentences: list[str] = []
        for para in self.paragraphs(text):
            if self.heading_re is not None and self.heading_re.match(para) and len(para.split()) <= 4:
                continue
            para = self._mask_abbreviations(para, extra)
            start = 0
            for m in self.sent_end_re.finditer(para):
                end = m.end()
                if end < len(para) and not para[end].isspace():
                    continue  # терминатор внутри слова (десятичные числа)
                if self.continuation_re is not None and self.continuation_re.match(para, end):
                    continue  # после терминатора — строчная («кивнул… потом», «— Стой! — крикнул»): фраза продолжается
                chunk = para[start:end].strip()
                if chunk:
                    sentences.append(chunk.replace(ABBR_MASK, "."))
                start = end
            tail = para[start:].strip()
            if tail:
                sentences.append(tail.replace(ABBR_MASK, "."))
        return sentences

    # ---------------------------------------------------------------- прямая речь

    def is_dialogue_paragraph(self, para: str) -> bool:
        return para.lstrip().startswith(self.dash_marks)

    def narration_only(self, text: str) -> str:
        """Повествование без реплик персонажей: в абзаце с тире реплики и авторская речь чередуются по атрибуциям
        («— Иди, — сказал он. — Отец ждёт.» → «сказал он»; «— Сынок, — сказал сосед, — иди домой.» → «сказал сосед»);
        абзац-реплика без атрибуции отбрасывается целиком."""
        kept: list[str] = []
        for para in self.paragraphs(text):
            if not self.is_dialogue_paragraph(para):
                kept.append(para)
                continue
            if self.speech_attr_re is None:
                continue
            segments = self.speech_attr_re.split(para)  # реплика, авторская речь, реплика, …
            kept.extend(seg.strip() for seg in segments[1::2])
        return "\n\n".join(k for k in kept if k)

    # ---------------------------------------------------------------- основы слов

    def stems(self, word: str) -> list[str]:
        """Основы слова для маркеров: «отец» → [«отец», «отц»], «семья» → [«семь»], «сын» → [«сын»]."""
        w = self.normalize_word(word)
        stem = w
        for end in sorted(self.stem_endings, key=len, reverse=True):
            if w.endswith(end) and len(w) - len(end) >= self.min_stem:
                stem = w[: -len(end)]
                break
        stems = [stem]
        if self.fleeting_re is not None:
            m = self.fleeting_re.match(stem)
            if m and len(m.group(1)) + 1 >= self.min_stem:
                stems.append(m.group(1) + m.group(2))
        return stems

    def _unnormalized(self, fragment: str) -> str:
        """Фрагмент регэкспа по нормализованной основе, совпадающий и с исходными буквами («е» → «[её]»):
        основы нормализуются (`нормализация`), а проза — нет."""
        for a, b in self.normalization.items():
            if a != b and len(a) == 1 and len(b) == 1:
                fragment = fragment.replace(b, f"[{b}{a}]")
        return fragment

    def item_pattern(self, item: str) -> re.Pattern:
        """Регэксп словосочетания из стоп-листа: каждое слово — по основе с допустимыми окончаниями."""
        parts = []
        inflections = self._unnormalized(self.inflections)
        for w in item.split():
            alts = "|".join(self._unnormalized(re.escape(s)) for s in self.stems(w))
            parts.append(rf"(?:{alts})(?:{inflections})?")
        return re.compile(rf"(?<!{self.letter})" + r"\s+".join(parts) + rf"(?!{self.letter})", re.IGNORECASE)

    # ---------------------------------------------------------------- лексемы и вставки

    def lexemes(self, name: str) -> set[str]:
        return {self.normalize_word(w) for w in self.lexemes_raw(name)}

    def lexemes_raw(self, name: str) -> list[str]:
        return list(self.lexeme_sets.get(name) or [])

    def strip_document_inserts(self, text: str) -> str:
        """Текст без документов-вставок (блоки между маркерами `документ_начало` … `документ_конец`)."""
        out: list[str] = []
        inside = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(self.doc_start):
                inside = True
                continue
            if s.startswith(self.doc_end):
                inside = False
                continue
            if not inside:
                out.append(line)
        return "\n".join(out)

    def has_document_insert(self, text: str) -> bool:
        return self.doc_start in text and self.doc_end in text


# ------------------------------------------------------------------ загрузка


def load_abbreviations(path: Path | None) -> list[tuple[str, bool]]:
    """Старый пополняемый словарь `сокращения.txt`: строка — сокращение, префикс «~» — контекстное."""
    if path is None or not Path(path).exists():
        return []
    out: list[tuple[str, bool]] = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        out.append((ln.lstrip("~").strip(), ln.startswith("~")))
    return out


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = list(out[k]) + [x for x in v if x not in out[k]]
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _from_data(data: dict) -> Language:
    mark = str(data.get("контекст_сокращения", "~"))
    abbrs: dict[str, bool] = {}
    for a in data.get("сокращения") or []:
        a = str(a).strip()
        if a:
            abbrs[a.lstrip(mark).strip()] = a.startswith(mark)
    heads = data.get("заголовки_в_прозе") or []
    stems = data.get("основы") or {}
    return Language(
        code=str(data.get("язык", DEFAULT)), name=str(data.get("название", "")),
        word_re=re.compile(data.get("слово") or r"\w+(?:-\w+)*"),
        sent_end_re=re.compile(data.get("конец_предложения") or r"[.!?…]+"),
        initial_re=re.compile(data["инициал"]) if data.get("инициал") else None,
        heading_re=re.compile(r"^(?:" + "|".join(re.escape(h) for h in heads) + r")\b[^.!?…]*$", re.IGNORECASE) if heads else None,
        abbreviations=sorted(abbrs.items(), key=lambda kv: len(kv[0]), reverse=True),
        spaced_abbr_re=re.compile(data["сокращения_с_пробелом"], re.IGNORECASE) if data.get("сокращения_с_пробелом") else None,
        dash_marks=tuple(str(x) for x in (data.get("тире_реплики") or ["—"])),
        speech_attr_re=re.compile(data["атрибуция_реплики"]) if data.get("атрибуция_реплики") else None,
        normalization={str(k): str(v) for k, v in (data.get("нормализация") or {}).items()},
        lexeme_sets={str(k): [str(x) for x in v] for k, v in (data.get("лексемы") or {}).items()},
        stem_endings=tuple(str(x) for x in (stems.get("окончания_основы") or ())),
        inflections=str(stems.get("флексии") or ""),
        min_stem=int(stems.get("мин_основа") or 3),
        fleeting_re=re.compile(stems["беглая_гласная"]) if stems.get("беглая_гласная") else None,
        letter=str(data.get("буква") or LETTER),
        abbr_context=str(data.get("контекст_сокращения_перед") or r"[0-9а-яёa-z]"),
        continuation_re=re.compile(data["продолжение_фразы"]) if data.get("продолжение_фразы") else None,
        initial_not_after_re=re.compile(data["инициал_не_после"]) if data.get("инициал_не_после") else None,
        doc_start=str(data.get("документ_начало") or DOC_START),
        doc_end=str(data.get("документ_конец") or DOC_END),
        paragraph_per_line=bool(data.get("абзац_по_строке", True)),
        raw=data,
        months={str(k).lower(): int(v) for k, v in (data.get("месяцы") or {}).items()},
        name_rules=dict(data.get("имена") or {}),
    )


def _engine_yaml(code: str) -> dict:
    res = resources.files("konveyer").joinpath(f"языки/{code}.yaml")
    try:
        return yaml.safe_load(res.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, OSError):
        raise ValueError(f"язык «{code}» не поддерживается движком: нет konveyer/языки/{code}.yaml") from None


def _stamp(path: Path) -> tuple:
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else ()


@functools.lru_cache(maxsize=16)
def _cached(code: str, project_root: str | None, stamp: tuple) -> Language:
    data = _engine_yaml(code)
    if project_root:
        p = Path(project_root) / "языки" / f"{code}.yaml"
        if p.exists():
            data = _merge(data, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        legacy = Path(project_root) / "сокращения.txt"
        if legacy.exists():
            data = _merge(data, {"сокращения": [("~" if c else "") + a for a, c in load_abbreviations(legacy)]})
    return _from_data(data)


def get(code: str = DEFAULT, project_root: Path | None = None) -> Language:
    """Языковой модуль: движок + переопределения проекта (кэш по mtime файлов проекта)."""
    root = str(project_root) if project_root else None
    stamp = ()
    if project_root:
        stamp = (_stamp(Path(project_root) / "языки" / f"{code}.yaml"), _stamp(Path(project_root) / "сокращения.txt"))
    return _cached(code, root, stamp)


def for_project(project_root: Path | None) -> Language:
    """Язык прозы проекта из манифеста (`проект.язык_прозы`), по умолчанию русский."""
    code = DEFAULT
    if project_root is not None:
        from . import manifest as manifest_mod

        man = manifest_mod.load(project_root)
        if man is not None and man.проект.язык_прозы:
            code = man.проект.язык_прозы
    return get(code, project_root)
