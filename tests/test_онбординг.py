"""Онбординг и импорт (раздел 5): форматы и качество извлечения, классификация без API, предпросмотр = выгрузка,
нормализация дословна, транзакция применения, повторный импорт с конфликтом, отчёт готовности."""

from __future__ import annotations

import csv
import json
import os
import sys
import types as _types
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import catalog, exporter, gitops, manifest as manifest_mod, project
from konveyer.cli import app
from konveyer.config import Config
from konveyer.onboarding import apply as apply_mod, classify, importer, normalize, propose, report
from konveyer.paths import Workspace

DEMO = Path(__file__).resolve().parent.parent / "konveyer" / "data" / "демо"
runner = CliRunner()

PLAN = "# Поглавник\n\n## Глава 1 — Утро\n\n- Дата: 1 мая 1995\n- Фокал: Анна\n- Объём: 300\n- Биты:\n  - Анна приезжает\n"
MATRIX = "# Матрица знаний\n\nКто что знает.\n\n| факт | субъект | узнаёт в главе |\n|---|---|---|\n| ключ у Анны | Пётр | 1 |\n| сторож жив | Анна | 1 |\n"
MATRIX_RAW = "вложенная папка__матрица знаний.md"


def _optional(name: str):
    """Необязательная библиотека формата; сломанное расширение (паника cryptography у pypdf) — тоже «нет»."""
    try:
        return __import__(name)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001
        return None


def _needs(name: str):
    mod = _optional(name)
    if mod is None:
        pytest.skip(f"библиотека {name} недоступна")
    return mod
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


def _materials(src: Path, *, office: bool = False) -> None:
    """Папка материалов автора: базовые форматы без необязательных библиотек; `office=True` — ещё .docx и .xlsx."""
    (src / "план глав.md").write_text(PLAN, encoding="utf-8")
    (src / "стиль.md").write_text(STYLE, encoding="utf-8")
    (src / "заметки.txt").write_bytes("Заметки автора про сюжет.".encode("cp1251"))
    (src / "вложенная папка").mkdir()
    (src / "вложенная папка" / "матрица знаний.md").write_text(MATRIX, encoding="utf-8")
    if office:
        _office_materials(src)
    with open(src / "закладки.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["plant_id", "что", "положена", "выстрел"])
        w.writerow(["P-1", "записка", "т1 гл1", "т1 гл1"])
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


def _office_materials(src: Path) -> None:
    from docx import Document
    import openpyxl

    d = Document()
    d.add_heading("Матрица знаний", 1)
    d.add_paragraph("Кто что знает.")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["факт", "субъект", "узнаёт в главе"], ["ключ у Анны", "Пётр", "1"], ["сторож жив", "Анна", "1"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    d.save(src / "вложенная папка" / "матрица знаний.docx")
    wb = openpyxl.Workbook()
    sh = wb.active
    sh.title = "Закладки"
    sh.append(["plant_id", "что", "положена", "выстрел"])
    sh.append(["P-1", "записка", "т1 гл1", "т1 гл1"])
    wb.save(src / "закладки.xlsx")


class _Cp866ZipInfo(zipfile.ZipInfo):
    """Запись архива как из Проводника Windows: имя в cp866 без бита UTF-8."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp866"), self.flag_bits & ~0x800


def _minimal_pdf(text: str) -> bytes:
    """PDF с текстовым слоем (латиница, стандартный шрифт) — без внешних библиотек."""
    stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ------------------------------------------------------------------ 5.1 импорт


def test_импорт_форматы(proj):
    _needs("docx"), _needs("openpyxl")
    ws, lib, src = proj
    _materials(src, office=True)
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
    assert by["заметки.txt"].качество["оценка"] == "чисто" and "cp1251" in by["заметки.txt"].качество["заметки"][0]  # перекодирован без потерь
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


def test_импорт_повреждённые_архивы_и_края(proj, monkeypatch):
    """5.8 (1): у каждого файла папки — запись в индексе с извлечением либо явной причиной; битый .docx/.xlsx,
    вложенный и cp866-архив, .tsv и CSV с «;», пустой файл, битая ссылка, скобки и пробелы в именах, .JSON."""
    ws, lib, src = proj
    (src / "a.md").write_text("# А\n", encoding="utf-8")
    (src / "битый.docx").write_bytes(b"PK\x03\x04junk")
    (src / "битый.xlsx").write_bytes(b"nezip")
    (src / "пусто.txt").write_bytes(b"")
    (src / "CON.md").write_text("# con\n", encoding="utf-8")
    (src / "имя (в скобках) и пробелами.md").write_text("# скобки\n", encoding="utf-8")
    (src / "Т.JSON").write_text('{\n\t"имя": "Анна"\n}', encoding="utf-8")
    with open(src / "хроника.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter=";").writerows([["дата", "событие", "глава"], ["12.06.1995", "пропал сторож", "1"]])
    with open(src / "закл.tsv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t").writerows([["plant_id", "что", "положена"], ["P-1", "записка", "т1 гл1"]])
    with zipfile.ZipFile(src / "вложенный.zip", "w") as z:
        z.writestr(_Cp866ZipInfo("план глав.md"), PLAN)
        z.writestr("ещё/сцены.md", "# Сцены\n")
    if hasattr(os, "symlink"):
        try:
            os.symlink(src / "нет такого.md", src / "битая ссылка.md")
        except (OSError, NotImplementedError):
            pass
    n_files = sum(1 for p in src.rglob("*") if p.is_file() or p.is_symlink())
    monkeypatch.chdir(src.parent)
    rep = importer.import_path(ws, Path(src.name))  # относительный путь — в индексе абсолютный (FR-ON-22)
    index = {e.файл: e for e in importer.load_index(ws)}
    assert len(index) == n_files + 1  # +1: архив дал два файла вместо одной записи
    assert all(e.извлечено_в or e.причина for e in index.values())
    assert all(Path(e.исходный_путь.split("!")[0]).is_absolute() for e in index.values())
    assert "не прочитан" in index["битый.docx"].причина and "не прочитан" in index["битый.xlsx"].причина
    assert "пуст" in index["пусто.txt"].причина and "CON_.md" in index
    assert index["имя (в скобках) и пробелами.md"].извлечено_в
    assert "| имя: Анна" in (ws.root / index["Т.JSON"].извлечено_в).read_text(encoding="utf-8") or \
        "- имя: Анна" in (ws.root / index["Т.JSON"].извлечено_в).read_text(encoding="utf-8")
    assert "| дата | событие | глава |" in (ws.root / index["хроника.csv"].извлечено_в).read_text(encoding="utf-8")
    assert "| plant_id | что | положена |" in (ws.root / index["закл.tsv"].извлечено_в).read_text(encoding="utf-8")
    # вложенный архив распакован, имена cp866 перекодированы, путь источника указывает внутрь архива
    assert "вложенный__план глав.md" in index and index["вложенный__план глав.md"].исходный_путь.endswith("вложенный.zip!вложенный/план глав.md")
    assert "вложенный__ещё__сцены.md" in index
    if "битая ссылка.md" in index:
        assert "ссылка" in index["битая ссылка.md"].причина
    # повторный импорт из другой папки: ничего не добавлено, «источник исчез» не ставится ложно
    monkeypatch.chdir(ws.root)
    rep2 = importer.import_path(ws, src)
    assert not rep2.added and not rep2.changed
    assert not any(e.источник_исчез for e in importer.load_index(ws) if e.хэш)
    # сбой извлечения одного файла не роняет импорт и не оставляет оригиналов-сирот
    (src / "новый.md").write_text("# Новый\n", encoding="utf-8")
    (src / "ещё.md").write_text("# Ещё\n", encoding="utf-8")
    real = importer.extract_mod.extract

    def boom(path):
        if path.name == "ещё.md":
            raise RuntimeError("библиотека формата упала")
        return real(path)

    monkeypatch.setattr(importer.extract_mod, "extract", boom)
    rep3 = importer.import_path(ws, src)
    index = {e.файл: e for e in importer.load_index(ws)}
    originals = {p.name for p in (ws.root / "сырьё" / "оригиналы").iterdir()}
    assert "новый.md" in index and index["новый.md"].извлечено_в
    assert "ещё.md" in index and "прервано" in index["ещё.md"].причина and index["ещё.md"] in rep3.rejected
    assert originals - set(index) == set()  # каждый оригинал на диске — в индексе
    # сбой копирования (диск) — индекс всё равно сохранён с уже импортированными записями
    (src / "последний.md").write_text("# Последний\n", encoding="utf-8")
    (src / "0_первый.md").write_text("# Первый\n", encoding="utf-8")
    real_copy = importer.shutil.copyfile

    def failing_copy(a, b):
        if Path(a).name == "последний.md":
            raise OSError("диск переполнен")
        return real_copy(a, b)

    monkeypatch.setattr(importer.extract_mod, "extract", real)
    monkeypatch.setattr(importer.shutil, "copyfile", failing_copy)
    with pytest.raises(OSError):
        importer.import_path(ws, src)
    index = {e.файл: e for e in importer.load_index(ws)}
    assert "0_первый.md" in index and "последний.md" not in index
    assert rep.index_path.exists()


def test_извлечение_docx_xlsx_структура(proj):
    """Объединённые ячейки не сдвигают колонки, соседние равные значения сохраняются, гиперссылки, сноски
    и нумерованные списки читаются (FR-ON-2, FR-ON-23)."""
    docx_lib = _needs("docx")
    openpyxl = _needs("openpyxl")
    from docx.oxml import OxmlElement

    ws, lib, src = proj
    d = docx_lib.Document()
    t = d.add_table(rows=2, cols=3)
    for r, row in enumerate([["факт", "субъект", "узнаёт"], ["да", "да", "1"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    t2 = d.add_table(rows=2, cols=3)
    t2.cell(0, 0).merge(t2.cell(0, 1)).text = "шапка"
    t2.cell(0, 2).text = "x"
    for c, v in enumerate(["a", "b", "c"]):
        t2.cell(1, c).text = v
    p = d.add_paragraph("Смотри ")
    link, run, text = OxmlElement("w:hyperlink"), OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "ссылку"
    run.append(text)
    link.append(run)
    p._p.append(link)
    d.add_paragraph("часть первая", style="List Number")
    d.add_paragraph("часть вторая", style="List Number")
    d.add_paragraph("маркер", style="List Bullet")
    d.save(src / "т.docx")
    wb = openpyxl.Workbook()
    sh = wb.active
    sh.append(["a", "b", "c"])
    sh.append([1, 2, 3])
    sh.merge_cells("A1:B1")
    wb.save(src / "к.xlsx")
    rep = importer.import_path(ws, src)
    by = {e.файл: e for e in rep.added}
    md = (ws.root / by["т.docx"].извлечено_в).read_text(encoding="utf-8")
    assert "| да | да | 1 |" in md and "| шапка |  | x |" in md and "| a | b | c |" in md
    assert "Смотри ссылку" in md and "1. часть первая" in md and "2. часть вторая" in md and "- маркер" in md
    assert by["т.docx"].качество["таблиц_распознано"] == 2 and "объединёнными" in by["т.docx"].качество["подозрительные_места"][0]
    xmd = (ws.root / by["к.xlsx"].извлечено_в).read_text(encoding="utf-8")
    assert "| a |  | c |" in xmd and by["к.xlsx"].качество["оценка"] == "с потерями"
    assert "объединённые" in by["к.xlsx"].качество["подозрительные_места"][0]
    # книга закрыта — оригинал можно удалить (Windows) и переоткрыть
    (ws.root / "сырьё" / "оригиналы" / "к.xlsx").read_bytes()


def test_импорт_pdf_с_текстовым_слоем(proj):
    _needs("pypdf")
    ws, lib, src = proj
    (src / "план.pdf").write_bytes(_minimal_pdf("Chapter plan: chapter one, chapter two"))
    rep = importer.import_path(ws, src)
    e = rep.added[0]
    assert e.извлечено_в and "Chapter plan" in (ws.root / e.извлечено_в).read_text(encoding="utf-8"), e.причина


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
    assert by["план глав.md"].тип == "план_глав" and by["закладки.csv"].тип == "закладки"
    assert by[MATRIX_RAW].тип == "эпистемика" and by["мир.html"].тип == "мир"
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
    pv = next(p for p in props if p.файл == MATRIX_RAW).предпросмотр
    assert pv["records"] == 2 and pv["rows"][0]["fact"] == "ключ у Анны" and pv["rows"][0]["subject"] == "Пётр"
    assert pv["to_window"] and pv["internal"]
    cm = next(p for p in props if p.файл == MATRIX_RAW).колонки
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
    # 2) правка автора в каноне и правка в источнике в РАЗНЫХ строках → построчное слияние, обе правки на месте
    canon_edit = doc.read_text(encoding="utf-8").replace("Анна приезжает", "Анна приезжает поездом")
    doc.write_text(canon_edit, encoding="utf-8")
    gitops.commit_all(lib, "правка автора")
    (src / "план глав.md").write_text(PLAN.replace("Утро", "Рассвет").replace("Объём: 300", "Объём: 350"), encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    merged = doc.read_text(encoding="utf-8")
    assert res.updated == ["23_План_глав_Том1.md"] and not res.conflicts
    assert "Анна приезжает поездом" in merged and "Объём: 350" in merged and "<<<<<<<" not in merged
    assert normalize.source_of(merged) and normalize.source_of(merged).startswith("сырьё/оригиналы/план глав~")
    # 3) обе стороны правят ОДНУ строку → конфликт, канон не тронут, в файле конфликта — слияние с маркерами
    canon_edit = merged.replace("Рассвет", "Рассвет и туман")
    doc.write_text(canon_edit, encoding="utf-8")
    gitops.commit_all(lib, "правка автора 2")
    (src / "план глав.md").write_text(PLAN.replace("Утро", "Закат").replace("Объём: 300", "Объём: 350"), encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert len(res.conflicts) == 1 and doc.read_text(encoding="utf-8") == canon_edit
    conflict = (ws.root / res.conflicts[0]).read_text(encoding="utf-8")
    assert "Закат" in conflict and "туман" in conflict and "Прежнее извлечение" in conflict
    assert apply_mod.CONFLICT_A in conflict and apply_mod.CONFLICT_B in conflict and "=источник" in conflict
    # повтор без решения — тот же конфликт, а не потеря данных; решение «источник» снимает его
    newest = next(p for p in props if p.файл.startswith("план глав~") and p.имя_документа == "23_План_глав_Том1.md" and p.обновление)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert len(res.conflicts) == 1
    propose.set_decision(ws, newest.файл, "источник")
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert res.updated == ["23_План_глав_Том1.md"] and "Закат" in doc.read_text(encoding="utf-8") and not res.conflicts
    raw = {e.файл: e for e in importer.load_index(ws)}
    assert raw[newest.файл].статус == "в_каноне" and raw[newest.файл].документ_канона == "23_План_глав_Том1.md"
    assert sum(1 for e in raw.values() if e.статус == "в_каноне" and e.документ_канона == "23_План_глав_Том1.md") == 1
    # 4) решение «канон»: правка автора остаётся, новая версия считается внесённой, конфликт не повторяется
    canon_edit = doc.read_text(encoding="utf-8").replace("Закат", "Закат над депо")
    doc.write_text(canon_edit, encoding="utf-8")
    gitops.commit_all(lib, "правка автора 3")
    (src / "план глав.md").write_text(PLAN.replace("Утро", "Ночь").replace("Объём: 300", "Объём: 350"), encoding="utf-8")
    importer.import_path(ws, src)
    props, note = propose.build(ws)
    propose.save(ws, props, note)
    newest = next(p for p in props if p.обновление)
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert len(res.conflicts) == 1
    propose.set_decision(ws, newest.файл, "канон")
    res = apply_mod.apply(ws, Config(), lib, author_confirmed=True)
    assert doc.read_text(encoding="utf-8") == canon_edit and res.updated == ["23_План_глав_Том1.md"] and not res.conflicts
    with pytest.raises(apply_mod.OnboardingError):  # применять больше нечего — конфликт не возвращается
        apply_mod.apply(ws, Config(), lib, author_confirmed=True)
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
