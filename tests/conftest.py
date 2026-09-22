import shutil
from importlib import resources
from pathlib import Path

import pytest

from konveyer import exporter, guard
from konveyer.paths import Workspace

DEMO = Path(str(resources.files("konveyer").joinpath("data/демо")))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """Рабочая область с демо-библиотекой и стартовым регрессионным корпусом."""
    shutil.copytree(DEMO / "Библиотека", tmp_path / "Библиотека")
    (tmp_path / "конфиг.yaml").write_text("library_dir: Библиотека\n", encoding="utf-8")
    golden = tmp_path / "регрессия" / "золотые"
    golden.mkdir(parents=True)
    for f in (DEMO / "регрессия").glob("*.json"):
        shutil.copyfile(f, golden / f.name)
    workspace = Workspace(tmp_path)
    guard.set_library_dir(tmp_path / "Библиотека")
    exporter.run_export(tmp_path / "Библиотека", workspace.exports, workspace.logs)
    return workspace


@pytest.fixture
def library(ws: Workspace) -> Path:
    return ws.root / "Библиотека"
