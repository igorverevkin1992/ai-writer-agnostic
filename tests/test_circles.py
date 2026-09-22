"""Круги истории: материалы по канону, деградация без API, приём ответа модели, панель."""

import json
import threading
import urllib.request

import pytest

from konveyer import adapters, circles, exporter, guard, server
from konveyer.config import Config
from konveyer.paths import Workspace

REPO = __import__("pathlib").Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"

pytestmark = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def real(tmp_path):
    (tmp_path / "конфиг.yaml").write_text(f'library_dir: "{LIBRARY.as_posix()}"\n', encoding="utf-8")
    ws = Workspace(tmp_path)
    guard.set_library_dir(LIBRARY)
    exporter.run_export(LIBRARY, ws.exports, ws.logs)
    return ws


def test_акты_тома_и_охваты(real):
    parts = exporter.load_parts(real.exports)
    assert [(p["part"], p["from_chapter"], p["to_chapter"]) for p in parts] == [
        (1, 1, 9), (2, 10, 18), (3, 19, 26), (4, 27, 36), (5, 37, 46)
    ]
    acts = exporter.load_acts(real.exports)
    assert [(a.act, a.from_chapter, a.to_chapter) for a in acts] == [(1, 1, 9), (2, 10, 18), (3, 19, 36), (4, 37, 46)]
    assert acts[2].parts == "III–IV" and "Обретение" in acts[2].steps
    assert len(circles.targets(real, "всё")) == 1 + 4 + 46
    assert circles.targets(real, "части") == circles.targets(real, "акты")  # старое имя охвата — синоним
    assert circles.targets(real, "главы", chapter=5) == [("глава", 5)]
    frame = circles.frame_for_chapter([], acts, 20)
    assert frame["act"].act == 3 and frame["act"].title == "ТРАУР · КОММЕРСАНТ"


def test_материалы_не_раскрывают_лишнего(real):
    title, book = circles.build_material(real, "книга")
    assert "МОКРОЕ ДЕЛО" in book and "гл. 46" in book and "Реестр тайн" in book
    assert "## Акты тома" in book and "Акт 3 «ТРАУР · КОММЕРСАНТ» — гл. 19–36" in book
    title, act = circles.build_material(real, "акт", 1)
    assert "Акт 1" in title and "гл. 9" in act and "гл. 10" not in act and "Шаги круга тома" in act
    title, act3 = circles.build_material(real, "акт", 3)
    assert "гл. 19" in act3 and "гл. 36" in act3 and "гл. 37" not in act3
    title, ch = circles.build_material(real, "глава", 5)
    assert "обыск стола" in ch and "М-04" in ch and "М-06" not in ch  # знание фокала, не тайны


def test_без_api_сохраняются_промпты(real):
    result = circles.run(real, Config(), "акты")
    assert result["ручной_режим"] and len(result["промпты"]) == 4 and not result["готово"]
    assert (real.root / "драматургия" / "промпты" / "акт_1.md").exists()


def test_генерация_через_подменённый_адаптер(real, monkeypatch):
    def fake(system, user, mc, api, logs_dir, *, role, chapter=None):
        assert "восемь шагов" in system.lower() or "кругу истории" in system
        return json.dumps({
            "title": "т", "summary": "суть",
            "steps": [{"n": i, "name": f"шаг {i}", "text": "…", "chapters": "гл. 1"} for i in range(1, 9)],
            "weak_spot": "нет",
        }, ensure_ascii=False)

    monkeypatch.setattr(adapters, "call_anthropic", fake)
    result = circles.run(real, Config(), "книга")
    assert len(result["готово"]) == 1 and not result["ручной_режим"]
    md = (real.root / "драматургия" / "книга.md").read_text(encoding="utf-8")
    assert "# Круг истории" in md and "## 8. шаг 8" in md
    # повторный запуск без --заново ничего не делает
    assert circles.run(real, Config(), "книга")["готово"] == []
    assert circles.list_circles(real)[0]["scope"] == "книга"


def test_ручной_приём_и_панель(real, monkeypatch):
    monkeypatch.chdir(real.root)
    raw = 'Вот круг:\n{"steps": [{"n": 1, "name": "Ты", "text": "Степан у стола"}], "summary": "обыск"}'
    path = circles.accept_manual(real, "глава", 5, raw)
    assert path.name == "глава_05.md" and "Глава 5" in path.read_text(encoding="utf-8")

    srv = server.serve(real, Config(), LIBRARY, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/circles", timeout=5) as r:
            data = json.loads(r.read().decode())
        assert data["parts"][0]["title"] == "МОКРОЕ ДЕЛО" and len(data["acts"]) == 4
        assert data["circles"][0]["key"] == 5
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/command", method="POST",
            data=json.dumps({"cmd": "story-circles", "params": {"scope": "книга"}}).encode(),
            headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read().decode())["job"]["name"] == "story-circles"
    finally:
        srv.shutdown(); srv.server_close()
