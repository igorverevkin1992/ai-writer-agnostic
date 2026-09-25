"""Этап 5 аудита: непокрытые сценарии — пере-тест/add-golden (В, FR-R3), `run` после приёмки,
эндпоинты панели (draft/diff/log/canon-batch/rollback), ручной apply-edits из панели, meta черновика (Д-6)."""

import json
import shutil
import threading
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from konveyer import review, server, verifier2, writer
from konveyer.cli import app
from konveyer.config import Config
from konveyer.fsm import ChapterState


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
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


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def _post(url: str, body: dict):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _chapter_at_review(ws, n: int = 1, text: str = "Первая фраза. Вторая фраза. Третья фраза.\n") -> ChapterState:
    """Глава в состоянии «на-приёмке» с черновиком 1 и пустыми флагами."""
    st = ChapterState(ws, n)
    ws.chapter_dir(n).mkdir(parents=True, exist_ok=True)
    ws.draft_path(n, 1).write_text(text, encoding="utf-8")
    st.transition("собрано", "compile")
    st.set_draft(1)
    for state, cmd in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    (ws.chapter_dir(n) / "вердикт.json").write_text(json.dumps({"chapter": n, "draft": 1, "checks": []}), encoding="utf-8")
    verifier2.save_flags(ws, n, [])
    review.build_review_pack(ws, n, 1)
    st.data["база_приёмки"] = 1
    st.transition("на-приёмке", "review")
    return ChapterState(ws, n)


# ------------------------------------------------------------- retest / golden


def test_retest_пакет_и_запрет_фиксации(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    runner = CliRunner()
    r = runner.invoke(app, ["пере-тест", "--chapter", "1"])
    assert r.exit_code == 0, r.output
    packs = sorted((ws.root / "пере-тест").iterdir())
    assert packs and (packs[-1] / "ПРОМПТ_раунд1.md").exists() and (packs[-1] / "РЕЗУЛЬТАТЫ.md").exists()
    assert not ws.window_path(1).exists()  # окно главы в работе не создаётся/не трогается (2.10)
    # фиксация без прогона регрессии запрещена (FR-R3)
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1 and "FR-RG-3" in r.output
    # после зелёного прогона — разрешена, но только с пакетом, где есть ответ модели-Писателя (FR-RT-2)
    assert runner.invoke(app, ["regress"]).exit_code == 0
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1 and "нет пакета" in r.output, r.output
    (packs[-1] / "ответ_gemini-3.1-pro.md").write_text("Ответ ручного прогона.\n", encoding="utf-8")
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 0, r.output
    assert any(p.joinpath("журнал_запись.md").exists() for p in (ws.root / "пере-тест").iterdir())


def test_add_golden_через_cli_и_прогон(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    runner = CliRunner()
    n_before = len(list((ws.regression / "золотые").glob("*.json")))
    frag = ws.root / "фрагмент.md"
    frag.write_text("Он был предельно, невероятно и абсолютно точен. Очень.", encoding="utf-8")
    r = runner.invoke(app, ["add-golden", "красный: усилители", str(frag), "--expect", "V1.4_усилители", "--focal", "Каширин"])
    assert r.exit_code == 0, r.output
    files = list((ws.regression / "золотые").glob("*.json"))
    assert len(files) == n_before + 1
    r = runner.invoke(app, ["regress"])
    assert r.exit_code == 0, r.output
    report = json.loads((ws.regression / "report.json").read_text(encoding="utf-8"))
    assert report["всего"] >= n_before + 1


def test_регрессия_с_пустым_корпусом_не_зелёная(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    shutil.rmtree(ws.regression / "золотые")
    (ws.regression / "золотые").mkdir()
    from konveyer import regression
    r = CliRunner().invoke(app, ["regress"])
    assert "ПУСТ" in r.output.upper()
    assert regression.is_green(ws) is not True


# ------------------------------------------------------------- run после приёмки


def test_run_проходит_правки_и_останавливается_на_приёмке(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    review.save_edits(ws, 1, [])  # правок нет → Писатель не вызывается
    r = CliRunner().invoke(app, ["run", "1"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "дифф-контроль" and st.draft == 2 and "Пауза такта: приёмка автора" in r.output
    # принято → run строит пакет и останавливается на подписи
    st.transition("принято", "accept")
    r = CliRunner().invoke(app, ["run", "1"])
    assert r.exit_code == 0, r.output
    assert (ws.chapter_dir(1) / "пакет_канона.md").exists() and "подпишите пакет" in r.output


def test_run_пауза_при_нечистом_диффе(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    (ws.chapter_dir(1) / "правки.md").write_text("УКАЗАНИЕ: переписать вторую фразу\n", encoding="utf-8")

    def fake_apply(ws_, cfg, chapter, base_k, edits, new_k=None, **kw):
        writer._save_draft(ws_, chapter, new_k, "Первая фраза. Другая фраза. Третья фраза. Самоволие.\n", cfg, mode="правки")
        return new_k

    monkeypatch.setattr(writer, "apply_edits", fake_apply)
    r = CliRunner().invoke(app, ["run", "1"])
    assert r.exit_code == 0, r.output
    # FR-TK-3: самовольные изменения возвращают главу в «правки»; отчёт дифф.json остаётся
    assert "дифф-контроль не чист" in r.output and ChapterState(ws, 1).state == "правки"
    report = json.loads((ws.chapter_dir(1) / "дифф.json").read_text(encoding="utf-8"))
    assert report["unauthorized"]


def test_meta_черновика_пинует_модель_и_параметры(ws):
    writer._save_draft(ws, 3, 1, "Текст.", Config(), mode="генерация")
    meta = json.loads((ws.chapter_dir(3) / "черновик_1.meta.json").read_text(encoding="utf-8"))
    assert meta["model"] and "params" in meta and meta["mode"] == "генерация"


# ------------------------------------------------------------- панель: эндпоинты


def test_панель_черновики_дифф_лог_пакет_и_откат(panel, ws, library):
    _chapter_at_review(ws, 1)
    ws.draft_path(1, 2).write_text("Первая фраза. Иная фраза. Третья фраза.\n", encoding="utf-8")
    st = ChapterState(ws, 1)
    st.set_draft(2)
    st.transition("правки", "apply-edits (manual)")

    status, d = _get(f"{panel}/api/chapter/1/draft/1")
    assert status == 200 and "Вторая фраза" in d["text"]
    status, diff = _get(f"{panel}/api/chapter/1/diff/1/2")
    assert status == 200 and any("Иная" in line for line in diff["lines"])
    status, log = _get(f"{panel}/api/log")
    assert status == 200 and isinstance(log, list)

    status, _ = _post(f"{panel}/api/chapter/1/canon-batch", {"text": "# пакет\n"})
    assert status == 200 and (ws.chapter_dir(1) / "пакет_канона.md").read_text(encoding="utf-8").startswith("# пакет")

    status, r = _post(f"{panel}/api/chapter/1/rollback", {"to": "на-приёмке"})
    assert status == 200 and ChapterState(ws, 1).state == "на-приёмке"
    status, r = _post(f"{panel}/api/chapter/1/rollback", {"to": "зафиксировано"})
    assert status == 400


def test_панель_ручной_apply_edits_из_приёмки(panel, ws):
    _chapter_at_review(ws, 1)
    status, r = _post(f"{panel}/api/chapter/1/manual-draft", {"text": "Первая фраза. Другая фраза. Третья фраза.\n"})
    assert status == 200, r
    st = ChapterState(ws, 1)
    assert st.state == "правки" and st.draft == 2 and st.data.get("итераций_правок", 0) == 0
    assert ws.draft_path(1, 2).exists()
    # промпт правок и Э2 — по POST, GET ничего не пишет
    status, _ = _get(f"{panel}/api/chapter/1/prompt/edits")
    assert status == 200 and not (ws.chapter_dir(1) / "промпт_правок.md").exists()
    status, _ = _post(f"{panel}/api/chapter/1/prompt/edits", {})
    assert status == 200 and (ws.chapter_dir(1) / "промпт_правок.md").exists()
