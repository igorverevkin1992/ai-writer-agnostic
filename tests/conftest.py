import shutil
from importlib import resources
from pathlib import Path

import pytest

from konveyer import exporter, guard
from konveyer.paths import Workspace

DEMO = Path(str(resources.files("konveyer").joinpath("data/демо")))


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Тесты идут без ключей моделей (NFR-9): все роли — в ручном режиме, сеть не используется.
    Тест, которому нужен ключ, задаёт его сам через monkeypatch.setenv."""
    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """Рабочая область с демо-библиотекой и стартовым регрессионным корпусом."""
    shutil.copytree(DEMO / "Библиотека", tmp_path / "Библиотека")
    shutil.copyfile(DEMO / "проект.yaml", tmp_path / "проект.yaml")
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


@pytest.fixture
def passing_draft() -> str:
    """Текст, проходящий Э1 демо-проекта без брака (объём, длины фраз, лексика по нормам 02_Стиль_и_голос.md)."""
    return (Path(__file__).parent / "данные" / "черновик_э1.md").read_text(encoding="utf-8")
