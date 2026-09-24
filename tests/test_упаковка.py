"""Упаковка и установка (NFR-3, NFR-12): колесо содержит подпакеты и данные движка, тяжёлые разборщики — в extras,
lock-файл покрывает extras разработки; ярлыки панели включают UTF-8 (NFR-2)."""

import re
import tomllib
from pathlib import Path

from setuptools import find_packages

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_подпакеты_входят_в_колесо():
    """`konveyer.steps`, `konveyer.onboarding` и любые новые подпакеты попадают в дистрибутив (find, не список)."""
    include = PYPROJECT["tool"]["setuptools"]["packages"]["find"]["include"]
    found = set(find_packages(where=str(ROOT), include=include))
    on_disk = {p.parent.relative_to(ROOT).as_posix().replace("/", ".") for p in (ROOT / "konveyer").rglob("__init__.py")}
    assert on_disk <= found, sorted(on_disk - found)
    assert {"konveyer.steps", "konveyer.onboarding"} <= found


def test_данные_движка_в_package_data():
    """Каждая папка данных пакета (типы, модули, методики, языки, шаблоны, data — панель, демо, профили) объявлена."""
    globs = PYPROJECT["tool"]["setuptools"]["package-data"]["konveyer"]
    data_dirs = sorted({p.relative_to(ROOT / "konveyer").parts[0] for p in (ROOT / "konveyer").rglob("*")
                        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts})
    for d in data_dirs:
        assert any(g.startswith(d + "/") for g in globs), f"папка данных «{d}» не объявлена в package-data"
    exclude = PYPROJECT["tool"]["setuptools"]["exclude-package-data"]["konveyer"]
    assert any("__pycache__" in g for g in exclude)


def test_extras_разборщиков_и_lock():
    """python-docx/openpyxl/pypdf — необязательные extras (NFR-12); `dev` их включает (тесты онбординга),
    requirements.lock пинует их и всё из `dev` и `llm`."""
    extras = PYPROJECT["project"]["optional-dependencies"]
    names = lambda reqs: {re.split(r"[<>=!~\[ ]", r, 1)[0].lower() for r in reqs}  # noqa: E731
    assert names(extras["onboarding"]) == {"python-docx", "openpyxl", "pypdf"}
    assert names(extras["onboarding"]) <= names(extras["dev"])
    lock = {ln.split("==")[0].strip().lower().replace("_", "-") for ln in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
            if "==" in ln and not ln.startswith("#")}
    for extra in ("onboarding", "dev", "llm"):
        assert names(extras[extra]) <= lock, (extra, names(extras[extra]) - lock)


def test_ярлыки_панели_включают_utf8_и_исполняемы():
    bat = (ROOT / "Запустить_панель.bat").read_text(encoding="utf-8")
    assert "chcp 65001" in bat and "PYTHONUTF8=1" in bat
    cmd = ROOT / "Запустить_панель.command"
    assert "PYTHONUTF8=1" in cmd.read_text(encoding="utf-8")
    mode = int(subprocess_mode(cmd), 8)
    assert mode & 0o111, "у .command должен стоять бит исполнения (git сохраняет его)"


def subprocess_mode(path: Path) -> str:
    import subprocess

    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", str(path.relative_to(ROOT))],
                         capture_output=True, text=True, encoding="utf-8").stdout.strip()
    return out.split()[0][-3:] if out else "755"
