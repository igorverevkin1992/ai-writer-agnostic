"""Этап 3 второго аудита, п. 16: Э1 без мёртвых и ложных проверок (TTR по тому, речь персонажей,
сплиттер, доля диалога, однострочные абзацы, блок документа-вставки)."""

from pathlib import Path

import pytest

from konveyer import exporter, guard, lint, textutils, verifier1
from konveyer.paths import Workspace
from konveyer.schemas import Brief, Norm, StopRule

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture
def real(tmp_path):
    (tmp_path / "конфиг.yaml").write_text(f'library_dir: "{LIBRARY.as_posix()}"\n', encoding="utf-8")
    ws = Workspace(tmp_path)
    guard.set_library_dir(LIBRARY)
    exporter.run_export(LIBRARY, ws.exports, ws.logs)
    return ws


# ------------------------------------------------------------- сплиттер (2.6)


def test_сокращения_с_пробелом_и_инициалы_не_режут_фразу():
    s = textutils.split_sentences("Дело, т. е. папка, лежало на столе. Лемм А. Х. подписал и вышел. И т. д.")
    assert s == ["Дело, т. е. папка, лежало на столе.", "Лемм А. Х. подписал и вышел.", "И т. д."]


@real_only
def test_средняя_длина_принятых_глав_не_сдвинулась():
    """Правка сплиттера не должна ломать калибровку Р-015: сдвиг средней — в пределах 0,3 слова."""
    for name, was in (("Том1_Глава05.md", 6.2), ("Том1_Глава04_МАКЕТ.md", 7.29)):
        text = (LIBRARY / "Проза" / name).read_text(encoding="utf-8")
        sents = [s for s in textutils.split_sentences(text) if textutils.words(s)]
        avg = sum(len(textutils.words(s)) for s in sents) / len(sents)
        assert abs(avg - was) <= 0.3, (name, avg)


# ------------------------------------------------------------- стоп-лексика (2.3)


def test_стоп_лист_линии_не_ловит_реплики_других():
    """03: стоп-лист линии касается ВНУТРЕННЕЙ речи фокала — «— Сынок, — сказал Бугаев» не нарушение."""
    text = "Штерн склонился над телом.\n\n— Сынок, — сказал Бугаев и отвернулся.\n\nШтерн молчал."
    assert "сынок" not in textutils.narration_only(text).lower()
    assert "штерн молчал" in textutils.narration_only(text).lower()
    # тот же маркер в повествовании фокала — по-прежнему нарушение
    leak = "Штерн склонился над телом. Он подумал об отце и промолчал."
    assert "отце" in textutils.narration_only(leak)


def _norms():
    n = Norm(min=1.0, max=100.0, unit="", source="тест")
    return {k: n for k in ("средняя_длина", "короткая_фраза_порог", "длинная_фраза_порог", "доля_коротких",
                           "доля_длинных", "был_на_250", "объём_главы", "утечка_нграмма", "повтор_нграмма",
                           "ttr_окно_слов", "ttr_мин")}


def test_стоп_лексика_линии_по_повествованию_в_э1():
    brief = Brief(chapter=1, focal="Штерн", year=1926)
    rules = [StopRule(scope="повествователь", rule_id="0.3-Штерн", items=["отец", "сын"],
                      applies_to={"focal": "Штерн"}, action="запрет")]
    norms = _norms()
    norms["ttr_окно_слов"] = Norm(min=10.0, max=10.0, unit="слов", source="тест")
    norms["ttr_мин"] = Norm(min=0.4, unit="доля", source="тест")
    dialogue = "Штерн вошёл в прозекторскую.\n\n— Сынок, — сказал Бугаев.\n\nШтерн промолчал."
    checks = {c.check_id: c for c in verifier1.analyze(dialogue, "", brief, norms, rules)}
    assert checks["V1.5_стоп_лексика"].status == "PASS"
    narration = "Штерн вошёл в прозекторскую. Он вспомнил отца и промолчал."
    checks = {c.check_id: c for c in verifier1.analyze(narration, "", brief, norms, rules)}
    assert checks["V1.5_стоп_лексика"].status == "FLAG" and "отец" in checks["V1.5_стоп_лексика"].actual


# ------------------------------------------------------------- TTR по тому (2.2)


@real_only
def test_ttr_окно_считается_по_тому_а_не_по_части(real):
    import shutil

    real.chapter_dir(5).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LIBRARY / "Проза" / "Том1_Глава05.md", real.draft_path(5, 1))
    from konveyer import compiler

    compiler.compile_window(real, LIBRARY, 5)
    ttr = next(c for c in verifier1.run_verify1(real, 5, 1).checks if c.check_id == "V1.8b_ttr_окно")
    # раньше здесь всегда было «часть короче 10000 слов — не считается»
    assert "не считается" not in ttr.actual and ttr.actual.split()[0].replace(".", "").isdigit()
    assert "брак 0.4" in ttr.threshold  # «переводной уровень 0,40 = брак» из 02 §5


# ------------------------------------------------------------- новые проверки §5


def test_доля_диалога_однострочные_абзацы_и_документ(ws, library):
    brief = exporter.load_brief(ws.exports, 1)
    brief.documents = ["№1 (после главы): рапорт"]
    norms = exporter.load_norms(ws.exports)
    norms["доля_диалога"] = Norm(min=0.15, max=0.2, unit="доля строк", source="02 §5")
    norms["фраз_в_абзаце"] = Norm(min=2.0, max=5.0, unit="фраз", source="02 §5")
    stops = exporter.load_stoplists(ws.exports)
    text = "Первый абзац. Второй абзац тут же.\n\nОдна фраза.\n\n— Реплика.\n\nЕщё абзац. И ещё фраза."
    checks = {c.check_id: c for c in verifier1.analyze(text, "", brief, norms, stops)}
    assert checks["V1.9a_доля_диалога"].actual == "0.25"       # 1 реплика из 4 абзацев
    assert "однострочных абзацев: 2" in checks["V1.9b_фраз_в_абзаце"].note
    assert checks["V1.11_документ_вставка"].status == "BRAK"   # бриф требует документ, блока нет
    with_doc = text + "\n\n→ ДОКУМЕНТ\nРапорт.\n← КОНЕЦ ДОКУМЕНТА\n"
    checks = {c.check_id: c for c in verifier1.analyze(with_doc, "", brief, norms, stops)}
    assert checks["V1.11_документ_вставка"].status == "PASS"


# ------------------------------------------------------------- ПРОЗА-3 (2.1)


@real_only
def test_линтер_подсвечивает_принятую_главу_вне_норм(real):
    """Р-015 против Р-018: гл. 5 принята при средней 6,2 — автор должен видеть противоречие."""
    report = lint.run_lint(LIBRARY, real.exports, real.logs, export=False)
    f = next((f for f in report.findings if f.code == "ПРОЗА-3"), None)
    assert f is not None and "Глава05" in f.file and f.severity == "предупреждение"
    assert "V1.2a_средняя_длина" in f.message and "решение автора" in f.message
    assert report.errors == 0


# ------------------------------------------------------------- точность маркеров линтера (п. 18)


def test_маркеры_тайн_по_границам_слова():
    """«сынок» ≠ «сын», «активно» ≠ «актив» (аудит 2, находка 3.9); оборот — дословно."""
    assert lint.marker_hit("Бугаев сказал: молодец, сынок", ["сын"]) is None
    assert lint.marker_hit("работа активно продолжается", ["актив"]) is None
    assert lint.marker_hit("его сын пропал", ["сын"]) == "сын"
    assert lint.marker_hit("говорили о сыне", ["сын"]) == "сын"          # косвенный падеж — ловится
    assert lint.marker_hit("завербован сетью в подворотне", ["завербован сетью"]) == "завербован сетью"
    assert lint.marker_hit("сеть работала", ["завербован сетью"]) is None


def test_маркер_в_реплике_чужого_персонажа_не_знание_фокала(ws, library):
    """Проза: «— Сынок, — сказал Бугаев» — не утечка тайны фокала; то же в повествовании — утечка."""
    from konveyer.schemas import InfoBan

    brief = exporter.load_brief(ws.exports, 1)
    bans = [InfoBan(ban_id="Т-99", text="тайна", secret=True, markers=["сын"], known_by={}, until_chapter=40)]
    prose = library / "Проза" / f"Том1_Глава0{brief.chapter}.md"

    def run() -> list:
        ctx = lint.load_context(library, ws.exports, 1)
        ctx.infobans = bans
        return [f for f in lint.check_prose(ctx) if f.code == "ПРОЗА-1"]

    prose.write_text("Он вошёл в контору.\n\n— Сынок, — сказал Бугаев и отвернулся.\n\nКаширин молчал.\n", encoding="utf-8")
    assert run() == []
    prose.write_text("Он вошёл в контору.\n\nКаширин вспомнил про сына и промолчал.\n", encoding="utf-8")
    hits = run()
    assert len(hits) == 1 and "Т-99" in hits[0].message


