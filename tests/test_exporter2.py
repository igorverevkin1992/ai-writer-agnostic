"""Экспорт (FR-EX-1…FR-EX-5, FR-DT-4, П-5, П-6): все ошибки разом с файлом и строкой, выгрузки типов проекта,
не-UTF-8, документы выключенных модулей, отпечаток конфигурации, лишние поля схемы, акты, заглушки каркасов,
заголовок выгрузок с датой, детерминизм корпуса, известные имена и нормализация фокала."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from konveyer import exporter, lint, metrics, project
from konveyer.mdparse import MarkupError
from konveyer.paths import Workspace


def _append(path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_все_ошибки_разом_с_файлом_и_строкой(ws, library):
    matrix = library / "31_Матрица_знаний.md"
    plants = library / "32_Реестр_закладок.md"
    m_line = len(matrix.read_text(encoding="utf-8").splitlines()) + 1
    p_line = len(plants.read_text(encoding="utf-8").splitlines()) + 1
    _append(matrix, "| x |\n")
    _append(plants, "| y |\n")
    with pytest.raises(exporter.ExportErrors) as e:
        exporter.run_export(library, ws.exports, ws.logs)
    assert len(e.value.errors) == 2
    text = str(e.value)
    assert f"31_Матрица_знаний.md:{m_line}:" in text and f"32_Реестр_закладок.md:{p_line}:" in text


def test_тип_проекта_с_новой_выгрузкой_пишется(ws, library):
    (ws.root / "типы").mkdir(exist_ok=True)
    (ws.root / "типы" / "мойтип.yaml").write_text(
        "тип: мойтип\nназначение: свой реестр\nмножественность: один\nизвлечения:\n"
        "  - имя: записи\n    выгрузка: мой.json\n    схема: словарь\n    результат: список\n    форматы:\n"
        "      - вид: таблица\n        колонки:\n          id: {синонимы: [id], обязательна: да, роль: ключ}\n"
        "          текст: {синонимы: [текст]}\n", encoding="utf-8")
    (library / "50_Мой.md").write_text("# Мой\n\n| id | текст |\n|---|---|\n| a | первый |\n", encoding="utf-8")
    _append(ws.root / "проект.yaml", "  - файл: 50_Мой.md\n    тип: мойтип\n")
    exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    data = json.loads((ws.exports / "мой.json").read_text(encoding="utf-8"))
    assert data == [{"id": "a", "текст": "первый"}]
    assert "мой.json" in json.loads((ws.exports / "индекс.json").read_text(encoding="utf-8"))["files"]


def test_не_utf8_проза_ошибка_а_не_трейсбек(ws, library):
    (library / "Проза" / "Том1_Глава05.md").write_bytes("# Глава 5\n\nТекст в старой кодировке.\n".encode("cp1251"))
    with pytest.raises(exporter.ExportErrors) as e:
        exporter.run_export(library, ws.exports, ws.logs)
    assert "Том1_Глава05.md:1:" in str(e.value) and "UTF-8" in str(e.value)
    checks = project.readiness(ws.root, library)  # «доктор» не падает
    assert checks


def test_сломанный_документ_выключенного_модуля_не_блокирует_экспорт(ws, library):
    text = (ws.root / "проект.yaml").read_text(encoding="utf-8").replace("  закладки: вкл\n", "  закладки: выкл\n")
    (ws.root / "проект.yaml").write_text(text, encoding="utf-8")
    _append(library / "32_Реестр_закладок.md", "| y |\n")
    hashes = exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    assert "plants.json" in hashes
    warnings = exporter.export_warnings(ws.exports)
    assert len(warnings) == 1 and warnings[0].startswith("32_Реестр_закладок.md:")
    assert any("выключенного модуля" in c.label for c in project.readiness(ws.root, library))
    # включённый модуль — по-прежнему ошибка
    (ws.root / "проект.yaml").write_text(text.replace("  закладки: выкл\n", "  закладки: вкл\n"), encoding="utf-8")
    with pytest.raises(MarkupError):
        exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)


def test_отпечаток_учитывает_манифест_и_типы_проекта(ws, library):
    f1 = exporter.canon_fingerprint(library, ws.root)
    _append(ws.root / "проект.yaml", "# правка манифеста\n")
    f2 = exporter.canon_fingerprint(library, ws.root)
    assert f1 != f2
    (ws.root / "типы").mkdir(exist_ok=True)
    (ws.root / "типы" / "x.yaml").write_text("тип: x\n", encoding="utf-8")
    assert exporter.canon_fingerprint(library, ws.root) != f2
    # кэш линтера не переживает выключение модулей
    r1 = lint.run_lint(library, ws.exports, ws.logs, root=ws.root)
    text = (ws.root / "проект.yaml").read_text(encoding="utf-8").replace(": вкл", ": выкл")
    (ws.root / "проект.yaml").write_text(text, encoding="utf-8")
    r2 = lint.run_lint(library, ws.exports, ws.logs, root=ws.root)
    assert r1.fingerprint != r2.fingerprint


def test_неизвестное_поле_записи_ошибка(ws, library):
    (ws.root / "типы").mkdir(exist_ok=True)
    (ws.root / "типы" / "хроника_эпохи.yaml").write_text(
        "тип: хроника_эпохи\nизвлечения:\n  - имя: хроника\n    выгрузка: chronicle.json\n    схема: ChronicleEvent\n"
        "    результат: список\n    форматы:\n      - вид: таблица\n        колонки:\n"
        "          дата: {синонимы: [дата], обязательна: да}\n          событие: {синонимы: [событие], обязательна: да}\n"
        "        запись: {date: дата, evnt: событие}\n", encoding="utf-8")
    with pytest.raises(exporter.ExportErrors) as e:
        exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    assert "неизвестные поля схемы ChronicleEvent: evnt" in str(e.value) and "17_Хроника_1995.md:" in str(e.value)


def test_акт_без_диапазона_глав_ошибка_со_строкой(ws, library):
    (library / "21_Акты.md").write_text(
        "# Акты\n\n| Акт | Название | Главы |\n|---|---|---|\n| 1 | «Письмо» | 1–4 |\n| 2 | «Архив» | много |\n"
        "| 3 | «Каркас» | ⚠ заполнить |\n", encoding="utf-8")
    _append(ws.root / "проект.yaml", "  - файл: 21_Акты.md\n    тип: акты\n    том: 1\n")
    with pytest.raises(exporter.ExportErrors) as e:
        exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    assert len(e.value.errors) == 1 and "21_Акты.md:6:" in str(e.value) and "«2»" in str(e.value)


def test_стартовый_комплект_без_заглушек_в_выгрузках(tmp_path):
    created = project.create(project.ProjectSpec(root=tmp_path / "серия", name="Проба", git=False))
    ws = Workspace(created.root)
    exporter.run_export(created.library, ws.exports, ws.logs, 1, created.root)
    for f in sorted(ws.exports.glob("*.json")):
        text = f.read_text(encoding="utf-8")
        assert "⚠ заполнить" not in text, f.name
    assert json.loads((ws.exports / "dossiers.json").read_text(encoding="utf-8")) == []
    stops = json.loads((ws.exports / "stoplists.json").read_text(encoding="utf-8"))
    assert all(r["items"] for r in stops)
    assert json.loads((ws.exports / "narration.json").read_text(encoding="utf-8"))[0]["laws"].count("⚠") == 0


def test_заголовок_выгрузок_с_детерминированной_датой(ws, library):
    index = json.loads((ws.exports / "индекс.json").read_text(encoding="utf-8"))
    assert {"версия_схемы", "дата", "том", "отпечаток_канона", "files"} <= set(index)
    assert index["дата"] == "" and "volume" not in index  # библиотека без git — даты нет
    subprocess.run(["git", "init", "-q"], cwd=library, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=library, check=True, env={"GIT_COMMITTER_DATE": "2020-01-02T03:04:05+00:00",
                                                "GIT_AUTHOR_DATE": "2020-01-02T03:04:05+00:00", "PATH": "/usr/bin:/bin"})
    exporter.run_export(library, ws.exports, ws.logs)
    index = json.loads((ws.exports / "индекс.json").read_text(encoding="utf-8"))
    assert index["дата"].startswith("2020-01-02")
    shutil.rmtree(library / ".git")


def test_корпус_и_журнал_детерминированы(ws, library, tmp_path):
    """П-6: две копии одной библиотеки дают байт-в-байт одинаковую папку выгрузок; повторный экспорт без изменений —
    ноль записей в журнале."""
    root2 = tmp_path / "копия"
    shutil.copytree(ws.root / "Библиотека", root2 / "Библиотека")
    shutil.copyfile(ws.root / "проект.yaml", root2 / "проект.yaml")
    ws2 = Workspace(root2)
    exporter.run_export(root2 / "Библиотека", ws2.exports, ws2.logs, 1, root2)
    for f in sorted(ws.exports.rglob("*")):
        if f.is_file():
            assert f.read_bytes() == (ws2.exports / f.relative_to(ws.exports)).read_bytes(), f.name
    lines = (ws.logs / "экспорт.jsonl").read_text(encoding="utf-8").splitlines()
    exporter.run_export(library, ws.exports, ws.logs)
    assert (ws.logs / "экспорт.jsonl").read_text(encoding="utf-8").splitlines() == lines


def test_известные_имена_без_слов_заголовка_таблицы():
    text = "| Том | Персонаж | Примечание |\n|---|---|---|\n| 1 | Иван · Зоя | Гуляев — никогда не фокален |\n"
    assert exporter._focal_names(text) == ["Зоя", "Иван"]


def test_фокал_глазами_и_месяц(ws, library):
    plan = library / "23_Поглавник_Том1.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace("- Фокал: Каширин\n", "- Фокал: глазами Зои\n", 1), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    b1 = exporter.load_brief(ws.exports, 1)
    assert b1.focal == "Зоя" and "Зоя" not in b1.participants
    assert exporter._month("1995.06.12") == 6 and exporter._month("12.06.1995") == 6 and exporter._month("май 1996") == 5


def test_карточка_несёт_строку_и_секции(ws):
    doss = {d.name: d for d in exporter.load_dossiers(ws.exports)}
    assert doss["Зоя"].line >= 1 and doss["Зоя"].sections.get("profile", 0) > 1


def test_лексемные_нормы_не_текут_между_проектами(ws, library):
    """Лексемная норма живёт в норме проекта, а не в общем для процесса реестре (П-6): экспорт её принимает,
    метрика строится из единицы на каждый прогон, другой проект (без такой нормы) её не видит."""
    style = library / "02_Стиль_и_голос.md"
    text = style.read_text(encoding="utf-8")
    anchor = "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |\n"
    assert anchor in text
    style.write_text(text.replace(anchor, anchor + "| лексемы_тест | тест | — | 2 | — | слово1, слово2 на 1000 |\n"),
                     encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    norms = exporter.load_norms(ws.exports)
    assert "лексемы_тест" in norms and metrics.unknown_norms(norms) == []
    assert "лексемы_тест" in {m.id for m in metrics.active_metrics(norms)} and "лексемы_тест" not in metrics.REGISTRY
    # другой проект (без такой нормы) её не видит: реестр не хранит чужих динамических метрик
    assert metrics.unknown_norms(["лексемы_тест"]) == ["лексемы_тест"]
    # норма с неразобранной единицей — ошибка экспорта с подсказкой формата
    style.write_text(style.read_text(encoding="utf-8").replace("слово1, слово2 на 1000", "доля"), encoding="utf-8")
    with pytest.raises(exporter.ExportErrors, match="лексемы_тест.*слово1, слово2 на 1000"):
        exporter.run_export(library, ws.exports, ws.logs)


def test_псевдосубъекты_и_исключения_прозы_проекта(ws, library):
    (ws.root / "типы").mkdir(exist_ok=True)
    (ws.root / "типы" / "эпистемика.yaml").write_text("тип: эпистемика\nпсевдосубъекты: [Читатель, Автор]\n", encoding="utf-8")
    assert exporter.pseudo_subjects(ws.root) == {"Читатель", "Автор"}
    assert "Автор" not in exporter.known_names_of(ws.exports, ws.root)
    (ws.root / "типы" / "проза.yaml").write_text("тип: проза\nисключить: [макет, черновик]\n", encoding="utf-8")
    (library / "Проза" / "Том1_Глава05_черновик.md").write_text("# Глава 5\n\nтекст\n", encoding="utf-8")
    assert [p.name for _, p in exporter.prose_files(library, 1, ws.root)] == ["Том1_Глава03.md"]


def test_ошибка_регэкспа_типа_проекта_с_файлом(ws, library):
    (ws.root / "типы").mkdir(exist_ok=True)
    (ws.root / "типы" / "мойтип.yaml").write_text(
        "тип: мойтип\nмножественность: один\nизвлечения:\n  - имя: строки\n    выгрузка: мой.json\n    схема: словарь\n"
        "    результат: список\n    форматы:\n      - вид: строки\n        регэксп: '[abc'\n", encoding="utf-8")
    (library / "50_Мой.md").write_text("# Мой\n\nabc\n", encoding="utf-8")
    _append(ws.root / "проект.yaml", "  - файл: 50_Мой.md\n    тип: мойтип\n")
    with pytest.raises(exporter.ExportErrors) as e:
        exporter.run_export(library, ws.exports, ws.logs, 1, ws.root)
    assert "50_Мой.md:1:" in str(e.value) and "строки" in str(e.value)
