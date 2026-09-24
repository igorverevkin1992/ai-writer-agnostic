"""Э1 без мёртвых и ложных проверок на демо-проекте: сплиттер, речь персонажей, стоп-лексика линии, доля диалога,
однострочные абзацы, блок документа-вставки, маркеры тайн по границам слова. Проверки на библиотеке эталона —
в tests/профиль_угар/test_э1_эталон.py (KONVEYER_ETALON)."""

from konveyer import exporter, lint, textutils, verifier1
from konveyer.schemas import Brief, Norm, StopRule


# ------------------------------------------------------------- сплиттер


def test_сокращения_с_пробелом_и_инициалы_не_режут_фразу():
    s = textutils.split_sentences("Дело, т. е. папка, лежало на столе. Лемм А. Х. подписал и вышел. И т. д.")
    assert s == ["Дело, т. е. папка, лежало на столе.", "Лемм А. Х. подписал и вышел.", "И т. д."]



# ------------------------------------------------------------- стоп-лексика


def test_стоп_лист_линии_не_ловит_реплики_других():
    """Стоп-лист линии касается ВНУТРЕННЕЙ речи фокала — «— Сынок, — сказал Бугаев» не нарушение (FR-V1-4)."""
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
    rules = [StopRule(scope="0.3", rule_id="0.3-Штерн", items=["отец", "сын"],
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


# ------------------------------------------------------------- доля диалога, абзацы, документ-вставка


def test_доля_диалога_однострочные_абзацы_и_документ(ws, library):
    brief = exporter.load_brief(ws.exports, 1)
    brief.documents = ["№1 (после главы): рапорт"]
    norms = exporter.load_norms(ws.exports)
    norms["доля_диалога"] = Norm(min=0.15, max=0.2, unit="доля строк", source="стиль")
    norms["фраз_в_абзаце"] = Norm(min=2.0, max=5.0, unit="фраз", source="стиль")
    stops = exporter.load_stoplists(ws.exports)
    text = "Первый абзац. Второй абзац тут же.\n\nОдна фраза.\n\n— Реплика.\n\nЕщё абзац. И ещё фраза."
    checks = {c.check_id: c for c in verifier1.analyze(text, "", brief, norms, stops)}
    assert checks["V1.9a_доля_диалога"].actual == "0.25"       # 1 реплика из 4 абзацев
    assert "однострочных абзацев: 2" in checks["V1.9b_фраз_в_абзаце"].note
    assert checks["V1.11_документ_вставка"].status == "BRAK"   # бриф требует документ, блока нет
    with_doc = text + "\n\n→ ДОКУМЕНТ\nРапорт.\n← КОНЕЦ ДОКУМЕНТА\n"
    checks = {c.check_id: c for c in verifier1.analyze(with_doc, "", brief, norms, stops)}
    assert checks["V1.11_документ_вставка"].status == "PASS"


# ------------------------------------------------------------- точность маркеров линтера


def test_маркеры_тайн_по_границам_слова():
    """«сынок» ≠ «сын», «активно» ≠ «актив»; оборот — дословно (FR-WN-3)."""
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


