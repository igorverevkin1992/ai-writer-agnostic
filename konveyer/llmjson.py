"""Извлечение JSON из ответа LLM (Верификатор-2, Канонист).

Ответ модели может содержать пояснительную прозу со скобками — жадные регэкспы
вида `\\[.*\\]` ломаются. Здесь: приоритет ограждённому ```json-блоку, затем
попытка raw_decode с каждой открывающей скобки до первого валидного значения
нужного типа.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _acceptable(obj, kind: type) -> bool:
    if not isinstance(obj, kind):
        return False
    # массив флагов — это массив объектов: «[2]» из прозы ответа не подходит
    if kind is list:
        return all(isinstance(item, dict) for item in obj)
    return True


def _candidates(raw: str, kind: type):
    """Все корректные JSON нужного типа: сначала из ```-ограждений, затем с каждой открывающей скобки."""
    opener = "[" if kind is list else "{"
    decoder = json.JSONDecoder()
    for m in _FENCE_RE.finditer(raw):
        try:
            obj = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        if _acceptable(obj, kind):
            yield obj
    for m in re.finditer(re.escape(opener), raw):
        try:
            obj, _ = decoder.raw_decode(raw, m.start())
        except json.JSONDecodeError:
            continue
        if _acceptable(obj, kind):
            yield obj


def extract_json(raw: str, kind: type) -> list | dict:
    """Корректный JSON типа `kind` (list или dict) из текста ответа: приоритет ```-ограждению; пустое
    значение («[]» из пояснения «если нарушений нет — верну []») принимается, только если непустого
    в ответе нет — иначе настоящие флаги терялись бы, а глава считалась чистой (FR-V2-4)."""
    empty = None
    for obj in _candidates(raw, kind):
        if obj:
            return obj
        if empty is None:
            empty = obj
    if empty is not None:
        return empty
    raise ValueError(
        f"в ответе модели не найден корректный JSON ({'массив' if kind is list else 'объект'})."
    )
