"""Тесты продуктовых улучшений: doctor, status, resolve, edits, check, diff, init --демо…"""

import subprocess

from typer.testing import CliRunner

from konveyer import review
from konveyer.apilog import log_call
from konveyer.cli import app
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution

runner = CliRunner()


def test_status_подсказывает_следующий_шаг(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 1)
    st.transition("собрано")
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "konveyer write 1" in r.output


def test_status_карточка_главы(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано"]:
        st.transition(s)
    from konveyer import verifier2

    verifier2.save_flags(ws, 1, [Flag(flag_id="F-001", type="самоволка", quote="цитата", rule="—", kind="samovolka")])
    review.save_resolutions(ws, 1, [Resolution(flag_id="F-001")])
    r = runner.invoke(app, ["status", "1"])
    assert r.exit_code == 0
    assert "F-001" in r.output and "Без решения автора" in r.output
    assert "konveyer verify1 1" in r.output


def test_resolve_список_и_решение(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    from konveyer import verifier2

    verifier2.save_flags(ws, 2, [Flag(flag_id="F-002", type="самоволка", quote="запах табака", rule="—", kind="samovolka")])
    review.save_resolutions(ws, 2, [Resolution(flag_id="F-002")])

    r = runner.invoke(app, ["resolve", "2"])
    assert r.exit_code == 0 and "F-002" in r.output and "БЕЗ РЕШЕНИЯ" in r.output

    r = runner.invoke(app, ["resolve", "2", "F-002", "канонизировать", "--реестр", "эпистемика"])
    assert r.exit_code == 0, r.output
    saved = review.load_resolutions(ws, 2)
    assert saved[0].decision == "канонизировать" and saved[0].target_registry == "эпистемика"

    assert runner.invoke(app, ["resolve", "2", "F-002", "удалить"]).exit_code == 1  # неверное решение
    assert runner.invoke(app, ["resolve", "2", "F-999", "вычеркнуть"]).exit_code == 1  # нет такой


def test_edits_предпросмотр(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    d = ws.chapter_dir(3)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(3, 1).write_text("Чай остыл. Зоя молчала.", encoding="utf-8")
    ChapterState(ws, 3).set_draft(1)
    (d / "правки.md").write_text(
        "БЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\n\nБЫЛО: Нет такой фразы.\nСТАЛО: Другая.\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["edits", "3"])
    assert r.exit_code == 0
    assert "✓ найдено" in r.output and "НЕ найдено" in r.output and "[2]" in r.output


def test_check_произвольного_файла(ws, monkeypatch, tmp_path):
    monkeypatch.chdir(ws.root)
    f = ws.root / "кусок.md"
    f.write_text("Менталитет был странный. Всё было очень тихо и совершенно пусто.", encoding="utf-8")
    r = runner.invoke(app, ["check", str(f), "--фокал", "Каширин", "--год", "1995"])
    assert r.exit_code == 0, r.output
    assert "V1.5_стоп_лексика" in r.output and "Итог: BRAK" in r.output


def test_diff_черновиков(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 4)
    ws.chapter_dir(4).mkdir(parents=True, exist_ok=True)
    ws.draft_path(4, 1).write_text("Первая строка.\nВторая строка.\n", encoding="utf-8")
    ws.draft_path(4, 2).write_text("Первая строка.\nВторая строка изменена.\n", encoding="utf-8")
    st.set_draft(2)
    r = runner.invoke(app, ["diff", "4"])
    assert r.exit_code == 0
    assert "-Вторая строка." in r.output and "+Вторая строка изменена." in r.output
    ws.draft_path(4, 3).write_text("Первая строка.\nВторая строка изменена.\n", encoding="utf-8")
    r = runner.invoke(app, ["diff", "4", "2", "3"])
    assert "идентичны" in r.output


def test_init_демо_играбелен(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init", "--демо"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "Библиотека" / "02_Стиль_и_голос.md").exists()
    assert list((tmp_path / "регрессия" / "золотые").glob("*.json"))
    # сквозной smoke на развёрнутом демо
    assert runner.invoke(app, ["export"]).exit_code == 0
    assert runner.invoke(app, ["compile", "1"]).exit_code == 0
    assert runner.invoke(app, ["regress"]).exit_code == 0


def test_doctor(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "конфиг.yaml" in r.output
    assert "GEMINI_API_KEY" in r.output and "ANTHROPIC_API_KEY" in r.output
    assert "библиотека под git" in r.output  # демо-библиотека без git — подсказка


def test_rollback_на_шаг_назад(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 5)
    st.transition("собрано")
    st.transition("сгенерировано")
    r = runner.invoke(app, ["rollback", "5", "-y"])
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, 5).state == "собрано"
    # пустая история — просим явный --to
    assert runner.invoke(app, ["rollback", "6", "-y"]).exit_code == 1


def test_backup_push(ws, library, monkeypatch, tmp_path):
    monkeypatch.chdir(ws.root)
    for args in (["init"], ["config", "user.email", "a@b.c"], ["config", "user.name", "А"],
                 ["add", "-A"], ["commit", "-m", "старт"]):
        subprocess.run(["git", "-C", str(library), *args], check=True, capture_output=True)
    bare = tmp_path / "backup.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(library), "remote", "add", "резерв", str(bare)], check=True, capture_output=True)
    r = runner.invoke(app, ["backup", "--push", "-y"])
    assert r.exit_code == 0, r.output
    assert "✓ резерв" in r.output
    log = subprocess.run(["git", "-C", str(bare), "log", "--oneline"], capture_output=True, text=True, encoding="utf-8").stdout
    assert "старт" in log


def test_log_показывает_вызовы(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    log_call(ws.logs, role="писатель", model="m-1", tokens_in=100, tokens_out=200, cost_est=0.0123, chapter=1, duration=2.5)
    log_call(ws.logs, role="верификатор-2", model="m-2", chapter=1, duration=1.0, error="Boom")
    r = runner.invoke(app, ["log"])
    assert r.exit_code == 0
    assert "писатель" in r.output and "0.0123" in r.output and "ОШИБКА" in r.output
