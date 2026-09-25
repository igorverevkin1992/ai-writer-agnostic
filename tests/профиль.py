"""Профиль эталона (УГАР) для тестов: плагин-парсер загружается из данных движка, как это делает
`declparse.resolve_plugin` для проекта с папкой `типы/парсеры/`; библиотека эталона подключается только через
переменную окружения KONVEYER_ETALON (миграционные тесты FR-MG-1…4 в tests/профиль_угар)."""

import os
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from konveyer import declparse

PROFILE = Path(str(resources.files("konveyer").joinpath("data/профили/угар")))
LIBRARY_ENV = "KONVEYER_ETALON"  # путь к библиотеке эталона для миграционных тестов (FR-MG-*)


def etalon_path() -> Path | None:
    """Путь к библиотеке эталона из KONVEYER_ETALON; None — переменная не задана (тесты профиля пропускаются).
    Заданная, но несуществующая папка — ошибка конфигурации, а не повод для пропуска (14.3.8)."""
    raw = os.environ.get(LIBRARY_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir() or not any(path.glob("*.md")):
        raise RuntimeError(f"{LIBRARY_ENV}={raw}: папка библиотеки эталона не найдена или пуста")
    return path


def plugin(name: str = "угар"):
    return declparse._load_module_from(PROFILE / "типы" / "парсеры" / f"{name}.py", name)


realcanon = plugin()


@contextmanager
def markers():
    """Маркеры окна из типов профиля УГАР (компилятор без проекта знает только ссылки на тома)."""
    from konveyer import compiler

    compiler.configure_markers(PROFILE)
    try:
        yield
    finally:
        compiler.configure_markers(None)
