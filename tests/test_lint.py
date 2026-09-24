"""Линтер канона: проверки противоречий (демо и реальная библиотека), исправления, наблюдатель, API панели, CLI."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import canonwatch, guard, lint, server
from konveyer.cli import app
from konveyer.config import Config
from konveyer.paths import Workspace
from konveyer.schemas import LintFix

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ------------------------------------------------------------- машинный слой


def test_демо_библиотека_без_противоречий(ws, library):
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert report.errors == 0 and report.warnings == 0, [f.message for f in report.findings]
    assert (ws.logs / "линтер.json").exists() and (ws.logs / "линтер.md").exists()


def test_хронология_глав(ws, library):
    _edit(library / "23_Поглавник_Том1.md", "- Дата: 12 июня 1995", "- Дата: 12 июля 1995")  # гл. 1 позже гл. 5
    report = lint.run_lint(library, ws.exports, ws.logs)
    codes = {f.code for f in report.findings}
    assert "ХРОН-2" in codes and report.errors >= 1
    f = next(f for f in report.findings if f.code == "ХРОН-2")
    assert "23_Поглавник_Том1.md" in f.file and "хронологию" in f.message


def test_ссылки_на_главы_вне_тома(ws, library):
    _edit(library / "31_Матрица_знаний.md", "| M-002 | у Зои есть ключ от гаража №14 | Каширин | 5 |",
          "| M-002 | у Зои есть ключ от гаража №14 | Каширин | 40 |")
    _edit(library / "32_Реестр_закладок.md", "| P-001 | записка без подписи на столе | т1 гл1 | т1 гл6 |",
          "| P-001 | записка без подписи на столе | т1 гл9 | т1 гл1 |")
    report = lint.run_lint(library, ws.exports, ws.logs)
    codes = [f.code for f in report.findings]
    assert "МАТР-1" in codes and "ЗАКЛ-2" in codes
    m = next(f for f in report.findings if f.code == "МАТР-1")
    assert m.line and "31_Матрица_знаний.md" in m.file


def test_ошибка_разметки_как_находка(ws, library):
    p = library / "31_Матрица_знаний.md"
    p.write_text(p.read_text(encoding="utf-8").replace("| M-001 | записка", "| M-001 записка", 1), encoding="utf-8")
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert report.errors == 1 and report.findings[0].code == "РАЗМ-1"
    assert report.findings[0].file.endswith("31_Матрица_знаний.md") and report.findings[0].line


def test_исправление_применяется_только_в_сессии_канона(ws, library):
    p = library / "Досье" / "Персонаж_Зоя.md"
    line = next(i for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "24 года" in l)
    fix = LintFix(file="Досье/Персонаж_Зоя.md", line=line, old="24 года", new="25 лет")
    with pytest.raises(guard.CanonWriteError):
        lint.apply_fix(library, fix)
    with guard.canon_write_session():
        lint.apply_fix(library, fix)
    assert "25 лет" in p.read_text(encoding="utf-8")
    with guard.canon_write_session(), pytest.raises(ValueError, match="изменилась"):
        lint.apply_fix(library, fix)  # строка уже другая — исправление устарело
    with pytest.raises(ValueError, match="вне библиотеки"), guard.canon_write_session():
        lint.apply_fix(library, LintFix(file="../конфиг.yaml", line=1, old="x", new="y"))


def test_модельный_слой_разбирает_ответ(ws, library):
    doc = library / "23_Поглавник_Том1.md"
    raw = 'Нашёл:\n[{"quote": "Зоя впервые упоминает гаражи", "problem": "гаражи уже названы в гл. 1", "suggestion": "убрать «впервые»", "severity": "ошибка"}]'
    found = lint.parse_llm_findings(raw, library, doc)
    assert len(found) == 1 and found[0].source == "модель" and found[0].line and found[0].severity == "ошибка"
    base = lint.run_lint(library, ws.exports, ws.logs)
    merged = lint.merge_llm(base, found, ws.logs)
    assert merged.errors == 1 and any(f.source == "модель" for f in merged.findings)


def test_модельный_слой_без_api_сохраняет_промпты(ws, library):
    findings, prompts = lint.run_lint_llm(ws, Config(), library, [library / "23_Поглавник_Том1.md"])
    assert not findings and len(prompts) == 1 and Path(prompts[0]).exists()


@real_only
def test_реальная_библиотека_без_ошибок(tmp_path):
    """Реальный канон: ошибок уровня «ошибка» нет; предупреждения — подсветка для автора, а не шум.
    Работает на копии библиотеки во временной папке pytest — живой канон автора не трогается, мусора не остаётся."""
    import shutil

    lib = tmp_path / "Библиотека"
    shutil.copytree(LIBRARY, lib, ignore=shutil.ignore_patterns(".git"))
    ws = Workspace(tmp_path)
    guard.set_library_dir(lib)
    report = lint.run_lint(lib, ws.exports, ws.logs, root=ws.root)
    assert report.errors == 0, [f.message for f in report.findings if f.severity == "ошибка"]
    codes = {f.code for f in report.findings}
    # расхождение реестра и матрицы по Т-07 снято автором (Р-033): на чистом каноне ТАЙНА-1 нет
    assert "ТАЙНА-1" not in codes
    # возраст «гл. 41 т.1» больше не принимается за возраст (ложных ДОСЬЕ-1 нет)
    assert not any(f.code == "ДОСЬЕ-1" and "41" in f.message for f in report.findings)
    # участники сцен без карточки досье и карточки без «Физики» — заметки для автора (аудит 7.6, 3.10)
    notes = {f.code: [x.message for x in report.findings if x.code == f.code] for f in report.findings}
    assert all(f.severity == "заметка" for f in report.findings if f.code in ("ПОГЛ-2", "ДОСЬЕ-6"))
    who = {re.search(r"«([^»]+)»", m).group(1) for m in notes["ПОГЛ-2"]}
    assert {"Куратор ОГПУ", "тело Клюева у сейфа", "Веры Холодовой", "поляк", "посредник", "оперативник"} <= who
    assert not any(w.lower().startswith(("чекист", "резидент")) for w in who)  # «чекистской мистификации», резидент = Штерн
    assert next(m for m in notes["ПОГЛ-2"] if "«поляк»" in m).endswith("(гл. 29, 31, 37, 40)")
    no_physique = {m.split(":")[0] for m in notes["ДОСЬЕ-6"]}
    assert {"РОМАН ЗАВАРЗИН", "АСЯ ГРИНБЕРГ", "ФРОЛ БУГАЕВ", "ОЛЬГА ЛЕММ"} <= no_physique and len(no_physique) == 7
    assert not any(n.startswith(("АРИСТАРХ", "СТЕПАН", "АНДРЕЙ")) for n in no_physique)  # у Лемма, Степана, Штерна «Физика» есть


# ------------------------------------------------------------- наблюдатель


def test_наблюдатель_замечает_изменение(library):
    seen: list[list[str]] = []
    w = canonwatch.CanonWatcher(library, seen.append, interval=0.01)
    assert w.poll() == []
    (library / "36_Журнал_решений.md").write_text("# новый\n", encoding="utf-8")
    assert w.poll() == []            # изменение замечено, ждём «устоявшегося» mtime
    assert w.poll() == ["36_Журнал_решений.md"]
    assert w.poll() == []


# ------------------------------------------------------------- панель


@pytest.fixture
def panel(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", srv.api  # type: ignore[attr-defined]
    srv.shutdown()
    srv.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def _post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_панель_канон_чтение_правка_и_линт(panel, ws, library):
    base, api = panel
    status, docs = _get(f"{base}/api/canon")
    assert status == 200 and any(d["path"] == "23_Поглавник_Том1.md" for d in docs["docs"])
    status, doc = _get(f"{base}/api/canon/doc?path=" + urllib.parse.quote("23_Поглавник_Том1.md"))
    assert status == 200 and "Глава 1" in doc["text"]
    # вне библиотеки — отказ
    try:
        urllib.request.urlopen(f"{base}/api/canon/doc?path=" + urllib.parse.quote("../конфиг.yaml"), timeout=5)
        raise AssertionError("ожидался отказ")
    except urllib.error.HTTPError as e:
        assert e.code == 400

    # правка с противоречием → сохранение проходит (файл автора), линт подсвечивает ошибку
    text = doc["text"].replace("- Дата: 12 июня 1995", "- Дата: 12 июля 1995")
    status, r = _post(f"{base}/api/canon/doc", {"path": "23_Поглавник_Том1.md", "text": text, "version": doc["version"]})
    assert status == 200, r and r["version"] != doc["version"]
    assert "12 июля" in (library / "23_Поглавник_Том1.md").read_text(encoding="utf-8")
    assert r["lint"]["errors"] >= 1
    status, l = _get(f"{base}/api/lint")
    assert status == 200 and any(f["code"] == "ХРОН-2" for f in l["report"]["findings"])
    # устаревшая версия — конфликт (версия = хэш содержимого: переживает JSON/JavaScript, в отличие от mtime_ns)
    status, r = _post(f"{base}/api/canon/doc", {"path": "23_Поглавник_Том1.md", "text": text, "version": doc["version"]})
    assert status == 409 and "изменён на диске" in r["error"] and r["code"] == "конфликт"
    assert isinstance(doc["version"], str) and len(doc["version"]) == 64
    # сводка в состоянии панели
    status, st = _get(f"{base}/api/state")
    assert st["lint"]["errors"] >= 1


def test_панель_конфликт_версии_409_и_перезапись_без_версии(panel, ws, library):
    """Аудит 5.2: конфликт версии — отдельный код 409 с «code»: «конфликт», а не общий 400;
    повторная отправка без version — осознанная перезапись автором («Перезаписать всё равно»)."""
    base, api = panel
    rel = "23_Поглавник_Том1.md"
    status, doc = _get(f"{base}/api/canon/doc?path=" + urllib.parse.quote(rel))
    assert status == 200
    # документ изменили «на диске» (другой редактор) после того, как автор открыл его в панели
    p = library / rel
    disk = doc["text"].replace("Глава 1", "Глава 1 (правка на диске)", 1)
    p.write_text(disk, encoding="utf-8")
    mine = doc["text"] + "\n<!-- правка автора в панели -->\n"
    status, r = _post(f"{base}/api/canon/doc", {"path": rel, "text": mine, "version": doc["version"]})
    assert status == 409, r
    assert r["code"] == "конфликт" and "изменён на диске" in r["error"]
    assert p.read_text(encoding="utf-8") == disk          # ничего не затёрто
    # перечитать: версия на диске новая и отличается от той, что была у автора
    status, fresh = _get(f"{base}/api/canon/doc?path=" + urllib.parse.quote(rel))
    assert status == 200 and fresh["version"] != doc["version"] and "правка на диске" in fresh["text"]
    # «Перезаписать всё равно»: без version — проверка версии не выполняется, файл перезаписан
    status, r = _post(f"{base}/api/canon/doc", {"path": rel, "text": mine})
    assert status == 200, r
    assert r["saved"] == rel and r["version"] not in (doc["version"], fresh["version"])
    assert p.read_text(encoding="utf-8") == mine
    # прочие ошибки остаются 400 без «code»: путь вне библиотеки
    status, r = _post(f"{base}/api/canon/doc", {"path": "../конфиг.yaml", "text": "x"})
    assert status == 400 and "code" not in r


def test_панель_применяет_исправление(panel, ws, library):
    base, api = panel
    p = library / "Досье" / "Персонаж_Зоя.md"
    line = next(i for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "24 года" in l)
    fix = {"file": "Досье/Персонаж_Зоя.md", "line": line, "old": "24 года", "new": "25 лет", "note": "тест"}
    status, r = _post(f"{base}/api/lint/fix", {"fix": fix})  # ровно то, что автор видел и подтвердил
    assert status == 200, r
    assert "25 лет" in p.read_text(encoding="utf-8")
    status, r = _post(f"{base}/api/lint/fix", {"fix": fix})  # строка уже другая — устаревшее исправление отвергается
    assert status == 400 and "изменилась" in r["error"]
    status, r = _post(f"{base}/api/lint/fix", {"index": 0})
    assert status == 400
    status, r = _post(f"{base}/api/lint/fix", {"fix": {"file": "../конфиг.yaml", "line": 1, "old": "x", "new": "y"}})
    assert status == 400


def test_панель_наблюдатель_перепроверяет_после_правки_на_диске(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    api = srv.api  # type: ignore[attr-defined]
    api.watcher.interval = 0.02
    api.watcher.start()
    try:
        _edit(library / "23_Поглавник_Том1.md", "- Дата: 12 июня 1995", "- Дата: 12 июля 1995")
        deadline = time.time() + 5
        while time.time() < deadline and not (api.lint_report and api.lint_report.errors):
            time.sleep(0.05)
        assert api.lint_report and api.lint_report.errors >= 1
        assert api.lint_changed == ["23_Поглавник_Том1.md"]
    finally:
        api.watcher.stop()
        srv.server_close()


# ------------------------------------------------------------- CLI


def test_cli_lint(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    runner = CliRunner()
    r = runner.invoke(app, ["lint"])
    assert r.exit_code == 0 and "ошибок 0" in r.output
    _edit(library / "23_Поглавник_Том1.md", "- Дата: 12 июня 1995", "- Дата: 12 июля 1995")
    r = runner.invoke(app, ["lint"])
    assert r.exit_code == 1 and "ХРОН-2" in r.output
    assert (ws.logs / "линтер.md").read_text(encoding="utf-8").count("ошибка") >= 1


# ------------------------------------------------------------- этап 1 второго аудита (4.4–4.6, 4.10)


def test_сбой_линтера_становится_находкой_а_не_циклом(ws, library, monkeypatch):
    """Файл не в UTF-8 в библиотеке: наблюдатель/панель не зацикливаются, автор видит находку с причиной (NFR-2)."""
    (library / "Досье" / "Персонаж_Плохой.md").write_bytes("# Досье\n\nТекст в cp1251: ёж".encode("cp1251"))
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert report.errors == 1 and report.findings[0].code == "РАЗМ-1" and "UTF-8" in report.findings[0].message
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    api = srv.api  # type: ignore[attr-defined]
    try:
        api.request_lint(["Досье/Персонаж_Плохой.md"], wait=10.0)
        assert api.lint_report and api.lint_report.errors == 1
        f = api.lint_report.findings[0]
        assert f.code == "РАЗМ-1" and "UTF-8" in f.message and not api.lint_pending and not api.lint_running
        # GET /api/lint — без побочных эффектов: выгрузки не пересобираются, отчёт тот же
        stamp = (ws.exports / "индекс.json").stat().st_mtime_ns
        assert api.lint()["report"]["findings"][0]["code"] == "РАЗМ-1"
        assert (ws.exports / "индекс.json").stat().st_mtime_ns == stamp
    finally:
        api.stop_lint_worker()
        srv.server_close()
    # CLI: тот же сбой — находка и код возврата 1, без трейсбека
    r = CliRunner().invoke(app, ["lint"])
    assert r.exit_code == 1 and "РАЗМ-1" in r.output and "Traceback" not in r.output


def test_очередь_линтера_ждёт_занятый_сервер(ws, library, monkeypatch):
    """Пока идёт задача (JobRunner занят), запрос не теряется: выполняется после освобождения."""
    monkeypatch.chdir(ws.root)
    srv = server.serve(ws, Config(), library, port=0, watch=False)
    api = srv.api  # type: ignore[attr-defined]
    try:
        with api.jobs.exclusive():
            api.request_lint(["x.md"], wait=1.5)
            assert api.lint_pending and api.lint_report is None
        api._lint_done.wait(10.0)
        assert api.lint_report is not None and not api.lint_pending
    finally:
        api.stop_lint_worker()
        srv.server_close()


def test_модельный_слой_только_внутри_библиотеки(ws, library, monkeypatch):
    secret = ws.root / "секрет.txt"
    secret.write_text("ключ", encoding="utf-8")
    with pytest.raises(ValueError, match="внутри библиотеки"):
        lint.resolve_library_files(library, ["../секрет.txt"])
    with pytest.raises(ValueError, match="нет такого"):
        lint.resolve_library_files(library, ["нет.md"])
    assert lint.resolve_library_files(library, ["23_Поглавник_Том1.md"]) == [(library / "23_Поглавник_Том1.md").resolve()]
    monkeypatch.chdir(ws.root)
    r = CliRunner().invoke(app, ["lint", "--llm", "--файл", "../секрет.txt"])
    assert r.exit_code == 1 and "внутри библиотеки" in r.output
    assert not any("ключ" in p.read_text(encoding="utf-8") for p in (ws.logs / "линтер_промпты").glob("*")) if (ws.logs / "линтер_промпты").exists() else True


def test_модельный_слой_сбой_одного_документа_не_теряет_остальные(ws, library, monkeypatch):
    from konveyer import adapters

    calls = []

    def fake_call(system, user, mc, api, logs_dir, *, role, **kw):
        calls.append(user.split("\n", 1)[0])
        if len(calls) == 1:
            return "никакого JSON тут нет"
        if len(calls) == 2:
            raise RuntimeError("HTTP 500 от API")
        return '[{"quote": "Глава 1", "problem": "проблема", "suggestion": "решение", "severity": "заметка"}]'

    monkeypatch.setattr(adapters, "call_anthropic", fake_call)
    docs = [library / "23_Поглавник_Том1.md", library / "31_Матрица_знаний.md", library / "36_Журнал_решений.md"]
    findings, prompts = lint.run_lint_llm(ws, Config(), library, docs)
    assert len(calls) == 3 and not prompts
    codes = [f.code for f in findings]
    assert codes.count("ЛИНТ-0") == 2 and "МОДЕЛЬ" in codes
    with pytest.raises(ValueError, match="лимит"):
        lint.run_lint_llm(ws, Config(), library, docs, max_calls=2)


def test_панель_lint_с_ошибками_не_помечается_сбоем(ws, library, monkeypatch):
    """Ошибки канона — результат проверки, а не сбой задачи: в панели задача «lint» завершается «готово»."""
    _edit(library / "23_Поглавник_Том1.md", "- Дата: 12 июня 1995", "- Дата: 12 июля 1995")
    monkeypatch.chdir(ws.root)
    r = CliRunner().invoke(app, ["lint", "--no-strict"])
    assert r.exit_code == 0 and "ХРОН-2" in r.output
    r = CliRunner().invoke(app, ["lint", "--llm", "--no-strict"])
    assert r.exit_code == 0 and "Модельный слой пропущен" in r.output


def test_хронология_стык_года_не_ошибка(ws, library):
    """«30 декабря» → «2 января» без явного года — переход через Новый год, а не нарушение хронологии."""
    _edit(library / "23_Поглавник_Том1.md", "- Дата: 3 июля 1995", "- Дата: 30 декабря")
    _edit(library / "23_Поглавник_Том1.md", "- Дата: 18 июля 1995", "- Дата: 2 января")
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert not any(f.code == "ХРОН-2" for f in report.findings)
