"""Адаптеры API моделей (раздел 6): Писатель — Gemini, Верификатор-2/Канонист — Anthropic.

Общие требования §6.3: ключи только из окружения/.env (Д-9); ретраи — 3 попытки
с экспоненциальной паузой на сетевых/5xx; таймаут 120 с; при исчерпании —
понятная ошибка и подсказка ручного режима (NFR-3). Каждый вызов — строка
журналы/api.jsonl. Тексты передаются ТОЛЬКО в эти два API.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .apilog import log_call
from .config import ApiConfig, ModelConfig
from .errors import ManualMode


# API недоступен — конвейер деградирует в ручной режим (NFR-3). Класс живёт в konveyer/errors.py
# (семейство StepError: интерфейсы переводят его в код возврата 2); здесь — прежнее имя.
ManualModeNeeded = ManualMode


def _estimate_cost(mc: ModelConfig, tokens_in: int | None, tokens_out: int | None) -> float | None:
    """Оценка стоимости вызова по ценам из конфиг.yaml (§6.3)."""
    if not mc.price_in_per_1m and not mc.price_out_per_1m:
        return None
    return round(
        (tokens_in or 0) * mc.price_in_per_1m / 1_000_000
        + (tokens_out or 0) * mc.price_out_per_1m / 1_000_000,
        6,
    )


def _retryable(e: Exception) -> bool:
    """Ретраим только сетевые/временные ошибки (§6.3): 5xx, 408, 429 и ошибки
    без HTTP-статуса. Постоянные клиентские (401/403/404/422…) — сразу стоп."""
    status = getattr(e, "status_code", None)
    if status is None:
        status = getattr(getattr(e, "response", None), "status_code", None)
    if status is None:
        status = getattr(e, "code", None)  # ошибки google-genai (APIError) несут HTTP-статус в .code
    if isinstance(status, int) and not isinstance(status, bool):
        return status >= 500 or status in (408, 429)
    return True


def _retry_call(fn, api: ApiConfig, logs_dir: Path, *, role: str, mc: ModelConfig, chapter: int | None):
    last_error: Exception | None = None
    for attempt in range(api.retries):
        start = time.monotonic()
        try:
            text, tokens_in, tokens_out = fn()
            log_call(
                logs_dir,
                role=role,
                model=mc.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_est=_estimate_cost(mc, tokens_in, tokens_out),
                chapter=chapter,
                duration=time.monotonic() - start,
            )
            return text
        except Exception as e:
            last_error = e
            log_call(
                logs_dir,
                role=role,
                model=mc.model,
                chapter=chapter,
                duration=time.monotonic() - start,
                error=f"{type(e).__name__}: {e}",
            )
            if not _retryable(e):
                break
            if attempt < api.retries - 1:
                time.sleep(api.backoff_base_s * (2**attempt))
    raise ManualModeNeeded(
        f"Вызов {role} ({mc.model}) не удался после {api.retries} попыток: {last_error}",
        "скопируйте входной файл (окно/промпт) в чат модели вручную и сохраните ответ "
        "в ожидаемый файл артефакта — каждый шаг такта исполним отдельно (FR-O2).",
    )


def call_gemini(prompt: str, mc: ModelConfig, api: ApiConfig, logs_dir: Path, chapter: int | None = None) -> str:
    """Писатель (§6.1): один вызов = одна глава/сцена; system-инструкций вне окна нет."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ManualModeNeeded(
            "Не найден GEMINI_API_KEY (задайте в .env, Д-9).",
            "скопируйте главы/N/окно.md в чат Gemini и сохраните ответ как главы/N/черновик_1.md, затем продолжите с `konveyer verify1 N`.",
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ManualModeNeeded(
            "SDK google-genai не установлен (pip install 'konveyer[llm]').",
            "ручной вызов модели через веб-интерфейс, ответ — в главы/N/черновик_k.md.",
        )

    def do():
        # повторы — только наши (§6.3): встроенные ретраи SDK не умножаем (4.6)
        http_kwargs: dict = {"timeout": api.timeout_s * 1000}
        retry_options = getattr(types, "HttpRetryOptions", None)
        if retry_options is not None:
            http_kwargs["retry_options"] = retry_options(attempts=1)
        client = genai.Client(api_key=key, http_options=types.HttpOptions(**http_kwargs))
        resp = client.models.generate_content(
            model=mc.model, contents=prompt, config=types.GenerateContentConfig(**mc.params)
        )
        usage = getattr(resp, "usage_metadata", None)
        return (
            resp.text or "",
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
        )

    return _retry_call(do, api, logs_dir, role="писатель", mc=mc, chapter=chapter)


def call_anthropic(
    system: str,
    user: str,
    mc: ModelConfig,
    api: ApiConfig,
    logs_dir: Path,
    *,
    role: str,
    chapter: int | None = None,
) -> str:
    """Верификатор-2 / Канонист (§6.2)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ManualModeNeeded(
            "Не найден ANTHROPIC_API_KEY (задайте в .env, Д-9).",
            "скопируйте подготовленный промпт (главы/N/*_prompt.md) в чат Claude и сохраните JSON-ответ в ожидаемый файл.",
        )
    try:
        import anthropic
    except ImportError:
        raise ManualModeNeeded(
            "SDK anthropic не установлен (pip install 'konveyer[llm]').",
            "ручной вызов модели через веб-интерфейс.",
        )

    def do():
        # max_retries=0: повторы — только наши (§6.3), иначе до 3×3 попыток по 120 с (4.6)
        client = anthropic.Anthropic(api_key=key, timeout=float(api.timeout_s), max_retries=0)
        resp = client.messages.create(
            model=mc.model,
            max_tokens=mc.params.get("max_tokens", 8192),
            system=system,
            messages=[{"role": "user", "content": user}],
            **{k: v for k, v in mc.params.items() if k != "max_tokens"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    return _retry_call(do, api, logs_dir, role=role, mc=mc, chapter=chapter)


# ------------------------------------------------------ сверка пинов с API (аудит 2, этап 5, п. 31)

PROBE_TIMEOUT_S = 10


def probe_model(mc: ModelConfig, timeout_s: float = PROBE_TIMEOUT_S) -> tuple[bool | None, str]:
    """Существует ли модель `mc.model` у провайдера — ТОЛЬКО чтение метаданных (models.get/retrieve),
    ни одной генерации. (True, «имя») — есть; (False, причина) — не найдена/снята; (None, причина) —
    не проверено (нет ключа/SDK/сети). Ошибки никогда не поднимаются: это диагностика `doctor`."""
    if mc.provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return None, "нет GEMINI_API_KEY"
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return None, "SDK google-genai не установлен"
        try:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
            info = client.models.get(model=mc.model)
            return True, getattr(info, "display_name", None) or getattr(info, "name", None) or mc.model
        except Exception as e:  # noqa: BLE001 — диагностика: любая ошибка → вердикт словами
            if _http_status(e) == 404:
                return False, f"модель «{mc.model}» не найдена в API (снята или неверный ID)"
            return None, f"не проверено: {type(e).__name__}: {str(e)[:120]}"
    if mc.provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None, "нет ANTHROPIC_API_KEY"
        try:
            import anthropic
        except ImportError:
            return None, "SDK anthropic не установлен"
        try:
            client = anthropic.Anthropic(api_key=key, timeout=float(timeout_s), max_retries=0)
            info = client.models.retrieve(mc.model)
            return True, getattr(info, "display_name", None) or getattr(info, "id", None) or mc.model
        except Exception as e:  # noqa: BLE001
            if _http_status(e) == 404:
                return False, f"модель «{mc.model}» не найдена в API (снята или неверный ID)"
            return None, f"не проверено: {type(e).__name__}: {str(e)[:120]}"
    return None, f"неизвестный провайдер «{mc.provider}»"


def _http_status(e: Exception) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    v = getattr(getattr(e, "response", None), "status_code", None)
    return v if isinstance(v, int) else None
