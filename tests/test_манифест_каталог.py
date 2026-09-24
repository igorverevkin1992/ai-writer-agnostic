"""Аудит кластера «манифест и каталог типов»: флаги да/нет в спецификациях, ключ типа = поле «тип», ошибки YAML и схемы
по-русски со строкой, переключатели модулей, проверки манифеста (поля, тома, дубли, методики), миграция схемы,
маски и вложенные папки в карте, каркасы всех модулей проходят экспорт, чек-листы и замысел читаются целиком,
сигнатуры без ложных гипотез, окно без служебных порогов и номеров документов эталона, конфиг нового проекта."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import catalog, compiler, declparse, exporter, lang, lint, manifest as manifest_mod, metrics, project
from konveyer.cli import app
from konveyer.onboarding import classify
from konveyer.paths import Workspace, find_workspace
from konveyer.schemas import Brief, StopRule

runner = CliRunner()
TYPES = Path(catalog.__file__).parent / "типы"


def _create(tmp_path: Path, **kw) -> project.CreatedProject:
    kw.setdefault("git", False)
    return project.create(project.ProjectSpec(root=tmp_path / "серия", name="Серия", **kw))


def _export(created: project.CreatedProject, volume: int = 1) -> Workspace:
    ws = Workspace(created.root)
    exporter.run_export(created.library, ws.exports, ws.logs, volume, created.root)
    return ws


# ------------------------------------------------------------------ флаги «да/нет» (C1-1, A2-14)


def test_флаг_нет_читается_как_ложь():
    assert catalog.flag("нет") is False and catalog.flag("выкл") is False and catalog.flag(None) is False
    assert catalog.flag("да") is True and catalog.flag(True) is True and catalog.flag(1) is True
    with pytest.raises(ValueError, match="да/нет"):
        catalog.flag("может быть")
    # журнал решений объявлен «обязателен_для_такта: нет» — такт без него возможен (FR-LC-2)
    assert catalog.load_types(None)["журнал_решений"].required_for_tact is False


def test_экспорт_без_журнала_решений_проходит(tmp_path):
    created = _create(tmp_path)
    journal = next(e for e in manifest_mod.load(created.root).библиотека if e.тип == "журнал_решений")
    (created.library / journal.файл).unlink()
    man = manifest_mod.load(created.root)
    man.библиотека = [e for e in man.библиотека if e.тип != "журнал_решений"]
    manifest_mod.save(created.root, man)
    _export(created)


def test_заменить_нет_дополняет_тип_движка(tmp_path):
    (tmp_path / "типы").mkdir()
    (tmp_path / "типы" / "мир.yaml").write_text("заменить: нет\nназначение: свой мир\n", encoding="utf-8")
    spec = catalog.load_types(tmp_path)["мир"]
    assert spec.purpose == "свой мир" and spec.extractions and spec.source == "проект"


def test_обязательна_нет_не_требует_колонку():
    cols = {"а": {"обязательна": "нет"}, "б": {"обязательна": "да"}}
    assert declparse.match_columns(["б"], cols, {}) == {"б": "б"}
    assert declparse.match_columns(["а"], cols, {}) is None


# ------------------------------------------------------------------ каталог (A2-15, C1-29, A2-34, C1-16)


def test_тип_проекта_с_чужим_полем_тип_отклоняется(tmp_path):
    (tmp_path / "типы").mkdir()
    (tmp_path / "типы" / "мойтип.yaml").write_text("тип: другой\nизвлечения: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="мойтип.yaml.*тип: другой"):
        catalog.load_types(tmp_path)


def test_ошибка_yaml_типа_называет_файл_и_строку(tmp_path):
    (tmp_path / "типы").mkdir()
    (tmp_path / "типы" / "кривой.yaml").write_text("тип: кривой\nизвлечения: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"кривой\.yaml:\d+: не читается как YAML"):
        catalog.load_types(tmp_path)


def test_битый_манифест_ошибка_по_русски_без_трейсбека(tmp_path, monkeypatch):
    created = _create(tmp_path)
    manifest_mod.path_of(created.root).write_text("проект: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"проект\.yaml:\d+: не читается как YAML"):
        manifest_mod.load(created.root)
    monkeypatch.chdir(created.root)
    r = runner.invoke(app, ["export"])
    assert r.exit_code == 1 and "не читается как YAML" in r.output and "Traceback" not in r.output, r.output


# ------------------------------------------------------------------ манифест: схема (A2-11, C1-15, A2-12)


@pytest.mark.parametrize("value", ["on", "true", "1", "yes", "да", "вкл"])
def test_переключатель_модуля_принимает_булевы_значения(tmp_path, value):
    (tmp_path / "проект.yaml").write_text(f"модули:\n  фокализация: {value}\n", encoding="utf-8")
    man = manifest_mod.load(tmp_path)
    assert man.module_enabled("фокализация") and man.модули["фокализация"] == "вкл"


def test_переключатель_модуля_нет_и_мусор(tmp_path):
    (tmp_path / "проект.yaml").write_text("модули:\n  фокализация: нет\n  эпистемика: false\n", encoding="utf-8")
    man = manifest_mod.load(tmp_path)
    assert not man.module_enabled("фокализация") and not man.module_enabled("эпистемика")
    (tmp_path / "проект.yaml").write_text("модули:\n  фокализация: иногда\n", encoding="utf-8")
    with pytest.raises(ValueError, match="проект.yaml:2.*фокализация.*вкл/выкл"):
        manifest_mod.load(tmp_path)


def test_ошибки_схемы_по_русски_со_строкой_и_неизвестные_ключи(tmp_path):
    (tmp_path / "проект.yaml").write_text("проект:\n  имя: X\n  томов_план: два\nмодуль:\n  фокализация: вкл\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        manifest_mod.load(tmp_path)
    msg = str(e.value)
    assert "проект.yaml:3: проект.томов_план: ожидается целое число" in msg, msg
    assert "проект.yaml:4: модуль: неизвестный ключ" in msg, msg
    assert "Input should" not in msg
    (tmp_path / "проект.yaml").write_text("библиотека:\n  - файл: a.md\n    тип: стиль\n  - файл: b.md\n    тип: мир\n    тома: 2\n",
                                          encoding="utf-8")
    with pytest.raises(ValueError, match=r"проект\.yaml:6: библиотека\.1\.тома: неизвестный ключ"):
        manifest_mod.load(tmp_path)


def test_манифест_новее_движка_отклоняется(tmp_path):
    (tmp_path / "проект.yaml").write_text(f"версия_схемы: {manifest_mod.SCHEMA_VERSION + 6}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="новее, чем у движка"):
        manifest_mod.load(tmp_path)


# ------------------------------------------------------------------ манифест: проверка карты (A2-13, C1-35, A2-16)


def test_валидатор_ловит_поля_тома_дубли_и_методики(tmp_path):
    created = _create(tmp_path, volumes=2)
    lib = created.library
    (lib / "23_План_глав.md").write_text("# План\n", encoding="utf-8")
    (lib / "12_Хронология.md").write_text("# Хронология\n\n| id | дата | событие |\n|---|---|---|\n", encoding="utf-8")
    text = (
        "версия_схемы: 1\nпроект:\n  имя: X\n  томов_план: 2\nмодули: {}\nметодики:\n  том: несуществующая_методика\n"
        "библиотека:\n"
        "  - файл: 12_Хронология.md\n    тип: хронология\n    колонки: {несуществующее: Дата}\n"
        "  - файл: 12_Хронология.md\n    тип: мир\n"
        "  - файл: 23_План_глав_Том1.md\n    тип: план_глав\n    том: 9\n"
        "  - файл: 23_План_глав_Том2.md\n    тип: план_глав\n    том: 1\n"
        "  - файл: 23_План_глав.md\n    тип: план_глав\n"
    )
    manifest_mod.path_of(created.root).write_text(text, encoding="utf-8")
    man = manifest_mod.load(created.root)
    types, modules = catalog.load_types(created.root), catalog.load_modules(created.root)
    errors = manifest_mod.validate(man, lib, types, modules, text, methodics={"круг_хармона"})
    joined = "\n".join(errors)
    assert "проект.yaml:9:" in joined and "колонки: неизвестные поля типа «хронология»: несуществующее" in joined, joined
    assert "файл уже есть в карте" in joined
    assert "проект.yaml:14:" in joined and "том 9 больше плана" in joined, joined
    assert "«том: 1», а по имени файла — том 2" in joined
    assert "23_План_глав.md»: тип «план_глав» потомный, а том не задан" in joined
    assert "методика «несуществующая_методика» неизвестна" in joined
    # потомный документ без тома читается как том 1 (совместимость), не теряется молча
    assert [p.name for p in man.docs(lib, "план_глав", 1, types)] == ["23_План_глав.md", "23_План_глав_Том2.md"]
    assert man.docs(lib, "план_глав", 2, types) == []
    # те же ошибки записей карты видит экспорт — с файлом и строкой манифеста (FR-MF-2)
    with pytest.raises(exporter.ExportErrors) as ei:
        exporter.run_export(lib, created.root / "выгрузки", created.root / "журналы", 1, created.root)
    joined = "\n".join(str(e) for e in ei.value.errors)
    assert "проект.yaml:14: «23_План_глав_Том1.md»: том 9 больше плана" in joined and "файл уже есть в карте" in joined, joined


def test_доктор_показывает_ошибки_манифеста(tmp_path):
    created = _create(tmp_path)
    man = manifest_mod.load(created.root)
    man.методики.глава = "нет_такой"
    manifest_mod.save(created.root, man)
    labels = [c.label for c in project.readiness(created.root, created.library) if c.ok is False]
    assert any("методика «нет_такой» неизвестна" in lb for lb in labels), labels
    (created.library / "14_Мир.md").unlink()
    labels = [c.label for c in project.readiness(created.root, created.library) if c.ok is False]
    assert any("«14_Мир.md»: файла нет в библиотеке" in lb for lb in labels), labels


# ------------------------------------------------------------------ миграция (A2-19, D1-21)


def test_миграция_манифеста_с_копией_и_автоматически(tmp_path):
    created = _create(tmp_path)
    path = manifest_mod.path_of(created.root)
    old = "проект:\n  имя: Старая\n"
    path.write_text(old, encoding="utf-8")
    assert manifest_mod.schema_version(created.root) == 0
    done, note = manifest_mod.migrate(created.root)
    assert done and "поднят до версии 1" in note
    backups = list(created.root.glob("проект.v0.*.yaml"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == old
    man = manifest_mod.load(created.root)
    assert man.версия_схемы == manifest_mod.SCHEMA_VERSION and man.проект.имя == "Старая" and man.библиотека == []
    assert manifest_mod.migrate(created.root) == (False, "схема уже версии 1")
    # старый манифест мигрирует сам при первом чтении карты (effective) и доктор сообщает об этом
    path.write_text(old, encoding="utf-8")
    for b in created.root.glob("проект.v0.*.yaml"):
        b.unlink()
    manifest_mod.effective(created.root, created.library)
    assert manifest_mod.schema_version(created.root) == manifest_mod.SCHEMA_VERSION
    assert list(created.root.glob("проект.v0.*.yaml"))


# ------------------------------------------------------------------ карта: маски, вложенные папки, маркер тома (A2-21, A2-5, C1-38)


def test_маска_в_карте_сопоставляется_и_не_вне_карты(tmp_path):
    created = _create(tmp_path, modules=("эпистемика",))
    lib = created.library
    (lib / "31_Матрица_Том2.md").write_text((lib / "31_Эпистемика_Том1.md").read_text(encoding="utf-8"), encoding="utf-8")
    man = manifest_mod.load(created.root)
    man.библиотека = [e for e in man.библиотека if e.тип != "эпистемика"] + [
        manifest_mod.LibraryEntry(файл="31_*.md", тип="эпистемика", колонки={"факт": "Факт"})]
    assert man.entry_for("31_Эпистемика_Том1.md").колонки == {"факт": "Факт"}
    assert man.entry_for("31_Матрица_Том2.md") is not None and man.entry_for("32_Другое.md") is None
    assert manifest_mod.unmapped(man, lib) == []
    assert [p.name for p in man.docs(lib, "эпистемика", 2, catalog.load_types(created.root))] == ["31_Матрица_Том2.md"]


def test_вложенная_папка_не_теряется_молча(tmp_path):
    created = _create(tmp_path)
    nested = created.library / "Досье" / "Второстепенные"
    nested.mkdir()
    (nested / "Сторож.md").write_text("# Сторож\n\n## Профиль\n\nСторож гаражей.\n\n## Физика\n\nСед.\n\n## Речевой паспорт\n\nКраток.\n",
                                     encoding="utf-8")
    man = manifest_mod.load(created.root)
    assert man.entry_for("Досье/Второстепенные/Сторож.md") is None
    assert manifest_mod.unmapped(man, created.library) == ["Досье/Второстепенные/Сторож.md"]
    labels = [c.label for c in project.readiness(created.root, created.library)]
    assert any("вне карты" in lb and "Сторож" in lb for lb in labels), labels
    inferred = manifest_mod.infer(created.library, catalog.load_types(created.root))
    assert any(e.файл == "Досье/Второстепенные/" and e.тип == "персонажи" for e in inferred.библиотека)


@pytest.mark.parametrize("name,vol", [("02_Стиль_Том_2.md", 2), ("23_Поглавник_том2.md", 2), ("23_Поглавник_T2.md", 2),
                                      ("23_Поглавник_Т02.md", 2), ("31_Матрица_Том 3.md", 3), ("Фантом_2.md", None),
                                      ("Досье_Тома.md", None), ("02_Стиль.md", None)])
def test_маркер_тома_в_имени(name, vol):
    assert manifest_mod.doc_volume(Path(name)) == vol


def test_потомный_документ_без_тома_том_1_и_доктор_просит_том(tmp_path):
    created = _create(tmp_path, volumes=2, modules=("закладки",))
    lib = created.library
    (lib / "32_Закладки_Том2.md").unlink()
    (lib / "32_Закладки_Том1.md").rename(lib / "32_Закладки.md")
    man = manifest_mod.load(created.root)
    man.библиотека = [e for e in man.библиотека if e.тип != "закладки"] + [manifest_mod.LibraryEntry(файл="32_Закладки.md", тип="закладки")]
    manifest_mod.save(created.root, man)
    types = catalog.load_types(created.root)
    # потомный документ без «том:» и маркера читается как том 1; общесерийный (стиль) — в каждом томе (FR-EX-4)
    assert [p.name for p in man.docs(lib, "закладки", 1, types)] == ["32_Закладки.md"]
    assert man.docs(lib, "закладки", 2, types) == []
    assert [p.name for p in man.docs(lib, "стиль", 2, types)] == ["02_Стиль.md"]
    errors = manifest_mod.validate(man, lib, types, catalog.load_modules(created.root))
    assert any("«32_Закладки.md»: тип «закладки» потомный, а том не задан" in e for e in errors), errors
    # экспорт этим не блокируется (П-5): документ тома 1 разбирается
    _export(created)


def test_собственный_документ_движка_вне_карты(tmp_path):
    from konveyer.canonist import INBOX_DOC

    created = _create(tmp_path)
    (created.library / INBOX_DOC).write_text("# Входящие\n\n## Глава 1\n- факт\n", encoding="utf-8")
    man = manifest_mod.load(created.root)
    assert manifest_mod.unmapped(man, created.library) == []
    assert not any(e.файл == INBOX_DOC for e in manifest_mod.infer(created.library, catalog.load_types(created.root)).библиотека)


def test_служебные_маски_из_манифеста_и_профиля(tmp_path):
    created = _create(tmp_path, profile="угар", starter=False)
    man = manifest_mod.load(created.root)
    assert "ИНСТРУМЕНТ_*" in man.служебные
    (created.library / "ИНСТРУМЕНТ_Заметка.md").write_text("# Заметка\n", encoding="utf-8")
    (created.library / "Тест_Писателя").mkdir()
    (created.library / "Тест_Писателя" / "Промпт.md").write_text("# Промпт\n", encoding="utf-8")
    (created.library / "Свой.md").write_text("# Свой\n", encoding="utf-8")
    assert manifest_mod.unmapped(man, created.library) == ["Свой.md"]
    inferred = manifest_mod.infer(created.library, catalog.load_types(created.root), exclude=man.служебные)
    assert not any(e.файл.startswith(("ИНСТРУМЕНТ_", "Тест_")) for e in inferred.библиотека)
    # движок соглашений именования не знает (П-1)
    src = Path(manifest_mod.__file__).read_text(encoding="utf-8") + Path(project.__file__).read_text(encoding="utf-8")
    assert "ИНСТРУМЕНТ_" not in src and "Журнал_решений.md" not in src


# ------------------------------------------------------------------ стартовый комплект (A2-1, A2-31, A2-4, A2-18, A2-30)


def _optional_modules() -> list[str]:
    return sorted(m for m, s in catalog.load_modules(None).items() if not s.base)


@pytest.mark.parametrize("module", _optional_modules())
def test_каркас_каждого_модуля_проходит_экспорт(tmp_path, module):
    created = _create(tmp_path, modules=(module,), volumes=2)
    ws = _export(created)
    for p in ws.exports.glob("*.json"):
        assert "заполнить" not in p.read_text(encoding="utf-8"), p.name
    report = lint.run_lint(created.library, ws.exports, ws.logs, root=created.root)
    assert report.errors == 0 and report.warnings == 0, [f.message for f in report.findings]


def test_все_модули_разом_и_разбор_каркасов_своими_типами(tmp_path):
    created = _create(tmp_path, modules=tuple(_optional_modules()), volumes=2)
    _export(created)
    types = catalog.load_types(created.root)
    for spec in types.values():
        if not spec.extractions or spec.multiplicity == "папка":
            continue
        doc = tmp_path / f"{spec.name}.md"
        doc.write_text(project.skeleton_for(spec, 1), encoding="utf-8")
        for ext in spec.extractions:
            records, _ = declparse.parse_document(doc, list(ext.get("форматы") or []), declparse.ParseContext())
            assert records is not None or catalog.flag(ext.get("необязательно")), (spec.name, ext["имя"])


def test_заглушки_каркаса_не_попадают_в_выгрузки(tmp_path):
    assert declparse.strip_placeholders("- Одна глава — одна голова.\n- ⚠ заполнить\n⚠ заполнить (см. выше)\n") == "- Одна глава — одна голова."
    created = _create(tmp_path, modules=("фокализация",))
    ws = _export(created)
    narration = exporter.load_narration(ws.exports)
    assert narration and "заполнить" not in narration[0].laws and "одна голова" in narration[0].laws
    # карточка-каркас («# Имя», все секции «⚠ заполнить») записи не даёт: заглушек в выгрузке нет, «Имя» — не персонаж
    assert exporter.load_dossiers(ws.exports) == []
    assert "заполнить" not in (ws.exports / "dossiers.json").read_text(encoding="utf-8")
    # .gitignore рабочей области исключает только производное: журналы такта и сырьё версионируются (П-7)
    ignored = (created.root / ".gitignore").read_text(encoding="utf-8").split()
    assert "журналы/" not in ignored and "сырьё/" not in ignored and "выгрузки/" in ignored


def test_выгрузки_другой_версии_схемы_пересобираются(tmp_path):
    import json

    created = _create(tmp_path)
    ws = _export(created)
    idx = ws.exports / exporter.INDEX
    data = json.loads(idx.read_text(encoding="utf-8"))
    data["версия_схемы"] = exporter.SCHEMA_VERSION + 1
    idx.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="схемой версии"):
        exporter.load_stoplists(ws.exports)
    assert exporter.load_manifest(ws.exports) == {}
    _export(created)
    assert exporter.exports_schema_version(ws.exports) == exporter.SCHEMA_VERSION and exporter.load_stoplists(ws.exports) == []


def test_табличный_план_глав_даёт_брифы(tmp_path):
    created = _create(tmp_path)
    (created.library / "23_План_глав_Том1.md").write_text(
        "# 23. План глав · Том 1\n\n| гл | фокал | дата | событие | читатель |\n|---|---|---|---|---|\n"
        "| 1 | Анна | 1 мая | Анна приезжает; находит ключ | ключ у Анны |\n", encoding="utf-8")
    ws = _export(created)
    briefs = exporter.load_briefs(ws.exports)
    assert len(briefs) == 1 and briefs[0].beats == ["Анна приезжает", "находит ключ"] and briefs[0].focal == "Анна"
    assert Brief(chapter=1, beats="один бит").beats == ["один бит"]


def test_индекс_библиотеки_создаётся_и_пересобирается(tmp_path, monkeypatch):
    created = _create(tmp_path)
    man = manifest_mod.load(created.root)
    idx = next(e for e in man.библиотека if e.тип == "индекс_библиотеки")
    text = (created.library / idx.файл).read_text(encoding="utf-8")
    assert "02_Стиль.md" in text and "стиль" in text and "правится только через манифест" in text
    assert project.index_outdated(created.root, created.library) is None
    (created.library / "16_Топография.md").write_text("# 16. Топография\n\n| место | описание |\n|---|---|\n", encoding="utf-8")
    man.библиотека.append(manifest_mod.LibraryEntry(файл="16_Топография.md", тип="топография"))
    manifest_mod.save(created.root, man)
    assert project.index_outdated(created.root, created.library) is not None
    assert any("индекс библиотеки не совпадает" in c.label for c in project.readiness(created.root, created.library))
    monkeypatch.chdir(created.root)
    r = runner.invoke(app, ["проект", "индекс", "-y", "--без-коммита"])
    assert r.exit_code == 0 and "пересобран" in r.output, r.output
    assert "16_Топография.md" in (created.library / idx.файл).read_text(encoding="utf-8")
    r = runner.invoke(app, ["проект", "индекс", "-y", "--без-коммита"])
    assert r.exit_code == 0 and "актуален" in r.output, r.output


def test_журнал_решений_по_каркасу_типа(tmp_path):
    created = _create(tmp_path)
    man = manifest_mod.load(created.root)
    journal = next(e for e in man.библиотека if e.тип == "журнал_решений")
    text = (created.library / journal.файл).read_text(encoding="utf-8")
    spec = catalog.load_types(created.root)["журнал_решений"]
    assert text.splitlines()[0] == spec.skeleton.splitlines()[0]
    assert "FR-SC-10" in text and "⚠ заполнить" not in text
    ws = _export(created)
    decisions = exporter.load_decisions(ws.exports)
    assert len(decisions) == 1 and decisions[0].date


def test_готовность_смотрит_на_текущий_том(tmp_path):
    created = _create(tmp_path, volumes=2)
    plan = "# 23. План глав · Том {v}\n\n## Глава 1 — Начало\n\n- Дата: 1 мая\n- Фокал: Анна\n- Объём: 3000\n- Биты:\n  - Анна приезжает\n"
    (created.library / "23_План_глав_Том1.md").write_text(plan.format(v=1), encoding="utf-8")
    (created.library / "23_План_глав_Том2.md").write_text(plan.format(v=2).replace("- Объём: 3000\n", ""), encoding="utf-8")
    assert any(c.ok and "в плане глав" in c.label for c in project.readiness(created.root, created.library))
    manifest_mod.set_volume(created.root, 2)
    checks = project.readiness(created.root, created.library)
    assert any("объём главы не задан" in c.label for c in checks), [c.label for c in checks]


# ------------------------------------------------------------------ секции целиком (C1-2) и знание (C1-34)


def test_чек_листы_и_замысел_демо_читаются(ws, library):
    exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    col = exporter.collect(library, 1, ws.root)
    checklists = col.data["checklists.json"]
    # секции чек-листа — отдельные записи (привязка к модулям по пометке в заголовке), заголовок 1-го уровня пуст
    assert checklists and any("Фокал главы совпадает с планом" in c.text for c in checklists)
    assert any(c.text.strip() and c.module == "эпистемика" for c in checklists)
    method = col.data["method.json"]
    assert method and "цена молчания" in method[0].theme and method[0].principles


def test_правила_ячейки_знания_из_yaml():
    rules = {"всегда|с начала": 0, "—|нет|пусто": None, "ch.N[ / source]": "N", "_курсив_": "частичное_знание"}
    compiled = declparse.compile_cell_rules(rules)
    assert declparse._knowledge_cell("ch. 7 / letter", compiled) == (7, "")
    assert declparse._knowledge_cell("пусто", compiled) == (None, "")
    assert declparse._knowledge_cell("с начала", compiled) == (0, "")
    assert declparse.partial_marker(rules) == "_"


# ------------------------------------------------------------------ коды линтера типов (B1-15, D2-7)


def test_коды_типов_действуют_по_документу_но_явное_выкл_побеждает():
    modules, types = catalog.load_modules(None), catalog.load_types(None)
    assert "АКТ-1" not in catalog.enabled_lint_codes(modules, set(), types, set())
    assert "АКТ-1" in catalog.enabled_lint_codes(modules, set(), types, {"акты"})
    assert "АКТ-1" not in catalog.enabled_lint_codes(modules, set(), types, {"акты"}, disabled={"драматургия"})
    assert manifest_mod.Manifest(модули={"драматургия": "выкл", "арки": "вкл"}).disabled_modules() == {"драматургия"}
    assert "ЧАСТЬ-1" not in catalog.all_lint_codes(modules, types)


# ------------------------------------------------------------------ окно: без порогов верификатора и номеров эталона (D2-1, D2-13)


def test_окно_без_служебных_порогов_и_номеров_документов(ws, library):
    exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    path, _ = compiler.compile_window(ws, library, 1)
    text = path.read_text(encoding="utf-8")
    for token in ("утечка_нграмма", "повтор_нграмма", "ttr_окно_слов", "объём_допуск", "(0.3)", "(0.4)"):
        assert token not in text, token
    assert "все линии" in text or "лексика эпохи" in text
    assert all(r.scope in ("линии", "эпоха") for r in exporter.load_stoplists(ws.exports))


# ------------------------------------------------------------------ ограничение стоп-правил томом и главой (D2-26)


def test_стоп_правило_ограничено_томом_и_главой(tmp_path):
    rule = StopRule(rule_id="Л-4", items=["папа"], applies_to={"focal": "Зоя", "volume": 1, "until_chapter": 2})
    assert metrics.stoplist_applies(rule, Brief(chapter=2, volume=1, focal="Зоя"))
    assert not metrics.stoplist_applies(rule, Brief(chapter=3, volume=1, focal="Зоя"))
    assert not metrics.stoplist_applies(rule, Brief(chapter=1, volume=2, focal="Зоя"))
    assert compiler._line_rules([rule], ["Зоя"], None, Brief(chapter=3, volume=1, focal="Зоя")) == []
    doc = tmp_path / "03_Повествование.md"
    doc.write_text("# 03\n\n## Стоп-листы\n\n| rule_id | фокал | слова | действие | том | до главы |\n|---|---|---|---|---|---|\n"
                   "| Л-4 | Зоя | папа; отец | запрет | 1 | 2 |\n| Л-5 | все | ясно | флаг | — | — |\n", encoding="utf-8")
    ext = next(e for e in catalog.load_types(None)["повествование"].extractions if e["имя"] == "стоп_листы_линий")
    records, _ = declparse.parse_document(doc, ext["форматы"], declparse.ParseContext())
    # том — диапазон {from, to} (колонка «тома»/«том»: «1», «1–2», «с 3»), как у года; «до главы» — включительно
    assert records[0]["applies_to"] == {"focal": "Зоя", "volume": {"from": 1, "to": 1}, "until_chapter": 2}
    assert records[0]["scope"] == "линии"
    assert records[1]["applies_to"] == {"all": True}


# ------------------------------------------------------------------ сигнатуры (A1-22, D2-14, A1-32, D2-15)


def test_сигнатуры_без_ложных_гипотез(tmp_path):
    types = catalog.load_types(None)

    def best(name: str, text: str) -> dict[str, float]:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return {h.type: h.confidence for h in classify.classify_file(p, types)}

    plain = best("план_2024.md", "# Заметки\n\nПросто текст без таблиц и дат в начале строк.\n")
    assert plain.get("хроника_эпохи", 0) < 0.35
    demo = Path(project.resources.files("konveyer").joinpath("data/демо/Библиотека/20_План_томов.md"))
    assert {h.type: h.confidence for h in classify.classify_file(demo, types)}.get("хроника_эпохи", 0) < 0.35
    chron = best("события.md", "# События\n\n| id | дата | событие | участники |\n|---|---|---|---|\n"
                 "| 1 | 12.06.1995 | пропал сторож | Гуляев |\n| 2 | 1995, июль | нашли ключ | Зоя |\n")
    assert max(chron, key=chron.get) == "хронология"
    frame = best("21_Каркасы_Том1.md", "# 21. Каркасы · Том 1\n\nМетодика: трёхактная\n\n## Арка тома\n\n- Шаг 1: завязка\n")
    assert frame.get("методика", 0) < 0.35


# ------------------------------------------------------------------ окно типов, роли, конфиг, рабочая область (A2-27, A2-36, A2-29, B3-26)


def test_типы_питающие_окно_объявляют_что_показывать():
    for spec in catalog.load_types(None).values():
        if "окно" in spec.feeds:
            assert spec.window.get("показывать") is not None or spec.window.get("запрещено"), spec.name


def test_роли_безымянных_и_не_имена_в_языковом_слое():
    assert "роли_безымянных" not in (TYPES / "персонажи.yaml").read_text(encoding="utf-8")
    roles = lang.get().unnamed_roles
    assert "сторож" in roles and "врач" in roles
    text = "| Линия | Тома |\n|---|---|\n| Анна | 1–2 |\n| Борис — никогда не фокален | — |\n"
    assert exporter._focal_names(text, lang.get().not_names) == ["Анна"]


def test_конфиг_нового_проекта_по_тз_v1(tmp_path):
    from konveyer.config import load_config

    created = _create(tmp_path)
    text = (created.root / "конфиг.yaml").read_text(encoding="utf-8")
    assert not re.search(r"Р-\d|FR-E\d|NFR-\d|п\. \d|§\s*[56]\.\d|Д-6|Д-1[12]|R-6", text), text
    for key in ("писатель:", "верификатор2:", "канонист:", "аналитик", "архивариус", "ручной", "режим_без_обучения",
                "пороги:", "пауза_автора_мин", "лимит_окна", "текущий_том"):
        assert key in text, key
    cfg = load_config(Workspace(created.root))
    roles = cfg.roles()
    assert roles["писатель"].provider == "gemini" and roles["канонист"].provider == "anthropic"
    assert roles["аналитик"].model == roles["канонист"].model and roles["писатель"].no_training
    assert "цена_вход_1м" in text and roles["писатель"].price_in_per_1m == 0.0
    from konveyer.config import ModelConfig

    assert ModelConfig(provider="ручной", model="x", цена_вход_1м=1.5, цена_выход_1м=3).price_out_per_1m == 3.0
    assert cfg.window_soft_limit_chars == 80000 and cfg.volume == 1


def test_команды_не_работают_вне_рабочей_области(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="рабочая область не найдена"):
        find_workspace(tmp_path)
    r = runner.invoke(app, ["статус"])
    assert r.exit_code == 1 and "рабочая область не найдена" in r.output and "Traceback" not in r.output, r.output
