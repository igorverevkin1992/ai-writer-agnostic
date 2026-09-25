"""Э1 и линтер на библиотеке эталона (FR-MG-3, FR-MG-4): калибровка сплиттера, TTR по тому, принятая глава
вне норм подсвечивается, чистый канон — без ошибок и ложных находок."""

from __future__ import annotations

import re
import shutil

from konveyer import compiler, lint, textutils, verifier1


def test_средняя_длина_принятых_глав_не_сдвинулась(ugar):
    """Правка сплиттера не должна ломать калибровку норм эталона: сдвиг средней — в пределах 0,3 слова."""
    _ws, lib, _ = ugar
    for name, was in (("Том1_Глава05.md", 6.2), ("Том1_Глава04_МАКЕТ.md", 7.29)):
        text = (lib / "Проза" / name).read_text(encoding="utf-8")
        sents = [s for s in textutils.split_sentences(text) if textutils.words(s)]
        avg = sum(len(textutils.words(s)) for s in sents) / len(sents)
        assert abs(avg - was) <= 0.3, (name, avg)


def test_ttr_окно_считается_по_тому_а_не_по_части(ugar_copy):
    ws, lib, _ = ugar_copy
    ws.chapter_dir(5).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lib / "Проза" / "Том1_Глава05.md", ws.draft_path(5, 1))
    compiler.compile_window(ws, lib, 5)
    ttr = next(c for c in verifier1.run_verify1(ws, 5, 1).checks if c.check_id == "V1.8b_ttr_окно")
    assert "не считается" not in ttr.actual and ttr.actual.split()[0].replace(".", "").isdigit()
    assert "брак 0.4" in ttr.threshold  # «переводной уровень 0,40 = брак» из стилевого регламента


def test_линтер_подсвечивает_принятую_главу_вне_норм(ugar):
    """Гл. 5 принята при средней 6,2 против коридора 9–12 — автор должен видеть противоречие (ПРОЗА-3)."""
    ws, lib, _ = ugar
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False, root=ws.root, use_cache=False)
    f = next((f for f in report.findings if f.code == "ПРОЗА-3"), None)
    assert f is not None and "Глава05" in f.file and f.severity == "предупреждение"
    assert "V1.2a_средняя_длина" in f.message and "решение автора" in f.message


def test_линтер_на_чистом_каноне_без_ошибок_и_шума(ugar):
    """Ошибок уровня «ошибка» нет; заметки о сценах без карточки досье и карточках без «Физики» — для автора,
    без ложных срабатываний на служебные обороты; возраст «гл. 41 т.1» не принимается за возраст."""
    ws, lib, _ = ugar
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False, root=ws.root, use_cache=False)
    # единственная ошибка чистого эталона — найденное линтером противоречие самого канона (тайна раскрывается в главе,
    # чей фокал её не знает); ошибок разметки и отказов модельного слоя нет
    errors = {f.code for f in report.findings if f.severity == "ошибка"}
    assert errors <= {"ТАЙНА-4"}, [f.message for f in report.findings if f.severity == "ошибка"]
    codes = {f.code for f in report.findings}
    assert "РАЗМ-1" not in codes and "ЛИНТ-0" not in codes
    assert "ТАЙНА-1" not in codes  # расхождение реестра и матрицы снято автором: на чистом каноне его нет
    assert not any(f.code == "ДОСЬЕ-1" and "41" in f.message for f in report.findings)
    assert all(f.severity == "заметка" for f in report.findings if f.code in ("ПОГЛ-2", "ДОСЬЕ-6"))
    notes = {f.code: [x.message for x in report.findings if x.code == f.code] for f in report.findings}
    who = {re.search(r"«([^»]+)»", m).group(1) for m in notes.get("ПОГЛ-2", [])}
    assert who and not any(w.lower().startswith(("чекист", "резидент")) for w in who)  # обороты, не персонажи
    for m in notes.get("ПОГЛ-2", []):
        assert re.search(r"\(гл\. [\d, ]+\)\.", m), m  # у каждой заметки — главы, где имя встречается
    no_physique = {m.split(":")[0] for m in notes.get("ДОСЬЕ-6", [])}
    # у главных фокалов «Физика» заполнена — их в заметках нет
    assert not any(n.startswith(("АРИСТАРХ", "СТЕПАН", "АНДРЕЙ")) for n in no_physique)
