"""Каркасы драматургии на демо-проекте: охваты и материалы по канону, деградация без API, приём ответа модели, панель.
(Ожидания по эталону — в tests/профиль_угар/test_миграция.py.)"""

import json
import threading
import urllib.request

import pytest

from konveyer import adapters, circles, exporter, server
from konveyer.config import Config, set_volume
from konveyer.paths import Workspace


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def ws2(ws, library) -> Workspace:
    """Демо, том 2: единственный том демо с таблицей актов и каркасами в каноне."""
    exporter.run_export(library, ws.exports, ws.logs, 2)
    set_volume(ws, 2)
    return Workspace(ws.root, 2)


def test_акты_тома_и_охваты(ws2):
    parts = exporter.load_parts(ws2.exports)
    assert [(p["part"], p["from_chapter"], p["to_chapter"]) for p in parts] == [(1, 1, 2), (2, 3, 4)]
    acts = exporter.load_acts(ws2.exports)
    assert [(a.act, a.title, a.from_chapter, a.to_chapter, a.parts, a.steps) for a in acts] == [
        (1, "Письмо", 1, 2, "I", "1–4"), (2, "Архив", 3, 4, "II", "5–8")]
    assert len(circles.targets(ws2, "всё")) == 1 + 2 + 4
    assert circles.targets(ws2, "части") == circles.targets(ws2, "акты")  # старое имя охвата — синоним
    assert circles.targets(ws2, "главы", chapter=3) == [("глава", 3)]
    with pytest.raises(ValueError, match="охват"):
        circles.targets(ws2, "сцены")
    frame = circles.frame_for_chapter(exporter.load_circles(ws2.exports), acts, 4)
    assert frame["act"].act == 2 and frame["act"].title == "Архив" and frame["chapter"] is None
    assert [s.n for s in frame["book_steps"]] == [7, 8] and frame["act_steps"] == []  # каркас акта 2 в канон не внесён


def test_материалы_не_раскрывают_лишнего(ws2):
    title, book = circles.build_material(ws2, "книга")
    assert title == "Книга (том целиком)" and "## Акты тома" in book and "Акт 2 «Архив» — гл. 3–4: шаги 5–8" in book
    assert "гл. 4 · 1 июня 1996 · фокал Каширин · участники: Зоя, Гуляев" in book and "## Реестр тайн" in book
    assert "тайна происхождения Зои" in book  # аналитик тома видит реестр тайн целиком
    title, act = circles.build_material(ws2, "акт", 1)
    assert title == "Акт 1 «Письмо»" and "гл. 1 ·" in act and "гл. 2 ·" in act and "гл. 3 ·" not in act
    assert "Шаги каркаса тома, за которые отвечает акт: 1–4" in act and "## Каркас уровня выше" in act
    title, ch = circles.build_material(ws2, "глава", 4)
    assert title == "Глава 4" and "Архив депо достают из ямы" in ch
    assert "[M-105] архив депо в яме гаража №14" in ch and "M-103" not in ch  # знание фокала, не чужое
    assert "## Каркас уровня выше" in ch and "Том: шаг 7 «Возвращение»" in ch
    with pytest.raises(FileNotFoundError, match="акта 9"):
        circles.build_material(ws2, "акт", 9)


def test_без_api_сохраняются_промпты(ws2):
    result = circles.run(ws2, Config(), "акты")
    assert result["ручной_режим"] and len(result["промпты"]) == 2 and not result["готово"]
    prompt = (ws2.root / "драматургия" / "промпты" / "акт_1.md").read_text(encoding="utf-8")
    assert "<!-- system -->" in prompt and "«Гаражи»" in prompt and "# Акт 1 «Письмо»" in prompt


def test_генерация_через_подменённый_адаптер(ws2, monkeypatch):
    def fake(system, user, mc, api, logs_dir, *, role, chapter=None):
        assert "кругу истории" in system and "Книга (том целиком)" in user
        return json.dumps({
            "title": "т", "summary": "суть",
            "steps": [{"n": i, "name": f"шаг {i}", "text": "…", "chapters": "гл. 1"} for i in range(1, 9)],
            "weak_spot": "нет",
        }, ensure_ascii=False)

    monkeypatch.setattr(adapters, "call_anthropic", fake)
    result = circles.run(ws2, Config(), "книга")
    assert len(result["готово"]) == 1 and not result["ручной_режим"] and not result["сбои"]
    md = (ws2.root / "драматургия" / "книга.md").read_text(encoding="utf-8")
    assert md.startswith("# Каркас · т") and "## 8. шаг 8" in md
    # повторный запуск без --заново ничего не делает; черновик отличается от канона тома 2
    assert circles.run(ws2, Config(), "книга")["готово"] == []
    assert circles.list_circles(ws2)[0]["scope"] == "книга" and circles.canon_status(ws2) == {"книга": "отличается от канона"}


def test_ручной_приём_и_панель(ws2, library, monkeypatch):
    monkeypatch.chdir(ws2.root)
    raw = 'Вот круг:\n{"steps": [{"n": 1, "name": "Ты", "text": "Каширин у окна"}], "summary": "письмо"}'
    path = circles.accept_manual(ws2, "глава", 3, raw)
    assert path.name == "глава_03.md" and "Глава 3" in path.read_text(encoding="utf-8")

    srv = server.serve(ws2, Config(), library, port=0, watch=False)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/circles", timeout=5) as r:
            data = json.loads(r.read().decode())
        assert data["parts"][0]["title"] == "Письмо" and len(data["acts"]) == 2 and data["in_canon"] == 3
        assert data["circles"][0]["key"] == 3 and data["canon_status"] == {"глава_03": "не в каноне"}
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/command", method="POST",
            data=json.dumps({"cmd": "story-circles", "params": {"scope": "книга"}}).encode(),
            headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read().decode())["job"]["name"] == "story-circles"
    finally:
        srv.shutdown()
        srv.server_close()
