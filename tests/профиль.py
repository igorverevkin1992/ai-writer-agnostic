"""Плагин-парсер профиля эталона (УГАР) для тестов: загружается из данных движка, как это делает
`declparse.resolve_plugin` для проекта с папкой `типы/парсеры/`."""

from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from konveyer import declparse

PROFILE = Path(str(resources.files("konveyer").joinpath("data/профили/угар")))
LIBRARY_ENV = "KONVEYER_ЭТАЛОН"  # путь к библиотеке УГАР для миграционных тестов (FR-MG-*)


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
