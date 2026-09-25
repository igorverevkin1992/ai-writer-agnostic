"""Цикл автора в панели (FR-UI-*, FR-AD-7, FR-CT-2): серверная часть.

Каждый тест ловит именно ту ошибку, что описана в АУДИТ_2.md.
"""

import http.client
import json
import threading
import time

import pytest

from konveyer import cancel, review, server, verifier2
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def panel(ws, library, monkeypatch):
    """Живой сервер на свободном порту; возвращает (порт, PanelAPI) — задачи можно подменять напрямую."""
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, srv.api  # type: ignore[attr-defined]
    srv.shutdown()
    srv.server_close()


def _req(port: int, method: str, path: str, body: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    if method == "POST":
        hdrs["X-Konveyer-Panel"] = "1"
    conn.request(method, path, body=json.dumps(body if body is not None else {}).encode() if method == "POST" else None,
                 headers=hdrs)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    ctype = r.getheader("Content-Type") or ""
    return r.status, (json.loads(raw.decode()) if "json" in ctype else raw)


def _wait_idle(api: server.PanelAPI, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not api.jobs.busy:
            return api.jobs.full()
        time.sleep(0.05)
    raise TimeoutError("задача не завершилась")


# ----------------------------------------------------------- 5.4 связь и ошибки


def test_неизвестный_api_путь_404_json(panel):
    """Раньше неизвестный GET /api/… отдавал index.html с 200 → r.json() падал, «{}» считался успехом."""
    port, _ = panel
    code, data = _req(port, "GET", "/api/no-such-path")
    assert code == 404 and isinstance(data, dict) and "нет такого пути" in data["error"]
    code, data = _req(port, "GET", "/api/chapter/1/nope")
    assert code == 404 and isinstance(data, dict)
    # SPA-маршруты вне /api по-прежнему получают index.html
    code, body = _req(port, "GET", "/some/page")
    assert code == 200 and b"<div id=\"root\">" in body


def test_ошибка_без_абсолютного_пути(panel, ws):
    """FileNotFoundError с полным путём → 404 и текст без пути машины автора."""
    port, _ = panel
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    code, data = _req(port, "GET", "/api/chapter/1/draft/99")
    assert code == 404, data
    assert str(ws.root) not in data["error"] and "Errno" not in data["error"]
    assert data["error"] == "нет черновика 99 у главы 1"
    # чужое исключение с полным путём — санитизируется словами
    assert server._sanitize(f"нет {ws.root / 'главы' / 'x'}", panel[1]) == "нет рабочая область/главы/x"
    # документ канона, которого нет — тоже 404 без путей
    code, data = _req(port, "GET", "/api/canon/doc?path=none.md")
    assert code == 404 and str(ws.root) not in data["error"]


def test_занят_задачей_423(panel):
    """Пока идёт задача, изменяющие запросы получают 423 «сервер занят», а не 400 «ошибка ввода»."""
    port, api = panel
    release = threading.Event()
    api.jobs.start("write", 1, lambda: release.wait(5))
    try:
        code, data = _req(port, "POST", "/api/command", {"cmd": "compile", "chapter": 1})
        assert code == 423 and "дождитесь" in data["error"]
        code, data = _req(port, "POST", "/api/chapter/1/edits", {"text": ""})
        assert code == 423
        # GET, читающий под замком (сохранение промпта — POST), тоже 423, а не 500
        code, data = _req(port, "POST", "/api/chapter/1/prompt/verify2", {})
        assert code == 423
    finally:
        release.set()
    _wait_idle(api)


def test_500_пишет_трейсбек_в_лог(panel, ws, monkeypatch):
    port, api = panel

    def boom():
        raise KeyError("сломалось")

    monkeypatch.setattr(api, "api_log", boom)
    code, data = _req(port, "GET", "/api/log")
    assert code == 500 and "внутренняя ошибка" in data["error"] and "панель.log" in data["error"]
    log = (ws.logs / "панель.log").read_text(encoding="utf-8")
    assert "KeyError" in log and "GET /api/log" in log


# ----------------------------------------------------------- 5.5 задача: прогресс и отмена


def test_прогресс_из_строк_n_из_m(panel):
    port, api = panel

    def job():
        print("круг книги [1/51]")
        print("акт 1 [2/51] …")
        print("без счётчика")

    api.jobs.start("story-circles", None, job)
    _wait_idle(api)
    _, state = _req(port, "GET", "/api/state")
    assert state["job"]["progress"] == [2, 51]
    assert state["job"]["status"] == "готово"


def test_отмена_задачи_через_api(panel):
    """POST /api/job/cancel ставит флаг; задача, дошедшая до cancel.check(), завершается «остановлено»."""
    port, api = panel
    reached = threading.Event()

    def job():
        for i in range(200):
            print(f"вызов [{i + 1}/200]")
            reached.set()
            cancel.check("между вызовами")
            time.sleep(0.02)

    api.jobs.start("story-circles", None, job)
    assert reached.wait(2)
    code, data = _req(port, "POST", "/api/job/cancel", {})
    assert code == 200 and data["job"]["cancel_requested"] is True
    job_final = _wait_idle(api)
    assert job_final["status"] == "остановлено"
    assert "остановлено автором" in job_final["output"]
    assert not cancel.requested()  # флаг не переживает задачу
    # останавливать нечего → 400
    code, data = _req(port, "POST", "/api/job/cancel", {})
    assert code == 400 and "нет выполняющейся" in data["error"]


def test_отмена_через_friendly_тоже_остановлено(panel):
    """Задача завершилась ошибкой шага (StepError, код 1) уже после запроса остановки —
    по флагу cancel_requested сервер всё равно показывает «остановлено», а не «ошибка»."""
    from konveyer.steps import StepError

    port, api = panel
    reached = threading.Event()

    def job():
        reached.set()
        while not cancel.requested():
            time.sleep(0.02)
        cancel.clear()
        raise StepError("остановлено между вызовами")  # ожидаемая ошибка шага, код 1

    api.jobs.start("write", 1, job)
    assert reached.wait(2)
    code, _ = _req(port, "POST", "/api/job/cancel", {})
    assert code == 200
    assert _wait_idle(api)["status"] == "остановлено"


def test_флаг_отмены_не_переживает_задачу(panel):
    """Задача завершилась, не дойдя до точки отмены — следующая не должна упасть на первом check()."""
    _, api = panel
    api.jobs.start("compile", 1, lambda: time.sleep(0.05))
    api.jobs.cancel()
    _wait_idle(api)
    assert not cancel.requested()
    api.jobs.start("compile", 1, lambda: cancel.check())
    assert _wait_idle(api)["status"] == "готово"


# ----------------------------------------------------------- 5.6 «Вычеркнуть все»


def _chapter_with_samovolki(ws, n: int = 2):
    st = ChapterState(ws, n)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    ws.draft_path(n, 1).write_text("Чай остыл. Зоя молчала. Дверь скрипнула.", encoding="utf-8")
    st.set_draft(1)
    verifier2.save_flags(ws, n, [
        Flag(flag_id="F-001", type="самоволка", quote="Чай остыл.", rule="—", kind="samovolka"),
        Flag(flag_id="F-002", type="самоволка", quote="Зоя молчала.", rule="—", kind="samovolka"),
        Flag(flag_id="F-003", type="самоволка", quote="Дверь скрипнула.", rule="—", kind="samovolka"),
    ])
    review.save_resolutions(ws, n, [
        Resolution(flag_id="F-001", decision="канонизировать", target_registry="эпистемика"),
        Resolution(flag_id="F-002"),
        Resolution(flag_id="F-003"),
    ])


def test_resolve_all_вычёркивает_только_нерешённые(panel, ws):
    port, _ = panel
    _chapter_with_samovolki(ws)
    code, data = _req(port, "POST", "/api/chapter/2/resolve-all", {"decision": "вычеркнуть"})
    assert code == 200 and data["resolved"] == 2 and data["flag_ids"] == ["F-002", "F-003"]
    rs = {r.flag_id: r for r in review.load_resolutions(ws, 2)}
    assert rs["F-001"].decision == "канонизировать" and rs["F-001"].target_registry == "эпистемика"
    assert rs["F-002"].decision == "вычеркнуть" and rs["F-002"].target_registry is None
    assert rs["F-003"].decision == "вычеркнуть"
    assert review.unresolved_samovolki(ws, 2) == []
    # повтор — нечего решать, файл не трогается
    code, data = _req(port, "POST", "/api/chapter/2/resolve-all", {"decision": "вычеркнуть"})
    assert code == 200 and data["resolved"] == 0


def test_resolve_all_проверяет_решение(panel, ws):
    port, _ = panel
    _chapter_with_samovolki(ws)
    code, data = _req(port, "POST", "/api/chapter/2/resolve-all", {"decision": "сжечь"})
    assert code == 400 and "решение" in data["error"]
    code, data = _req(port, "POST", "/api/chapter/2/resolve-all", {"decision": "канонизировать", "registry": "9.9"})
    assert code == 400 and "реестр" in data["error"]
    # «отклонить» всем сразу нельзя: причина нужна по каждому флагу (FR-RV-2)
    code, data = _req(port, "POST", "/api/chapter/2/resolve-all", {"decision": "отклонить"})
    assert code == 400 and "вычеркнуть" in data["error"]
    assert len(review.unresolved_samovolki(ws, 2)) == 2


# ----------------------------------------------------------- 5.7 «сегодня: N мин автора»


def test_author_today_min_в_state(panel, ws):
    from datetime import datetime, timedelta, timezone

    port, _ = panel
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки"]:
        st.transition(s)
    # переписываем историю: 5 минут авторской паузы «на-приёмке» сегодня, 30 минут — позавчера.
    # «Сейчас» — полдень местной даты: интервал не уедет во «вчера» при запуске около полуночи
    now = datetime.now(timezone.utc).astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    hist = st.data["история"]
    hist[-2]["время"] = (now - timedelta(minutes=5)).isoformat()   # в: на-приёмке
    hist[-1]["время"] = now.isoformat()                               # в: правки
    yesterday = now - timedelta(days=2)
    hist[0]["время"] = (yesterday - timedelta(minutes=30)).isoformat()
    hist[1]["время"] = yesterday.isoformat()
    st._save()
    _, state = _req(port, "GET", "/api/state")
    assert 4.9 <= state["author_today_min"] <= 5.1, state["author_today_min"]
