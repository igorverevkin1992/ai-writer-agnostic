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


def test_verify1_требует_состояния(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["verify1", "1"])
    assert r.exit_code != 0  # глава ещё не сгенерирована


def test_дашборд_строится(ws):
    path = dashboard.build_dashboard(ws)
    assert path.exists() and "КОНВЕЙЕР" in path.read_text(encoding="utf-8")
