"""Конфигурация конвейера: конфиг.yaml + секреты из .env (Д-6, Д-9, Д-11, Д-12).

Здесь живут ТОЛЬКО технологические параметры (модели, ретраи, лимит окна).
Нормы прозы — только в norms.json, сгенерированном из канона (критерий приёмки 6).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from . import guard
from .paths import Workspace

# Д-8: git принимает --author только в виде «Имя <email>»
COMMIT_AUTHOR_RE = re.compile(r"^[^<>]*\S\s+<[^<>@\s]+@[^<>@\s]+>$")


class ModelConfig(BaseModel):
    provider: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)  # Д-6: параметры пинуются
    # цены за 1 млн токенов (для cost_est в журналы/api.jsonl, §6.3); 0 = не считать
    price_in_per_1m: float = 0.0
    price_out_per_1m: float = 0.0


class ApiConfig(BaseModel):
    retries: int = 3            # §6.3: 3 попытки
    backoff_base_s: float = 2.0  # экспоненциальная пауза
    timeout_s: int = 120        # §6.3: таймаут 120 с


class Config(BaseModel):
    library_dir: str = "Библиотека"
    writer: ModelConfig = ModelConfig(provider="gemini", model="gemini-3.1-pro")  # Р-016
    verifier2: ModelConfig = ModelConfig(provider="anthropic", model="claude-sonnet-4-5")  # Д-11
    canonist: ModelConfig = ModelConfig(provider="anthropic", model="claude-sonnet-4-5")  # Д-11
    api: ApiConfig = ApiConfig()
    window_soft_limit_chars: int = 80_000  # Д-12
    auto_retries_verify1: int = 2          # §5.4: авто-повтор ≤2
    edit_cycle_max_iterations: int = 3     # FR-E3: ≤3 итераций
    commit_author: str | None = None       # Д-8: авторство коммита — автор ("Имя <email>")
    backup_remotes_min: int = 2            # NFR-6
    backup_dir: str | None = None          # архив рабочей области (главы/, журналы/…); None = ../архивы по запросу
    backup_keep: int = 10                  # сколько последних архивов хранить
    volume: int = 1                        # текущий том рабочей области (аудит 2, п. 27): главы, экспорт, документы канона

    @field_validator("volume")
    @classmethod
    def _volume_positive(cls, v: int) -> int:
        if int(v) < 1:
            raise ValueError(f"volume в конфиг.yaml — номер тома, целое число ≥ 1, получено: {v}.")
        return int(v)

    @field_validator("commit_author")
    @classmethod
    def _commit_author_format(cls, v: str | None) -> str | None:
        """Неверный формат сорвал бы коммит приёмки уже ПОСЛЕ записи в библиотеку (2.6) —
        проверяем при загрузке конфига, до любой записи."""
        if v is None or not str(v).strip():
            return None
        v = str(v).strip()
        if not COMMIT_AUTHOR_RE.match(v):
            raise ValueError(
                f"commit_author в конфиг.yaml должен иметь вид «Имя <email>», получено: «{v}» (Д-8)."
            )
        return v


def load_config(ws: Workspace) -> Config:
    path = ws.root / "конфиг.yaml"
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
    """Минимальный разбор .env (Д-9): KEY=VALUE, строки с # игнорируются."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_VOLUME_LINE_RE = re.compile(r"^volume\s*:.*$", re.MULTILINE)


def set_volume(ws: Workspace, volume: int) -> Path:
    """Переключает текущий том в конфиг.yaml (`konveyer volume open/close`), не трогая остальные строки
    и комментарии автора: правится (или дописывается) только строка `volume: N`."""
    if int(volume) < 1:
        raise ValueError(f"номер тома должен быть ≥ 1, получено: {volume}.")
    path = ws.root / "конфиг.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"volume: {int(volume)}"
    if _VOLUME_LINE_RE.search(text):
        text = _VOLUME_LINE_RE.sub(line, text, count=1)
    else:
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + f"# Текущий том рабочей области (`konveyer volume open N`).\n{line}\n"
    guard.write_text(path, text)
    return path
