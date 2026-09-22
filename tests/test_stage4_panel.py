"""Этап 4 аудита — панель и сервер: 4.2–4.4, 4.6 (серверная часть), 5.3–5.6.

Каждый тест ловит именно ту ошибку, что описана в АУДИТ.md.
"""

import http.client
import json
import re
import threading
import time

import pytest

from konveyer import htmlreview, review, server, verifier2
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution


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
    yield port
    srv.shutdown()
    srv.server_close()


def _req(port: int, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    """Сырой HTTP-запрос: Host/Origin задаются явно (urllib их подменяет)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    if method == "POST":
        hdrs["X-Konveyer-Panel"] = "1"
    hdrs.update(headers or {})
    conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=hdrs)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    ctype = r.getheader("Content-Type") or ""
    return r.status, (json.loads(raw.decode()) if "json" in ctype else raw)


def _wait_job(port: int, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = _req(port, "GET", "/api/job")
        if job and job.get("status") != "выполняется":
            return job
        time.sleep(0.1)
    raise TimeoutError("задача не завершилась")


# ----------------------------------------------------------- 4.2 Host / Origin


def test_чужой_host_403(panel):
    """DNS-rebinding: чужое имя в Host → 403 и для GET, и для POST."""
    code, data = _req(panel, "GET", "/api/state", headers={"Host": "evil.example:80"})
    assert code == 403 and "Host" in data["error"]
    code, data = _req(panel, "POST", "/api/command", {"cmd": "export"}, headers={"Host": f"evil.example:{panel}"})
    assert code == 403
    # без Host вообще — тоже 403
    code, _ = _req(panel, "GET", "/api/state", headers={"Host": ""})
    assert code == 403
    # localhost:<порт> — допустим
    code, _ = _req(panel, "GET", "/api/state", headers={"Host": f"localhost:{panel}"})
    assert code == 200


def test_чужой_origin_403(panel):
    code, data = _req(panel, "POST", "/api/command", {"cmd": "export"}, headers={"Origin": "http://evil.example"})
    assert code == 403 and "Origin" in data["error"]
    # локальный Origin проходит (команда стартует)
    code, data = _req(panel, "POST", "/api/command", {"cmd": "export"}, headers={"Origin": f"http://localhost:{panel}"})
    assert code == 200, data
    _wait_job(panel)


# ----------------------------------------------------------- 4.3 GET без побочных эффектов


def test_get_промпт_не_пишет_файл_а_post_пишет(panel, ws):
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1"]:
        st.transition(s)
    ws.draft_path(1, 1).write_text("Текст для проверки Э2.", encoding="utf-8")
    st.set_draft(1)

    code, data = _req(panel, "GET", "/api/chapter/1/prompt/verify2")
    assert code == 200 and "ТЕКСТ ГЛАВЫ" in data["text"]
    assert not (ws.chapter_dir(1) / "промпт_э2.md").exists()

    code, data = _req(panel, "POST", "/api/chapter/1/prompt/verify2", {})
    assert code == 200 and data["saved"].endswith("промпт_э2.md")
    assert (ws.chapter_dir(1) / "промпт_э2.md").read_text(encoding="utf-8") == data["text"]

    # неизвестный вид промпта — 400, а не 500
    code, data = _req(panel, "GET", "/api/chapter/1/prompt/rm_rf")
    assert code == 400 and "неизвестный промпт" in data["error"]


def test_dashboard_по_get_не_пишет_файл(panel, ws):
    code, body = _req(panel, "GET", "/dashboard")
    assert code == 200 and "КОНВЕЙЕР".encode() in body
    assert not (ws.root / "дашборд.html").exists()


# ----------------------------------------------------------- 4.4 одна блокировка


def test_exclusive_не_ждёт_а_отклоняет():
    jobs = server.JobRunner()
    with jobs.exclusive():
        with pytest.raises(RuntimeError, match="дождитесь"), jobs.exclusive():
            pass
        with pytest.raises(RuntimeError, match="дождитесь"):
            jobs.start("compile", 1, lambda: None)
    # после выхода замок свободен
    with jobs.exclusive():
        pass


def test_синхронная_операция_блокирует_задачу_и_наоборот(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    api = server.PanelAPI(ws, Config(), library)
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(5)

    api.jobs.start("write", 1, slow)
    started.wait(2)
    try:
        for op in (lambda: api.accept(1), lambda: api.manual_draft(1, "текст"), lambda: api.save_edits(1, ""),
                   lambda: api.resolve(1, "F-1", "вычеркнуть", None), lambda: api.save_canon_batch(1, ""),
                   lambda: api.manual_circle("книга", None, "{}"), lambda: api.run_command("compile", 1)):
            with pytest.raises(RuntimeError, match="дождитесь"):
                op()
    finally:
        release.set()
    deadline = time.time() + 5
    while api.jobs.busy and time.time() < deadline:
        time.sleep(0.05)
    assert not api.jobs.busy
    # замок отпущен: синхронная операция снова проходит до FSM-проверки
    with pytest.raises(RuntimeError, match="приёмка|дифф-контроль|состояни"):
        api.accept(1)


def test_двойная_отправка_manual_draft_даёт_один_черновик(panel, ws, library, monkeypatch):
    """5.4/4.4: два одновременных POST manual-draft → второй 400 «дождитесь», черновик_2 не появляется."""
    from konveyer import compiler
    from konveyer.steps import tact

    compiler.compile_window(ws, library, 1)
    ChapterState(ws, 1).transition("собрано", "compile")

    orig = tact.write

    def slow_write(chapter, manual=False):
        time.sleep(0.7)
        return orig(chapter, manual=manual)

    monkeypatch.setattr(tact, "write", slow_write)

    results: list[tuple[int, dict]] = []

    def send():
        results.append(_req(panel, "POST", "/api/chapter/1/manual-draft", {"text": "Текст главы из чата."}))

    t1 = threading.Thread(target=send)
    t2 = threading.Thread(target=send)
    t1.start()
    time.sleep(0.15)
    t2.start()
    t1.join(10)
    t2.join(10)

    codes = sorted(c for c, _ in results)
    assert codes == [200, 423], results
    rejected = next(d for c, d in results if c == 423)
    assert "дождитесь" in rejected["error"]
    assert ws.draft_path(1, 1).exists() and not ws.draft_path(1, 2).exists()
    assert ChapterState(ws, 1).state == "сгенерировано"


# ----------------------------------------------------------- 4.6 валидация и лимиты


def test_тело_больше_лимита_413(panel):
    conn = http.client.HTTPConnection("127.0.0.1", panel, timeout=5)
    conn.putrequest("POST", "/api/chapter/1/edits")
    conn.putheader("Host", f"127.0.0.1:{panel}")
    conn.putheader("X-Konveyer-Panel", "1")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(server.MAX_BODY + 1))
    conn.endheaders()  # тело не шлём — сервер обязан ответить, не читая его
    r = conn.getresponse()
    assert r.status == 413
    assert "МБ" in json.loads(r.read().decode())["error"]
    conn.close()


def test_manual_draft_проверяет_состояние_до_записи(panel, ws):
    """Глава «не-начато»: файл черновика не должен появиться."""
    code, data = _req(panel, "POST", "/api/chapter/4/manual-draft", {"text": "Текст."})
    assert code == 400 and "не принимается" in data["error"]
    assert not ws.draft_path(4, 1).exists()


def test_manual_flags_проверяет_состояние_до_записи(panel, ws):
    st = ChapterState(ws, 1)
    st.transition("собрано")
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    verifier2.save_flags(ws, 1, [Flag(flag_id="F-OLD", type="бриф", quote="старое", rule="—")])
    raw = '[{"flag_id": "F-NEW", "type": "бриф", "quote": "новое", "rule": "—"}]'
    code, data = _req(panel, "POST", "/api/chapter/1/manual-flags", {"text": raw})
    assert code == 400 and "не принимаются" in data["error"]
    # флаги.json не затёрт
    assert verifier2.load_flags(ws, 1)[0].flag_id == "F-OLD"


def test_валидация_scope_key_registry(panel, ws):
    bad = [
        {"scope": "том", "key": None, "text": "{}"},
        {"scope": "акт", "key": "abc", "text": "{}"},
        {"scope": "акт", "key": None, "text": "{}"},
        {"scope": "книга", "key": 1, "text": "{}"},
        {"scope": "книга", "key": None, "text": "  "},
    ]
    for body in bad:
        code, data = _req(panel, "POST", "/api/circles/manual", body)
        assert code == 400, body
    assert not (ws.root / "драматургия").exists() or not list((ws.root / "драматургия").glob("*.json"))

    st = ChapterState(ws, 2)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    verifier2.save_flags(ws, 2, [Flag(flag_id="F-009", type="самоволка", quote="Чай.", rule="—", kind="samovolka")])
    review.save_resolutions(ws, 2, [Resolution(flag_id="F-009")])
    code, data = _req(panel, "POST", "/api/chapter/2/resolve",
                      {"flag_id": "F-009", "decision": "канонизировать", "registry": "9.9"})
    assert code == 400 and "реестр" in data["error"]
    assert review.load_resolutions(ws, 2)[0].decision is None


def test_тело_не_объект_400(panel):
    conn = http.client.HTTPConnection("127.0.0.1", panel, timeout=5)
    conn.request("POST", "/api/chapter/1/edits", body=b"[1, 2]",
                 headers={"Host": f"127.0.0.1:{panel}", "X-Konveyer-Panel": "1", "Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 400
    conn.close()


def test_статика_за_симлинком(monkeypatch, tmp_path):
    """4.6/5.7: konveyer/data за симлинком — resolve() корня, иначе 403 на всё."""
    real = server._static_root()
    link = tmp_path / "линк"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as e:  # Windows без режима разработчика: симлинки требуют прав (WinError 1314)
        pytest.skip(f"симлинки недоступны: {e}")
    monkeypatch.setattr(server, "_static_root", lambda: link.resolve())
    assert server._static_root() == real  # оба resolve'нуты → parents совпадают


# ----------------------------------------------------------- 5.5 /api/state без полного лога


def test_state_несёт_хвост_лога_а_не_весь(panel, ws):
    code, data = _req(panel, "POST", "/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200
    job = data["job"]
    assert "output" not in job and "output_tail" in job and "output_len" in job
    full = _wait_job(panel)
    _, state = _req(panel, "GET", "/api/state")
    sj = state["job"]
    assert "output" not in sj
    assert sj["output_len"] == len(full["output"]) > 0
    assert sj["output_tail"] == full["output"][-server.OUTPUT_TAIL:]
    assert sj["started"] == full["started"] and sj["status"] == full["status"] == "готово"


# ----------------------------------------------------------- 5.6 diff-check как авторская правка


def test_diff_check_author_в_командах(ws, library, monkeypatch):
    from konveyer.steps import tact

    monkeypatch.chdir(ws.root)
    assert "diff-check-author" in server.COMMANDS
    calls: list[tuple] = []
    monkeypatch.setattr(tact, "diff_check", lambda chapter, author_fix=False: calls.append((chapter, author_fix)))
    api = server.PanelAPI(ws, Config(), library)
    api.run_command("diff-check-author", 7)
    deadline = time.time() + 5
    while api.jobs.busy and time.time() < deadline:
        time.sleep(0.05)
    assert calls == [(7, True)]
    api.run_command("diff-check", 7)
    while api.jobs.busy and time.time() < deadline:
        time.sleep(0.05)
    assert calls[-1] == (7, False)


# ----------------------------------------------------------- 5.3 подсветка пересекающихся цитат


def _marks_balanced(html_text: str) -> None:
    assert html_text.count("<mark") == html_text.count("</mark>")
    # внутри атрибутов нет тегов
    assert not re.search(r'title="[^"]*<', html_text)
    # снятие тегов возвращает ровно экранированный текст (ничего не потеряно и не удвоено)


def _strip(html_text: str) -> str:
    return re.sub(r"</?mark[^>]*>", "", html_text)


def test_highlight_пересечения_и_вложения():
    raw = "Чай остыл давно. Зоя <б> молчала и ждала. Потом ушла."
    marks = [
        ("остыл давно. Зоя", "FLAG", "a-1", "пересекается с первой"),
        ("Чай остыл давно.", "samovolka", "a-F-1", "самоволка"),
        ("Зоя <б> молчала", "BRAK", "a-2", "начинается внутри первой"),
        ("молчала", "FLAG", "a-3", "вложена в предыдущую"),
        ("Потом ушла.", "violation", "a-F-2", "отдельная"),
        ("Чай остыл давно.", "FLAG", "a-F-1", "повтор якоря"),
        ("нет такого", "FLAG", "a-9", "не найдена"),
    ]
    out = htmlreview.highlight(raw, marks)
    _marks_balanced(out)
    assert _strip(out) == htmlreview._esc(raw)
    ids = re.findall(r'<mark id="([^"]+)"', out)
    # a-F-1 начинается раньше a-1 → a-1 (пересечение) отброшена; a-2 начинается после
    # конца a-F-1 → принята; a-3 вложена в a-2 → отброшена; повтор якоря и ненайденная — нет
    assert ids == ["a-F-1", "a-2", "a-F-2"]
    assert "&lt;б&gt;" in out


def test_highlight_цитата_внутри_тултипа():
    """Старый код искал цитату в уже размеченном HTML и попадал внутрь title= предыдущей."""
    raw = "Зоя молчала. Чай остыл."
    marks = [
        ("Зоя молчала.", "samovolka", "a-F-1", "F-1: правило «Чай остыл.» нарушено"),
        ("Чай остыл.", "FLAG", "a-F-2", "F-2"),
    ]
    out = htmlreview.highlight(raw, marks)
    _marks_balanced(out)
    assert _strip(out) == htmlreview._esc(raw)
    assert out.count("<mark") == 2
    assert 'title="F-1: правило «Чай остыл.» нарушено"' in out
    assert '<mark id="a-F-2" class="m-FLAG" title="F-2">Чай остыл.</mark>' in out


def test_highlight_экранирует_атрибуты():
    raw = "Слово."
    out = htmlreview.highlight(raw, [("Слово", 'x" onmouseover="alert(1)', 'a-"><img src=x onerror=alert(1)>', 'tt "q" <b>')])
    _marks_balanced(out)
    assert "onerror=" not in out.replace("&gt;", "").replace("&lt;", "") or "<img" not in out
    assert 'id="a-&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in out


def test_review_html_с_пересекающимися_цитатами(ws):
    """приёмка.html: три пересекающиеся цитаты Э1/Э2 → корректная разметка, текст не теряется."""
    from konveyer.schemas import CheckResult, Verdict
    import json as _json

    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2"]:
        st.transition(s)
    raw = "Чай остыл давно. Зоя молчала и ждала."
    ws.draft_path(1, 1).write_text(raw, encoding="utf-8")
    st.set_draft(1)
    verdict = Verdict(chapter=1, draft=1, checks=[
        CheckResult(check_id="1.2", status="FLAG", threshold="≤1", actual="2", quotes=["остыл давно. Зоя"], rule_source="02 §5"),
    ])
    (ws.chapter_dir(1) / "вердикт.json").write_text(_json.dumps(verdict.model_dump(), ensure_ascii=False), encoding="utf-8")
    verifier2.save_flags(ws, 1, [
        Flag(flag_id="F-001", type="самоволка", quote="Чай остыл давно.", rule="Чай остыл давно. — в брифе нет", kind="samovolka"),
        Flag(flag_id="F-002", type="бриф", quote="Зоя молчала", rule="—"),
    ])
    html_text = htmlreview.build_review_html(ws, 1, 1).read_text(encoding="utf-8")
    prose = html_text.split('<div class="prose">')[1].split("</div>")[0]
    _marks_balanced(prose)
    assert _strip(prose) == htmlreview._esc(raw)
    assert '<mark id="a-F-001"' in prose and '<mark id="a-F-002"' in prose
    assert 'id="a-1.2-0"' not in prose  # пересекается с F-001 → отброшена
