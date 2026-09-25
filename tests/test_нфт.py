"""§11 НФТ (NFR-6): производительность измеряется, а не декларируется. Пороги — с запасом к ТЗ
(«Э1 — секунды», «экраны панели на проекте в 500 глав — десятки миллисекунд при тёплом кэше»), чтобы тест
был устойчив на медленных машинах CI, но ловил регрессию на порядок."""

from __future__ import annotations

import time

from konveyer import server, verifier1
from konveyer.config import Config
from konveyer.fsm import ChapterState

SENTENCES = (
    "Каширин нашёл записку утром возле хлебницы. Бумага пахла чужим табаком. Он положил её в карман. "
    "Зоя молчала и ждала у окна, пока чайник не начал стучать крышкой. "
)


def test_э1_три_тысячи_слов_за_секунды(ws):
    raw = SENTENCES * (3000 // len(SENTENCES.split()) + 1)
    assert len(raw.split()) >= 3000
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    checks = verifier1.analyze_text(ws, 1, raw)
    elapsed = time.perf_counter() - t0
    assert checks
    assert elapsed < 5.0, f"Э1 на 3000 слов: {elapsed:.2f} с"


def test_панель_500_глав_с_тёплым_кэшем(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    for n in range(1, 501):
        st = ChapterState(ws, n)
        st.data["состояние"] = "не-начато" if n % 2 else "собрано"
        st._save()
    api = server.PanelAPI(ws, Config(), library)
    try:
        t0 = time.perf_counter()
        state = api.state()
        cold = time.perf_counter() - t0
        assert len(state["chapters"]) == 500
        t0 = time.perf_counter()
        for _ in range(3):
            api.state()
        warm = (time.perf_counter() - t0) / 3
    finally:
        api.stop_lint_worker()
    assert cold < 30.0, f"холодный кэш: {cold:.2f} с"
    assert warm < 1.0, f"тёплый кэш: {warm:.3f} с"
