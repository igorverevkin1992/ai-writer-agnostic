"""B2-1/B4-25: битая строка api.jsonl не роняет учёт, статус тома и журнал."""

from typer.testing import CliRunner

from konveyer import apilog
from konveyer.cli import app

runner = CliRunner()


def test_битая_строка_журнала_не_ломает_сводки(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    apilog.log_call(ws.logs, role="писатель", model="m", tokens_in=10, tokens_out=5, cost_est=0.01, chapter=1, duration=1.0)
    path = ws.logs / "api.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2025-01-01T00:00:00+00:00", "role": "писа')  # обрыв процесса посреди записи
    # следующая запись не склеивается с оборванной
    apilog.log_call(ws.logs, role="канонист", model="m", tokens_in=1, tokens_out=1, cost_est=0.02, chapter=1, duration=1.0)
    rows, bad = apilog.read_log_report(ws.logs)
    assert bad == 1 and [r["role"] for r in rows] == ["писатель", "канонист"]
    for args in (["учёт"], ["том", "статус"], ["журнал"], ["doctor"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 0 and "Traceback" not in r.output and "Unterminated" not in r.output, (args, r.output)
    assert "нечитаемых строк: 1" in runner.invoke(app, ["журнал"]).output
    assert "нечитаемых строк: 1" in runner.invoke(app, ["учёт"]).output
    assert "нечитаемых строк 1" in runner.invoke(app, ["doctor"]).output
