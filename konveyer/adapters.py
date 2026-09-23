"""Адаптеры API моделей (раздел 9 ТЗ): провайдеры gemini и anthropic плюс «ручной провайдер».

FR-AD-1: провайдер, модель, параметры и цены — в конфиге; смена провайдера не требует правки кода.
FR-AD-2: роли привязаны к моделям независимо (`Config.role`). FR-AD-4: повторы с экспоненциальной паузой на
сетевых и серверных ошибках, таймаут; при исчерпании — ручной режим с готовым промптом. FR-AD-5: ошибки биллинга,
квоты и доступа распознаются и объясняются по-русски отдельно. FR-AD-6: ключи только из окружения/.env и никогда
не печатаются (маскируются в сообщениях и журнале). FR-AD-7: флаг отмены проверяется между вызовами (cancel).
Каждый вызов — строка журнала `журналы/api.jsonl` с токенами и расчётной стоимостью (FR-CT-1).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import cancel
from .apilog import log_call
from .config import ApiConfig, Config, ModelConfig
from .errors import ManualMode

ManualModeNeeded = ManualMode

KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}
_KEY_NAME_RE = re.compile(r"[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)[A-Z0-9_]*")


class BillingError(RuntimeError):
    """Биллинг, квота или доступ (FR-AD-5): частый случай на старте; повторы бесполезны."""


def mask_secrets(text: str) -> str:
    """Значения ключей из окружения никогда не попадают в текст (FR-AD-6)."""
    for name, value in os.environ.items():
        if _KEY_NAME_RE.fullmatch(name) and value and len(value) >= 8 and value in text:
            text = text.replace(value, f"<{name}>")
    return text


def _estimate_cost(mc: ModelConfig, tokens_in: int | None, tokens_out: int | None) -> float | None:
    if not mc.price_in_per_1m and not mc.price_out_per_1m:
        return None
    return round((tokens_in or 0) * mc.price_in_per_1m / 1_000_000 + (tokens_out or 0) * mc.price_out_per_1m / 1_000_000, 6)


def estimate_cost_before(mc: ModelConfig, prompt_chars: int, expected_out_tokens: int = 2000) -> float | None:
    """Оценка стоимости ДО вызова по размеру промпта (~3 знака на токен) и ценам конфига (FR-EC-1)."""
    if not mc.price_in_per_1m and not mc.price_out_per_1m:
        return None
    return round(prompt_chars / 3 * mc.price_in_per_1m / 1_000_000 + expected_out_tokens * mc.price_out_per_1m / 1_000_000, 4)


def _http_status(e: Exception) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    v = getattr(getattr(e, "response", None), "status_code", None)
    return v if isinstance(v, int) else None


def classify_error(e: Exception) -> str:
    """«биллинг» | «доступ» | «квота» | «сеть» | «клиент»: по HTTP-статусу и тексту ошибки."""
    status = _http_status(e)
    text = f"{type(e).__name__}: {e}".lower()
    if status in (401, 403) or "api key" in text or "unauthorized" in text or "permission" in text:
        return "доступ"
    if status == 402 or "billing" in text or "payment" in text or "insufficient" in text or "credit" in text:
        return "биллинг"
    if status == 429 or "quota" in text or "rate limit" in text or "resource_exhausted" in text:
        return "квота"
    if status is None or status >= 500 or status == 408:
        return "сеть"
    return "клиент"


def explain_error(e: Exception, role: str) -> str:
    kind = classify_error(e)
    msg = mask_secrets(f"{type(e).__name__}: {str(e)[:200]}")
    if kind == "доступ":
        return f"{role}: провайдер отказал в доступе — ключ неверен, отозван или не имеет прав ({msg}). Проверьте ключ в .env."
    if kind == "биллинг":
        return f"{role}: провайдер сообщает об ошибке оплаты — не пополнен баланс или не привязана карта ({msg})."
    if kind == "квота":
        return f"{role}: исчерпана квота или лимит запросов ({msg}). Подождите или поднимите лимит в кабинете провайдера."
    if kind == "сеть":
        return f"{role}: сетевая или серверная ошибка ({msg})."
    return f"{role}: ошибка запроса ({msg})."


def _retryable(e: Exception) -> bool:
    kind = classify_error(e)
    if kind in ("доступ", "биллинг", "клиент"):
        return False
    return True


def _retry_call(fn, api: ApiConfig, logs_dir: Path, *, role: str, mc: ModelConfig, chapter: int | None):
    last_error: Exception | None = None
    for attempt in range(api.retries):
        start = time.monotonic()
        try:
            text, tokens_in, tokens_out = fn()
            log_call(logs_dir, role=role, model=mc.model, tokens_in=tokens_in, tokens_out=tokens_out,
                     cost_est=_estimate_cost(mc, tokens_in, tokens_out), chapter=chapter, duration=time.monotonic() - start)
            return text
        except Exception as e:  # noqa: BLE001 — любая ошибка провайдера: журнал + решение о повторе
            last_error = e
            log_call(logs_dir, role=role, model=mc.model, chapter=chapter, duration=time.monotonic() - start,
                     error=mask_secrets(f"{classify_error(e)}: {type(e).__name__}: {e}"))
            if not _retryable(e):
                break
            if attempt < api.retries - 1:
                time.sleep(api.backoff_base_s * (2 ** attempt))
    assert last_error is not None
    kind = classify_error(last_error)
    reason = explain_error(last_error, f"Вызов {role} ({mc.model})")
    if kind in ("доступ", "биллинг", "квота"):
        reason = f"{reason} [{kind}]"
    else:
        reason = f"{reason} — после {api.retries} попыток"
    raise ManualModeNeeded(
        reason,
        "скопируйте входной файл (окно/промпт) в чат модели вручную и сохраните ответ в ожидаемый файл артефакта — "
        "каждый шаг такта исполним отдельно.",
    )


def api_key_for(provider: str) -> str | None:
    for name in KEY_ENV.get(provider, ()):
        if os.environ.get(name):
            return os.environ[name]
    return None


def _need_key(provider: str, hint: str) -> str:
    key = api_key_for(provider)
    if not key:
        names = " или ".join(KEY_ENV.get(provider, (f"ключ провайдера {provider}",)))
        raise ManualModeNeeded(f"Не найден {names} (задайте в .env).", hint)
    return key


def call_gemini(prompt: str, mc: ModelConfig, api: ApiConfig, logs_dir: Path, chapter: int | None = None,
                role: str = "писатель") -> str:
    """Один вызов = один промпт; system-инструкций вне окна нет (FR-WR-1)."""
    key = _need_key("gemini", "скопируйте окно/промпт в чат модели и сохраните ответ файлом артефакта, затем продолжите такт.")
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ManualModeNeeded("SDK google-genai не установлен (pip install 'konveyer[llm]').",
                               "ручной вызов модели через веб-интерфейс, ответ — файлом артефакта.") from None

    def do():
        http_kwargs: dict = {"timeout": api.timeout_s * 1000}
        retry_options = getattr(types, "HttpRetryOptions", None)
        if retry_options is not None:
            http_kwargs["retry_options"] = retry_options(attempts=1)
        client = genai.Client(api_key=key, http_options=types.HttpOptions(**http_kwargs))
        resp = client.models.generate_content(model=mc.model, contents=prompt, config=types.GenerateContentConfig(**mc.params))
        usage = getattr(resp, "usage_metadata", None)
        return (resp.text or "", getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None))

    return _retry_call(do, api, logs_dir, role=role, mc=mc, chapter=chapter)


def call_anthropic(system: str, user: str, mc: ModelConfig, api: ApiConfig, logs_dir: Path, *, role: str,
                   chapter: int | None = None) -> str:
    key = _need_key("anthropic", "скопируйте подготовленный промпт в чат модели и сохраните JSON-ответ в ожидаемый файл.")
    try:
        import anthropic
    except ImportError:
        raise ManualModeNeeded("SDK anthropic не установлен (pip install 'konveyer[llm]').",
                               "ручной вызов модели через веб-интерфейс.") from None

    def do():
        client = anthropic.Anthropic(api_key=key, timeout=float(api.timeout_s), max_retries=0)
        resp = client.messages.create(
            model=mc.model, max_tokens=mc.params.get("max_tokens", 8192), system=system,
            messages=[{"role": "user", "content": user}], **{k: v for k, v in mc.params.items() if k != "max_tokens"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    return _retry_call(do, api, logs_dir, role=role, mc=mc, chapter=chapter)


def call_model(mc: ModelConfig, api: ApiConfig, system: str, user: str, logs_dir: Path, *, role: str,
               chapter: int | None = None) -> str:
    """Вызов по провайдеру из конфига (FR-AD-1): «ручной» — сразу ручной режим (промпт уже сохранён вызывающим)."""
    cancel.check(f"перед вызовом {role}")
    if mc.manual:
        raise ManualModeNeeded(f"Роль «{role}» настроена на ручной провайдер (конфиг.yaml).",
                               "промпт сохранён в папке главы — прогоните его вручную и сохраните ответ в ожидаемый файл.")
    if mc.provider == "gemini":
        prompt = f"{system}\n\n{user}" if system else user
        return call_gemini(prompt, mc, api, logs_dir, chapter=chapter, role=role)
    if mc.provider == "anthropic":
        return call_anthropic(system, user, mc, api, logs_dir, role=role, chapter=chapter)
    raise ManualModeNeeded(f"Неизвестный провайдер «{mc.provider}» у роли «{role}» (допустимо: gemini, anthropic, ручной).",
                           "исправьте конфиг.yaml или прогоните промпт вручную.")


def call_role(cfg: Config, role_name: str, system: str, user: str, logs_dir: Path, *, role: str | None = None,
              chapter: int | None = None) -> str:
    """Вызов роли по имени (FR-AD-2): модель берётся из `cfg.role(role_name)`."""
    mc = cfg.role(role_name)
    return call_model(mc, cfg.api, system, user, logs_dir, role=role or role_name, chapter=chapter)


# ------------------------------------------------------ сверка пинов с API (FR-RT-3)

PROBE_TIMEOUT_S = 10


def probe_model(mc: ModelConfig, timeout_s: float = PROBE_TIMEOUT_S) -> tuple[bool | None, str]:
    """Существует ли модель у провайдера — ТОЛЬКО чтение метаданных. (True, имя) — есть; (False, причина) —
    не найдена; (None, причина) — не проверено (нет ключа/SDK/сети). Ошибки никогда не поднимаются."""
    if mc.manual:
        return None, "ручной провайдер — проверять нечего"
    if mc.provider == "gemini":
        key = api_key_for("gemini")
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
        except Exception as e:  # noqa: BLE001
            if _http_status(e) == 404:
                return False, f"модель «{mc.model}» не найдена в API (снята или неверный ID)"
            return None, mask_secrets(f"не проверено: {type(e).__name__}: {str(e)[:120]}")
    if mc.provider == "anthropic":
        key = api_key_for("anthropic")
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
            return None, mask_secrets(f"не проверено: {type(e).__name__}: {str(e)[:120]}")
    return None, f"неизвестный провайдер «{mc.provider}»"
