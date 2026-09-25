"""Э1 на библиотеке эталона (KONVEYER_ETALON): калибровка средней длины принятых глав, TTR по тому, ПРОЗА-3 —
значения эталонной серии живут здесь, в тестах профиля, а не в тестах движка (П-1)."""

from __future__ import annotations

import shutil


from konveyer import compiler, lint, textutils, verifier1
# фикстура `ugar` (проект профиля с копией библиотеки эталона) — из tests/профиль_угар/conftest.py;
# без KONVEYER_ETALON тесты пропускаются


def test_средняя_длина_принятых_глав_не_сдвинулась(ugar):
    """Правки сплиттера не ломают калибровку норм эталона: сдвиг средней — в пределах 0,3 слова."""
    ws, lib, man = ugar
    for name, was in (("Том1_Глава05.md", 6.2), ("Том1_Глава04_МАКЕТ.md", 7.29)):
        text = (lib / "Проза" / name).read_text(encoding="utf-8")
        sents = [s for s in textutils.split_sentences(text) if textutils.words(s)]
        avg = sum(len(textutils.words(s)) for s in sents) / len(sents)
        assert abs(avg - was) <= 0.3, (name, avg)


def test_ttr_окно_считается_по_тому_а_не_по_части(ugar):
    ws, lib, man = ugar
    ws.chapter_dir(5).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lib / "Проза" / "Том1_Глава05.md", ws.draft_path(5, 1))
    compiler.compile_window(ws, lib, 5)
    ttr = next(c for c in verifier1.run_verify1(ws, 5, 1).checks if c.check_id == "V1.8b_ttr_окно")
    assert "не считается" not in ttr.actual and ttr.actual.split()[0].replace(".", "").isdigit()
    assert "брак 0.4" in ttr.threshold  # порог брака TTR — из документа стиля эталона


def test_линтер_подсвечивает_принятую_главу_вне_норм(ugar):
    """Принятая глава 5 с средней 6,2 при норме 9–12 — автор должен видеть противоречие (ПРОЗА-3)."""
    ws, lib, man = ugar
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False, root=ws.root, use_cache=False)
    f = next((f for f in report.findings if f.code == "ПРОЗА-3"), None)
    assert f is not None and "Глава05" in f.file and f.severity == "предупреждение"
    assert "V1.2a_средняя_длина" in f.message and "решение автора" in f.message
