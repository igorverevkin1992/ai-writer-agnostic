"""Регрессия (FR-R2…FR-R4): 100% стартового корпуса ловится (критерий приёмки 4)."""

import json

from konveyer import regression
from konveyer.schemas import GoldenTest


def test_стартовый_корпус_зелёный(ws):
    report = regression.run_regression(ws)
    assert report["зелёная"], report
    e1 = [r for r in report["результаты"] if not r.get("skipped")]
    assert len(e1) == 4  # четыре «красных» теста Э1
    for r in e1:
        assert r["поймано"] and not r["пропущено"]


def test_э2_пропускается_без_llm(ws):
    report = regression.run_regression(ws, llm=False)
    skipped = [r for r in report["результаты"] if r.get("skipped")]
    assert any("Э2" in r["skipped"] for r in skipped)


def test_пропуск_флага_красная(ws):
    regression.add_test(
        ws,
        GoldenTest(
            test_id="ложный_ожидаемый",
            fragment="Он вышел из дома ранним летним утром и неторопливо пошёл к станции по знакомой дороге.",
            context_slice={"focal": "Каширин", "year": 1995},
            expected_flags=["V1.5_стоп_лексика"],  # флага заведомо не будет
        ),
    )
    report = regression.run_regression(ws)
    assert not report["зелёная"]
    assert regression.is_green(ws) is False  # FR-R3: блокирует смену конфигурации
    data = json.loads((ws.regression / "report.json").read_text(encoding="utf-8"))
    assert "ложный_ожидаемый" in data["провалено"]
