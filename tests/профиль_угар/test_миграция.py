"""Миграция эталона как приёмочный тест универсальности (14.2 ТЗ): библиотека УГАРа проходит онбординг как обычный
проект (FR-MG-1), окна глав сверяются с эталоном по составу фактов и фильтру знания (FR-MG-2), метрики Э1 на принятой
главе совпадают с эталонными (FR-MG-3), линтер даёт те же классы находок (FR-MG-4)."""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

from konveyer import catalog, compiler, exporter, guard, lint, manifest as manifest_mod, project, verifier1
from konveyer.paths import Workspace
from tests.профиль import LIBRARY_ENV

_FALLBACK = Path(__file__).resolve().parents[3] / "igorverevkin1992" / "ugar-library"
_ENV = os.environ.get(LIBRARY_ENV) or os.environ.get("KONVEYER_ЭТАЛОН")
ETALON = Path(_ENV) if _ENV else _FALLBACK  # пустой путь не должен превращаться в текущую папку (Path("") — это cwd)
pytestmark = pytest.mark.skipif(not ETALON.is_dir(), reason=f"библиотека эталона не подключена ({LIBRARY_ENV})")


@pytest.fixture(scope="module")
def ugar(tmp_path_factory):
    """Проект из профиля «угар» с копией библиотеки эталона: карта — выведена классификацией (онбординг без исключений)."""
    root = tmp_path_factory.mktemp("угар")
    created = project.create(project.ProjectSpec(root=root / "проект", name="УГАР", profile="угар", starter=False, git=False, volumes=11))
    shutil.copytree(ETALON, created.library, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    types = catalog.load_types(created.root)
    man = manifest_mod.infer(created.library, types)
    man.проект.имя, man.проект.томов_план = "УГАР", 11
    for m in ("фокализация", "информрежим", "эпистемика", "закладки", "континуити", "дозы_прошлого", "документы_вставки",
              "хроника_эпохи", "драматургия", "арки"):
        man.модули[m] = "вкл"
    man.методики.том = man.методики.акт = man.методики.глава = "круг_хармона"
    manifest_mod.save(created.root, man)
    ws = Workspace(created.root)
    guard.set_library_dir(created.library)
    exporter.run_export(created.library, ws.exports, ws.logs, 1, created.root)
    return ws, created.library, man


def test_миграция_онбординг_без_исключений(ugar):
    """FR-MG-1: карта библиотеки выведена машинным слоем; экспорт без единой ошибки."""
    ws, lib, man = ugar
    by = {e.файл: e.тип for e in man.библиотека}
    expect = {"02_Стилевой_регламент.md": "стиль", "03_Правила_фокализации.md": "повествование", "04_Языковой_канон.md": "язык",
              "12_Генеральная_хронология_фабулы.md": "хронология", "21_Круги_истории_Том1.md": "каркасы", "22_Арки_Том1.md": "арки",
              "23_Поглавник_Часть_I.md": "план_глав", "31_Эпистемическая_матрица_Том1.md": "эпистемика",
              "33_Континуити_трекер.md": "континуити", "36_Журнал_канонических_решений.md": "журнал_решений",
              "УГАР_Том1_Реестр_информационного_режима.md": "информрежим", "Досье/": "персонажи", "Проза/": "проза"}
    wrong = {f: (t, by.get(f)) for f, t in expect.items() if by.get(f) != t}
    assert not wrong, wrong
    assert len([f for f in expect if by.get(f) == expect[f]]) / len(expect) >= 0.9
    col = exporter.collect(lib, 1, ws.root)
    assert col.errors == [], [str(e) for e in col.errors]


def test_миграция_выгрузки_как_в_эталоне(ugar):
    ws, lib, man = ugar
    briefs = exporter.load_briefs(ws.exports)
    assert len(briefs) == 46 and all(b.year == 1926 and b.volume == 1 for b in briefs)
    b5 = exporter.load_brief(ws.exports, 5)
    assert b5.focal == "Степан" and "18.04" in b5.date and "Лемм" in b5.participants and b5.scenes
    assert exporter.load_brief(ws.exports, 22).focal == "Лемм"
    norms = exporter.load_norms(ws.exports)
    n = norms["средняя_длина"]
    assert (n.min, n.max, n.brak) == (9, 12, 7) and "Р-015" in n.source
    assert norms["доля_коротких"].min == 0.30 and norms["был_на_250"].max == 1 and norms["ttr_мин"].min == 0.46
    assert norms["усилители_на_1000"].max == 2
    stops = exporter.load_stoplists(ws.exports)
    stern = [r for r in stops if r.applies_to.get("focal") == "Штерн" and r.kind == "лексика"]
    assert stern and {"отец", "сын", "семья", "папа"} <= set(stern[0].items)
    assert any(r.kind == "усилитель" and "предельно" in r.items for r in stops)
    matrix = exporter.load_matrix(ws.exports)
    assert {f.fact_id for f in matrix if f.subject == "Степан" and f.from_chapter is not None and f.from_chapter <= 5} == {"М-04"}
    bans = exporter.load_infobans(ws.exports)
    assert len([b for b in bans if b.secret]) == 10 and next(b for b in bans if "сын Лемма" in b.text).until_chapter == 46
    plants = exporter.load_plants(ws.exports)
    assert any(p.chapters == [6] and {"vol": 6} in p.fires for p in plants)
    names = sorted(d.name for d in exporter.load_dossiers(ws.exports))
    assert names == ["Ася", "Бугаев", "Заварзин", "Ковров", "Лемм", "Мередит", "Ольга", "Ремез", "Степан", "Штерн"]
    assert len(exporter.load_export(ws.exports, "chronology.json")) > 50 and len(exporter.load_arcs(ws.exports)) > 0


def test_миграция_окно_главы_5_эквивалентно_эталону(ugar):
    """FR-MG-2: состав секций и фильтр знания как в эталонном окне (Тест_Писателя/ПРОМПТ_Глава5.md)."""
    ws, lib, man = ugar
    path, breakdown = compiler.compile_window(ws, lib, 5)
    w = path.read_text(encoding="utf-8")
    for section in ("роль и запреты", "регистр и стиль", "фокализация", "персонажи сцены", "что знает фокал", "бриф", "формат выдачи"):
        assert f"<!-- СЕКЦИЯ: {section} -->" in w, section
    assert "Фокал: Степан" in w and "### Степан" in w and "### Лемм" in w and "### Штерн" not in w
    assert "М-04" in w and "сын Лемма" not in w and "Подлог 1913" not in w  # фильтр знания
    etalon = (ETALON / "Тест_Писателя" / "ПРОМПТ_Глава5.md").read_text(encoding="utf-8")
    for word in ("Степан", "Лемм", "рапорт"):
        assert word in w and word in etalon
    # ни одного маркера тайны, недоступной фокалу, в окне
    for b in exporter.load_infobans(ws.exports):
        if b.secret and not b.known_to("Степан", 5):
            for m in b.markers:
                assert m.lower() not in w.lower(), (b.ban_id, m)
    assert compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8") == w  # детерминизм


def test_миграция_метрики_э1_главы_5(ugar):
    """FR-MG-3: метрики Э1 на принятой главе 5 совпадают с эталонными в пределах допуска (Д-12)."""
    ws, lib, man = ugar
    text = (lib / "Проза" / "Том1_Глава05.md").read_text(encoding="utf-8")
    compiler.compile_window(ws, lib, 5)
    checks = {c.check_id: c for c in verifier1.analyze_text(ws, 5, text)}
    assert checks["V1.2a_средняя_длина"].status == "BRAK" and abs(float(checks["V1.2a_средняя_длина"].actual) - 6.2) <= 0.05
    assert checks["V1.4_усилители"].status == "FLAG"
    assert checks["V1.5_стоп_лексика"].status == "PASS" and checks["V1.6_утечка_окна"].status == "PASS"
    assert checks["V1.2e_объём"].status == "PASS" and "700" in checks["V1.2e_объём"].threshold


def test_миграция_линтер_те_же_классы(ugar):
    """FR-MG-4: на нетронутой библиотеке эталона — только известные классы находок и ни одной ошибки разметки."""
    ws, lib, man = ugar
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False, root=ws.root, use_cache=False)
    codes = Counter(f.code for f in report.findings)
    assert "РАЗМ-1" not in codes and "ЛИНТ-0" not in codes
    assert set(codes) <= {"ПОГЛ-2", "ДОСЬЕ-6", "МАТР-3", "ТАЙНА-4", "ПРОЗА-3", "ДОСЬЕ-1", "КАНОН-1", "АРКА-1", "АРКА-2", "КРУГ-1"}, codes
    assert codes["ПРОЗА-3"] == 1 and codes["ТАЙНА-4"] == 1 and codes["МАТР-3"] == 2
    for f in report.findings:
        assert f.file and "Что сделать" in f.message
