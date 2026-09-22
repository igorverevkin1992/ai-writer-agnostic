"""Настройка: init — каркас рабочей области (NFR-1), `demo` — демо-библиотека и золотые тесты."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..paths import Workspace
from .common import colors, echo, secho


def init(demo: bool = False) -> Workspace:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, папки (NFR-1). Возвращает рабочую область."""
    ws = Workspace(Path.cwd())
    if not (ws.root / "конфиг.yaml").exists():
        shutil.copyfile(Path(__file__).parent.parent / "data" / "конфиг.пример.yaml", ws.root / "конфиг.yaml")
    for d in (ws.exports, ws.chapters, ws.logs, ws.templates, ws.regression / "золотые"):
        d.mkdir(parents=True, exist_ok=True)
    env_example = ws.root / ".env.example"
    if not env_example.exists():
        env_example.write_text("GEMINI_API_KEY=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    if demo:
        from importlib import resources

        demo_root = Path(str(resources.files("konveyer").joinpath("data/демо")))
        if not (ws.root / "Библиотека").exists():
            shutil.copytree(demo_root / "Библиотека", ws.root / "Библиотека")
        for f in (demo_root / "регрессия").glob("*.json"):
            target = ws.regression / "золотые" / f.name
            if not target.exists():
                shutil.copyfile(f, target)
        secho(
            "Демо развёрнуто. Попробуйте: `konveyer export` → `konveyer compile 1` → `konveyer status` → `konveyer regress`.",
            fg=colors.GREEN,
        )
        echo("Ключи API не обязательны: без них каждый шаг подскажет ручной режим (NFR-3).")
        return ws
    secho("Рабочая область готова. Заполните конфиг.yaml и .env (Д-9), положите Библиотека/.", fg=colors.GREEN)
    echo("Хотите пощупать конвейер на примере — `konveyer init --демо`. Диагностика: `konveyer doctor`.")
    return ws
