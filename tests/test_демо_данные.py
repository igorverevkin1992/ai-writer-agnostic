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


def test_стоп_лист_линии_не_подсказывает_тайну(ws, library):
    """FR-WN-4/FR-C3: правило «Зоя: не употреблять — отец; папа» совпадает с маркером тайны B-001 («отец Зои»);
    фокалу, который тайны не знает (гл. 1, Каширин), оно не показывается, знающему (гл. 4, Зоя) — показывается."""
    from konveyer import compiler

    hidden = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    assert "[Л-4]" not in hidden and "отец" not in hidden.lower() and "папа" not in hidden.lower()
    assert "[Л-2]" in hidden  # остальные правила линии Зои — как были
    shown = compiler.compile_window(ws, library, 4)[0].read_text(encoding="utf-8")
    assert "[Л-4] линия «Зоя»: не употреблять — отец; папа" in shown


def test_противоречия_демо_объявлены_модулями_и_покрывают_все_документы():
    """NFR-9: каждый учебный код — объявленный код линтера; тайны, закладки, каркасы, проза, индекс — все классы."""
    from konveyer.steps import setup

    items = setup.demo_contradictions()
    declared = catalog.all_lint_codes(catalog.load_modules(DEMO))
    assert items and all(c["код"] in declared for c in items), sorted({c["код"] for c in items} - declared)
    assert {c["код"].split("-")[0] for c in items} >= {"ХРОН", "МАТР", "ТАЙНА", "ЗАКЛ", "КОНТ", "ФОКАЛ", "ДОСЬЕ", "ПОГЛ", "КРУГ", "ПРОЗА", "КАНОН"}


def test_init_демо_с_противоречиями(tmp_path, monkeypatch):
    """`konveyer init --демо --противоречия`: библиотека, манифест и учебные противоречия; линтер их находит."""
    from typer.testing import CliRunner

    from konveyer import exporter, lint
    from konveyer.cli import app
    from konveyer.paths import Workspace

    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["init", "--демо", "--противоречия"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "проект.yaml").exists() and (tmp_path / "Библиотека" / "21_Каркасы_Том1.md").exists()
    assert "внесено противоречий" in r.output
    ws = Workspace(tmp_path)
    exporter.run_export(tmp_path / "Библиотека", ws.exports, ws.logs)
    report = lint.run_lint(tmp_path / "Библиотека", ws.exports, ws.logs, use_cache=False)
    assert report.errors and report.warnings and report.notes
