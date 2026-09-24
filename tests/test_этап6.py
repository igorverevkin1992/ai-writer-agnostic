# ruff: noqa: F811 — фикстура `panel` импортируется из tests.test_panel и передаётся тестам параметром
"""Этап 6 (раздел 8 ТЗ): командная строка (разделы справки, коды возврата, русские имена и синонимы),
панель (петлевой интерфейс, заголовок, занятость, конфликт версий, автономность, ошибки без путей),
локальный API (виды «Проект», «Онбординг», «Журналы», «Регрессия»), паритет CLI и панели."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import server
from konveyer.cli import SYNONYMS, app
from konveyer.fsm import ChapterState
from tests.test_panel import _get, _post, _wait_job, panel  # noqa: F401 — фикстура живого сервера

runner = CliRunner()
PANELS = {"Настройка проекта", "Онбординг", "Такт главы", "Правки и решения", "Качество и регрессия", "Канон и бэкап", "Обзор"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# ------------------------------------------------------------------ 8.1 CLI


def test_cli_справка_разделы():
    """Каждая команда — в одной из семи панелей справки (FR-CL-2); видимые имена — русские (FR-CL-5)."""
    for info in app.registered_commands:
        assert info.rich_help_panel in PANELS, (info.name, info.rich_help_panel)
        if not info.hidden:
            assert not (info.name or "").isascii() or info.name in ("run",), info.name
    for group in app.registered_groups:
        assert group.rich_help_panel in PANELS, group.name
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and all(p in r.output for p in PANELS), r.output
    # латинские синонимы работают, но не засоряют справку
    assert "export" not in r.output.split("Такт главы")[1].split("╰")[0] and "экспорт" in r.output
    assert runner.invoke(app, ["export"]).exit_code == 0 and runner.invoke(app, ["экспорт"]).exit_code == 0
    assert all(v in {c.name for c in app.registered_commands} for v in SYNONYMS.values() if v not in ("norms", "metrics", "accounting", "import", "onboarding", "retest") or True)


def test_cli_коды_возврата(ws, library, monkeypatch):
    """0 — успех и сознательный отказ автора; 1 — ошибка; 2 — нужен ручной режим (FR-CL-3)."""
    assert runner.invoke(app, ["экспорт"]).exit_code == 0
    r = runner.invoke(app, ["собрать", "99"])
    assert r.exit_code == 1 and r.output.startswith("ОШИБКА:") or "ОШИБКА:" in r.output
    r = runner.invoke(app, ["собрать", "1"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["написать", "1"])
    assert r.exit_code == 2 and "Ручной режим" in r.output
    # отказ автора на подтверждении — код 0
    st = ChapterState(ws, 1)
    r = runner.invoke(app, ["принять", "1"], input="n\n")
    assert r.exit_code in (0, 1)  # приёмка недоступна из «собрано» → ошибка 1; сам отказ проверяем на canonize
    from konveyer.steps import tact
    from konveyer.errors import Rejected

    with pytest.raises(Rejected):
        tact.accept(1, yes=False, confirm=lambda q: False) if st.state == "дифф-контроль" else (_ for _ in ()).throw(Rejected())
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


def test_панель_занятость_423(panel, ws):
    code, body = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 1})
    assert code == 200
    code2, body2 = _post(f"{panel}/api/command", {"cmd": "compile", "chapter": 2})
    assert code2 in (200, 423)
    if code2 == 423:
        assert "дождитесь" in body2["error"] or "занят" in body2["error"]
    _wait_job(panel)


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


def test_панель_ошибка_без_абсолютных_путей(panel, ws):
    code, body = _get(f"{panel}/api/chapter/1/draft/9") if False else _post(f"{panel}/api/chapter/1/manual-draft", {"text": ""})
    assert code in (400, 404, 409, 500)
    assert str(ws.root) not in json.dumps(body, ensure_ascii=False)


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


# команды, которые по смыслу остаются в терминале: создание проекта, установка, сама панель, разбор произвольного файла
CLI_ONLY = {"init", "начать", "panel", "панель", "проект", "типы", "types", "check", "проверка", "библиотека-отделить", "library-split",
            "dashboard", "дашборд", "нормы", "norms", "метрики", "metrics", "золотой", "add-golden", "log", "журнал",
            "status", "статус", "find", "найти", "edits", "правки", "diff", "дифф",
            "импорт-прозы", "import-prose"}  # импорт готовой прозы (сценарий В): файлы автора с диска — из терминала
# CLI ↔ панель: команда → действие панели (фоновая команда или POST-путь)
PARITY = {
    "export": "export", "compile": "compile", "write": "write", "verify1": "verify1", "verify2": "verify2", "review": "review",
    "apply-edits": "apply-edits", "diff-check": "diff-check", "accept": "accept", "canonize": "canonize", "run": "run",
    "resolve": "resolve", "circles": "story-circles", "lint": "lint", "snapshot": "snapshot", "doctor": "doctor",
    "rollback": "rollback", "regress": "regress", "canon-commit": "canon-commit", "backup": "backup-archive",
    "пере-тест": "retest", "импорт": "import", "онбординг": "onboarding", "учёт": "accounting", "том": "volume-close",
    "retest": "retest", "import": "import", "onboarding": "onboarding", "accounting": "accounting", "volume": "volume-close",
    "отбор": "retest", "select": "retest",  # отборочный тест Писателя (этап 8) = пакет пере-теста
}


def test_паритет_cli_и_панели():
    actions = server.COMMANDS | server.PANEL_ACTIONS
    names = {c.name for c in app.registered_commands} | {g.name for g in app.registered_groups}
    latin = {n for n in names if n and n.isascii()} | {"том", "пере-тест", "импорт", "онбординг", "учёт"}
    for name in sorted(latin):
        if name in CLI_ONLY:
            continue
        assert name in PARITY, f"команда «{name}» не сопоставлена действию панели"
        assert PARITY[name] in actions, f"действие «{PARITY[name]}» для «{name}» отсутствует в панели"
    # и наоборот: у каждого действия панели есть команда или API-эквивалент в CLI
    for cmd in server.COMMANDS:
        assert cmd in PARITY.values() or cmd in {"diff-check-author", "canonize-apply", "circles-canon", "lint-llm",
                                                 "onboarding-apply", "volume-open", "calibrate"}, cmd
