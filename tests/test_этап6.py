# ruff: noqa: F811 — фикстура `panel` импортируется из tests.test_panel и передаётся тестам параметром
"""Этап 6 (раздел 8 ТЗ): командная строка (разделы справки, коды возврата, русские имена и синонимы),
панель (петлевой интерфейс, заголовок, занятость, конфликт версий, автономность, ошибки без путей),
локальный API (виды «Проект», «Онбординг», «Журналы», «Регрессия»), паритет CLI и панели."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import canonist, review, server, verifier1, verifier2
from konveyer.cli import SYNONYMS, app
from konveyer.config import Config
from konveyer.fsm import ChapterState
from tests.test_panel import _get, _post, _wait_job, panel  # noqa: F401 — фикстура живого сервера

PANEL_SRC = Path(__file__).resolve().parent.parent / "panel" / "src"

runner = CliRunner()
PANELS = {"Настройка проекта", "Онбординг", "Такт главы", "Правки и решения", "Качество и регрессия", "Канон и бэкап", "Обзор"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# ------------------------------------------------------------------ 8.1 CLI


def test_cli_справка_разделы():
    """Каждая команда — в одной из семи панелей справки (FR-CL-2); видимые имена — русские, латинские —
    скрытые синонимы, у каждого — видимый русский двойник (FR-CL-5)."""
    commands = {c.name: c for c in app.registered_commands}
    for info in app.registered_commands:
        assert info.rich_help_panel in PANELS, (info.name, info.rich_help_panel)
        if not info.hidden:
            assert not (info.name or "").isascii(), f"латинское имя «{info.name}» видно в справке"
    for group in app.registered_groups:
        assert group.rich_help_panel in PANELS, group.name
    # каждый синоним зарегистрирован: латинская команда скрыта, русская — видна (и наоборот для русских первичных)
    names = set(commands) | {g.name for g in app.registered_groups}
    assert set(SYNONYMS.values()) <= names, set(SYNONYMS.values()) - names
    for a, b in SYNONYMS.items():
        latin, russian = (a, b) if a.isascii() else (b, a)
        assert commands[latin].hidden and not commands[russian].hidden, (latin, russian)
        assert commands[latin].callback is commands[russian].callback, (latin, russian)
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and all(p in r.output for p in PANELS), r.output
    # латинские синонимы работают, но не засоряют справку
    assert "export" not in r.output.split("Такт главы")[1].split("╰")[0] and "экспорт" in r.output
    assert runner.invoke(app, ["export"]).exit_code == 0 and runner.invoke(app, ["экспорт"]).exit_code == 0


def _chapter_before_accept(ws, library, chapter: int = 1) -> None:
    """Глава в «дифф-контроль» с чистым диффом и без самоволок — приёмка ждёт только ответа автора."""
    from konveyer import compiler

    compiler.compile_window(ws, library, chapter)
    st = ChapterState(ws, chapter)
    st.transition("собрано", "compile")
    ws.draft_path(chapter, 1).write_text("Каширин нашёл записку утром возле хлебницы.", encoding="utf-8")
    st.set_draft(1)
    for state, cmd in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    verifier2.save_flags(ws, chapter, [])
    review.build_review_pack(ws, chapter, 1)
    st.transition("на-приёмке", "review")
    review.save_edits(ws, chapter, [])
    shutil.copyfile(ws.draft_path(chapter, 1), ws.draft_path(chapter, 2))
    st.set_draft(2)
    st.transition("правки", "apply-edits")
    verifier1.diff_check(ws, chapter, 1, 2, [])
    st.transition("дифф-контроль", "diff-check")


def test_cli_коды_возврата(ws, library, monkeypatch):
    """0 — успех и сознательный отказ автора; 1 — ошибка; 2 — нужен ручной режим (FR-CL-3)."""
    assert runner.invoke(app, ["экспорт"]).exit_code == 0
    r = runner.invoke(app, ["собрать", "99"])
    assert r.exit_code == 1 and "ОШИБКА:" in r.output, r.output
    r = runner.invoke(app, ["собрать", "1"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["написать", "1"])
    assert r.exit_code == 2 and "Ручной режим" in r.output
    # приёмка недоступна из «собрано» — ошибка, код 1
    r = runner.invoke(app, ["принять", "1"], input="n\n")
    assert r.exit_code == 1 and "ОШИБКА:" in r.output
    # отказ автора на подтверждении приёмки — код 0, состояние не изменилось
    _chapter_before_accept(ws, library, 2)
    r = runner.invoke(app, ["принять", "2"], input="n\n")
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, 2).state == "дифф-контроль"
    # согласие — принято; отказ применить пакет в канон — тоже код 0, глава остаётся «принято»
    r = runner.invoke(app, ["принять", "2"], input="y\n")
    assert r.exit_code == 0 and ChapterState(ws, 2).state == "принято", r.output
    canonist.build_batch(ws, Config(), 2, 2)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-q", "-m", "канон"]):
        subprocess.run(["git", "-C", str(library), *args], check=True, capture_output=True)
    r = runner.invoke(app, ["канон", "2", "--apply"], input="n\n")
    assert r.exit_code == 0 and ChapterState(ws, 2).state == "принято", r.output
    r = runner.invoke(app, ["проект", "создать", str(ws.root / "x"), "-y"])
    assert r.exit_code == 0


# ------------------------------------------------------------------ 8.2 панель


def test_панель_только_петлевой_интерфейс(panel):
    srv = server.serve(*_ws_args(panel), port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()
    req = urllib.request.Request(f"{panel}/api/state", headers={"Host": "evil.example:80"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403


def _ws_args(panel_url):
    from konveyer.paths import Workspace
    from konveyer.config import library_dir, load_config

    ws = Workspace(Path.cwd())
    cfg = load_config(ws)
    return ws, cfg, library_dir(ws, cfg)


def test_панель_post_требует_заголовок(panel):
    code, body = _post(f"{panel}/api/command", {"cmd": "export"}, with_header=False)
    assert code == 403 and "X-Konveyer-Panel" in body["error"]


def test_панель_занятость_423(panel, ws, monkeypatch):
    """Пока идёт задача, вторая получает ровно 423 «дождитесь…» (FR-PN-1), а не стартует параллельно:
    сборка окна подменена медленной, чтобы второй запрос гарантированно застал первую задачу."""
    from konveyer.steps import tact

    orig = tact.compile

    def slow_compile(chapter):
        time.sleep(0.8)
        return orig(chapter)

    monkeypatch.setattr(tact, "compile", slow_compile)
    code, body = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200 and body["job"]["status"] == "выполняется"
    code2, body2 = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 2})
    assert code2 == 423 and "дождитесь" in body2["error"] and "Сборка" not in body2["error"]
    assert "compile" in body2["error"]  # какая задача занята — по имени
    job = _wait_job(panel)
    assert job["status"] == "готово" and job["chapter"] == 1 and ChapterState(ws, 2).state == "не-начато"


def test_панель_конфликт_версий_409(panel, library):
    from urllib.parse import quote

    code, doc = _get(f"{panel}/api/canon/doc?path={quote('23_Поглавник_Том1.md')}")
    assert code == 200
    (library / "23_Поглавник_Том1.md").write_text(doc["text"] + "\n<!-- правка на диске -->\n", encoding="utf-8")
    code, body = _post(f"{panel}/api/canon/doc", {"path": "23_Поглавник_Том1.md", "text": doc["text"] + "\nx\n", "version": doc["version"]})
    assert code == 409 and body.get("code") == "конфликт"


def test_панель_нет_внешних_ресурсов():
    """Собранная панель автономна: ни ссылок в сеть, ни внешних шрифтов и библиотек (FR-PN-5, NFR-8)."""
    root = Path(str(resources.files("konveyer").joinpath("data/панель")))
    files = [p for p in root.rglob("*") if p.is_file()]
    assert any(p.name == "index.html" for p in files) and any(p.suffix == ".js" for p in files)
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"https?://[^\s\"'`)]+", text):
            url = m.group(0)
            # допустимы только пространства имён и ссылки на лицензии внутри минифицированного кода react
            assert re.match(r"https?://(www\.w3\.org|react\.dev|reactjs\.org|github\.com/facebook|fb\.me|legacy\.reactjs\.org)", url), (p.name, url)
        assert "<link" not in text or "rel=\"stylesheet\" href=\"./" in text or "href=\"./" in text
        assert "@import" not in text and "fonts.googleapis" not in text and "cdn." not in text


def _get_any(url: str):
    try:
        return _get(url)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


@pytest.mark.parametrize("what", ["черновик", "промпт каркаса", "документ канона", "пустой черновик", "внутренний путь", "вывод задачи"])
def test_панель_ошибка_без_абсолютных_путей(panel, ws, library, monkeypatch, what):
    """FR-PN-6/FR-SC-9: ни ответы об ошибках, ни вывод задач не содержат абсолютных путей машины автора."""
    if what == "черновик":
        code, body = _get_any(f"{panel}/api/chapter/1/draft/9")
        assert code == 404 and "нет черновика 9" in body["error"]
    elif what == "промпт каркаса":
        code, body = _get_any(f"{panel}/api/circles/prompt/nope")
        assert code == 404 and "нет промпта" in body["error"]
    elif what == "документ канона":
        code, body = _get_any(f"{panel}/api/canon/doc?path=none.md")
        assert code == 404 and "нет документа" in body["error"]
    elif what == "пустой черновик":
        code, body = _post(f"{panel}/api/chapter/1/manual-draft", {"text": ""})
        assert code == 400 and "пустой" in body["error"]
    elif what == "внутренний путь":
        # исключение ядра с полным путём: в ответе путь заменён словами «рабочая область»
        missing = ws.root / "журналы" / "api.jsonl"
        monkeypatch.setattr(server.PanelAPI, "api_log", lambda self, n=30: (_ for _ in ()).throw(FileNotFoundError(f"нет файла {missing}")))
        code, body = _get_any(f"{panel}/api/log")
        assert code == 404 and "рабочая область/журналы/api.jsonl" in body["error"]
    else:
        code, _ = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
        assert code == 200
        body = _wait_job(panel)
        assert body["status"] == "готово" and "окно.md" in body["output"]
    dump = json.dumps(body, ensure_ascii=False)
    assert str(ws.root) not in dump and str(library) not in dump


# ------------------------------------------------------------------ 8.3 API: новые виды


def test_панель_вид_проект(panel):
    code, data = _get(f"{panel}/api/project")
    assert code == 200 and data["паспорт"]["имя"] == "Гаражи" and data["готов_к_такту"] is True
    mods = {m["имя"]: m for m in data["модули"]}
    assert mods["бриф"]["базовый"] and mods["эпистемика"]["включён"]
    assert any(e["тип"] == "план_глав" for e in data["карта"]) and data["вне_карты"] == []


def test_панель_вид_онбординг(panel, ws, tmp_path):
    src = tmp_path / "материалы"
    src.mkdir()
    (src / "заметки.md").write_text("# Заметки\n\nпросто текст\n", encoding="utf-8")
    code, _ = _post(f"{panel}/api/command", {"cmd": "import", "params": {"path": str(src)}})
    assert code == 200 and _wait_job(panel)["status"] == "готово"
    code, _ = _post(f"{panel}/api/command", {"cmd": "onboarding"})
    assert code == 200 and _wait_job(panel)["status"] == "готово"
    code, data = _get(f"{panel}/api/onboarding")
    assert code == 200 and [e["файл"] for e in data["сырьё"]] == ["заметки.md"] and data["предложения"][0]["файл"] == "заметки.md"
    assert "## 3. Чего не хватает по модулям" in data["отчёт"]
    code, r = _post(f"{panel}/api/onboarding/decision", {"file": "заметки.md", "decision": "сырьё"})
    assert code == 200 and r["решение"] == "сырьё"
    code, r = _post(f"{panel}/api/onboarding/decision", {"file": "заметки.md", "decision": "чушь"})
    assert code == 400 and "допустимо" in r["error"]


def test_панель_вид_журналы_и_регрессия(panel):
    code, data = _get(f"{panel}/api/journals")
    assert code == 200 and data["том"] == 1 and "прогноз" in data and "глав_в_плане" in data
    code, data = _get(f"{panel}/api/regression")
    assert code == 200 and len(data["тесты"]) >= 1 and "зелёная" in data
    code, _ = _post(f"{panel}/api/command", {"cmd": "regress"})
    assert code == 200 and _wait_job(panel)["status"] == "готово"
    code, data = _get(f"{panel}/api/regression")
    assert data["зелёная"] is True and data["отчёт"]["всего"] >= 1


# ------------------------------------------------------------------ паритет


# команды, которые по смыслу остаются в терминале (с обоснованием): установка и создание проекта (панели ещё нет),
# запуск самой панели, разбор произвольного файла с диска, генерация документации в папку, переезд библиотеки
CLI_ONLY = {
    "init", "начать", "panel", "панель", "проект", "check", "проверка", "типы", "types", "библиотека-отделить", "library-split",
}
# CLI ↔ панель: команда → действие панели (фоновая команда `runCommand("…")` или POST/GET-путь `/api/…`)
PARITY = {
    "export": "export", "compile": "compile", "write": "write", "verify1": "verify1", "verify2": "verify2", "review": "review",
    "apply-edits": "apply-edits", "diff-check": "diff-check", "accept": "/api/chapter/{n}/accept", "canonize": "canonize",
    "run": "run", "resolve": "/api/chapter/{n}/resolve", "circles": "story-circles", "lint": "lint", "snapshot": "snapshot",
    "doctor": "doctor", "rollback": "/api/chapter/{n}/rollback", "regress": "regress", "canon-commit": "canon-commit",
    "backup": "backup-archive", "retest": "retest", "import": "import", "onboarding": "onboarding", "accounting": "accounting",
    "volume": "volume-close", "status": "/api/state", "find": "/api/find", "log": "/api/journals", "edits": "/api/chapter/{n}/edits",
    "diff": "/api/chapter/{n}/diff/", "dashboard": "/dashboard", "нормы": "calibrate", "norms": "calibrate",
    "метрики": "/api/metrics", "metrics": "/api/metrics", "add-golden": "/api/regression/golden", "золотой": "/api/regression/golden",
}
# действия панели без своей команды CLI → чем они делаются в терминале
PANEL_TO_CLI = {
    "diff-check-author": "diff-check", "canonize-apply": "canonize", "circles-canon": "circles", "lint-llm": "lint",
    "onboarding-apply": "onboarding", "volume-open": "volume", "calibrate": "нормы",
    "resolve-all": "resolve", "lint-fix": "lint", "circles-manual": "circles", "manual-draft": "write", "manual-flags": "verify2",
    "canon-doc": "canon-commit", "onboarding-decision": "onboarding", "job-cancel": "run", "canon-batch": "canonize",
    "edits": "edits", "prompt": "write", "accept": "accept", "rollback": "rollback", "resolve": "resolve",
}


def _panel_actions() -> tuple[set[str], set[str]]:
    """Действия, которые реально вызывает фронтенд: команды `runCommand("…")` и пути `/api/…` в panel/src."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(PANEL_SRC.glob("*.ts*")))
    # runCommand("…") плюс таблица кнопок такта ChapterView.ACTIONS (`cmd: "…"`)
    commands = set(re.findall(r'runCommand\("([\w-]+)"', src)) | set(re.findall(r'\bcmd: "([\w-]+)"', src))
    paths = set(re.findall(r'[`"](/api/[^`"?]*|/dashboard)', src))
    return commands, paths


def _path_used(action: str, paths: set[str]) -> bool:
    want = action.replace("{n}", "${chapter}").replace("${chapter}", "")
    return any(want.replace("//", "/").rstrip("/") in p.replace("${chapter}", "").replace("${d.chapter}", "").replace("//", "/") for p in paths)


def test_паритет_cli_и_панели():
    """FR-PN-7 (§8.3 «Приёмка интерфейсов»): каждая команда CLI есть в панели (по её исходникам, а не по белому
    списку сервера) и каждое действие панели есть в CLI; белый список сервера совпадает с тем, что панель вызывает."""
    commands, paths = _panel_actions()
    names = {c.name for c in app.registered_commands} | {g.name for g in app.registered_groups}
    latin = {n for n in names if n and n.isascii()} | {"нормы", "метрики", "золотой"}
    for name in sorted(latin):
        if name in CLI_ONLY:
            continue
        assert name in PARITY, f"команда «{name}» не сопоставлена действию панели"
        action = PARITY[name]
        if action.startswith("/"):
            assert _path_used(action, paths), f"панель не обращается к «{action}» для «{name}»"
        else:
            assert action in commands, f"панель не вызывает команду «{action}» для «{name}»"
    # белый список сервера = то, что панель вызывает (ни лишних, ни отсутствующих кнопок — B3-9)
    assert commands == server.COMMANDS, (commands ^ server.COMMANDS)
    # и наоборот: у каждого действия панели есть команда CLI
    for cmd in sorted(server.COMMANDS | server.PANEL_ACTIONS):
        if cmd in PARITY.values():
            continue
        if cmd in {"state", "chapter", "draft", "diff", "window", "find", "circles", "lint", "canon", "log", "job",
                   "project", "onboarding", "journals", "regression"}:
            continue  # чтение: статус/поиск/журнал (`konveyer статус/найти/журнал`)
        assert PANEL_TO_CLI.get(cmd) in names, f"действие панели «{cmd}» недоступно командой CLI"
    # действия панели, у которых раньше не было команды: --все, --исправить, --принять (B3-30)
    help_text = runner.invoke(app, ["решение", "--help"]).output + runner.invoke(app, ["линтер", "--help"]).output \
        + runner.invoke(app, ["каркас", "--help"]).output
    for opt in ("--все", "--исправить", "--принять"):
        assert opt in help_text, opt
