"""Тесты профиля эталона (УГАР) — приёмочные тесты универсальности на живой библиотеке (14.2 ТЗ, FR-MG-1…4).

Библиотека подключается переменной окружения KONVEYER_ETALON=/путь/к/ugar-library (в CI — клон репозитория
эталона). Без переменной тесты пропускаются с пометкой; заданный, но отсутствующий путь — ошибка, не пропуск
(критерий приёмки 14.3.8: профиль эталона проверяется в каждом прогоне CI)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from konveyer import catalog, exporter, guard, manifest as manifest_mod, project
from konveyer.paths import Workspace
from tests.профиль import LIBRARY_ENV, etalon_path

# модули и методика, с которыми эталон вёлся в конвейере (у самой библиотеки манифеста нет — он выводится онбордингом)
ETALON_MODULES = ("фокализация", "информрежим", "эпистемика", "закладки", "континуити", "дозы_прошлого",
                  "документы_вставки", "хроника_эпохи", "драматургия", "арки")
ETALON_METHODIC = "круг_хармона"
ETALON_VOLUMES = 11


@pytest.fixture(scope="session")
def etalon() -> Path:
    """Папка библиотеки эталона (только чтение)."""
    try:
        path = etalon_path()
    except RuntimeError as e:
        pytest.fail(str(e))
    if path is None:
        pytest.skip(f"библиотека эталона не подключена (задайте {LIBRARY_ENV})")
    return path


def _make_project(root: Path, etalon: Path) -> tuple[Workspace, Path, manifest_mod.Manifest]:
    """Проект из профиля «угар» с копией библиотеки эталона: карта — выведена классификацией (онбординг без исключений)."""
    created = project.create(project.ProjectSpec(root=root, name="УГАР", profile="угар", starter=False, git=False,
                                                 volumes=ETALON_VOLUMES))
    shutil.copytree(etalon, created.library, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    types = catalog.load_types(created.root)
    man = manifest_mod.infer(created.library, types)
    man.проект.имя, man.проект.томов_план = "УГАР", ETALON_VOLUMES
    for m in ETALON_MODULES:
        man.модули[m] = "вкл"
    man.методики.том = man.методики.акт = man.методики.глава = ETALON_METHODIC
    manifest_mod.save(created.root, man)
    ws = Workspace(created.root)
    guard.set_library_dir(created.library)
    exporter.run_export(created.library, ws.exports, ws.logs, 1, created.root)
    return ws, created.library, man


@pytest.fixture(scope="session")
def ugar(tmp_path_factory, etalon: Path) -> tuple[Workspace, Path, manifest_mod.Manifest]:
    """Общий проект эталона на сессию: библиотеку не менять (для мутаций — `ugar_copy`)."""
    return _make_project(tmp_path_factory.mktemp("угар") / "проект", etalon)


@pytest.fixture
def ugar_copy(tmp_path: Path, ugar) -> tuple[Workspace, Path, manifest_mod.Manifest]:
    """Свежая копия проекта эталона (с выгрузками) для тестов, которые правят библиотеку или рабочую область."""
    src_ws, _src_lib, man = ugar
    root = tmp_path / "проект"
    shutil.copytree(src_ws.root, root)
    ws = Workspace(root)
    lib = root / "Библиотека"
    guard.set_library_dir(lib)
    return ws, lib, man


@pytest.fixture(autouse=True)
def _guard_on_shared_library(request):
    """Ограждение записи указывает на библиотеку общего проекта (другие тесты сессии переставляют его на демо)."""
    if "ugar" in request.fixturenames and "ugar_copy" not in request.fixturenames:
        _ws, lib, _man = request.getfixturevalue("ugar")
        guard.set_library_dir(lib)
    yield
