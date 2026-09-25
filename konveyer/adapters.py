"""Адаптеры API моделей (раздел 9 ТЗ): провайдеры gemini и anthropic плюс «ручной провайдер».

FR-AD-1: провайдер, модель, параметры и цены — в конфиге; смена провайдера не требует правки кода.
FR-AD-2: роли привязаны к моделям независимо (`Config.role`). FR-AD-4: повторы с экспоненциальной паузой на
сетевых и серверных ошибках, таймаут; при исчерпании — ручной режим с готовым промптом. FR-AD-5: ошибки биллинга,
квоты и доступа распознаются и объясняются по-русски отдельно. FR-AD-6: ключи только из окружения/.env и никогда
не печатаются (маскируются в сообщениях и журнале). FR-AD-7: флаг отмены проверяется между вызовами и в паузах
между повторами (cancel). Каждый вызов — строка журнала `журналы/api.jsonl` с токенами и расчётной стоимостью
(FR-CT-1, FR-WR-5). Перед каждым вызовом роли срабатывает хук `before_call` (ставит `steps.common._ctx()`):
оценка стоимости, пороги экономики и предупреждение о смене пина — одинаково для всех ролей (FR-EC-1, FR-RT-2).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable

from . import __version__, cancel
from .apilog import log_call
from .config import ApiConfig, Config, ModelConfig
from .errors import ManualMode

ManualModeNeeded = ManualMode

KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}
SDK_MODULE = {"gemini": "google.genai", "anthropic": "anthropic"}
_KEY_NAME_RE = re.compile(r"[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)[A-Z0-9_]*")

# Хук перед вызовом модели: (роль для журнала, ключ роли в конфиге, модель, размер промпта в знаках).
# Ядро шагов ставит сюда печать оценки стоимости, порогов и смены пина; тесты и библиотечное использование — None.
BeforeCall = Callable[[str, "str | None", ModelConfig, int], None]
before_call: BeforeCall | None = None

# Пауза между повторами спит короткими отрезками, чтобы «Остановить» действовало и во время ожидания (FR-AD-7).
_SLEEP_SLICE_S = 0.2


class BillingError(RuntimeError):
    """Биллинг, квота или доступ (FR-AD-5): частый случай на старте; повторы бесполезны."""


class EmptyResponse(RuntimeError):
    """Провайдер вернул пустой текст (отказ, блокировка фильтром, пустой кандидат): ответом не считается,
    попытка повторяется как сетевая ошибка."""


class TruncatedResponse(RuntimeError):
    """Ответ модели оборван по лимиту выходных токенов: усечённый текст нельзя принять за полный
    (черновик без финала, JSON без закрывающей скобки). Повторять бессмысленно — нужно поднять лимит."""

    def __init__(self, limit: int | None = None, text: str = "", reason: str | None = None):
        super().__init__(
            "ответ оборван по лимиту выходных токенов"
            + (f" (max_tokens={limit})" if limit else "") + (f" (причина завершения: {reason})" if reason else "")
            + " — поднимите params.max_tokens у роли в конфиг.yaml"
        )
        self.limit = limit
        self.text = text


def mask_secrets(text: str) -> str:
    """Значения ключей из окружения никогда не попадают в текст (FR-AD-6)."""
    for name, value in os.environ.items():
        if _KEY_NAME_RE.fullmatch(name) and value and len(value) >= 8 and value in text:
            text = text.replace(value, f"<{name}>")
    return text


def _short(e: Exception, limit: int) -> str:
    """Текст ошибки для автора: сначала маскирование ключей, затем усечение — чтобы граница усечения
    не разрезала ключ и его начало не утекло в вывод (FR-AD-6, FR-SC-9)."""
    return mask_secrets(f"{type(e).__name__}: {e}")[:limit]


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


_NETWORK_NAME_RE = re.compile(r"connection|timeout|timed ?out|network|unavailable|overloaded|deadline|remoteprotocol|"
                              r"readerror|writeerror|socket|dns|сеть|соединен", re.IGNORECASE)


def _is_network(e: Exception) -> bool:
    """Сетевой ли сбой без HTTP-статуса: по классу исключения (ConnectionError, TimeoutError, OSError и SDK-классы
    APIConnectionError/APITimeoutError/httpx.*) и по тексту. Программные ошибки адаптера (ValueError, TypeError,
    AttributeError, KeyError) сетью не считаются — повторять их бессмысленно."""
    if isinstance(e, (ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(e, (ValueError, TypeError, AttributeError, KeyError, IndexError, TruncatedResponse)):
        return False
    names = " ".join(k.__name__ for k in type(e).__mro__) + " " + type(e).__module__
    return bool(_NETWORK_NAME_RE.search(names)) or bool(_NETWORK_NAME_RE.search(str(e)))


def classify_error(e: Exception) -> str:
    """«биллинг» | «доступ» | «квота» | «сеть» | «клиент» | «пустой ответ» | «обрыв»: по типу, HTTP-статусу и тексту."""
    if isinstance(e, EmptyResponse):
        return "пустой ответ"
    if isinstance(e, TruncatedResponse):
        return "обрыв"
    status = _http_status(e)
    text = f"{type(e).__name__}: {e}".lower()
    if status in (401, 403) or "api key" in text or "unauthorized" in text or "permission" in text:
        return "доступ"
    if status == 402 or "billing" in text or "payment" in text or "insufficient" in text or "credit" in text:
        return "биллинг"
    if status == 429 or "quota" in text or "rate limit" in text or "resource_exhausted" in text:
        return "квота"
    if status is None:
        return "сеть" if _is_network(e) else "клиент"
    if status >= 500 or status == 408:
        return "сеть"
    return "клиент"


def explain_error(e: Exception, role: str) -> str:
    kind = classify_error(e)
    msg = _short(e, 200)
    if kind == "доступ":
        return f"{role}: провайдер отказал в доступе — ключ неверен, отозван или не имеет прав ({msg}). Проверьте ключ в .env."
    if kind == "биллинг":
        return f"{role}: провайдер сообщает об ошибке оплаты — не пополнен баланс или не привязана карта ({msg})."
    if kind == "квота":
        return f"{role}: исчерпана квота или лимит запросов ({msg}). Подождите или поднимите лимит в кабинете провайдера."
    if kind == "сеть":
        return f"{role}: сетевая или серверная ошибка ({msg})."
    if kind == "пустой ответ":
        return f"{role}: модель вернула пустой ответ — отказ или блокировка ({msg}); черновик не сохранён."
    if kind == "обрыв":
        return f"{role}: {mask_secrets(str(e))}; усечённый текст не сохранён."
    return f"{role}: ошибка запроса ({msg})."


def _retryable(e: Exception) -> bool:
    return classify_error(e) in ("сеть", "квота", "пустой ответ")


def _sleep_cancellable(seconds: float, where: str) -> None:
    """Пауза перед повтором с проверкой флага отмены: «Остановить» не ждёт конца экспоненциальной паузы."""
    cancel.check(where)
    deadline = time.monotonic() + seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(_SLEEP_SLICE_S, left))
        cancel.check(where)


def _retry_call(fn, api: ApiConfig, logs_dir: Path, *, role: str, mc: ModelConfig, chapter: int | None):
    last_error: Exception | None = None
    attempts = 0
    for attempt in range(api.retries):
        attempts = attempt + 1
        start = time.monotonic()
        try:
            text, tokens_in, tokens_out = fn()
            if not (text or "").strip():
                raise EmptyResponse("пустой текст ответа")
            log_call(logs_dir, role=role, model=mc.model, version=__version__, tokens_in=tokens_in, tokens_out=tokens_out,
                     cost_est=_estimate_cost(mc, tokens_in, tokens_out), chapter=chapter, duration=time.monotonic() - start)
            return text
        except Exception as e:  # noqa: BLE001 — любая ошибка провайдера: журнал + решение о повторе
            last_error = e
            log_call(logs_dir, role=role, model=mc.model, version=__version__, chapter=chapter,
                     duration=time.monotonic() - start, error=mask_secrets(f"{classify_error(e)}: {type(e).__name__}: {e}"))
            if not _retryable(e):
                break
            if attempt < api.retries - 1:
                _sleep_cancellable(api.backoff_base_s * (2 ** attempt), f"пауза перед повтором вызова «{role}»")
    assert last_error is not None
    kind = classify_error(last_error)
    reason = explain_error(last_error, f"Вызов {role} ({mc.model})")
    if kind in ("доступ", "биллинг", "квота", "обрыв"):
        reason = f"{reason} [{kind}]"
    if attempts > 1:
        reason = f"{reason} — после {attempts} попыток"
    if kind == "обрыв":
        hint = ("поднимите params.max_tokens у роли в конфиг.yaml и повторите шаг, либо прогоните промпт вручную "
                "и сохраните ответ в ожидаемый файл артефакта.")
    else:
        hint = ("скопируйте входной файл (окно/промпт) в чат модели вручную и сохраните ответ в ожидаемый файл артефакта — "
                "каждый шаг такта исполним отдельно.")
    raise ManualModeNeeded(reason, hint)


# причины завершения, означающие обрыв по лимиту токенов (anthropic `stop_reason`, gemini `finish_reason`)
TRUNCATED_REASONS = {"max_tokens", "length"}


def _gemini_finish_reason(resp) -> str | None:
    cands = getattr(resp, "candidates", None) or []
    reason = getattr(cands[0], "finish_reason", None) if cands else None
    return getattr(reason, "name", reason) if reason is not None else None


def _check_finish(reason, limit: int | None = None, text: str = "") -> None:
    """Обрыв по лимиту токенов — не ответ (FR-WR-1): усечённый текст черновиком не становится."""
    if reason is not None and _finish_reason_name(reason).lower() in TRUNCATED_REASONS:
        raise TruncatedResponse(limit, text, None if limit else _finish_reason_name(reason).lower())


def api_key_for(provider: str) -> str | None:
    for name in KEY_ENV.get(provider, ()):
        if os.environ.get(name):
            return os.environ[name]
    return None


def _sdk_missing(provider: str) -> bool:
    import importlib.util

    name = SDK_MODULE.get(provider)
    if not name:
        return False
    try:
        return importlib.util.find_spec(name) is None
    except (ModuleNotFoundError, ValueError):
        return True


def unavailable_reason(mc: ModelConfig, role: str) -> str | None:
    """Почему модель роли нельзя вызвать — БЕЗ вызова: ручной провайдер, неизвестный провайдер, нет ключа, нет SDK.
    None — вызов возможен (сеть и биллинг проверяются только самим вызовом). Шаги смотрят сюда до того, как
    расходовать счётчики (авто-повтор Э1) или обещать автору генерацию."""
    if mc.manual:
        return f"Роль «{role}» настроена на ручной провайдер (конфиг.yaml)."
    if mc.provider not in KEY_ENV:
        return f"Неизвестный провайдер «{mc.provider}» у роли «{role}» (допустимо: gemini, anthropic, ручной)."
    if not api_key_for(mc.provider):
        return f"Не найден {' или '.join(KEY_ENV[mc.provider])} (задайте в .env)."
    if _sdk_missing(mc.provider):
        return f"SDK {SDK_MODULE[mc.provider]} не установлен (pip install 'konveyer[llm]')."
    return None


def _need_key(provider: str, hint: str) -> str:
    key = api_key_for(provider)
    if not key:
        names = " или ".join(KEY_ENV.get(provider, (f"ключ провайдера {provider}",)))
        raise ManualModeNeeded(f"Не найден {names} (задайте в .env).", hint)
    return key


def _finish_reason_name(value) -> str:
    return str(getattr(value, "name", value) or "").upper()


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
        text = resp.text or ""
        _check_finish(_gemini_finish_reason(resp), mc.params.get("max_output_tokens"), text)
        return (text, getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None))

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
        max_tokens = mc.params.get("max_tokens", 8192)
        resp = client.messages.create(
            model=mc.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}], **{k: v for k, v in mc.params.items() if k != "max_tokens"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        _check_finish(getattr(resp, "stop_reason", None), max_tokens, text)
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    return _retry_call(do, api, logs_dir, role=role, mc=mc, chapter=chapter)


def call_model(mc: ModelConfig, api: ApiConfig, system: str, user: str, logs_dir: Path, *, role: str,
               chapter: int | None = None, role_key: str | None = None) -> str:
    """Вызов по провайдеру из конфига (FR-AD-1): «ручной» — сразу ручной режим (промпт уже сохранён вызывающим).
    `role_key` — имя роли в конфиге (для сверки пина); по умолчанию совпадает с `role`."""
    cancel.check(f"перед вызовом {role}")
    if mc.manual:
        raise ManualModeNeeded(f"Роль «{role}» настроена на ручной провайдер (конфиг.yaml).",
                               "промпт сохранён в папке главы — прогоните его вручную и сохраните ответ в ожидаемый файл.")
    if before_call is not None:
        before_call(role, role_key or role, mc, len(system) + len(user))
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
    return call_model(mc, cfg.api, system, user, logs_dir, role=role or role_name, chapter=chapter, role_key=role_name)


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
            return None, f"не проверено: {_short(e, 120)}"
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
            return None, f"не проверено: {_short(e, 120)}"
    return None, f"неизвестный провайдер «{mc.provider}»"
