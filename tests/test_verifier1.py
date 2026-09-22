"""Тесты Верификатора-1 (FR-V1.*): флаговые «красные» проверки (10.2)."""

import json

from konveyer import compiler, exporter, verifier1
from konveyer.schemas import Brief


def _analyze(ws, fragment, window="", **ctx):
    return verifier1.analyze(
        fragment,
        window,
        Brief(chapter=1, year=1995, focal="Каширин", **ctx),
        exporter.load_norms(ws.exports),
        exporter.load_stoplists(ws.exports),
        corpus_dir=ws.corpus,
    )


def _by_id(checks, check_id):
    return [c for c in checks if c.check_id == check_id]


def test_брак_средней_длины(ws):
    checks = _analyze(ws, "Он шёл. Дождь лил. Всё молчало. Ночь пришла. Свет погас.")
    assert _by_id(checks, "V1.2a_средняя_длина")[0].status == "BRAK"  # <7 — брак


def test_был_плотность(ws):
    text = "Вечер был долгим. Небо было низким. В доме было холодно. Каширин был мрачен. " * 3
    c = _by_id(_analyze(ws, text), "V1.3_был")[0]
    assert c.status == "FLAG" and c.quotes


def test_усилители(ws):
    text = "Он очень устал и совершенно не хотел спорить. Это было абсолютно невыносимо."
    c = _by_id(_analyze(ws, text), "V1.4_усилители")[0]
    assert c.status == "FLAG"


def test_стоп_лексика_по_фокалу_и_году(ws):
    checks = _analyze(ws, "Менталитет у мужиков особый, думал он, разглядывая интернет в голове.")
    flagged = _by_id(checks, "V1.5_стоп_лексика")
    found = " ".join(c.actual for c in flagged)
    assert "менталитет" in found and "интернет" in found

    # стоп-лист чужой линии (Зоя) к фокалу Каширина не применяется
    checks2 = _analyze(ws, "Он думал про амбивалентный вечер и молчал до самого дома.")
    assert all("амбивалентный" not in c.actual for c in _by_id(checks2, "V1.5_стоп_лексика"))


def test_утечка_окна(ws):
    window = "Пиши прозу главы строго по этому окну и не выходи за бриф."
    c = _by_id(
        _analyze(ws, "Он жил строго по этому окну и не выходи за бриф, шутила Зоя.", window),
        "V1.6_утечка_окна",
    )[0]
    assert c.status == "FLAG" and c.quotes


def test_межглавные_повторы(ws):
    c = _by_id(
        _analyze(ws, "Ветер гнал по перрону обрывки газет, и он поднял воротник."),
        "V1.7_межглавные_повторы",
    )[0]
    assert c.status == "FLAG"
    assert "Том1_Глава03" in c.note  # глава-источник указана


def test_объём_против_брифа(ws):
    c = _by_id(_analyze(ws, "Три слова всего тут.", volume_words=300), "V1.2e_объём")[0]
    assert c.status == "BRAK"


def test_вердикт_пишется_в_файл(ws, library):
    compiler.compile_window(ws, library, 1)
    draft = ws.draft_path(1, 1)
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("Он вышел из дома ранним утром и пошёл в сторону старых гаражей за линией.", encoding="utf-8")
    verdict = verifier1.run_verify1(ws, 1, 1)
    data = json.loads((ws.chapter_dir(1) / "вердикт.json").read_text(encoding="utf-8"))
    assert data["chapter"] == 1
    for c in data["checks"]:  # FR-V1.9: структура вердикта
        assert {"check_id", "status", "threshold", "actual", "quotes", "rule_source"} <= set(c)
    assert verdict.checks
