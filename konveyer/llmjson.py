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


def extract_json(raw: str, kind: type) -> list | dict:
    """Первый корректный JSON типа `kind` (list или dict) из текста ответа."""
    opener = "[" if kind is list else "{"
    decoder = json.JSONDecoder()

    for m in _FENCE_RE.finditer(raw):
        candidate = m.group(1).strip()
        try:
            obj = json.loads(candidate)
            if _acceptable(obj, kind):
                return obj
        except json.JSONDecodeError:
            pass

    for m in re.finditer(re.escape(opener), raw):
        try:
            obj, _ = decoder.raw_decode(raw, m.start())
            if _acceptable(obj, kind):
                return obj
        except json.JSONDecodeError:
            continue

    raise ValueError(
        f"в ответе модели не найден корректный JSON ({'массив' if kind is list else 'объект'})."
    )
