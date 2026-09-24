"""Конфигурация проекта: `конфиг.yaml` + секреты из `.env` (FR-AD-1, FR-AD-2, FR-AD-6, FR-EC-2).

Здесь живут ТОЛЬКО технологические параметры: провайдеры и модели по ролям, цены, повторы, лимиты, пути бэкапа.
Нормы прозы — только в выгрузках из канона (П-1, FR-V1-2). Роли привязываются к моделям независимо:
Писатель, Верификатор-2, Канонист, аналитик, линтер, архивариус — каждая может иметь свою модель; не заданная
роль наследует модель Канониста (проверки) или Писателя (генерация). Провайдер «ручной» — всегда ручной режим.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from . import guard
from .paths import Workspace

COMMIT_AUTHOR_RE = re.compile(r"^[^<>]*\S\s+<[^<>@\s]+@[^<>@\s]+>$")
CONFIG_NAME = "конфиг.yaml"
ROLES = ("писатель", "верификатор2", "канонист", "аналитик", "линтер", "архивариус")
ROLE_ALIASES = {"writer": "писатель", "verifier2": "верификатор2", "canonist": "канонист", "analyst": "аналитик",
                "linter": "линтер", "archivist": "архивариус"}
PROVIDERS = ("gemini", "anthropic", "ручной", "manual")


PRICE_ALIASES = {"цена_вход_1м": "price_in_per_1m", "цена_выход_1м": "price_out_per_1m"}


class ModelConfig(BaseModel):
    provider: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    price_in_per_1m: float = 0.0
    price_out_per_1m: float = 0.0
    no_training: bool = True   # FR-SC-10: режим без обучения на данных пользователя (фиксируется в журнале решений)

    @model_validator(mode="before")
    @classmethod
    def _price_aliases(cls, data: Any) -> Any:
        """Русские ключи цен (`цена_вход_1м`/`цена_выход_1м` за 1 млн токенов, FR-EC-1) — синонимы латинских."""
        if isinstance(data, dict) and any(k in data for k in PRICE_ALIASES):
            data = dict(data)
            for ru, en in PRICE_ALIASES.items():
                if ru in data:
                    data.setdefault(en, data.pop(ru))
        return data

    @property
    def manual(self) -> bool:
        return self.provider in ("ручной", "manual")


class ApiConfig(BaseModel):
    retries: int = 3
    backoff_base_s: float = 2.0
    timeout_s: int = 120


class Thresholds(BaseModel):
    """Пороги предупреждений экономики (FR-EC-2): 0 — не предупреждать."""

    chapter_cost_usd: float = 0.0
    daily_cost_usd: float = 0.0


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
    volume: int = 1                      # текущий том (совместимость; манифест `проект.yaml` сильнее)
    e2_max_flags: int = 12               # FR-V2-4: лимит числа флагов за прогон
    e2_quote_words: int = 20             # FR-V2-4: длина цитаты флага
    onboarding_model_layer: bool = True  # FR-ON-8: модельный слой онбординга по умолчанию
    onboarding_max_docs: int = 60        # R-9: лимит документов за прогон архивариуса
    classification_threshold: float = 0.35  # Д-11
    author_pause_min: int = 120          # FR-CT-2: авторская пауза дольше порога — перерыв, не работа

    @model_validator(mode="before")
    @classmethod
    def _russian_keys(cls, data: Any) -> Any:
        """Русские ключи конфига: `библиотека`, `модели: {писатель: …}`, `пороги`, `текущий_том`."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "библиотека" in d:
            d.setdefault("library_dir", d.pop("библиотека"))
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
    path = config_path(ws)
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(data)
    _load_dotenv(ws.root / ".env")
    return cfg


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
    line = f"volume: {int(volume)}"
    if _VOLUME_LINE_RE.search(text):
        text = _VOLUME_LINE_RE.sub(line, text, count=1)
    else:
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + f"# Текущий том рабочей области (`konveyer том открыть N`).\n{line}\n"
    guard.write_text(path, text)
    return path


def config_fingerprint_text(ws: Workspace) -> str:
    """Текст конфига для отпечатка регрессии (FR-RG-4)."""
    path = config_path(ws)
    return path.read_text(encoding="utf-8") if path.exists() else ""
