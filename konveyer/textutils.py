"""Текстовые счётчики Э1: предложения (Д-2), слова, n-граммы (Д-4), TTR (Д-3).

Деление на предложения: регэксп по `.!?…` с обработкой прямой речи
(тире-реплики) и словаря сокращений (пополняемый файл data/сокращения.txt).
Абзац — блок строк до пустой строки; мягкие переносы строк внутри абзаца
границей предложения не считаются. Инициалы («А. К. Штерн») — одно
предложение; однобуквенные сокращения (`~с.`, `~д.` в словаре) действуют
только перед цифрой или строчной буквой; одинокие заголовки «Глава пятая»
предложениями не считаются.
Метрики считаются по всему тексту, включая диалоги; исключаются только
размеченные документы-вставки (Д-7): блок между строками
`→ ДОКУМЕНТ` и `← КОНЕЦ ДОКУМЕНТА`.
"""

from __future__ import annotations

import re
from collections import Counter
from importlib import resources
from pathlib import Path

DOC_START = "→ ДОКУМЕНТ"
DOC_END = "← КОНЕЦ ДОКУМЕНТА"

_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z0-9]+(?:-[А-Яа-яЁёA-Za-z0-9]+)*")
# конец предложения: терминатор + закрывающие кавычки/скобки (остаются в предложении)
_SENT_END_RE = re.compile(r"[.!?…]+[»«\"')\]]*")
_ABBR_MASK = "\x01"  # непечатаемый маркер точки внутри сокращения


_CONTEXT_MARK = "~"  # префикс в словаре: сокращение действует только перед цифрой/строчной буквой
# одинокий заголовок внутри прозы: «Глава пятая», «Часть II», «Пролог» — без терминатора
_HEADING_RE = re.compile(r"^(?:Глава|Часть|Пролог|Эпилог)\b[^.!?…]*$", re.IGNORECASE)
# инициал: одиночная заглавная буква с точкой («А. К. Штерн», «Лемм А. Х. подписал») — предложение
# не заканчивается однобуквенным словом, поэтому такая точка границей не является
_INITIAL_RE = re.compile(r"(?<![А-Яа-яЁёA-Za-z])([А-ЯЁA-Z])\.")


def _load_abbreviations(extra_path: Path | None = None) -> list[tuple[str, bool]]:
    """[(сокращение, контекстное)]; контекстное (`~с.`) маскируется только перед цифрой
    или строчной буквой — иначе «с.» съедает границу предложения после слова на «с»."""
    lines = resources.files("konveyer").joinpath("data/сокращения.txt").read_text(encoding="utf-8").splitlines()
    if extra_path and extra_path.exists():
        lines += extra_path.read_text(encoding="utf-8").splitlines()
    abbrs: dict[str, bool] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        contextual = ln.startswith(_CONTEXT_MARK)
        abbrs[ln.lstrip(_CONTEXT_MARK).strip()] = contextual
    # длинные раньше коротких, чтобы «т.д.» маскировалось до «д.»
    return sorted(abbrs.items(), key=lambda kv: len(kv[0]), reverse=True)


def _mask_abbreviations(text: str, abbrs: list[tuple[str, bool]]) -> str:
    for abbr, contextual in abbrs:
        pattern = r"(?<![А-Яа-яЁёA-Za-z])" + re.escape(abbr)
        if contextual:
            pattern += r"(?=\s*[0-9а-яёa-z])"
        text = re.sub(pattern, abbr.replace(".", _ABBR_MASK), text)
    # «т. е.», «и т. д.», «т. н.» с пробелом: точка внутри сокращения не завершает предложение
    text = _SPACED_ABBR_RE.sub(lambda m: m.group(0).replace(".", _ABBR_MASK), text)
    return _INITIAL_RE.sub(lambda m: m.group(1) + _ABBR_MASK, text)


# сокращения из двух частей с пробелом: «т. е.», «и т. д.», «и т. п.», «т. н.», «т. к.»
_SPACED_ABBR_RE = re.compile(r"\b(?:и\s+)?т\.\s*[едпкн]\.", re.IGNORECASE)
# абзац прямой речи: начинается с тире; атрибуция внутри — «, — сказал N» / «. — N»
_SPEECH_ATTR_RE = re.compile(r"[,.!?…]\s*[—–-]\s+")


def narration_only(text: str) -> str:
    """Повествование и несобственно-прямая речь без реплик персонажей (03: стоп-лист линии
    касается ВНУТРЕННЕЙ речи фокала). Абзац с тире: реплика отбрасывается до атрибуции
    («— Сынок, — сказал Бугаев, и Штерн промолчал» → «и Штерн промолчал»); без атрибуции
    абзац отбрасывается целиком. Эвристика описана в Д-1."""
    kept: list[str] = []
    for para in paragraphs(text):
        if not para.lstrip().startswith(("—", "–", "-")):
            kept.append(para)
            continue
        m = _SPEECH_ATTR_RE.search(para)
        if m:
            kept.append(para[m.end():].strip())
    return "\n\n".join(k for k in kept if k)


def paragraphs(text: str) -> list[str]:
    """Абзацы: блоки строк, разделённые пустыми строками; переносы внутри блока — пробел."""
    result: list[str] = []
    block: list[str] = []
    for line in text.splitlines() + [""]:
        stripped = line.strip()
        if stripped:
            block.append(stripped)
        elif block:
            result.append(" ".join(block))
            block = []
    return result


def strip_document_inserts(text: str) -> str:
    """Убирает документы-вставки из текста повествователя (Д-7, FR-V1.1)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(DOC_START):
            inside = True
            continue
        if stripped.startswith(DOC_END):
            inside = False
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def strip_markdown(text: str) -> str:
    """Снимает заголовки (целиком — это метаданные файла, не проза) и выделение MD."""
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*-{3,}\s*$", "", text, flags=re.M)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return text


def split_sentences(text: str, extra_abbr: Path | None = None) -> list[str]:
    """Деление на предложения по Д-2."""
    abbrs = _load_abbreviations(extra_abbr)
    sentences: list[str] = []
    for para in paragraphs(text):
        if _HEADING_RE.match(para) and len(para.split()) <= 4:
            continue  # «Глава пятая» — заголовок в теле прозы, не предложение
        para = _mask_abbreviations(para, abbrs)
        # тире-реплики режем как обычные предложения; терминатор внутри слова
        # (десятичные числа) предложение не завершает
        start = 0
        for m in _SENT_END_RE.finditer(para):
            end = m.end()
            if end < len(para) and not para[end].isspace():
                continue
            chunk = para[start:end].strip()
            if chunk:
                sentences.append(chunk.replace(_ABBR_MASK, "."))
            start = end
        tail = para[start:].strip()
        if tail:
            sentences.append(tail.replace(_ABBR_MASK, "."))
    return sentences


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def word_count(text: str) -> int:
    return len(words(text))


def sentence_lengths(text: str, extra_abbr: Path | None = None) -> list[int]:
    return [len(words(s)) for s in split_sentences(text, extra_abbr) if words(s)]


def normalize(text: str) -> list[str]:
    """Нормализация для n-грамм (Д-4): нижний регистр, без пунктуации."""
    return [w.lower().replace("ё", "е") for w in words(text)]


def ngrams(tokens: list[str], n: int) -> Counter:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def ttr(tokens: list[str]) -> float:
    """TTR по словоформам без лемматизации (Д-3)."""
    if not tokens:
        return 0.0
    return len({t.lower() for t in tokens}) / len(tokens)


def rolling_ttr(tokens: list[str], window: int) -> list[tuple[int, float]]:
    """TTR нарастающим окном `window` слов: [(позиция конца окна, ttr)]."""
    result: list[tuple[int, float]] = []
    if len(tokens) < window:
        return result
    step = max(1, window // 10)
    for end in range(window, len(tokens) + 1, step):
        result.append((end, ttr(tokens[end - window : end])))
    return result


def narrator_text(raw: str) -> str:
    """Текст для метрик повествователя: без вставок-документов и разметки MD."""
    return strip_markdown(strip_document_inserts(raw))
