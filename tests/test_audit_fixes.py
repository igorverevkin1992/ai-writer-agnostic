"""Регрессионные тесты аудита: закрепляют исправленные дефекты."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from konveyer import adapters, exporter, regression, review, textutils
from konveyer.cli import app
from konveyer.config import Config, ModelConfig
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# --- баг: typer.OptionInfo истинен → run шёл по веткам --manual/--авторская-правка


def test_run_не_пропускает_э2(ws, monkeypatch):
    """`run` на «верифицировано-1» без API обязан остановиться (код 2),
    а не тихо принять отсутствующий флаги.json и перескочить Э2."""
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1"]:
        st.transition(s)
    d = ws.draft_path(1, 1)
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("Текст главы для проверки.", encoding="utf-8")
    st.set_draft(1)

    r = runner.invoke(app, ["run", "1"])
    assert r.exit_code == 2, r.output
    assert ChapterState(ws, 1).state == "верифицировано-1"  # Э2 не перескочен
    assert not (ws.chapter_dir(1) / "флаги.json").exists()


def test_verify2_manual_без_файла_ошибка(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 1)
    for s in ["собрано", "сгенерировано", "верифицировано-1"]:
        st.transition(s)
    r = runner.invoke(app, ["verify2", "1", "--manual"])
    assert r.exit_code == 1
    assert ChapterState(ws, 1).state == "верифицировано-1"


def test_apply_edits_лимит_итераций_стоп(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 2)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    st.data["итераций_правок"] = 3
    st._save()
    (ws.chapter_dir(2) / "правки.md").write_text("БЫЛО: а\nСТАЛО: б\n", encoding="utf-8")
    r = runner.invoke(app, ["apply-edits", "2"])
    assert r.exit_code == 1
    assert "лимит" in r.output + (r.stderr or "") and "--manual" in r.output + (r.stderr or "")
    assert ChapterState(ws, 2).state == "на-приёмке"  # состояние не тронуто


def test_недопустимый_переход_читаемая_ошибка(ws, monkeypatch):
    """TransitionError — сообщение, а не трейсбек."""
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["verify1", "9"])
    assert r.exit_code == 1
    assert r.exception is None or isinstance(r.exception, SystemExit)


# --- сценарий Б: canon-commit


def _init_git(lib):
    for args in (["init"], ["config", "user.email", "a@b.c"], ["config", "user.name", "Автор"],
                 ["add", "-A"], ["commit", "-m", "начало"]):
        subprocess.run(["git", "-C", str(lib), *args], check=True, capture_output=True)


def test_canon_commit_предупреждение_о_нормах(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    _init_git(library)
    p02 = library / "02_Стиль_и_голос.md"
    p02.write_text(p02.read_text(encoding="utf-8").replace(
        "| был_на_250 | «был/было/были» на 250 слов | — | 1 |",
        "| был_на_250 | «был/было/были» на 250 слов | — | 2 |"), encoding="utf-8")
    r = runner.invoke(app, ["canon-commit", "-m", "ослабил норму был", "-y"])
    assert r.exit_code == 0, r.output
    assert "Р-№" in r.output  # предупреждение: нормы изменены без ссылки на журнал
    log = subprocess.run(["git", "-C", str(library), "log", "-1", "--format=%s"],
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "ослабил норму был" in log

    # со ссылкой Р-№ предупреждения нет
    p02.write_text(p02.read_text(encoding="utf-8").replace("| — | 2 |", "| — | 3 |"), encoding="utf-8")
    r = runner.invoke(app, ["canon-commit", "-m", "правка норм (Р-020)", "-y"])
    assert r.exit_code == 0 and "Р-№" not in r.output


def test_canon_commit_без_изменений(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    _init_git(library)
    r = runner.invoke(app, ["canon-commit", "-m", "пусто", "-y"])
    assert r.exit_code == 0 and "нет изменений" in r.output


# --- точный поиск файла корпуса (Глава1 ≠ Глава10)


def test_find_corpus_file_границы_номера(ws):
    (ws.corpus / "Том1_Глава10.txt").write_text("десятая глава", encoding="utf-8")
    assert exporter.find_corpus_file(ws.corpus, 1, 1) is None  # Глава10 — не глава 1
    (ws.corpus / "Том1_Глава01.txt").write_text("первая глава", encoding="utf-8")
    assert exporter.find_corpus_file(ws.corpus, 1, 1).stem == "Том1_Глава01"
    assert exporter.find_corpus_file(ws.corpus, 10, 1).stem == "Том1_Глава10"
    assert exporter.find_corpus_file(ws.corpus, 1, 2) is None  # другой том
    assert exporter.find_corpus_file(ws.corpus, 3, 1).stem == "Том1_Глава03"


# --- Э2-регрессия по --llm (FR-R2) через подменённый адаптер


def test_регрессия_э2_через_llm(ws, monkeypatch):
    def fake_call(system, user, mc, api, logs_dir, *, role, chapter=None):
        assert "Регрессионный тест" in user
        return json.dumps([{
            "flag_id": "F-001", "type": "бриф", "severity": "важно",
            "quote": "Жизнь", "rule": "сентенция вне брифа",
            "recommendation": "вычеркнуть", "kind": "violation",
        }], ensure_ascii=False)

    monkeypatch.setattr(adapters, "call_anthropic", fake_call)
    report = regression.run_regression(ws, llm=True, cfg=Config())
    e2 = next(r for r in report["результаты"] if r["test_id"] == "красный_дс_сентенции_э2")
    assert e2.get("поймано") == ["бриф"] and not e2.get("пропущено")
    assert report["зелёная"]


def test_регрессия_э2_без_ключа_пропуск(ws):
    report = regression.run_regression(ws, llm=True, cfg=Config())
    e2 = next(r for r in report["результаты"] if r["test_id"] == "красный_дс_сентенции_э2")
    assert "API недоступен" in e2.get("skipped", "")


# --- cost_est (§6.3)


def test_оценка_стоимости():
    mc = ModelConfig(provider="gemini", model="m", price_in_per_1m=2.0, price_out_per_1m=10.0)
    assert adapters._estimate_cost(mc, 500_000, 100_000) == 2.0
    assert adapters._estimate_cost(ModelConfig(provider="g", model="m"), 1000, 1000) is None


# --- якоря флагов в приёмка.md (FR-E1)


def test_якоря_флагов_в_тексте(ws):
    d = ws.chapter_dir(4)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(4, 1).write_text("Чай остыл. Зоя не звонила.", encoding="utf-8")
    from konveyer import verifier2 as v2

    v2.save_flags(ws, 4, [Flag(flag_id="F-007", type="бриф", quote="Чай остыл.", rule="—")])
    review.build_review_pack(ws, 4, 1)
    text = (d / "приёмка.md").read_text(encoding="utf-8")
    assert "Чай остыл.【F-007】" in text


# --- шаблон правки.md не порождает фантомных правок


def test_шаблон_edits_без_фантомов(ws):
    d = ws.chapter_dir(5)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(5, 1).write_text("Текст.", encoding="utf-8")
    review.build_review_pack(ws, 5, 1)
    edits = review.parse_edits_md(ws, 5)  # нетронутый шаблон с примером в ```-блоке
    assert edits == []


# --- сплиттер: кавычки и десятичные числа


def test_сплиттер_сохраняет_кавычки_и_числа():
    sents = textutils.split_sentences("«Всё хорошо.» Он ушёл. Осталось 3.5 литра воды.")
    assert sents[0] == "«Всё хорошо.»"
    assert sents[2] == "Осталось 3.5 литра воды."
    assert len(sents) == 3
