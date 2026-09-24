"""Смоук-тесты CLI (FR-O2: каждый шаг — отдельная команда; NFR-2: интерфейс русский)."""

from typer.testing import CliRunner

from konveyer import dashboard
from konveyer.cli import app

runner = CliRunner()


def test_export_compile_status(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    assert runner.invoke(app, ["export"]).exit_code == 0
    r = runner.invoke(app, ["compile", "1"])
    assert r.exit_code == 0 and "Окно собрано" in r.output
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "собрано" in r.output


def test_ошибка_структуры_читаемая(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    path = library / "31_Матрица_знаний.md"
    path.write_text(path.read_text(encoding="utf-8") + "| x |\n", encoding="utf-8")
    r = runner.invoke(app, ["export"])
    assert r.exit_code == 1
    assert "Д-1" in r.output or "Д-1" in (r.stderr or "")


def _out(r) -> str:
    """stdout и stderr вызова (CliRunner разных версий click делит их по-разному)."""
    try:
        return r.output + (r.stderr or "")
    except ValueError:
        return r.output


def test_verify1_требует_состояния(ws, monkeypatch):
    """Глава ещё не сгенерирована: ошибка шага — код 1, «ОШИБКА: …», без трейсбека (FR-CL-3)."""
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["verify1", "1"])
    out = _out(r)
    assert r.exit_code == 1 and out.lstrip().startswith("ОШИБКА:"), out
    assert "Traceback" not in out and (r.exception is None or isinstance(r.exception, SystemExit))


ENGLISH_HELP_PHRASES = (
    "Usage:", "Options", "Arguments", "Commands", "Show this message", "Install completion", "[required]",
    "[default:", "[OPTIONS]", "COMMAND [ARGS]",
)


def _all_help_invocations() -> list[list[str]]:
    calls: list[list[str]] = [["--help"], ["--справка"], ["-h"]]
    for info in app.registered_commands:
        calls.append([info.name, "--справка"])
    for group in app.registered_groups:
        calls.append([group.name, "--справка"])
        for info in group.typer_instance.registered_commands:
            calls.append([group.name, info.name, "--справка"])
    return calls


def test_cli_справка_по_русски(monkeypatch):
    """Справка каждой команды без английских элементов typer/click (FR-CL-5, NFR-7): заголовки разделов,
    строка использования, опция справки; автодополнения оболочки нет."""
    monkeypatch.setenv("COLUMNS", "200")
    for args in _all_help_invocations():
        r = runner.invoke(app, args)
        out = _out(r)
        assert r.exit_code == 0, (args, out)
        assert "Использование:" in out and "справка" in out, (args, out)
        for phrase in ENGLISH_HELP_PHRASES:
            assert phrase not in out, (args, phrase, out)
    r = runner.invoke(app, ["--help"])
    assert "--install-completion" not in r.output and "--show-completion" not in r.output


def test_cli_ошибки_разбора_по_русски_код_1(ws, monkeypatch):
    """Опечатка в аргументах — не «ручной режим»: код 1 и «ОШИБКА: … . См. …» по-русски (FR-CL-3);
    вызов без аргументов — справка, код 0."""
    monkeypatch.chdir(ws.root)
    cases = {
        ("собрать", "abc"): "не целое число",
        ("несуществующая",): "нет команды",
        ("канон-коммит",): "не указана обязательная опция",
        ("собрать", "1", "2"): "лишние аргументы",
        ("написать", "1", "--варианты", "9"): "вне диапазона",
        ("написать", "1", "--нет-такой"): "нет опции",
        ("том", "открыть"): "не указан обязательный аргумент",
    }
    for args, expected in cases.items():
        r = runner.invoke(app, list(args))
        out = _out(r)
        assert r.exit_code == 1, (args, r.exit_code, out)
        line = next(ln for ln in out.splitlines() if ln.startswith("ОШИБКА:"))
        assert expected in line and "--справка" in line, (args, line)
        assert "Traceback" not in out and "Invalid value" not in out and "No such" not in out and "Missing" not in out, out
    for args in ([], ["том"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 0 and "Использование:" in r.output, (args, _out(r))


def test_дашборд_строится(ws):
    path = dashboard.build_dashboard(ws)
    assert path.exists() and "КОНВЕЙЕР" in path.read_text(encoding="utf-8")
