"""Конфигурация проекта: `конфиг.yaml` + секреты из `.env` (FR-AD-1, FR-AD-2, FR-AD-6, FR-EC-2).

Здесь живут ТОЛЬКО технологические параметры: провайдеры и модели по ролям, цены, повторы, лимиты, пути бэкапа.
Нормы прозы — только в выгрузках из канона (П-1, FR-V1-2). Роли привязываются к моделям независимо:
Писатель, Верификатор-2, Канонист, аналитик, линтер, архивариус — каждая может иметь свою модель; не заданные
аналитик, линтер и архивариус наследуют модель Канониста (`Config.role`). Провайдер «ручной» — всегда ручной режим;
неизвестный провайдер не отказ при загрузке (П-5): вызов уходит в ручной режим, `доктор` показывает его.

Русские ключи (П-8) равноправны латинским: `библиотека`, `модели: {писатель: …}`, `пороги`, `текущий_том`,
`лимит_окна`, `пауза_автора_мин`, `папка_архива`, `хранить_архивов`, `мест_хранения_мин`, `автор_коммита`,
`ориентиры`; у модели — `цена_вход_1м`, `цена_выход_1м`, `режим_без_обучения`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import guard
from .paths import Workspace

COMMIT_AUTHOR_RE = re.compile(r"^[^<>]*\S\s+<[^<>@\s]+@[^<>@\s]+>$")
CONFIG_NAME = "конфиг.yaml"
ROLES = ("писатель", "верификатор2", "канонист", "аналитик", "линтер", "архивариус")
ROLE_ALIASES = {"writer": "писатель", "verifier2": "верификатор2", "canonist": "канонист", "analyst": "аналитик",
                "linter": "линтер", "archivist": "архивариус"}
PROVIDERS = ("gemini", "anthropic", "ручной", "manual")


MODEL_KEY_SYNONYMS = {"цена_вход_1м": "price_in_per_1m", "цена_выход_1м": "price_out_per_1m", "параметры": "params",
                      "провайдер": "provider", "модель": "model"}
CONFIG_KEY_SYNONYMS = {
    "библиотека": "library_dir", "текущий_том": "volume", "лимит_окна": "window_soft_limit_chars",
    "пауза_автора_мин": "author_pause_min", "папка_архива": "backup_dir", "хранить_архивов": "backup_keep",
    "мест_хранения_мин": "backup_remotes_min", "автор_коммита": "commit_author", "ориентиры": "guidelines",
    "бюджет_линтера": "lint_budget_usd",
}


class ModelConfig(BaseModel):
    provider: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    price_in_per_1m: float = 0.0
    price_out_per_1m: float = 0.0
    no_training: bool = True   # FR-SC-10: режим без обучения на данных пользователя (фиксируется в журнале решений)

    @model_validator(mode="before")
    @classmethod
    def _russian_model_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for ru, en in MODEL_KEY_SYNONYMS.items():
            if ru in d:
                d.setdefault(en, d.pop(ru))
        if "режим_без_обучения" in d:
            d.setdefault("no_training", d.pop("режим_без_обучения") in (True, "да", "вкл", "yes"))
        return d

    @field_validator("provider")
    @classmethod
    def _provider_normalized(cls, v: str) -> str:
        """Регистр и пробелы не значимы («Ручной», « anthropic »); неизвестный провайдер допустим (П-5)."""
        return str(v).strip().lower()

    @property
    def manual(self) -> bool:
        return self.provider in ("ручной", "manual")

    @property
    def known_provider(self) -> bool:
        return self.provider in PROVIDERS


class ApiConfig(BaseModel):
    retries: int = 3
    backoff_base_s: float = 2.0
    timeout_s: int = 120


class Thresholds(BaseModel):
    """Пороги предупреждений экономики (FR-EC-2): 0 — не предупреждать."""

    chapter_cost_usd: float = 0.0
    daily_cost_usd: float = 0.0


class GuidelineSizes(BaseModel):
    """Размеры для ориентиров экономики (FR-EC-3), когда фактов ещё нет: окно Писателя в знаках, выход на
    генерацию и на проверку в токенах. Факты (главы/*/окно.md, журнал API) сильнее этих значений."""

    window_chars: int = 13_000
    out_tokens: int = 1_200
    check_out_tokens: int = 600

    @model_validator(mode="before")
    @classmethod
    def _russian_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for ru, en in (("окно_знаков", "window_chars"), ("выход_токенов", "out_tokens"), ("выход_проверки_токенов", "check_out_tokens")):
            if ru in d:
                d.setdefault(en, d.pop(ru))
        return d


class Config(BaseModel):
    library_dir: str = "Библиотека"
    writer: ModelConfig = ModelConfig(provider="gemini", model="gemini-3.1-pro")
    verifier2: ModelConfig = ModelConfig(provider="anthropic", model="claude-sonnet-4-5")
    canonist: ModelConfig = ModelConfig(provider="anthropic", model="claude-sonnet-4-5")
    analyst: ModelConfig | None = None
    linter: ModelConfig | None = None
    archivist: ModelConfig | None = None
    api: ApiConfig = ApiConfig()
    thresholds: Thresholds = Thresholds()
    guidelines: GuidelineSizes = GuidelineSizes()
    window_soft_limit_chars: int = 80_000
    window_prior_events_max: int = 24        # FR-WN-5: событий предыдущих глав в окне
    window_prior_continuity_max: int = 30    # FR-WN-5: деталей континуити в окне
    window_tail_paragraphs: int = 3          # FR-WN-5: хвост прозы предыдущей главы фокала, абзацев
    window_tail_chars: int = 1200            # FR-WN-5: … и знаков
    auto_retries_verify1: int = 2
    edit_cycle_max_iterations: int = 3
    commit_author: str | None = None
    backup_remotes_min: int = 2
    backup_dir: str | None = None
    backup_keep: int = 10
    push_after_canonize: bool = False    # FR-BK-1: после приёмки главы отправлять библиотеку во все remotes (сеть)
    volume: int = 1                      # текущий том (совместимость; манифест `проект.yaml` сильнее)
    e2_max_flags: int = 12               # FR-V2-4: лимит числа флагов за прогон
    e2_quote_words: int = 20             # FR-V2-4: длина цитаты флага
    onboarding_model_layer: bool = True  # FR-ON-8: модельный слой онбординга по умолчанию
    onboarding_max_docs: int = 60        # R-9: лимит документов за прогон архивариуса
    classification_threshold: float = 0.35  # Д-11
    author_pause_min: int = 120          # FR-CT-2: авторская пауза дольше порога — перерыв, не работа
    lint_budget_usd: float = 0.0         # FR-LT-3: бюджет модельного слоя линтера за прогон, $; 0 — без лимита

    @model_validator(mode="before")
    @classmethod
    def _russian_keys(cls, data: Any) -> Any:
        """Русские ключи конфига (П-8): см. докстринг модуля; латинские остаются скрытыми синонимами."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for ru, en in CONFIG_KEY_SYNONYMS.items():
            if ru in d:
                d.setdefault(en, d.pop(ru))
        models = d.pop("модели", None) or {}
        for k, v in list(d.items()):
            if k in ROLES:
                models.setdefault(k, d.pop(k))
        for k, v in models.items():
            role = ROLE_ALIASES.get(k, k)
            eng = {v2: k2 for k2, v2 in ROLE_ALIASES.items()}.get(role, role)
            if isinstance(v, dict) and "режим_без_обучения" in v:
                v = {**v, "no_training": v.pop("режим_без_обучения") in (True, "да", "вкл", "yes")}
            d.setdefault(eng, v)
        if "пороги" in d:
            t = d.pop("пороги") or {}
            d.setdefault("thresholds", {"chapter_cost_usd": t.get("стоимость_главы", t.get("chapter_cost_usd", 0.0)),
                                        "daily_cost_usd": t.get("расход_за_сутки", t.get("daily_cost_usd", 0.0))})
        if "текущий_том" in d:
            d.setdefault("volume", d.pop("текущий_том"))
        if "лимит_окна" in d:
            d.setdefault("window_soft_limit_chars", d.pop("лимит_окна"))
        for ru, en in (("окно_событий_макс", "window_prior_events_max"), ("окно_континуити_макс", "window_prior_continuity_max"),
                       ("окно_хвост_абзацев", "window_tail_paragraphs"), ("окно_хвост_знаков", "window_tail_chars")):
            if ru in d:
                d.setdefault(en, d.pop(ru))
        if "пауза_автора_мин" in d:
            d.setdefault("author_pause_min", d.pop("пауза_автора_мин"))
        if "push_после_приёмки" in d:
            d.setdefault("push_after_canonize", d.pop("push_после_приёмки") in (True, "да", "вкл", "yes"))
        return d

    @field_validator("volume")
    @classmethod
    def _volume_positive(cls, v: int) -> int:
        if int(v) < 1:
            raise ValueError(f"volume в конфиг.yaml — номер тома, целое число ≥ 1, получено: {v}.")
        return int(v)

    @field_validator("commit_author")
    @classmethod
    def _commit_author_format(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        v = str(v).strip()
        if not COMMIT_AUTHOR_RE.match(v):
            raise ValueError(f"commit_author в конфиг.yaml должен иметь вид «Имя <email>», получено: «{v}».")
        return v

    def role(self, name: str) -> ModelConfig:
        """Модель роли (FR-AD-2): аналитик/линтер/архивариус наследуют Канониста, если не заданы."""
        name = ROLE_ALIASES.get(name, name)
        if name == "писатель":
            return self.writer
        if name == "верификатор2":
            return self.verifier2
        if name == "канонист":
            return self.canonist
        if name == "аналитик":
            return self.analyst or self.canonist
        if name == "линтер":
            return self.linter or self.canonist
        if name == "архивариус":
            return self.archivist or self.canonist
        raise ValueError(f"неизвестная роль модели «{name}»; допустимо: {', '.join(ROLES)}")

    def roles(self) -> dict[str, ModelConfig]:
        return {r: self.role(r) for r in ROLES}


def config_path(ws: Workspace) -> Path:
    return ws.root / CONFIG_NAME


def load_config(ws: Workspace) -> Config:
    """конфиг.yaml рабочей области; нет файла — умолчания. Битый YAML или недопустимое значение —
    `ValueError` с русским текстом (интерфейсы печатают «ОШИБКА: …» без трейсбека, FR-CL-3)."""
    path = config_path(ws)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f" (строка {mark.line + 1}, столбец {mark.column + 1})" if mark is not None else ""
            raise ValueError(f"{path.name} не читается как YAML{where}: {getattr(e, 'problem', None) or e}. "
                             "Поправьте файл или восстановите из data/конфиг.пример.yaml.") from None
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: ожидался YAML-словарь «ключ: значение», а не {type(data).__name__}.")
    try:
        cfg = Config.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'конфиг'}: {_describe_validation(err)}" for err in e.errors()
        )
        raise ValueError(f"{path.name}: недопустимые значения — {problems}.") from None
    _load_dotenv(ws.root / ".env")
    return cfg


def _describe_validation(err: dict) -> str:
    kind = str(err.get("type", ""))
    value = err.get("input")
    shown = f"получено «{value}»" if not isinstance(value, dict) else "получен словарь"
    if kind.startswith("int_"):
        return f"ожидается целое число, {shown}"
    if kind.startswith("float_"):
        return f"ожидается число, {shown}"
    if kind.startswith("bool_"):
        return f"ожидается да/нет, {shown}"
    if kind in ("string_type", "str_type"):
        return f"ожидается строка, {shown}"
    if kind == "missing":
        return "обязательное поле не задано"
    if kind == "extra_forbidden":
        return "неизвестное поле"
    msg = str(err.get("msg", ""))
    return (msg.replace("Value error, ", "") or kind) + (f" ({shown})" if value is not None else "")


def library_dir(ws: Workspace, cfg: Config) -> Path:
    p = Path(cfg.library_dir)
    return p if p.is_absolute() else (ws.root / p)


def _load_dotenv(path: Path) -> None:
    """Минимальный разбор .env (FR-AD-6): KEY=VALUE, строки с # игнорируются; значения не печатаются."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_VOLUME_LINE_RE = re.compile(r"^(volume|текущий_том)\s*:.*$", re.MULTILINE)


def set_volume(ws: Workspace, volume: int) -> Path:
    """Переключает текущий том: в манифесте `проект.yaml` (если есть) и в конфиг.yaml (совместимость)."""
    if int(volume) < 1:
        raise ValueError(f"номер тома должен быть ≥ 1, получено: {volume}.")
    from . import manifest as manifest_mod

    if manifest_mod.path_of(ws.root).exists():
        manifest_mod.set_volume(ws.root, int(volume))
    path = config_path(ws)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    m = _VOLUME_LINE_RE.search(text)
    if m:
        # ключ автора сохраняется: `текущий_том: 2` остаётся русским, `volume: 2` — латинским
        text = _VOLUME_LINE_RE.sub(f"{m.group(1)}: {int(volume)}", text, count=1)
    else:
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + f"# Текущий том рабочей области (`konveyer том открыть N`).\nтекущий_том: {int(volume)}\n"
    guard.write_text(path, text)
    return path


def config_fingerprint_text(ws: Workspace) -> str:
    """Текст конфига для отпечатка регрессии (FR-RG-4)."""
    path = config_path(ws)
    return path.read_text(encoding="utf-8") if path.exists() else ""
