"""Такт главы и адаптеры (аудит кластера «такт_адаптеры»): лимит авто-повторов Э1 (FR-TK-3, FR-V1-5),
ручной Писатель не расходует счётчик и называет файл и команду (FR-TK-6, FR-WR-4), решение автора принять брак
(§1.3), подсказка следующего шага для каждого состояния (FR-TK-5), сквозной такт командами в ручном режиме
(§7.2 «Приёмка»), возврат в «правки» при грязном дифф-контроле (FR-TK-3), приёмка только по актуальному
отчёту (FR-RV-4), ручной ввод Э2 «как есть» (FR-AD-3), сохранность пакета и окна (П-7), русские имена команд
в подсказках (П-8)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from konveyer import adapters, apilog, fsm, review, server, verifier2, writer
from konveyer.cli import app
from konveyer.config import Config, load_config
from konveyer.fsm import ChapterState
from konveyer.steps import common, tact

runner = CliRunner()

BRAK_TEXT = "Первая фраза. Вторая фраза.\n"  # объём 4 слова → БРАК по норме объёма демо-проекта


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def _git(lib, *args):
    subprocess.run(["git", "-C", str(lib), *args], check=True, capture_output=True)


def _init_repo(lib):
    for a in (["init", "-q"], ["config", "user.email", "автор@example.com"], ["config", "user.name", "Автор"],
              ["add", "-A"], ["commit", "-q", "-m", "канон: начальное состояние"]):
        _git(lib, *a)


def _compiled(ws, n=1) -> ChapterState:
    r = runner.invoke(app, ["собрать", str(n)])
    assert r.exit_code == 0, r.output
    return ChapterState(ws, n)


def _generated(ws, text, n=1) -> ChapterState:
    _compiled(ws, n)
    ws.draft_path(n, 1).write_text(text, encoding="utf-8")
    r = runner.invoke(app, ["написать", str(n), "--manual"])
    assert r.exit_code == 0, r.output
    return ChapterState(ws, n)


# ------------------------------------------------------------------ Э1: авто-повторы, ручной Писатель, решение автора


def test_фсм_авто_повтор_лимит(ws, monkeypatch, passing_draft):
    """Лимит из конфига: после N состоявшихся авто-повторов — стоп с кодом 1 и вердиктом; счётчик == N."""
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nauto_retries_verify1: 1\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    monkeypatch.setattr(adapters, "_sdk_missing", lambda p: False)
    calls = []

    def fake_write(ws_, cfg, chapter, k):
        calls.append(k)
        writer._save_draft(ws_, chapter, k, BRAK_TEXT, cfg, mode="генерация")

    monkeypatch.setattr(writer, "write_chapter", fake_write)
    _generated(ws, BRAK_TEXT)
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 1, r.output
    assert "после 1 авто-повторов" in r.output and "--принять-брак" in r.output and "написать 1 --manual" in r.output
    st = ChapterState(ws, 1)
    assert st.state == "сгенерировано" and st.data["авто_повторов"] == 1 and st.draft == 2 and calls == [2]
    assert (ws.chapter_dir(1) / "вердикт_1.json").exists()  # вердикт бракованного черновика сохранён
    # повторный вызов не тратит ещё одну генерацию — сразу стоп
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 1 and calls == [2]
    # подсказка «что дальше» учитывает исчерпанный лимит
    r = runner.invoke(app, ["статус", "1"])
    assert "БРАК Э1 после 1 авто-повторов" in r.output and "написать 1 --manual" in r.output

    # успешный проход Э1 через команду
    ws.draft_path(1, 3).write_text(passing_draft, encoding="utf-8")
    assert runner.invoke(app, ["написать", "1", "--manual"]).exit_code == 0
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 0 and "Э1 пройден" in r.output and "проверить2 1" in r.output, r.output
    assert ChapterState(ws, 1).state == "верифицировано-1"


def test_ручной_писатель_не_расходует_авто_повторы(ws):
    """Без ключа авто-повтор невозможен: счётчик не растёт, подсказка называет файл и команду (FR-WR-4)."""
    _generated(ws, BRAK_TEXT)
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 2, r.output
    assert "черновик_2.md" in r.output and "konveyer написать 1 --manual" in r.output and "проверить1 1" in r.output
    assert "Traceback" not in r.output and "§" not in r.output
    st = ChapterState(ws, 1)
    assert st.state == "сгенерировано" and st.data["авто_повторов"] == 0 and st.draft == 1
    # ручной провайдер в конфиге — то же самое
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nwriter:\n  provider: ручной\n  model: '—'\n", encoding="utf-8")
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 2 and "ручной провайдер" in r.output and "черновик_2.md" in r.output
    assert ChapterState(ws, 1).data["авто_повторов"] == 0


def test_сбой_вызова_писателя_не_засчитывается_как_авто_повтор(ws, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    monkeypatch.setattr(adapters, "_sdk_missing", lambda p: False)

    def boom(*a, **k):
        raise adapters.ManualModeNeeded("сеть упала", "общая подсказка")

    monkeypatch.setattr(writer, "write_chapter", boom)
    _generated(ws, BRAK_TEXT)
    r = runner.invoke(app, ["проверить1", "1"])
    assert r.exit_code == 2 and "сеть упала" in r.output and "черновик_2.md" in r.output, r.output
    assert ChapterState(ws, 1).data["авто_повторов"] == 0


def test_принять_брак_решением_автора(ws):
    """Брак Э1 преодолевается только явным решением автора с причиной; решение — в истории и журнале."""
    _generated(ws, BRAK_TEXT)
    r = runner.invoke(app, ["проверить1", "1", "--принять-брак"])
    assert r.exit_code == 1 and "причин" in r.output and ChapterState(ws, 1).state == "сгенерировано"
    r = runner.invoke(app, ["проверить1", "1", "--принять-брак", "--причина", "короткая интермедия по замыслу"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "верифицировано-1" and st.data["брак_принят"]["причина"] == "короткая интермедия по замыслу"
    assert "V1.2e_объём" in st.data["брак_принят"]["метрики"]
    assert "брак принят автором" in st.data["история"][-1]["команда"]
    log = (ws.logs / tact.AUTHOR_DECISIONS_LOG).read_text(encoding="utf-8")
    entry = json.loads(log.splitlines()[-1])
    assert entry["глава"] == 1 and entry["решение"] == "принять брак Э1" and entry["причина"]


def test_write_без_окна_понятная_ошибка(ws):
    st = ChapterState(ws, 1)
    st.transition("собрано", "compile")  # окна нет
    r = runner.invoke(app, ["написать", "1"])
    assert r.exit_code == 1 and "окно.md" in r.output and "собрать 1" in r.output and "Traceback" not in r.output
    assert "Errno" not in r.output and str(ws.root) not in r.output


def test_write_ручной_режим_называет_файл_и_команду(ws):
    _compiled(ws, 1)
    r = runner.invoke(app, ["написать", "1"])
    assert r.exit_code == 2, r.output
    assert "черновик_1.md" in r.output and "konveyer написать 1 --manual" in r.output and "окно.md" in r.output
    assert "промпт сохранён" not in r.output  # промпт и есть окно


# ------------------------------------------------------------------ подсказка следующего шага (FR-TK-5)


def test_подсказка_покрывает_все_состояния():
    import re

    assert set(common.NEXT_STEP) == set(fsm.STATES)
    for state, hint in common.NEXT_STEP.items():
        assert " — " in hint, state  # команда и её смысл
        assert not re.search(r"konveyer [a-z]", hint), state  # русские имена команд (П-8)


@pytest.mark.parametrize("state", fsm.STATES)
def test_подсказка_следующего_шага(ws, library, state):
    """Для каждого состояния: подсказка непуста, одна и та же в `статус N` и в панели (/api/state)."""
    st = ChapterState(ws, 1)
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    st.data["состояние"] = state
    st._save()
    cfg = load_config(ws)
    expected = common.next_step(ws, ChapterState(ws, 1), cfg)
    assert expected
    r = runner.invoke(app, ["статус", "1"])
    assert r.exit_code == 0 and expected in r.output, r.output
    api = server.PanelAPI(ws, cfg, library)
    card = [c for c in api.state()["chapters"] if c["chapter"] == 1][0]
    assert card["next"] == expected
    assert api.chapter(1)["next"] == expected


def test_подсказка_учитывает_контекст(ws):
    st = ChapterState(ws, 1)
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки", "дифф-контроль"]:
        st.transition(s)
    cfg = Config()
    assert "дифф-контроль 1" in common.next_step(ws, st, cfg)  # отчёта нет → просят прогнать дифф-контроль
    (ws.chapter_dir(1) / "дифф.json").write_text(json.dumps({"draft_after": 0, "not_applied": [2], "unauthorized": []}), encoding="utf-8")
    assert "правки-внести 1" in common.next_step(ws, st, cfg)
    st.transition("принято")
    assert "канон 1 → konveyer канон 1 --apply" in common.next_step(ws, st, cfg)
    (ws.chapter_dir(1) / "пакет_канона.md").write_text("пакет", encoding="utf-8")
    assert common.next_step(ws, st, cfg).startswith("konveyer канон 1 --apply")


def test_подсказки_без_латинских_имён_команд(ws):
    """П-8: в выводе такта и статуса нет скрытых латинских имён команд (их нет в справке)."""
    import re

    _generated(ws, BRAK_TEXT)
    outputs = [runner.invoke(app, ["статус"]).output, runner.invoke(app, ["статус", "1"]).output,
               runner.invoke(app, ["проверить1", "1"]).output, runner.invoke(app, ["написать", "1"]).output]
    latin = re.compile(r"konveyer [a-z][a-z0-9-]*")
    for out in outputs:
        assert not latin.search(out), out


# ------------------------------------------------------------------ сквозной такт командами в ручном режиме


def test_такт_сквозной_ручной_режим(ws, library, passing_draft):
    """От «не-начато» до «зафиксировано» на демо без моделей: каждый шаг — команда, после каждой —
    новый ChapterState (как после перезапуска процесса)."""
    _init_repo(library)
    n = "1"

    def state():
        return ChapterState(ws, 1)  # перечитывается с диска

    assert runner.invoke(app, ["собрать", n]).exit_code == 0 and state().state == "собрано"
    ws.draft_path(1, 1).write_text(passing_draft, encoding="utf-8")
    assert runner.invoke(app, ["написать", n, "--manual"]).exit_code == 0 and state().state == "сгенерировано"
    assert "(manual)" in state().data["история"][-1]["команда"]
    r = runner.invoke(app, ["проверить1", n])
    assert r.exit_code == 0 and state().state == "верифицировано-1", r.output
    r = runner.invoke(app, ["проверить2", n])
    assert r.exit_code == 2 and "флаги.json" in r.output and "проверить2 1 --manual" in r.output
    (ws.chapter_dir(1) / "флаги.json").write_text(
        'Вот флаги:\n```json\n[{"flag_id": "F-001", "type": "самоволка", "quote": "Ветер стих", '
        '"rule": "в брифе нет", "kind": "samovolka"}]\n```\nКонец.', encoding="utf-8")
    r = runner.invoke(app, ["проверить2", n, "--manual"])
    assert r.exit_code == 0 and state().state == "верифицировано-2", r.output
    assert state().data["история"][-1]["команда"] == "verify2 (manual)"  # ручной ввод отмечен (FR-TK-6)
    assert json.loads((ws.chapter_dir(1) / "флаги.json").read_text(encoding="utf-8"))[0]["flag_id"] == "F-001"  # нормализован
    assert runner.invoke(app, ["приёмка", n]).exit_code == 0 and state().state == "на-приёмке"
    assert runner.invoke(app, ["решение", n, "F-001", "вычеркнуть"]).exit_code == 0
    (ws.chapter_dir(1) / "правки.md").write_text("БЫЛО: Снег таял медленно.\nСТАЛО: Снег таял быстро.\n", encoding="utf-8")
    r = runner.invoke(app, ["правки-внести", n])  # дословная пара — кодом, без модели
    assert r.exit_code == 0 and state().state == "правки" and state().draft == 2, r.output
    r = runner.invoke(app, ["дифф-контроль", n])
    assert r.exit_code == 0 and state().state == "дифф-контроль" and "чист" in r.output, r.output
    r = runner.invoke(app, ["принять", n, "--yes"])
    assert r.exit_code == 0 and state().state == "принято", r.output
    r = runner.invoke(app, ["канон", n])
    assert r.exit_code == 0 and (ws.chapter_dir(1) / "пакет_канона.md").exists(), r.output
    r = runner.invoke(app, ["канон", n, "--apply", "--yes"])
    assert r.exit_code == 0 and state().state == "зафиксировано", r.output
    sha = state().data["коммит_приёмки"]
    from konveyer import gitops

    assert gitops.find_chapter_commit(library, 1) == sha and "глава-1" in gitops.tags(library)
    assert "Снег таял быстро." in (library / "Проза" / "Том1_Глава01.md").read_text(encoding="utf-8")
    r = runner.invoke(app, ["статус", n])
    assert "готово ✓" in r.output


# ------------------------------------------------------------------ дифф-контроль, приёмка, Э2 вручную


def _at_review(ws, text="Первая фраза. Вторая фраза. Третья фраза.\n") -> ChapterState:
    st = ChapterState(ws, 1)
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text(text, encoding="utf-8")
    st.transition("собрано", "compile")
    st.set_draft(1)
    for s, c in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(s, c)
    (ws.chapter_dir(1) / "вердикт.json").write_text(json.dumps({"chapter": 1, "draft": 1, "checks": []}), encoding="utf-8")
    verifier2.save_flags(ws, 1, [])
    review.build_review_pack(ws, 1, 1)
    st.data["база_приёмки"] = 1
    st.transition("на-приёмке", "review")
    return ChapterState(ws, 1)


def test_фсм_дифф_возврат_в_правки(ws):
    """Самовольные изменения на дифф-контроле возвращают в «правки» (FR-TK-3); приёмка недоступна;
    следующая итерация правок идёт из «правки»."""
    _at_review(ws)
    (ws.chapter_dir(1) / "правки.md").write_text("БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n", encoding="utf-8")
    ws.draft_path(1, 2).write_text("Первая фраза. Другая фраза. Третья фраза. Самоволие.\n", encoding="utf-8")
    assert runner.invoke(app, ["правки-внести", "1", "--manual"]).exit_code == 0
    r = runner.invoke(app, ["дифф-контроль", "1"])
    assert r.exit_code == 0 and "возвращена в «правки»" in r.output and "правки-внести 1" in r.output, r.output
    st = ChapterState(ws, 1)
    assert st.state == "правки" and [h["в"] for h in st.data["история"][-2:]] == ["дифф-контроль", "правки"]
    assert (ws.chapter_dir(1) / "дифф.json").exists()
    r = runner.invoke(app, ["принять", "1", "--yes"])
    assert r.exit_code == 1 and "правки" in r.output
    # новая итерация из «правки»: чистый черновик → дифф-контроль чист → приёмка
    ws.draft_path(1, 3).write_text("Первая фраза. Другая фраза. Третья фраза.\n", encoding="utf-8")
    assert runner.invoke(app, ["правки-внести", "1", "--manual"]).exit_code == 0
    assert ChapterState(ws, 1).state == "правки" and ChapterState(ws, 1).draft == 3
    r = runner.invoke(app, ["дифф-контроль", "1"])
    assert r.exit_code == 0 and ChapterState(ws, 1).state == "дифф-контроль" and "чист" in r.output
    assert runner.invoke(app, ["принять", "1", "--yes"]).exit_code == 0
    # самовольные изменения снимает только автор (--авторская-правка)
    st = ChapterState(ws, 1)
    st.rollback("правки")
    ws.draft_path(1, 3).write_text("Первая фраза. Другая фраза. Третья фраза. Правка автора.\n", encoding="utf-8")
    r = runner.invoke(app, ["дифф-контроль", "1", "--авторская-правка"])
    assert r.exit_code == 0 and ChapterState(ws, 1).state == "дифф-контроль", r.output


def test_приёмка_требует_актуальный_дифф(ws):
    """Приёмка только из «дифф-контроль» с существующим и относящимся к текущему черновику отчётом (FR-RV-4)."""
    _at_review(ws)
    (ws.chapter_dir(1) / "правки.md").write_text("", encoding="utf-8")
    assert runner.invoke(app, ["правки-внести", "1"]).exit_code == 0
    assert runner.invoke(app, ["дифф-контроль", "1"]).exit_code == 0
    assert ChapterState(ws, 1).state == "дифф-контроль"
    report = ws.chapter_dir(1) / "дифф.json"
    report.unlink()
    r = runner.invoke(app, ["принять", "1", "--yes"])
    assert r.exit_code == 1 and "дифф-контроль 1" in r.output and ChapterState(ws, 1).state == "дифф-контроль"
    report.write_text(json.dumps({"chapter": 1, "draft_before": 1, "draft_after": 7, "applied_share": 1.0}), encoding="utf-8")
    r = runner.invoke(app, ["принять", "1", "--yes"])
    assert r.exit_code == 1 and "черновику 7" in r.output
    report.write_text("{битый", encoding="utf-8")
    r = runner.invoke(app, ["принять", "1", "--yes"])
    assert r.exit_code == 1 and "повреждён" in r.output and "Traceback" not in r.output
    assert runner.invoke(app, ["дифф-контроль", "1"]).exit_code == 0
    assert runner.invoke(app, ["принять", "1", "--yes"]).exit_code == 0


def test_э2_вручную_ответ_как_есть_и_вкус(ws):
    """`проверить2 --manual` принимает ответ модели с прозой и ограждением; `--вкус --manual` — файлом вкус.json."""
    st = ChapterState(ws, 1)
    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст для проверки.\n", encoding="utf-8")
    st.transition("собрано")
    st.set_draft(1)
    st.transition("сгенерировано")
    st.transition("верифицировано-1")
    (ws.chapter_dir(1) / "флаги.json").write_text("Нарушений нет:\n```json\n[]\n```\n", encoding="utf-8")
    (ws.chapter_dir(1) / "вкус.json").write_text('Советы:\n[{"flag_id": "T-1", "type": "ритм", "quote": "Текст", "rule": "монотонно"}]',
                                                  encoding="utf-8")
    r = runner.invoke(app, ["проверить2", "1", "--manual", "--вкус"])
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, 1).state == "верифицировано-2"
    assert json.loads((ws.chapter_dir(1) / "флаги.json").read_text(encoding="utf-8")) == []
    taste = verifier2.load_taste(ws, 1)
    assert len(taste) == 1 and taste[0].type == "вкус" and taste[0].severity == "мелочь"
    # нечитаемый ответ — понятная ошибка, а не трейсбек
    ChapterState(ws, 1).rollback("верифицировано-1")
    (ws.chapter_dir(1) / "флаги.json").write_text("Тут вообще нет JSON.", encoding="utf-8")
    r = runner.invoke(app, ["проверить2", "1", "--manual"])
    assert r.exit_code == 1 and "флаги.json" in r.output and "Traceback" not in r.output
    # статус не падает на файле флагов «как есть» (FR-AD-3)
    (ws.chapter_dir(1) / "флаги.json").write_text('Ответ:\n[{"flag_id": "F-9", "type": "бриф", "quote": "Текст", "rule": "—"}]', encoding="utf-8")
    r = runner.invoke(app, ["статус", "1"])
    assert r.exit_code == 0 and "F-9" in r.output


# ------------------------------------------------------------------ сохранность артефактов (П-7)


def test_канон_заново_сохраняет_правки_автора(ws, library):
    _init_repo(library)
    _at_review(ws)
    (ws.chapter_dir(1) / "правки.md").write_text("", encoding="utf-8")
    assert runner.invoke(app, ["правки-внести", "1"]).exit_code == 0
    assert runner.invoke(app, ["дифф-контроль", "1"]).exit_code == 0
    assert runner.invoke(app, ["принять", "1", "--yes"]).exit_code == 0
    assert runner.invoke(app, ["канон", "1"]).exit_code == 0
    batch = ws.chapter_dir(1) / "пакет_канона.md"
    batch.write_text(batch.read_text(encoding="utf-8") + "\n# моя правка\n", encoding="utf-8")
    r = runner.invoke(app, ["канон", "1"])
    assert r.exit_code == 1 and "--заново" in r.output and "моя правка" in batch.read_text(encoding="utf-8")
    r = runner.invoke(app, ["канон", "1", "--заново"])
    assert r.exit_code == 0 and "Прежний пакет сохранён" in r.output, r.output
    copies = list(ws.chapter_dir(1).glob("пакет_канона.*.md"))
    assert len(copies) == 1 and "моя правка" in copies[0].read_text(encoding="utf-8")
    assert "моя правка" not in batch.read_text(encoding="utf-8")


def test_собрать_окно_зафиксированной_главы_отказ_и_копия_окна(ws):
    _generated(ws, BRAK_TEXT)
    old_window = ws.window_path(1).read_text(encoding="utf-8")
    r = runner.invoke(app, ["собрать", "1"])  # из «сгенерировано»: пересборка с копией прежнего окна
    assert r.exit_code == 0 and "окно_1.md" in r.output, r.output
    assert (ws.chapter_dir(1) / "окно_1.md").read_text(encoding="utf-8") == old_window
    st = ChapterState(ws, 1)
    st.data["состояние"] = "зафиксировано"
    st._save()
    r = runner.invoke(app, ["собрать", "1"])
    assert r.exit_code == 1 and "зафиксирована" in r.output and "откат 1" in r.output


# ------------------------------------------------------------------ адаптеры


class _Http(Exception):
    def __init__(self, status, text="bad"):
        super().__init__(text)
        self.status_code = status


def test_адаптер_ключ_маскируется_до_усечения(monkeypatch):
    key = "sk-ant-api03-" + "A" * 80
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    for prefix_len in (150, 170, 185, 195):  # граница усечения проходит по ключу
        err = _Http(401, "x" * prefix_len + " key " + key + " rejected")
        msg = adapters.explain_error(err, "Писатель")
        assert "sk-ant-api03-A" not in msg and "AAAAAAAA" not in msg, msg
        assert len(msg) < 400
    _, note = adapters.probe_model.__wrapped__(None) if hasattr(adapters.probe_model, "__wrapped__") else (None, "")
    assert "AAAA" not in adapters._short(_Http(500, "x" * 110 + key), 120)


def test_адаптер_обрыв_по_лимиту_токенов(ws, monkeypatch):
    """stop_reason max_tokens: усечённый текст не принимается за полный, ошибка объясняет, что поднять."""
    import sys
    import types

    class _Usage:
        input_tokens = 1
        output_tokens = 2

    class _Resp:
        content = [types.SimpleNamespace(type="text", text="обрезанный текст")]
        usage = _Usage()
        stop_reason = "max_tokens"

    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: _Resp()))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ключ-ключ")
    from konveyer.config import ApiConfig, ModelConfig

    mc = ModelConfig(provider="anthropic", model="m", params={"max_tokens": 50})
    with pytest.raises(adapters.ManualModeNeeded) as e:
        adapters.call_anthropic("s", "u", mc, ApiConfig(retries=3, backoff_base_s=0.0), ws.logs, role="т")
    assert "max_tokens=50" in e.value.reason and "поднимите" in e.value.hint and "после" not in e.value.reason
    rows = apilog.read_log(ws.logs)
    assert len(rows) == 1 and rows[0]["error"].startswith("обрыв") and rows[0]["version"]


def test_адаптер_программная_ошибка_не_сеть_и_число_попыток(ws):
    from konveyer.config import ApiConfig, ModelConfig

    mc = ModelConfig(provider="anthropic", model="m")
    calls = []

    def bad_schema():
        calls.append(1)
        raise AttributeError("'NoneType' object has no attribute 'text'")

    with pytest.raises(adapters.ManualModeNeeded) as e:
        adapters._retry_call(bad_schema, ApiConfig(retries=3, backoff_base_s=0.0), ws.logs, role="т", mc=mc, chapter=None)
    assert len(calls) == 1 and "попыток" not in e.value.reason and "ошибка запроса" in e.value.reason
    assert adapters.classify_error(ValueError("blocked")) == "клиент"
    assert adapters.classify_error(ConnectionError("x")) == "сеть"
    assert adapters.classify_error(TimeoutError("x")) == "сеть"

    calls.clear()

    def flaky():
        calls.append(1)
        raise _Http(503, "unavailable")

    with pytest.raises(adapters.ManualModeNeeded) as e:
        adapters._retry_call(flaky, ApiConfig(retries=3, backoff_base_s=0.0), ws.logs, role="т", mc=mc, chapter=None)
    assert len(calls) == 3 and "после 3 попыток" in e.value.reason


def test_адаптер_пауза_между_повторами_прерывается_отменой(ws, monkeypatch):
    from konveyer import cancel
    from konveyer.config import ApiConfig, ModelConfig

    slept = []
    monkeypatch.setattr(adapters.time, "sleep", lambda s: slept.append(s) or cancel.request())
    monkeypatch.setattr(adapters, "_SLEEP_SLICE_S", 0.01)

    def flaky():
        raise _Http(503, "unavailable")

    cancel.clear()
    with pytest.raises(cancel.Cancelled, match="повтор"):
        adapters._retry_call(flaky, ApiConfig(retries=3, backoff_base_s=60.0), ws.logs, role="т",
                             mc=ModelConfig(provider="anthropic", model="m"), chapter=None)
    assert len(slept) == 1 and slept[0] <= 0.01  # первая же порция паузы, не 60 с
    assert not cancel.requested()


def test_недоступность_модели_без_вызова(monkeypatch):
    from konveyer.config import ModelConfig

    assert "ручной" in adapters.unavailable_reason(ModelConfig(provider="ручной", model="—"), "писатель")
    assert "GEMINI_API_KEY" in adapters.unavailable_reason(ModelConfig(provider="gemini", model="g"), "писатель")
    assert "провайдер" in adapters.unavailable_reason(ModelConfig(provider="чужой", model="g"), "писатель")
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    monkeypatch.setattr(adapters, "_sdk_missing", lambda p: False)
    assert adapters.unavailable_reason(ModelConfig(provider="gemini", model="g"), "писатель") is None


# ------------------------------------------------------------------ оценка стоимости и пины для всех ролей


def test_оценка_и_пин_перед_вызовом_любой_роли(ws, monkeypatch, passing_draft):
    """Перед вызовом Верификатора-2 (не только Писателя) печатаются оценка стоимости и предупреждение
    о смене пина роли (FR-EC-1, FR-RT-2)."""
    from konveyer import pins

    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nverifier2:\n  provider: gemini\n  model: v-old\n  price_in_per_1m: 1.0\n  price_out_per_1m: 2.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    pins.record(ws, load_config(ws))  # пин зафиксирован на v-old
    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nverifier2:\n  provider: gemini\n  model: v-new\n  price_in_per_1m: 1.0\n  price_out_per_1m: 2.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(adapters, "call_gemini", lambda *a, **k: "[]")
    _generated(ws, passing_draft)
    assert runner.invoke(app, ["проверить1", "1"]).exit_code == 0
    r = runner.invoke(app, ["проверить2", "1"])
    assert r.exit_code == 0, r.output
    assert "Оценка стоимости вызова «верификатор-2»" in r.output
    assert "пин модели изменён без пере-теста" in r.output and "v-old" in r.output and "v-new" in r.output


def test_битый_состояние_и_чужой_том_без_трейсбека(ws):
    """Крайние случаи: несуществующая глава/том и команда вне проекта — сообщение, не трейсбек."""
    r = runner.invoke(app, ["проверить1", "99"])
    assert r.exit_code == 1 and "Traceback" not in r.output and "99" in r.output
    r = runner.invoke(app, ["статус", "--том", "7"])
    assert r.exit_code == 0 and "тома 7 в работе нет" in r.output
    r = runner.invoke(app, ["принять", "5", "--yes"])
    assert r.exit_code == 1 and "не-начато" in r.output and "Traceback" not in r.output


def test_команда_вне_проекта(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp("пусто")  # не рабочая область фикстуры ws
    monkeypatch.chdir(tmp_path)
    for args in (["статус"], ["собрать", "1"], ["проверить1", "1"], ["учёт"], ["такт", "1"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and "Traceback" not in r.output, (args, r.output)
        assert "нет проекта" in r.output and "начать --демо" in r.output, (args, r.output)
        assert str(tmp_path) not in r.output  # без абсолютных путей (FR-SC-9)
    assert not (tmp_path / "журналы").exists()  # ничего не записано в случайную папку
    r = runner.invoke(app, ["доктор"])  # диагностика работает и вне проекта
    assert r.exit_code == 0 and "конфиг.yaml" in r.output


def test_фсм_недопустимые_переходы_командами(ws):
    """Команда не из своего состояния — понятный отказ без трейсбека, состояние не тронуто."""
    _compiled(ws, 1)
    for args in (["проверить1", "1"], ["проверить2", "1"], ["приёмка", "1"], ["правки-внести", "1"],
                 ["дифф-контроль", "1"], ["принять", "1", "--yes"], ["канон", "1"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and "команда требует" in r.output and "Traceback" not in r.output, (args, r.output)
    assert ChapterState(ws, 1).state == "собрано"
    with pytest.raises(fsm.TransitionError):
        ChapterState(ws, 1).transition("принято")
    with pytest.raises(fsm.TransitionError):
        ChapterState(ws, 1).transition("не-существует")


def test_write_варианты_ручной_режим_называет_файлы(ws, passing_draft):
    _compiled(ws, 1)
    r = runner.invoke(app, ["написать", "1", "--варианты", "2"])
    assert r.exit_code == 2, r.output
    assert "черновик_1.md" in r.output and "черновик_1.alt1.md" in r.output and "написать 1 --manual --варианты 2" in r.output
    ws.draft_path(1, 1).write_text(passing_draft, encoding="utf-8")
    shutil.copyfile(ws.draft_path(1, 1), ws.chapter_dir(1) / "черновик_1.alt1.md")
    r = runner.invoke(app, ["написать", "1", "--manual", "--варианты", "2"])
    assert r.exit_code == 0 and ChapterState(ws, 1).state == "сгенерировано", r.output


def test_параллельность_запрещена_замок_проекта(ws, passing_draft):
    """FR-TK-7: пока в проекте идёт задача другого процесса, вторая команда получает понятный отказ;
    замок мёртвого процесса снимается сам."""
    import os
    import subprocess
    import sys

    from konveyer import steps

    _compiled(ws, 1)
    ws.draft_path(1, 1).write_text(passing_draft, encoding="utf-8")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = ws.logs / steps.JOB_LOCK
        lock.write_text(json.dumps({"pid": other.pid, "задача": "write", "время": "2025-01-01T00:00:00+00:00"}), encoding="utf-8")
        r = runner.invoke(app, ["написать", "1", "--manual"])
        assert r.exit_code == 1 and "уже выполняется задача «write»" in r.output and str(other.pid) in r.output, r.output
        assert "Traceback" not in r.output and ChapterState(ws, 1).state == "собрано"
    finally:
        other.kill()
        other.wait()
    # процесс мёртв → замок осиротел → команда проходит и снимает замок за собой
    r = runner.invoke(app, ["написать", "1", "--manual"])
    assert r.exit_code == 0 and ChapterState(ws, 1).state == "сгенерировано", r.output
    assert not lock.exists()
    # замок нашего же процесса (задача панели в том же процессе) не блокирует
    lock.write_text(json.dumps({"pid": os.getpid(), "задача": "run"}), encoding="utf-8")
    assert runner.invoke(app, ["статус"]).exit_code == 0
    assert not lock.exists()


def test_init_создаёт_gitignore_с_env(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("новая")
    monkeypatch.chdir(root)
    (root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    r = runner.invoke(app, ["начать"])
    assert r.exit_code == 0, r.output
    text = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert text[0] == "*.pyc" and ".env" in text and "журналы/" in text
    r = runner.invoke(app, ["начать"])  # повторно — без дублей
    assert (root / ".gitignore").read_text(encoding="utf-8").count(".env") == 1
    assert "konveyer начать --демо" in r.output and "konveyer init" not in r.output
