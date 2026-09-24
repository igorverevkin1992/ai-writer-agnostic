"""Текстовые счётчики Э1 поверх языкового модуля (`lang.py`, FR-V1-3): предложения (Д-2), слова, n-граммы (Д-4),
TTR (Д-3), речь повествователя без реплик, документы-вставки (Д-7). Правила языка живут в `языки/<код>.yaml`;
здесь — совместимый интерфейс для проверок и линтера (русский по умолчанию)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import lang

DOC_START = lang.DOC_START
DOC_END = lang.DOC_END


def _lang(root: Path | None = None) -> lang.Language:
    return lang.get(lang.DEFAULT, root)


def narration_only(text: str) -> str:
    return _lang().narration_only(text)


def paragraphs(text: str) -> list[str]:
    return _lang().paragraphs(text)


def strip_document_inserts(text: str) -> str:
    return _lang().strip_document_inserts(text)


def strip_markdown(text: str) -> str:
    """Снимает заголовки (целиком — это метаданные файла, не проза) и выделение MD."""
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*-{3,}\s*$", "", text, flags=re.M)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return text


def split_sentences(text: str, extra_abbr: Path | None = None) -> list[str]:
    return _lang().split_sentences(text, extra_abbr)


def words(text: str) -> list[str]:
    return _lang().words(text)


def word_count(text: str) -> int:
    return len(words(text))


def sentence_lengths(text: str, extra_abbr: Path | None = None) -> list[int]:
    return [len(words(s)) for s in split_sentences(text, extra_abbr) if words(s)]


def normalize(text: str) -> list[str]:
    return _lang().normalize(text)


def ngrams(tokens: list[str], n: int) -> Counter:
    return Counter(tuple(tokens[i: i + n]) for i in range(len(tokens) - n + 1))


def ttr(tokens: list[str]) -> float:
    """TTR по словоформам без лемматизации (Д-3)."""
    if not tokens:
        return 0.0
    return len({t.lower() for t in tokens}) / len(tokens)


def rolling_ttr(tokens: list[str], window: int) -> list[tuple[int, float]]:
    """TTR нарастающим окном `window` слов: [(позиция конца окна, ttr)]."""
    result: list[tuple[int, float]] = []
    if window <= 0 or len(tokens) < window:
        return result
    step = max(1, window // 10)
    for end in range(window, len(tokens) + 1, step):
        result.append((end, ttr(tokens[end - window: end])))
    return result


def narrator_text(raw: str) -> str:
    """Текст для метрик повествователя: без вставок-документов и разметки MD."""
    return strip_markdown(strip_document_inserts(raw))
