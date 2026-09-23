"""Создание проекта (этап 1, FR-LC-*): `konveyer проект создать` — папки §6.1, манифест, стартовый комплект,
git библиотеки; экспорт и линтер молчат на пустом комплекте; `доктор` называет недостающее (FR-LC-2, FR-ON-20)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import catalog, exporter, lint, manifest as manifest_mod, project
from konveyer.cli import app
from konveyer.paths import Workspace

runner = CliRunner()


def _create(tmp_path: Path, **kw) -> project.CreatedProject:
    spec = project.ProjectSpec(root=tmp_path / "серия", name="Серия", **kw)
    return project.create(spec)


def test_создание_проекта_папки_манифест_комплект(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "т")
    created = _create(tmp_path, volumes=2, modules=("фокализация", "эпистемика", "драматургия"), author="Тест <t@t>")
    root = created.root
    for d in project.PROJECT_DIRS:
        assert (root / d).is_dir(), d
    assert (root / "конфиг.yaml").exists() and (root / "проект.yaml").exists() and (root / ".env.example").exists()
    man = manifest_mod.load(root)
    assert man.проект.имя == "Серия" and man.проект.томов_план == 2 and man.проект.текущий_том == 1
    assert man.module_enabled("драматургия") and not man.module_enabled("закладки")
    assert man.методики.for_level("глава") == ["круг_хармона"]
    names = sorted(p.relative_to(created.library).as_posix() for p in created.documents)
    assert "02_Стиль.md" in names and "23_План_глав_Том1.md" in names and "23_План_глав_Том2.md" in names
    assert "31_Эпистемика_Том1.md" in names and "21_Каркасы_Том1.md" in names and "Досье/Имя_персонажа.md" in names
    assert "32_Закладки_Том1.md" not in names  # модуль выключен — документа нет
    # манифест валиден, карта покрывает все документы
    types = catalog.load_types(root)
    assert manifest_mod.validate(man, created.library, types, catalog.load_modules(root)) == []
    assert manifest_mod.unmapped(man, created.library) == []
    # git внутри библиотеки с первым коммитом
    assert (created.library / ".git").is_dir() and created.commit


def test_пустой_комплект_проходит_экспорт_и_линтер(tmp_path):
    created = _create(tmp_path, git=False)
    ws = Workspace(created.root)
    exporter.run_export(created.library, ws.exports, ws.logs, 1, created.root)
    assert exporter.load_briefs(ws.exports) == [] and exporter.load_norms(ws.exports) == {}
    report = lint.run_lint(created.library, ws.exports, ws.logs, root=created.root)
    assert report.errors == 0 and report.warnings == 0, [f.message for f in report.findings]


def test_готовность_называет_недостающее(tmp_path):
    created = _create(tmp_path, git=False, modules=("эпистемика",))
    checks = project.readiness(created.root, created.library)
    labels = [c.label for c in checks]
    assert any("объём главы не задан" in lb for lb in labels)
    assert any("каркас не заполнен" in lb for lb in labels)
    assert not project.ready_for_tact(checks)
    # заполнили норму и главу — комплект готов
    style = created.library / "02_Стиль.md"
    style.write_text(style.read_text(encoding="utf-8").replace(
        "| объём_главы | объём главы | — | — | — | слов |", "| объём_главы | объём главы | 2000 | 4000 | — | слов |"), encoding="utf-8")
    plan = created.library / "23_План_глав_Том1.md"
    plan.write_text("# 23. План глав · Том 1\n\n## Глава 1 — Начало\n\n- Дата: 1 мая\n- Фокал: Анна\n- Биты:\n  - Анна приезжает\n",
                    encoding="utf-8")
    (created.library / "Досье" / "Имя_персонажа.md").rename(created.library / "Досье" / "Анна.md")
    (created.library / "Досье" / "Анна.md").write_text("# Анна\n\n## Профиль\n\nПриезжая.\n", encoding="utf-8")
    checks = project.readiness(created.root, created.library)
    assert project.ready_for_tact(checks), [c.label for c in checks]


def test_демо_готово_к_такту():
    demo = Path(str(project.resources.files("konveyer").joinpath("data/демо")))
    checks = project.readiness(demo, demo / "Библиотека")
    assert project.ready_for_tact(checks), [c.label for c in checks]


def test_профиль_неизвестен_и_папка_не_пуста(tmp_path):
    with pytest.raises(FileNotFoundError, match="профиль"):
        _create(tmp_path, profile="нет_такого", git=False)
    (tmp_path / "занято").mkdir()
    (tmp_path / "занято" / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        project.create(project.ProjectSpec(root=tmp_path / "занято", name="x", git=False))


def test_профиль_копирует_типы(tmp_path):
    created = _create(tmp_path, profile="угар", git=False)
    assert (created.root / "типы" / "маркеры_угар.yaml").exists()
    assert (created.root / "типы" / "парсеры" / "угар.py").exists()
    assert manifest_mod.load(created.root).проект.профиль == "угар"


def test_cli_проект_создать_и_доктор(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = runner.invoke(app, ["проект", "создать", "моя", "--имя", "Моя серия", "--томов", "1", "--без-git", "-y"])
    assert r.exit_code == 0 and "Проект «Моя серия» создан" in r.output, r.output
    monkeypatch.chdir(tmp_path / "моя")
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "Готовность к такту" in r.output and "объём главы не задан" in r.output and "каркас не заполнен" in r.output
    r = runner.invoke(app, ["проект", "создать", str(tmp_path / "моя"), "-y"])
    assert r.exit_code == 1 and "не пуста" in r.output, r.output
