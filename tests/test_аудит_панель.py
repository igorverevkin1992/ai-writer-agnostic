# ruff: noqa: F811 — фикстура `panel` импортируется из tests.test_panel и передаётся тестам параметром
"""Аудит кластера «панель»: сервер и API (FR-PN-*, FR-AP-2, FR-RV-2, FR-SC-6/7, П-1, П-5).

Каждый тест воспроизводит находку аудита: реестры для канонизации приходят с сервера, отклонение флага
с причиной, кириллица в пути и запросе, типы полей тела, атомарность сохранений, 404 для глав вне плана,
деградация при повреждённой главе, остановка потоков, импорт из самого проекта, вывод задач без путей,
методы кроме GET/POST, история документа, версии правок и пакета, символические ссылки, новые виды.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from konveyer import review, server, verifier2
from konveyer.canonist import registries
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution
from tests.test_panel import _post, _wait_job, panel  # noqa: F401 — живой сервер


def _get(url: str):
    """GET, который возвращает (код, тело) и для ошибок (в отличие от tests.test_panel._get)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode()) if "json" in r.headers.get("Content-Type", "") else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _raw(base: str, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    """Сырой запрос через сокет: путь уходит в UTF-8 как есть (без percent-кодирования), метод любой."""
    import socket

    port = int(base.rsplit(":", 1)[1])
    hdrs = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Content-Length": str(len(body or b""))}
    if method == "POST":
        hdrs["X-Konveyer-Panel"] = "1"
    hdrs.update(headers or {})
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(head.encode("utf-8") + (body or b""))
        r = http.client.HTTPResponse(sock, method=method)
        r.begin()
        raw = r.read()
        hdr = dict(r.getheaders())
    ctype = hdr.get("Content-Type", "")
    return r.status, (json.loads(raw.decode()) if "json" in ctype else raw), hdr


def _chapter_on_review(ws, n: int = 2, flag_id: str = "F-009") -> None:
    st = ChapterState(ws, n)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    ws.draft_path(n, 1).write_text("Чай остыл. Зоя молчала.", encoding="utf-8")
    st.set_draft(1)
    verifier2.save_flags(ws, n, [
        Flag(flag_id=flag_id, type="самоволка", quote="Чай остыл.", rule="—", kind="samovolka"),
        Flag(flag_id="F-010", type="бриф", quote="Зоя молчала.", rule="в брифе нет", kind="violation"),
    ])
    review.save_resolutions(ws, n, [Resolution(flag_id=flag_id)])


# ------------------------------------------------------------- B3-1 / C4-1 / A5-20: реестры с сервера


def test_реестры_приходят_с_сервера_и_канонизация_проходит(panel, ws):
    _chapter_on_review(ws)
    _, detail = _get(f"{panel}/api/chapter/2")
    names = [r["name"] for r in detail["registries"]]
    assert names and names == sorted(registries(ws.root)) and all(r["purpose"] for r in detail["registries"])
    code, _ = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-009", "decision": "канонизировать", "registry": names[0]})
    assert code == 200
    r = review.load_resolutions(ws, 2)[0]
    assert r.decision == "канонизировать" and r.target_registry == names[0]
    # реестры эталона («3.1» и т. п.) сервер не принимает — панель их и не предлагает
    code, body = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-009", "decision": "канонизировать", "registry": "3.1"})
    assert code == 400 and "реестр" in body["error"]


# ------------------------------------------------------------- A5-7 / B3-3 / C4-3 / A5-6: отклонить с причиной


def test_отклонить_флаг_с_причиной_из_панели(panel, ws):
    _chapter_on_review(ws)
    code, body = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-010", "decision": "отклонить"})
    assert code == 400 and "причин" in body["error"]
    code, body = _post(f"{panel}/api/chapter/2/resolve", {"flag_id": "F-010", "decision": "отклонить", "reason": "не нарушение"})
    assert code == 200, body
    res = {r.flag_id: r for r in review.load_resolutions(ws, 2)}
    assert res["F-010"].decision == "отклонить" and res["F-010"].reason == "не нарушение"
    log = (ws.logs / review.REJECTED_LOG).read_text(encoding="utf-8")
    assert '"flag_id": "F-010"' in log and "не нарушение" in log
    # «отклонить все» скопом — нельзя: без причины и без журнала
    code, body = _post(f"{panel}/api/chapter/2/resolve-all", {"decision": "отклонить"})
    assert code == 400 and "причин" in body["error"]
    assert res["F-009"].decision is None and review.load_resolutions(ws, 2)[0].decision is None
    code, body = _post(f"{panel}/api/chapter/2/resolve-all", {"decision": "вычеркнуть"})
    assert code == 200 and body["resolved"] == 1


# ------------------------------------------------------------- C4-2 / B3-12: кириллица в пути и запросе


def test_промпт_каркаса_с_кириллическим_именем(panel, ws):
    d = ws.root / "драматургия" / "промпты"
    d.mkdir(parents=True)
    (d / "книга.md").write_text("Промпт круга книги.", encoding="utf-8")
    (d / "акт_1.md").write_text("Промпт акта.", encoding="utf-8")
    code, data = _get(f"{panel}/api/circles")
    assert code == 200 and data["prompts"] == ["акт_1.md", "книга.md"] and data["canon_doc"].endswith(".md")
    code, data = _get(f"{panel}/api/circles/prompt/" + urllib.parse.quote("книга"))   # браузер: percent-кодирование
    assert code == 200 and data["text"] == "Промпт круга книги."
    code, data, _ = _raw(panel, "GET", "/api/circles/prompt/акт_1")                     # curl/скрипт: сырая кириллица
    assert code == 200 and data["text"] == "Промпт акта."
    code, data = _get(f"{panel}/api/circles/prompt/" + urllib.parse.quote("нет"))
    assert code == 404 and "нет промпта" in data["error"] and str(ws.root) not in data["error"]


def test_сырая_кириллица_в_query_читается_как_utf8(panel, ws):
    code, enc = _get(f"{panel}/api/find?q=" + urllib.parse.quote("Зоя"))
    code2, raw, _ = _raw(panel, "GET", "/api/find?q=Зоя")
    assert code == code2 == 200 and raw == enc and enc  # без перекодировки сырой запрос давал пустой поиск
    code, doc, _ = _raw(panel, "GET", "/api/canon/doc?path=23_Поглавник_Том1.md")
    assert code == 200 and doc["path"] == "23_Поглавник_Том1.md"


# ------------------------------------------------------------- B3-4 / C4-12 / C4-19 / C4-25: типы полей тела


def test_нестроковые_поля_тела_400(panel, ws, library):
    ChapterState(ws, 1).transition("собрано")
    for text in (None, 123, {"a": 1}, ["x"]):
        code, body = _post(f"{panel}/api/chapter/1/manual-draft", {"text": text})
        assert code == 400 and (text is None or "text" in body["error"]), (text, body)  # null = пусто, не «None»
    assert not ws.draft_path(1, 1).exists() and ChapterState(ws, 1).state == "собрано"
    doc = library / "33_Континуити.md"
    before = doc.read_text(encoding="utf-8")
    code, body = _post(f"{panel}/api/canon/doc", {"path": "33_Континуити.md", "text": {"a": 1}})
    assert code == 400 and doc.read_text(encoding="utf-8") == before
    # нестроковая version — ошибка клиента, а не «без версии = перезаписать»
    code, body = _post(f"{panel}/api/canon/doc", {"path": "33_Континуити.md", "text": before + "x\n", "version": 123})
    assert code == 400 and "version" in body["error"] and doc.read_text(encoding="utf-8") == before
    code, body = _post(f"{panel}/api/chapter/1/edits", {"text": 123})
    assert code == 400 and not (ws.chapter_dir(1) / "правки.md").exists()
    code, body = _post(f"{panel}/api/command", {"cmd": ["x"]})
    assert code == 400 and "cmd" in body["error"]
    code, body = _post(f"{panel}/api/chapter/1/resolve", {"flag_id": "F-1", "decision": "канонизировать", "registry": ["a"]})
    assert code == 400 and "registry" in body["error"]
    code, body = _post(f"{panel}/api/command", {"cmd": "lint-llm", "params": {"files": "abc"}})
    assert code == 400 and "files" in body["error"]


# ------------------------------------------------------------- B3-19 / C4-24 / C4-18: параметры команд до старта


def test_команда_без_главы_и_с_неверными_параметрами_400(panel):
    code, body = _post(f"{panel}/api/command", {"cmd": "compile"})
    assert code == 400 and "требует номер главы" in body["error"]
    code, body = _post(f"{panel}/api/command", {"cmd": "retest", "params": {"chapter": "abc"}})
    assert code == 400 and "chapter" in body["error"] and "invalid literal" not in body["error"]
    code, body = _post(f"{panel}/api/command", {"cmd": "volume-open", "params": {"volume": 0}})
    assert code == 400 and "volume" in body["error"]
    code, body = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 999})
    assert code == 404 and "нет в плане" in body["error"]
    _, job = _get(f"{panel}/api/job")
    assert not job  # ни одна задача не стартовала


# ------------------------------------------------------------- B3-5 / B3-6: ошибка данных не меняет файл


def test_отклонённое_сохранение_канона_не_меняет_файл(panel, library):
    rel = "33_Континуити.md"
    _, doc = _get(f"{panel}/api/canon/doc?path={urllib.parse.quote(rel)}")
    code, body = _post(f"{panel}/api/canon/doc", {"path": rel, "text": "сломано", "version": doc["version"]})
    assert code == 400 and "code" not in body
    assert (library / rel).read_text(encoding="utf-8") == doc["text"]  # на диске — прежний текст
    # версия не устарела: исправленный текст сохраняется без 409
    code, body = _post(f"{panel}/api/canon/doc", {"path": rel, "text": doc["text"] + "\n", "version": doc["version"]})
    assert code == 200, body


def test_правки_md_не_пишется_при_ошибке_формата(panel, ws):
    _chapter_on_review(ws)
    code, body = _post(f"{panel}/api/chapter/2/edits", {"text": "БЫЛО: Чай остыл.\nСТАЛО: Кофе.\n"})
    assert code == 200 and body["parsed"] == 1 and body["version"]
    good = (ws.chapter_dir(2) / "правки.md").read_text(encoding="utf-8")
    jsonl = (ws.chapter_dir(2) / "правки.jsonl").read_text(encoding="utf-8")
    code, body = _post(f"{panel}/api/chapter/2/edits", {"text": "БЫЛО: Чай\nСТАЛО: Кофе\nБЫЛО: x\n"})
    assert code == 400 and "правки.md:3" in body["error"]
    assert (ws.chapter_dir(2) / "правки.md").read_text(encoding="utf-8") == good
    assert (ws.chapter_dir(2) / "правки.jsonl").read_text(encoding="utf-8") == jsonl


# ------------------------------------------------------------- C4-16: версии правки.md и пакета


def test_правки_и_пакет_с_версией_409(panel, ws):
    _chapter_on_review(ws)
    code, body = _post(f"{panel}/api/chapter/2/edits", {"text": "УКАЗАНИЕ: короче.\n"})
    _, detail = _get(f"{panel}/api/chapter/2")
    assert detail["edits_version"] == body["version"]
    (ws.chapter_dir(2) / "правки.md").write_text("УКАЗАНИЕ: правка на диске.\n", encoding="utf-8")
    code, body = _post(f"{panel}/api/chapter/2/edits", {"text": "УКАЗАНИЕ: моя.\n", "version": detail["edits_version"]})
    assert code == 409 and body["code"] == "конфликт"
    assert "на диске" in (ws.chapter_dir(2) / "правки.md").read_text(encoding="utf-8")
    code, _ = _post(f"{panel}/api/chapter/2/edits", {"text": "УКАЗАНИЕ: моя.\n"})   # осознанная перезапись
    assert code == 200
    code, body = _post(f"{panel}/api/chapter/2/canon-batch", {"text": "- запись\n"})
    assert code == 200
    code, body = _post(f"{panel}/api/chapter/2/canon-batch", {"text": "- другая\n", "version": "устарела"})
    assert code == 409


# ------------------------------------------------------------- A5-19 / C4-4 / C4-27: новые файлы и симлинки


def test_канон_новые_файлы_в_прозе_и_новых_папках_запрещены(panel, ws, library):
    for rel in ("Проза/Подпапка/новая.md", "Проза/новая.md", "Новая_папка/док.md", "новый.md"):
        code, body = _post(f"{panel}/api/canon/doc", {"path": rel, "text": "# x\n"})
        assert code == 400 and "не создаётся" in body["error"], rel
        assert not (library / rel).exists()
    # папка прозы — из манифеста, не зашитое имя: переименовали в проекте → запрет идёт за манифестом
    man = ws.root / "проект.yaml"
    man.write_text(man.read_text(encoding="utf-8").replace("файл: Проза/", "файл: Рукопись/"), encoding="utf-8")
    shutil.move(str(library / "Проза"), str(library / "Рукопись"))
    code, body = _post(f"{panel}/api/canon/doc", {"path": "Рукопись/Том1_Глава09.md", "text": "Глава.\n"})
    assert code == 400 and "приёмк" in body["error"] and not (library / "Рукопись" / "Том1_Глава09.md").exists()


def test_символическая_ссылка_наружу_не_в_списке_документов(panel, library, tmp_path):
    outside = tmp_path / "снаружи.md"
    outside.write_text("# снаружи\n", encoding="utf-8")
    try:
        (library / "ссылка_наружу.md").symlink_to(outside)
        (library / "битая.md").symlink_to(tmp_path / "нет.md")
    except OSError as e:
        pytest.skip(f"симлинки недоступны: {e}")
    code, docs = _get(f"{panel}/api/canon")
    paths = {d["path"] for d in docs["docs"]}
    assert code == 200 and "ссылка_наружу.md" not in paths and "битая.md" not in paths and "33_Континуити.md" in paths


# ------------------------------------------------------------- C4-10 / B3-18: главы вне плана


def test_несуществующая_глава_404_и_не_создаёт_папок(panel, ws):
    for n in (0, 999):
        code, body = _get(f"{panel}/api/chapter/{n}")
        assert code == 404 and "нет в плане" in body["error"], n
    code, body = _post(f"{panel}/api/chapter/999/edits", {"text": "УКАЗАНИЕ: x\n"})
    assert code == 404 and not ws.chapter_dir(999).exists()
    code, body = _post(f"{panel}/api/chapter/999/canon-batch", {"text": "x"})
    assert code == 404 and not ws.chapter_dir(999).exists()
    _, state = _get(f"{panel}/api/state")
    assert all(c["chapter"] in range(1, 7) for c in state["chapters"])
    code, body = _get(f"{panel}/api/chapter/1/draft/9")
    assert code == 404 and "нет черновика 9" in body["error"]


# ------------------------------------------------------------- C4-11 / C4-9: ожидаемые ошибки — не 500


def test_решение_по_неизвестному_файлу_404(panel):
    code, body = _post(f"{panel}/api/onboarding/decision", {"file": "x", "decision": "принять"})
    assert code == 404 and "нет в предложении" in body["error"]


def test_повреждённая_глава_не_роняет_виды(panel, ws):
    d = ws.chapter_dir(5)
    d.mkdir(parents=True)
    (d / "состояние.yaml").write_text("глава: [\n", encoding="utf-8")
    code, body = _get(f"{panel}/api/chapter/5")
    assert code == 400 and "внутренняя ошибка" not in body["error"]
    code, journals = _get(f"{panel}/api/journals")
    assert code == 200 and any(c["глава"] == 5 and c["состояние"] == "повреждено" for c in journals["главы"])
    code, state = _get(f"{panel}/api/state")
    assert code == 200 and any(c["chapter"] == 5 and c["state"] == "повреждено" for c in state["chapters"])


# ------------------------------------------------------------- C4-6: потоки останавливаются


def test_потоки_панели_останавливаются_при_закрытии(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    names = ("canon-lint", "canon-watcher")
    for _ in range(3):
        srv = server.serve(ws, Config(), library, port=0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        srv.shutdown()
        srv.server_close()
    alive = [th.name for th in threading.enumerate() if th.name in names]
    assert alive == [], alive


# ------------------------------------------------------------- C4-5: импорт из самого проекта


def test_импорт_без_пути_и_из_проекта_отклоняется(panel, ws, library):
    code, body = _post(f"{panel}/api/command", {"cmd": "import"})
    assert code == 400 and "укажите" in body["error"]
    for src in (ws.root, library, library / "Проза"):
        code, body = _post(f"{panel}/api/command", {"cmd": "import", "params": {"path": str(src)}})
        assert code == 400, (src, body)
        assert str(ws.root) not in body["error"]
    assert not (ws.root / "сырьё").exists()
    from konveyer.onboarding import importer

    with pytest.raises(ValueError, match="внутри проекта"):
        importer.import_path(ws, library)


# ------------------------------------------------------------- B3-11 / B3-16 / D1-7: вывод без абсолютных путей


def test_вывод_задач_и_операций_без_абсолютных_путей(panel, ws):
    code, _ = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200
    job = _wait_job(panel)
    assert job["status"] == "готово" and "окно.md" in job["output"]
    _, state = _get(f"{panel}/api/state")
    for body in (job, state["job"]):
        assert str(ws.root) not in json.dumps(body, ensure_ascii=False)
    code, body = _post(f"{panel}/api/chapter/1/manual-draft", {"text": "Текст главы из чата."})
    assert code == 200 and "черновик_1.md" in body["output"] and str(ws.root) not in json.dumps(body, ensure_ascii=False)
    code, body = _post(f"{panel}/api/chapter/1/prompt/edits", {})
    assert code in (200, 400) and str(ws.root) not in json.dumps(body, ensure_ascii=False)


# ------------------------------------------------------------- B3-27 / C4-23: прочие методы


def test_неподдерживаемые_методы_405_json_без_версии_python(panel):
    for method in ("PUT", "DELETE", "OPTIONS", "PATCH"):
        code, body, hdr = _raw(panel, method, "/api/state")
        assert code == 405 and "не поддерживается" in body["error"] and hdr.get("Allow") == "GET, POST", method
        assert "Python" not in hdr.get("Server", "")
    code, body, hdr = _raw(panel, "GET", "/api/state")
    assert code == 200 and "Python" not in hdr.get("Server", "")


# ------------------------------------------------------------- B3-8 / C4-8: история документа


def test_история_документа_канона(panel, library):
    rel = "33_Континуити.md"
    code, hist = _get(f"{panel}/api/canon/history?path={urllib.parse.quote(rel)}")
    assert code == 200 and hist["git"] is False and hist["commits"] == []
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "Автор"], ["add", "-A"],
                 ["commit", "-q", "-m", "начальный канон"]):
        subprocess.run(["git", "-C", str(library), *args], check=True, capture_output=True)
    (library / rel).write_text((library / rel).read_text(encoding="utf-8") + "\n<!-- правка -->\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(library), "commit", "-q", "-am", "правка континуити"], check=True, capture_output=True)
    code, hist = _get(f"{panel}/api/canon/history?path={urllib.parse.quote(rel)}")
    assert code == 200 and hist["git"] is True and [c["message"] for c in hist["commits"]] == ["правка континуити", "начальный канон"]
    assert hist["commits"][0]["author"] == "Автор" and hist["commits"][0]["date"] and hist["uncommitted"] is False
    sha = hist["commits"][0]["sha"]
    code, diff = _get(f"{panel}/api/canon/history/diff?path={urllib.parse.quote(rel)}&sha={sha}")
    assert code == 200 and any(line.startswith("+<!-- правка -->") for line in diff["lines"])
    code, body = _get(f"{panel}/api/canon/history?path=" + urllib.parse.quote("../конфиг.yaml"))
    assert code == 400


# ------------------------------------------------------------- A1-23: типы в онбординге


def test_онбординг_отдаёт_каталог_типов_и_проверяет_имя(panel, ws, tmp_path):
    code, data = _get(f"{panel}/api/onboarding")
    names = {t["имя"] for t in data["типы"]}
    assert code == 200 and {"план_глав", "проза", "эпистемика"} <= names and all(t["назначение"] for t in data["типы"])
    src = tmp_path / "материалы"
    src.mkdir()
    (src / "заметки.md").write_text("# Заметки\n\nпросто текст\n", encoding="utf-8")
    assert _post(f"{panel}/api/command", {"cmd": "import", "params": {"path": str(src)}})[0] == 200 and _wait_job(panel)["status"] == "готово"
    assert _post(f"{panel}/api/command", {"cmd": "onboarding"})[0] == 200 and _wait_job(panel)["status"] == "готово"
    code, body = _post(f"{panel}/api/onboarding/decision", {"file": "заметки.md", "decision": "тип:несуществующий"})
    assert code == 400 and "неизвестен" in body["error"]
    code, body = _post(f"{panel}/api/onboarding/decision", {"file": "заметки.md", "decision": "тип:план_глав"})
    assert code == 200 and body["тип"] == "план_глав"


# ------------------------------------------------------------- B3-9 / C4-7 / B4-28: виды «Качество» и «Том»


def test_виды_качество_том_типы_метрики(panel, ws):
    code, q = _get(f"{panel}/api/quality")
    assert code == 200 and q["нормы"] and all(n["коридор"] and n["описание"] for n in q["нормы"]) and "регрессия" in q
    code, v = _get(f"{panel}/api/volume")
    assert code == 200 and v["том"] == 1 and v["глав_в_плане"] == 6 and "зафиксировано" in v and "тома" in v
    code, t = _get(f"{panel}/api/types")
    assert code == 200 and any(x["имя"] == "проза" for x in t["типы"])
    code, m = _get(f"{panel}/api/metrics")
    assert code == 200 and m["метрики"] and all(x["id"] and x["описание"] for x in m["метрики"])
    # золотой тест из панели — паритет с `konveyer золотой` (FR-R1, FR-PN-7)
    code, body = _post(f"{panel}/api/regression/golden", {"id": "панель-1", "fragment": "Он был очень-очень усталый.", "expect": ["V1.2"]})
    assert code == 200 and body["path"].startswith("регрессия/") and (ws.root / body["path"]).exists()
    _, reg = _get(f"{panel}/api/regression")
    assert any(t["id"] == "панель-1" for t in reg["тесты"])
    code, body = _post(f"{panel}/api/regression/golden", {"id": "", "fragment": "x"})
    assert code == 400
    code, body = _post(f"{panel}/api/regression/golden", {"id": "п2", "fragment": "x", "expect": "V1.2"})
    assert code == 400 and "expect" in body["error"]


def test_state_несёт_провайдеров_ролей(panel):
    _, state = _get(f"{panel}/api/state")
    assert state["providers"]["писатель"] and set(state["providers"]) >= {"писатель", "верификатор2", "линтер"}
    _, lint = _get(f"{panel}/api/lint")
    assert lint["llm_docs"] > 0 and lint["llm_provider"]


# ------------------------------------------------------------- D1-37 / C4-21: тесты фронтенда и сверка констант


PANEL_DIR = Path(__file__).resolve().parent.parent / "panel"


def test_подписи_задач_панели_покрывают_команды_сервера():
    """JOB_LABEL в panel/src/nextstep.ts — русская подпись для каждой команды server.COMMANDS, без лишних;
    реестры для канонизации в панели — с сервера (константы REGISTRIES нет)."""
    src = (PANEL_DIR / "src" / "nextstep.ts").read_text(encoding="utf-8")
    block = src.split("JOB_LABEL")[1].split("};")[0]
    keys = set(re.findall(r'^\s*"?([\w-]+)"?:\s*"', block, re.MULTILINE))
    assert keys == server.COMMANDS, keys ^ server.COMMANDS
    chapter_view = (PANEL_DIR / "src" / "ChapterView.tsx").read_text(encoding="utf-8")
    assert "REGISTRIES" not in chapter_view and "d.registries" in chapter_view


def test_панель_js_тесты():
    """Юнит-тесты чистых модулей панели (nextstep, edits, highlight, diff, api) — `npm test` в panel/ (Node 22)."""
    node = shutil.which("node")
    if node is None or not (PANEL_DIR / "node_modules" / "esbuild").exists():
        pytest.skip("нет Node или зависимостей панели (npm ci в panel/)")
    r = subprocess.run([node, "tests/run.mjs"], cwd=PANEL_DIR, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "# fail 0" in r.stdout and "# pass" in r.stdout


# ------------------------------------------------------------- B3-30: действия панели — командами CLI


def test_cli_решение_все_линтер_исправить_каркас_принять(ws, library, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from konveyer.cli import app

    monkeypatch.chdir(ws.root)
    runner = CliRunner()
    _chapter_on_review(ws)
    review.save_resolutions(ws, 2, [Resolution(flag_id="F-009"), Resolution(flag_id="F-011")])
    r = runner.invoke(app, ["решение", "2", "отклонить", "--все"])
    assert r.exit_code == 1 and "по одному флагу" in r.output
    r = runner.invoke(app, ["решение", "2", "канонизировать", "--все"])
    assert r.exit_code == 1 and "реестр" in r.output
    r = runner.invoke(app, ["решение", "2", "вычеркнуть", "--все"])
    assert r.exit_code == 0 and "Решено самоволок: 2" in r.output, r.output
    assert all(x.decision == "вычеркнуть" for x in review.load_resolutions(ws, 2))
    # линтер --исправить: механическое исправление применяется по подтверждению тем же путём, что кнопка панели
    doc = library / "23_Поглавник_Том1.md"
    text = doc.read_text(encoding="utf-8")
    r = runner.invoke(app, ["линтер", "--исправить", "-y"])
    assert r.exit_code == 0 and ("Механических исправлений" in r.output or "Исправлений применено" in r.output), r.output
    r = runner.invoke(app, ["линтер", "--исправить", "--llm"])
    assert r.exit_code == 1 and "не сочетается" in r.output
    assert doc.read_text(encoding="utf-8") == text or "Исправлений применено" in r.output
    # каркас --принять: ответ модели из файла, как «Принять круг» в панели
    answer = tmp_path / "ответ.json"
    answer.write_text(json.dumps({"title": "Круг книги", "steps": [{"n": i, "name": f"шаг {i}", "text": "…"} for i in range(1, 9)]},
                                 ensure_ascii=False), encoding="utf-8")
    r = runner.invoke(app, ["каркас", "--принять", "книга", "--ответ", str(answer)])
    assert r.exit_code == 0 and "Каркас принят" in r.output, r.output
    assert list((ws.root / "драматургия").glob("*.json"))
    r = runner.invoke(app, ["каркас", "--принять", "книга_1", "--ответ", str(answer)])
    assert r.exit_code == 1 and "книга" in r.output
    r = runner.invoke(app, ["каркас", "--принять", "акт_1"])
    assert r.exit_code == 1 and "файл" in r.output


# ------------------------------------------------------------- C4-14: предпросмотр внесения каркасов (FR-DR-4)


def test_предпросмотр_внесения_каркасов_в_канон(panel, ws):
    code, body = _get(f"{panel}/api/circles/preview")
    assert code == 400 and "черновиков каркасов нет" in body["error"]
    d = ws.root / "драматургия"
    d.mkdir()
    (d / "книга.json").write_text(json.dumps({"scope": "книга", "key": None, "title": "Круг книги",
                                              "steps": [{"n": i, "name": f"шаг {i}", "text": f"текст {i}"} for i in range(1, 9)]},
                                             ensure_ascii=False), encoding="utf-8")
    code, body = _get(f"{panel}/api/circles/preview")
    assert code == 200 and body["changed"] and body["doc"].endswith(".md") and any(line.startswith("+") for line in body["lines"])
    assert not any(str(ws.root) in line for line in body["lines"])
