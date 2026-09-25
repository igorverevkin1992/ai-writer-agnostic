"""Ручной режим без разрывов (§1.3, FR-TK-6, FR-WR-4): CLI write --manual и панель (вставка ответов)."""

import json
import threading
import urllib.request

import pytest
from typer.testing import CliRunner

from konveyer import compiler, server
from konveyer.cli import app
from konveyer.config import Config
from konveyer.fsm import ChapterState

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def panel(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        return e.code, json.loads(e.read().decode())


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read().decode())


# --- CLI: write --manual закрывает разрыв ручного режима


def test_write_manual_регистрирует_черновик(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    compiler.compile_window(ws, library, 1)
    st = ChapterState(ws, 1)
    st.transition("собрано", "compile")

    # без файла — понятная ошибка, состояние не тронуто
    r = runner.invoke(app, ["write", "1", "--manual"])
    assert r.exit_code == 1 and ChapterState(ws, 1).state == "собрано"

    ws.draft_path(1, 1).write_text("Вставленный вручную текст главы.", encoding="utf-8")
    r = runner.invoke(app, ["write", "1", "--manual"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "сгенерировано" and st.draft == 1
    # дальше verify1 доступен штатно
    assert st.data["авто_повторов"] == 0


# --- панель: полный ручной цикл из браузера


def test_панель_manual_draft(panel, ws, library):
    compiler.compile_window(ws, library, 1)
    ChapterState(ws, 1).transition("собрано", "compile")

    code, data = _post(panel, "/api/chapter/1/manual-draft", {"text": "Текст главы из чата модели."})
    assert code == 200 and data["draft"] == 1, data
    st = ChapterState(ws, 1)
    assert st.state == "сгенерировано"
    assert ws.draft_path(1, 1).read_text(encoding="utf-8").startswith("Текст главы")

    code, data = _post(panel, "/api/chapter/1/manual-draft", {"text": "   "})
    assert code == 400  # пустой текст


def test_панель_manual_flags_и_промпт(panel, ws):
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1"]:
        st.transition(s)
    ws.draft_path(1, 1).write_text("Текст для проверки Э2.", encoding="utf-8")
    st.set_draft(1)

    # промпт Э2 строится по требованию, без API; GET ничего не пишет,
    # файл создаёт POST — его и зовёт кнопка копирования в панели
    data = _get(panel, "/api/chapter/1/prompt/verify2")
    assert "ТЕКСТ ГЛАВЫ" in data["text"] and not (ws.chapter_dir(1) / "промпт_э2.md").exists()
    code, data = _post(panel, "/api/chapter/1/prompt/verify2", {})
    assert code == 200 and "ТЕКСТ ГЛАВЫ" in data["text"]
    assert (ws.chapter_dir(1) / "промпт_э2.md").exists()

    # ответ модели с прозой вокруг JSON принимается
    raw = 'Вот результат проверки:\n[{"flag_id": "F-001", "type": "бриф", "quote": "Текст", "rule": "проверка"}]'
    code, data = _post(panel, "/api/chapter/1/manual-flags", {"text": raw})
    assert code == 200 and data["flags"] == 1, data
    assert ChapterState(ws, 1).state == "верифицировано-2"
    saved = json.loads((ws.chapter_dir(1) / "флаги.json").read_text(encoding="utf-8"))
    assert saved[0]["flag_id"] == "F-001"


def test_панель_окно_и_поиск(panel, ws, library):
    compiler.compile_window(ws, library, 1)
    data = _get(panel, "/api/chapter/1/window")
    assert "СТРОГО ЗАПРЕЩЕНО" in data["text"]

    found = _get(panel, "/api/find?q=" + urllib.request.quote("записка"))
    assert "факт" in found and any("M-001" in h["ref"] for h in found["факт"])


def test_панель_run_до_паузы(panel, ws):
    """«Продолжить такт»: без ключей run доходит до write и говорит про ручной режим."""
    import time

    code, data = _post(panel, "/api/command", {"cmd": "run", "chapter": 1})
    assert code == 200
    deadline = time.time() + 15
    while time.time() < deadline:
        job = _get(panel, "/api/job")
        if job.get("status") != "выполняется":
            break
        time.sleep(0.15)
    assert job["status"] == "ручной-режим", job
    assert "Окно собрано" in job["output"]      # compile выполнился
    assert ChapterState(ws, 1).state == "собрано"


def test_изменения_заблокированы_во_время_задачи(ws, library, monkeypatch):
    """ensure_idle: пока задача идёт, accept/rollback/ручной ввод отклоняются."""
    monkeypatch.chdir(ws.root)
    api = server.PanelAPI(ws, Config(), library)
    api.jobs.job = {"name": "write", "chapter": 1, "status": "выполняется", "output": ""}
    with pytest.raises(RuntimeError, match="дождитесь"):
        api.accept(1)
    with pytest.raises(RuntimeError, match="дождитесь"):
        api.manual_draft(1, "текст")
    with pytest.raises(RuntimeError, match="дождитесь"):
        api.run_command("compile", 1)
