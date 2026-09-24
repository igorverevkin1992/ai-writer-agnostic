"""Демо-проект «Гаражи» как данные (NFR-9, FR-DM-3): индекс библиотеки равен генерации из манифеста, каждый файл
библиотеки описан манифестом, в документах нет нумерации реестров чужой серии, журнал решений без разрывов."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from konveyer import catalog, manifest as manifest_mod
from konveyer.onboarding.apply import render_index

DEMO = Path(str(resources.files("konveyer").joinpath("data/демо")))
LIBRARY = DEMO / "Библиотека"


def _man() -> manifest_mod.Manifest:
    return manifest_mod.load(DEMO)


def test_индекс_демо_равен_генерации_из_манифеста():
    """FR-DM-3: индекс «генерируется из манифеста» — и в демо он действительно равен генерации."""
    man = _man()
    idx = next(e for e in man.библиотека if e.тип == "индекс_библиотеки")
    assert (LIBRARY / idx.файл).read_text(encoding="utf-8") == render_index(man, catalog.load_types(DEMO))


def test_каждый_файл_демо_описан_манифестом():
    man = _man()
    for path in sorted(LIBRARY.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(LIBRARY).as_posix()
        covered = any(rel == e.файл or (e.is_folder and rel.startswith(e.файл.rstrip("/") + "/")) for e in man.библиотека)
        assert covered, f"{rel} лежит в библиотеке демо, но не описан в проект.yaml"
    for e in man.библиотека:
        assert (LIBRARY / e.файл.rstrip("/")).exists(), f"манифест ссылается на отсутствующий {e.файл}"


def test_в_демо_нет_нумерации_реестров_эталона():
    """П-1: демо — самостоятельная серия, номера реестров эталонной серии («реестр 3.1», «журнал 3.6») ей чужие."""
    bad = [f"{p.name}:{i}" for p in sorted(LIBRARY.rglob("*.md"))
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
           if re.search(r"\(?\bреестр\s+\d\.\d|\(\d\.\d\)|журнал\s+\d\.\d", line)]
    assert not bad, bad


def test_журнал_демо_без_разрывов_нумерации():
    man = _man()
    journal = next(e for e in man.библиотека if e.тип == "журнал_решений")
    ids = [int(m) for m in re.findall(r"^## Р-(\d+)", (LIBRARY / journal.файл).read_text(encoding="utf-8"), flags=re.M)]
    assert ids == list(range(1, len(ids) + 1)), ids
