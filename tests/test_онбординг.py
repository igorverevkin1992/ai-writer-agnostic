"""Онбординг и импорт (раздел 5): форматы и качество извлечения, классификация без API, предпросмотр = выгрузка,
нормализация дословна, транзакция применения, повторный импорт с конфликтом, отчёт готовности."""

from __future__ import annotations

import csv
import json
import sys
import types as _types
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import catalog, exporter, gitops, manifest as manifest_mod, project
from konveyer.cli import app
from konveyer.config import Config
from konveyer.onboarding import apply as apply_mod, classify, extract, importer, normalize, propose, report
from konveyer.paths import Workspace

DEMO = Path(__file__).resolve().parent.parent / "konveyer" / "data" / "демо"
runner = CliRunner()

PLAN = "# Поглавник\n\n## Глава 1 — Утро\n\n- Дата: 1 мая 1995\n- Фокал: Анна\n- Объём: 300\n- Биты:\n  - Анна приезжает\n"
STYLE = ("# Стиль\n\n## §1. Регистр\n\nСухой, точный.\n\n## §5. Численные нормы\n\n"
         "| id | параметр | мин | макс | брак | единица |\n|---|---|---|---|---|---|\n"
         "| объём_главы | объём главы | 200 | 400 | — | слов |\n")


@pytest.fixture
def proj(tmp_path: Path, monkeypatch) -> tuple[Workspace, Path, Path]:
    """Пустой проект без стартового комплекта + папка материалов автора."""
    created = project.create(project.ProjectSpec(root=tmp_path / "серия", name="Серия", starter=False,
                                                 modules=("эпистемика", "закладки"), author="Тест <t@t>"))
    gitops._git(created.library, "config", "user.email", "t@t")
    gitops._git(created.library, "config", "user.name", "t")
    if not gitops.has_commits(created.library):
        gitops._git(created.library, "commit", "-q", "--allow-empty", "-m", "init")
    monkeypatch.chdir(created.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    src = tmp_path / "материалы автора"
    src.mkdir()
    return Workspace(created.root), created.library, src


def _materials(src: Path) -> None:
    from docx import Document
    import openpyxl

    (src / "план глав.md").write_text(PLAN, encoding="utf-8")
    (src / "стиль.md").write_text(STYLE, encoding="utf-8")
    (src / "заметки.txt").write_bytes("Заметки автора про сюжет.".encode("cp1251"))
    d = Document()
    d.add_heading("Матрица знаний", 1)
    d.add_paragraph("Кто что знает.")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["факт", "субъект", "узнаёт в главе"], ["ключ у Анны", "Пётр", "1"], ["сторож жив", "Анна", "1"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    (src / "вложенная папка").mkdir()
    d.save(src / "вложенная папка" / "матрица знаний.docx")
    wb = openpyxl.Workbook()
    sh = wb.active
    sh.title = "Закладки"
    sh.append(["plant_id", "что", "положена", "выстрел"])
    sh.append(["P-1", "записка", "т1 гл1", "т1 гл1"])
    wb.save(src / "закладки.xlsx")
    with open(src / "хронология.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["дата", "событие", "глава"])
        w.writerow(["12.06.1995", "пропал сторож", "1"])
    (src / "мир.html").write_text("<html><body><h1>Мир</h1><p>Организации.</p><table><tr><th>название</th><th>описание</th>"
                                  "</tr><tr><td>Депо</td><td>железная дорога</td></tr></table></body></html>", encoding="utf-8")
    (src / "персонажи.json").write_text(json.dumps([{"имя": "Анна", "профиль": "приезжая"}], ensure_ascii=False), encoding="utf-8")
    (src / "нормы.yaml").write_text("нормы:\n  объём_главы: {мин: 2000, макс: 4000}\n", encoding="utf-8")
    (src / "текст.rtf").write_text(r"{\rtf1\ansi\ansicpg1251{\fonttbl{\f0 Arial;}}\f0 \'cf\'f0\'e8\'e2\'e5\'f2 \par}", encoding="latin-1")
    (src / "картинка.png").write_bytes(b"\x89PNG\r\n")
    (src / ("д" * 90 + ".md")).write_text("# Длинное имя\n", encoding="utf-8")


# ------------------------------------------------------------------ 5.1 импорт


def test_импорт_форматы(proj):
    ws, lib, src = proj
    _materials(src)
    rep = importer.import_path(ws, src)
    by = {e.файл: e for e in rep.added}
    # каждый файл — в индексе; кириллица, пробелы, вложенность — работают
    assert "вложенная папка__матрица знаний.docx" in by and "план глав.md" in by
    docx_md = (ws.root / by["вложенная папка__матрица знаний.docx"].извлечено_в).read_text(encoding="utf-8")
    assert "# Матрица знаний" in docx_md and "| факт | субъект | узнаёт в главе |" in docx_md and "| ключ у Анны | Пётр | 1 |" in docx_md
    assert by["вложенная папка__матрица знаний.docx"].качество["оценка"] == "чисто"
    xlsx_md = (ws.root / by["закладки.xlsx"].извлечено_в).read_text(encoding="utf-8")
    assert "| plant_id | что | положена | выстрел |" in xlsx_md and "| P-1 | записка |" in xlsx_md
    assert "| дата | событие | глава |" in (ws.root / by["хронология.csv"].извлечено_в).read_text(encoding="utf-8")
    assert "| название | описание |" in (ws.root / by["мир.html"].извлечено_в).read_text(encoding="utf-8")
    assert "| имя | профиль |" in (ws.root / by["персонажи.json"].извлечено_в).read_text(encoding="utf-8")
    assert "мин: 2000" in (ws.root / by["нормы.yaml"].извлечено_в).read_text(encoding="utf-8")
    assert "Привет" in (ws.root / by["текст.rtf"].извлечено_в).read_text(encoding="utf-8")
    assert "Заметки автора" in (ws.root / by["заметки.txt"].извлечено_в).read_text(encoding="utf-8")
    assert by["заметки.txt"].качество["оценка"] == "с потерями"  # перекодирован из cp1251
    # неподдерживаемый формат — сырьё без извлечения с причиной
    assert by["картинка.png"].извлечено_в is None and "не поддерживается" in by["картинка.png"].причина
    # длинное имя сокращено, соответствие в индексе
    long = next(e for e in rep.added if e.исходный_путь.endswith("д" * 90 + ".md"))
    assert len(long.файл) <= importer.MAX_NAME and (ws.root / "сырьё" / "оригиналы" / long.файл).exists()
    # оригиналы на месте и совпадают
    assert (ws.root / "сырьё" / "оригиналы" / "закладки.xlsx").read_bytes() == (src / "закладки.xlsx").read_bytes()
    # идемпотентность (FR-ON-5)
    rep2 = importer.import_path(ws, src)
    assert not rep2.added and len(rep2.skipped) == len(rep.added)
    assert len(importer.load_index(ws)) == len(rep.added)
    # архив
    with zipfile.ZipFile(src.parent / "архив.zip", "w") as z:
        z.writestr("из архива/сцены.md", "# Сцены\n\n- сцена 1\n")
    rep3 = importer.import_path(ws, src.parent / "архив.zip")
    assert [e.файл for e in rep3.added] == ["из архива__сцены.md"]


def test_импорт_скан_pdf_отклонён(proj, monkeypatch):
    ws, lib, src = proj

    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page(), _Page()]

    fake = _types.ModuleType("pypdf")
    fake.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    (src / "скан.pdf").write_bytes(b"%PDF-1.4 fake")
    rep = importer.import_path(ws, src)
    e = rep.added[0]
    assert e.извлечено_в is None and "OCR" in e.причина and "скан" in e.причина
    assert rep.rejected == [e]


# ------------------------------------------------------------------ 5.2 классификация


def test_классификация_сигнатуры():
    """Эталонный набор — демо-библиотека: ≥ 70 % документов получают верный тип машинным слоем."""
    types = catalog.load_types(DEMO)
    man = manifest_mod.load(DEMO)
    lib = DEMO / "Библиотека"
    total = ok = 0
    misses = []
    for p in sorted(lib.rglob("*.md")):
        e = man.entry_for(p.relative_to(lib).as_posix())
        if e is None:
            continue
        hyps = classify.classify_file(p, types)
        got = hyps[0].type if hyps and hyps[0].confidence >= propose.MIN_CONFIDENCE else "сырьё"
        total += 1
        ok += got == e.тип
        if got != e.тип:
            misses.append(f"{p.name}: {e.тип} → {got}")
    assert total >= 20 and ok / total >= 0.7, misses


def test_классификация_без_api(proj):
    ws, lib, src = proj
    _materials(src)
    importer.import_path(ws, src)
    props, note = propose.build(ws, cfg=Config(), use_model=True)  # ключей нет — модельный слой недоступен
    assert "модельный слой" in note
    by = {p.файл: p for p in props}
    assert by["план глав.md"].тип == "план_глав" and by["закладки.xlsx"].тип == "закладки"
    assert by["вложенная папка__матрица знаний.docx"].тип == "эпистемика" and by["мир.html"].тип == "мир"
    assert by["стиль.md"].тип == "стиль" and by["заметки.txt"].тип == "сырьё"
    assert all(p.источник_решения == "машина" for p in props)
    pj, pm = propose.save(ws, props, note)
    # воспроизводимость (FR-ON-10)
    first = pj.read_text(encoding="utf-8")
    props2, _ = propose.build(ws, cfg=Config(), use_model=False)
    propose.save(ws, props2, note)
    assert pj.read_text(encoding="utf-8") == first
    assert "| план глав.md | план_глав |" in pm.read_text(encoding="utf-8")


def test_предпросмотр_совпадает_с_выгрузкой(proj):
    """Что показано автору (записи предпросмотра) = что попало в JSON выгрузки после применения (FR-ON-11)."""
    ws, lib, src = proj
    _materials(src)
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    pv = next(p for p in props if p.файл == "вложенная папка__матрица знаний.docx").предпросмотр
    assert pv["records"] == 2 and pv["rows"][0]["fact"] == "ключ у Анны" and pv["rows"][0]["subject"] == "Пётр"
    assert pv["to_window"] and pv["internal"]
    cm = next(p for p in props if p.файл == "вложенная папка__матрица знаний.docx").колонки
    assert cm["generated"] == "fact_id" and cm["mapping"]["узнаёт"] == "узнаёт в главе"
    apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    matrix = exporter.load_matrix(ws.exports)
    assert [(f.fact, f.subject, f.from_chapter) for f in matrix] == [("ключ у Анны", "Пётр", 1), ("сторож жив", "Анна", 1)]
    assert [f.fact_id for f in matrix] == [r["fact_id"] for r in pv["rows"]]


# ------------------------------------------------------------------ 5.4 нормализация и применение


def test_нормализация_не_меняет_текст():
    types = catalog.load_types(None)
    text = ("# Мир\n\nАбзац первый — дословно, с «кавычками» и тире.\n\nВторой абзац.\n\n"
            "| Название | Описание |\n|---|---|\n| Депо | железная дорога |\n\nПосле таблицы.\n")
    norm = normalize.normalize(text, types["мир"], source="сырьё/оригиналы/мир.md")
    assert normalize.paragraphs(norm.text) == normalize.paragraphs(text)
    assert "<!-- источник: сырьё/оригиналы/мир.md -->" in norm.text
    assert "| название | описание |" in norm.text and norm.renamed == {"Название": "название", "Описание": "описание"}
    # обязательные секции создаются пустыми с подсказкой, ключ генерируется
    spec = types["эпистемика"]
    norm2 = normalize.normalize("# М\n\n| факт | субъект |\n|---|---|\n| а | б |\n", spec)
    assert "| fact_id | факт | субъект |" in norm2.text and "| М-001 | а | б |" in norm2.text and norm2.generated_ids == 1


def test_онбординг_транзакция(proj, monkeypatch):
    """Сбой на середине применения: ни один файл библиотеки, манифест и индекс сырья не изменены (FR-ON-17)."""
    ws, lib, src = proj
    _materials(src)
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    manifest_before = manifest_mod.path_of(ws.root).read_text(encoding="utf-8")
    index_before = importer.index_path(ws).read_text(encoding="utf-8")
    files_before = sorted(p.relative_to(lib).as_posix() for p in lib.rglob("*.md"))
    head = gitops.head(lib)
    calls = {"n": 0}
    real = apply_mod.guard.write_text

    def failing(path, text):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("диск переполнен")
        real(path, text)

    monkeypatch.setattr(apply_mod.guard, "write_text", failing)
    with pytest.raises(RuntimeError, match="диск"):
        apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert sorted(p.relative_to(lib).as_posix() for p in lib.rglob("*.md")) == files_before
    assert manifest_mod.path_of(ws.root).read_text(encoding="utf-8") == manifest_before
    assert importer.index_path(ws).read_text(encoding="utf-8") == index_before
    assert gitops.head(lib) == head and not gitops.dirty(lib)
    # без сбоя — одна транзакция и один коммит
    monkeypatch.setattr(apply_mod.guard, "write_text", real)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert res.commit and len(res.written) >= 5 and "заметки.txt" in res.raw_kept
    log = gitops._git(lib, "log", "--format=%s")
    assert log.splitlines()[0] == f"онбординг: {len(res.written)} документов"
    man = manifest_mod.load(ws.root)
    assert manifest_mod.validate(man, lib, catalog.load_types(ws.root), catalog.load_modules(ws.root)) == []
    assert (lib / "00_Индекс_библиотеки.md").exists() and "23_План_глав_Том1.md" in (lib / "00_Индекс_библиотеки.md").read_text(encoding="utf-8")
    plan = (lib / "23_План_глав_Том1.md").read_text(encoding="utf-8")
    assert "<!-- источник: сырьё/оригиналы/план глав.md -->" in plan  # FR-ON-18
    raw = {e.файл: e for e in importer.load_index(ws)}
    assert raw["план глав.md"].статус == "в_каноне" and raw["план глав.md"].документ_канона == "23_План_глав_Том1.md"
    # повторное применение — применять нечего, ничего не ломается
    with pytest.raises(apply_mod.OnboardingError):
        apply_mod.apply(ws, Config(), lib, author_confirmed=True)


def test_повторный_импорт_конфликт(proj):
    """Правка в источнике + правка в каноне → конфликт, а не потеря данных; только источник → обновление (FR-ON-21)."""
    ws, lib, src = proj
    (src / "план глав.md").write_text(PLAN, encoding="utf-8")
    (src / "стиль.md").write_text(STYLE, encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    doc = lib / "23_План_глав_Том1.md"
    # 1) изменился только источник → документ обновляется
    (src / "план глав.md").write_text(PLAN.replace("Утро", "Рассвет"), encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert res.updated == ["23_План_глав_Том1.md"] and "Рассвет" in doc.read_text(encoding="utf-8")
    # 2) правка автора в каноне + новая правка в источнике → конфликт, канон не тронут
    canon_edit = doc.read_text(encoding="utf-8").replace("Анна приезжает", "Анна приезжает поездом")
    doc.write_text(canon_edit, encoding="utf-8")
    gitops.commit_all(lib, "правка автора")
    (src / "план глав.md").write_text(PLAN.replace("Утро", "Закат"), encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert len(res.conflicts) == 1 and doc.read_text(encoding="utf-8") == canon_edit
    conflict = (ws.root / res.conflicts[0]).read_text(encoding="utf-8")
    assert "Закат" in conflict and "поездом" in conflict and "Прежнее извлечение" in conflict
    # FR-ON-22: источник исчез — только пометка
    (src / "стиль.md").unlink()
    importer.import_path(ws, src)
    assert next(e for e in importer.load_index(ws) if e.файл == "стиль.md").источник_исчез


# ------------------------------------------------------------------ 5.5 отчёт


def test_отчёт_называет_недостающее(proj):
    ws, lib, src = proj
    (src / "план глав.md").write_text(PLAN, encoding="utf-8")
    (src / "картинка.png").write_bytes(b"\x89PNG")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    text = report.build(ws, lib)
    assert "## 1. Что распознано" in text and "## 2. Что осталось сырьём" in text
    assert "## 3. Чего не хватает по модулям" in text and "## 4. Что делать дальше" in text
    assert "картинка.png — нет извлечения" in text
    assert "| эпистемика | эпистемика | эпистемика: нет |" in text and "стиль: нет" in text
    apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    text = report.build(ws, lib)
    assert "| план глав.md | план_глав | 23_План_глав_Том1.md | 1 |" in text and "план_глав: есть" in text
    # выключенный документ → модуль объявлен неполным
    man = manifest_mod.load(ws.root)
    man.entry_for("23_План_глав_Том1.md").выключен = True
    manifest_mod.save(ws.root, man)
    text = report.build(ws, lib)
    assert "план_глав: нет" in text
    assert "план_глав" in " ".join(c.label for c in project.readiness(ws.root, lib) if c.ok is False)


def test_cli_импорт_и_онбординг(proj):
    ws, lib, src = proj
    (src / "план глав.md").write_text(PLAN, encoding="utf-8")
    (src / "заметки.txt").write_text("заметки", encoding="utf-8")
    r = runner.invoke(app, ["импорт", str(src)])
    assert r.exit_code == 0 and "новых 2" in r.output, r.output
    r = runner.invoke(app, ["онбординг", "--решение", "заметки.txt=сырьё"])
    assert r.exit_code == 0 and "план глав.md → план_глав" in r.output and "решение: сырьё" in r.output, r.output
    assert (ws.root / "онбординг" / "предложение.md").exists() and (ws.root / "онбординг" / "отчёт.md").exists()
    r = runner.invoke(app, ["онбординг", "--решение", "заметки.txt=чушь"])
    assert r.exit_code == 1 and "допустимо" in r.output
    r = runner.invoke(app, ["онбординг", "--применить", "-y"])
    assert r.exit_code == 0 and "документов 1" in r.output and "закоммичен" in r.output, r.output
    assert (lib / "23_План_глав_Том1.md").exists()
