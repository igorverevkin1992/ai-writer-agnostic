"""Тесты локального сервера панели (этап 3): API, защита, статика, задачи."""

import json
import threading
import time
import urllib.error
import urllib.request
import urllib.parse

import pytest

from konveyer import review, server, verifier2
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def panel(ws, library, monkeypatch):
    """Живой сервер панели на свободном порту; cwd — рабочая область (для команд)."""
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode()) if "json" in r.headers.get("Content-Type", "") else r.read()


def _post(url: str, body: dict, with_header: bool = True):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **({"X-Konveyer-Panel": "1"} if with_header else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _wait_job(base: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = _get(f"{base}/api/job")
        if job and job.get("status") != "выполняется":
            return job
        time.sleep(0.15)
    raise TimeoutError("задача не завершилась")


def test_state_и_статика(panel):
    status, state = _get(f"{panel}/api/state")
    assert status == 200
    assert {b["chapter"] for b in state["briefs"]} == {1, 2, 3, 4, 5, 6}
    assert state["models"]["writer"]

    status, body = _get(f"{panel}/")
    assert status == 200 and (b"<!DOCTYPE html>" in body[:100] or b"<!doctype html>" in body[:100].lower())


def test_пост_без_заголовка_блокирован(panel):
    code, data = _post(f"{panel}/api/command", {"cmd": "export"}, with_header=False)
    assert code == 403 and "X-Konveyer-Panel" in data["error"]


def test_обход_статики_блокирован(panel):
    req = urllib.request.Request(f"{panel}/..%2f..%2f" + urllib.parse.quote("конфиг.yaml"))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read()
            assert b"library_dir" not in body  # отдаётся SPA, не конфиг
    except urllib.error.HTTPError as e:
        assert e.code in (400, 403, 404)


def test_команда_compile_как_задача(panel, ws):
    code, data = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200 and data["job"]["status"] == "выполняется"
    job = _wait_job(panel)
    assert job["status"] == "готово", job["output"]
    assert "Окно собрано" in job["output"]
    assert ChapterState(ws, 1).state == "собрано"
    # вторая задача одновременно не стартует? (после завершения — можно)
    code, data = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200
    _wait_job(panel)


def test_неизвестная_команда(panel):
    code, data = _post(f"{panel}/api/command", {"cmd": "rm-rf"})
    assert code == 400 and "неизвестная команда" in data["error"]


def test_карточка_правки_и_решения(panel, ws):
    st = ChapterState(ws, 2)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    ws.draft_path(2, 1).write_text("Чай остыл. Зоя молчала.", encoding="utf-8")
    st.set_draft(1)
    verifier2.save_flags(ws, 2, [Flag(flag_id="F-009", type="самоволка", quote="Чай остыл.", rule="—", kind="samovolka")])
    review.save_resolutions(ws, 2, [Resolution(flag_id="F-009")])

    # карточка
    _, detail = _get(f"{panel}/api/chapter/2")
    assert detail["state"] == "на-приёмке" and detail["text"].startswith("Чай")
    assert detail["flags"][0]["flag_id"] == "F-009"

    # правки: сохранение + разбор
    code, data = _post(f"{panel}/api/chapter/2/edits", {"text": "БЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\n"})
    assert code == 200 and data["parsed"] == 1
    _, detail = _get(f"{panel}/api/chapter/2")
    assert detail["edits_parsed"][0]["found"] is True

    # решение по самоволке
    code, _ = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-009", "decision": "канонизировать", "registry": "эпистемика"})
    assert code == 200
    assert review.load_resolutions(ws, 2)[0].decision == "канонизировать"
    code, data = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-009", "decision": "сжечь"})
    assert code == 400


def test_accept_охраняется_fsm(panel, ws):
    d = ws.chapter_dir(3)
    d.mkdir(parents=True, exist_ok=True)
    code, data = _post(f"{panel}/api/chapter/3/accept", {})
    assert code == 400  # глава не в «дифф-контроль»


def test_dashboard_отдаётся(panel):
    status, body = _get(f"{panel}/dashboard")
    assert status == 200 and "КОНВЕЙЕР".encode() in bytes(body)
