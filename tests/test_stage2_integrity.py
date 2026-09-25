"""Этап 2 аудита: целостность данных конвейера (АУДИТ.md 2.4–2.11, 4.6 адаптеры).

Каждый тест воспроизводит находку аудита на старом коде и проверяет исправление.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import adapters, canonist, exporter, gitops, guard, regression, review, verifier1, verifier2
from konveyer.cli import app
from konveyer.config import ApiConfig, Config, ModelConfig
from konveyer.fsm import STATES, ChapterState, TransitionError
from konveyer.mdparse import MarkupError
from konveyer.paths import Workspace
from konveyer.schemas import Edit, Flag, GoldenTest, Resolution
from tests.общие import _git, _init_repo

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def _chapter(ws: Workspace, n: int, *states: str, draft: int = 1) -> ChapterState:
    st = ChapterState(ws, n)
    ws.chapter_dir(n).mkdir(parents=True, exist_ok=True)
    for s in states:
        st.transition(s)
    st.set_draft(draft)
    return st


# ============================================================ 2.4 парсер правки.md


def test_edits_пустое_стало_удаление_и_соседняя_пара(ws):
    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "правки.md").write_text(
        "БЫЛО: Лишняя фраза.\nСТАЛО:\n\nБЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\n", encoding="utf-8"
    )
    edits = review.parse_edits_md(ws, 1)
    assert [(e.before, e.after) for e in edits] == [("Лишняя фраза.", ""), ("Чай остыл.", "Чай остыл давно.")]
    assert [e.seq for e in edits] == [1, 2]


def test_edits_указание_вплотную_отдельная_правка(ws):
    edits = review.parse_edits_text(
        "БЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\nУКАЗАНИЕ: убрать сентенцию\n", 1
    )
    assert len(edits) == 2
    assert edits[0].after == "Чай остыл давно."          # «УКАЗАНИЕ» не прилипло к «СТАЛО»
    assert edits[1].note == "свободное указание" and edits[1].after == "убрать сентенцию"


def test_edits_многострочные_значения(ws):
    edits = review.parse_edits_text(
        "БЫЛО: первая строка цитаты\nвторая строка цитаты\nСТАЛО: новая первая\nновая вторая\n", 1
    )
    assert edits[0].before == "первая строка цитаты\nвторая строка цитаты"
    assert edits[0].after == "новая первая\nновая вторая"


def test_edits_было_без_стало_ошибка_с_номером_строки(ws):
    text = "# Правки\n\n```\nБЫЛО: пример\nСТАЛО: пример\n```\n\nБЫЛО: одинокое\n\nУКАЗАНИЕ: что-то\n"
    with pytest.raises(review.EditsFormatError) as ei:
        review.parse_edits_text(text, 1)
    assert ei.value.line == 10 and "строка 8" in str(ei.value)  # номера строк с учётом код-блока
    with pytest.raises(review.EditsFormatError, match="без предшествующего"):
        review.parse_edits_text("СТАЛО: сирота\n", 1)
    with pytest.raises(review.EditsFormatError, match="до конца файла") as ei:
        review.parse_edits_text("# Правки\nБЫЛО: хвост файла\n", 1)
    assert ei.value.line == 2


def test_edits_ошибка_формата_читаема_в_cli(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    d = ws.chapter_dir(2)
    d.mkdir(parents=True, exist_ok=True)
    (d / "правки.md").write_text("БЫЛО: без пары\n", encoding="utf-8")
    r = runner.invoke(app, ["edits", "2"])
    assert r.exit_code == 1 and "правки.md:1" in r.output


# ============================================================ 2.5 дифф-контроль


def _drafts(ws, n: int, old: str, new: str) -> None:
    d = ws.chapter_dir(n)
    d.mkdir(parents=True, exist_ok=True)
    (d / "черновик_1.md").write_text(old, encoding="utf-8")
    (d / "черновик_2.md").write_text(new, encoding="utf-8")


def test_дифф_цитата_дважды_внесено_по_счётчикам(ws):
    _drafts(ws, 1, "Чай остыл. Зоя не звонила. Чай остыл.", "Чай остыл давно. Зоя не звонила. Чай остыл.")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл давно.")])
    assert report.not_applied == [] and report.applied_share == 1.0
    assert report.clean


def test_дифф_самоволие_в_блоке_с_объяснённой_правкой(ws):
    # блок difflib из двух предложений: одно объяснено правкой, второе — самоволие
    _drafts(ws, 1, "Чай остыл. Зоя не звонила. День шёл.", "Чай остыл давно. Зоя позвонила ночью. День шёл.")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл давно.")])
    assert any("позвонила" in u for u in report.unauthorized) and not report.clean


def test_дифф_удаление_правкой_чисто(ws):
    _drafts(ws, 1, "Чай остыл. Лишняя фраза. День шёл.", "Чай остыл. День шёл.")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Лишняя фраза.", after="")])
    assert report.clean and report.applied_share == 1.0


def test_дифф_правка_многострочное_стало_чисто(ws):
    _drafts(ws, 1, "Чай остыл. День шёл.", "Чай остыл. Он молчал. Она тоже. День шёл.")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл. Он молчал. Она тоже.")])
    assert report.clean


def test_авторская_правка_по_фрагментам(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    n = 3
    st = _chapter(ws, n, "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке")
    _drafts(ws, n, "Чай остыл. Зоя не звонила. День шёл.", "Чай остыл. Зоя позвонила ночью. Пошёл снег.")
    st.set_draft(2)
    st.data["база_правок"] = 1
    st._save()
    review.save_edits(ws, n, [])
    st.transition("правки")
    r = runner.invoke(app, ["diff-check", str(n), "--авторская-правка", "--фрагмент", "снег"])
    assert r.exit_code == 0, r.output
    data = json.loads((ws.chapter_dir(n) / "дифф.json").read_text(encoding="utf-8"))
    assert data["авторская_правка"]["снято"] == ["Пошёл снег."] and data["авторская_правка"]["фрагменты"] == ["снег"]
    assert data["unauthorized"] == ["Зоя позвонила ночью."]   # остальное остаётся самоволием
    # без перечня — как прежде снимаются все, но с записью, что снято
    r = runner.invoke(app, ["diff-check", str(n), "--авторская-правка"])
    assert r.exit_code == 0, r.output
    data = json.loads((ws.chapter_dir(n) / "дифф.json").read_text(encoding="utf-8"))
    assert data["unauthorized"] == [] and set(data["авторская_правка"]["снято"]) == {"Зоя позвонила ночью.", "Пошёл снег."}


# ============================================================ 2.6 атомарность apply_batch


def _prepare_accepted_chapter(ws, library, n=1, rows=("| — | Бумага пахла табаком | (сформулировать) |",)):
    ws.draft_path(n, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(n, 1).write_text("Каширин нашёл записку утром возле хлебницы.", encoding="utf-8")
    (ws.chapter_dir(n) / "пакет_канона.json").write_text(
        json.dumps({"facts": [{"registry": "эпистемика", "row": r} for r in rows]}, ensure_ascii=False), encoding="utf-8"
    )
    (ws.chapter_dir(n) / "пакет_канона.md").write_text(
        "# Пакет\n\n## Новые факты\n" + "\n".join(f"- РЕЕСТР эпистемика → {r}" for r in rows) + "\n", encoding="utf-8"
    )


def test_apply_batch_откатывает_библиотеку_при_сбое(ws, library, monkeypatch):
    _init_repo(library)
    _prepare_accepted_chapter(ws, library)
    head = gitops.head(library)
    real_export = exporter.run_export

    def broken_export(lib, exports, logs, volume=1, root=None, **kw):
        raise MarkupError(lib / "31_Матрица_знаний.md", 7, "в таблице 6 колонок, в строке — 5")

    monkeypatch.setattr(canonist.exporter, "run_export", broken_export)
    with pytest.raises(MarkupError):
        canonist.apply_batch(ws, Config(), library, 1, 1)
    # библиотека чистая: текст главы удалён, строка реестра не осталась, HEAD прежний
    assert not gitops.dirty(library) and gitops.head(library) == head
    assert not (library / "Проза" / "Том1_Глава01.md").exists()
    assert "табаком" not in (library / "31_Матрица_знаний.md").read_text(encoding="utf-8")
    # повтор после исправления — проходит (не заблокирован «грязным git»)
    monkeypatch.setattr(canonist.exporter, "run_export", real_export)
    commit = canonist.apply_batch(ws, Config(), library, 1, 1).commit
    assert commit != head and not gitops.dirty(library)
    assert "табаком" in (library / "31_Матрица_знаний.md").read_text(encoding="utf-8")


def test_commit_author_валидируется_в_конфиге(ws):
    with pytest.raises(ValueError, match="Имя <email>"):
        Config(commit_author="Просто Имя")
    assert Config(commit_author="Иван Петров <ivan@example.com>").commit_author == "Иван Петров <ivan@example.com>"
    assert Config(commit_author="").commit_author is None
    from konveyer.config import load_config

    (ws.root / "конфиг.yaml").write_text("commit_author: Имя без адреса\n", encoding="utf-8")
    with pytest.raises(ValueError, match="commit_author"):
        load_config(ws)


def test_canonize_apply_без_git_отказ_до_записи(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    n = 1
    _chapter(ws, n, "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке",
             "правки", "дифф-контроль", "принято")
    _prepare_accepted_chapter(ws, library, n)
    r = runner.invoke(app, ["canonize", str(n), "--apply", "-y"])
    assert r.exit_code == 1 and "не под git" in r.output
    assert ChapterState(ws, n).state == "принято"                     # FSM не переведён
    assert not (library / "Проза" / "Том1_Глава01.md").exists()        # ничего не записано


def test_демо_реестр_без_подходящей_таблицы_во_входящие(ws, library):
    """Строка для реестра, документа которого в библиотеке (ещё) нет, — во «Входящие», не сирота."""
    _init_repo(library)
    p33 = library / "33_Континуити.md"
    p33.unlink()
    _git(library, "commit", "-qam", "без реестра континуити")
    exporter.run_export(library, ws.exports, ws.logs)
    _prepare_accepted_chapter(ws, library, 1, rows=())
    (ws.chapter_dir(1) / "пакет_канона.md").write_text(
        "- РЕЕСТР континуити → | 01.08.1995 | событие | 1 | — |\n", encoding="utf-8"
    )
    canonist.apply_batch(ws, Config(), library, 1, 1)
    assert not p33.exists()
    assert "РЕЕСТР континуити" in (library / canonist.INBOX_DOC).read_text(encoding="utf-8")


# ============================================================ 2.8 регрессия


def test_регрессия_пустой_корпус_не_зелёная(ws):
    for f in regression.golden_dir(ws).glob("*.json"):
        f.unlink()
    report = regression.run_regression(ws)
    assert report["всего"] == 0 and report["зелёная"] is False and "пуст" in report["причина"]
    assert regression.is_green(ws) is False


def test_регрессия_все_э2_пропущены_не_зелёная(ws):
    for f in regression.golden_dir(ws).glob("*.json"):
        if "э2" not in f.name:
            f.unlink()
    report = regression.run_regression(ws, llm=False)
    assert report["всего"] > 0 and all(r.get("skipped") for r in report["результаты"])
    assert report["зелёная"] is False and "пропущены" in report["причина"]


def test_регрессия_устаревший_отчёт_не_считается(ws):
    report = regression.run_regression(ws)
    assert report["зелёная"] and set(report["хэши"]) >= {"конфиг.yaml", "проект.yaml", "шаблоны", "norms.json"}
    assert regression.is_green(ws) is True
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nwindow_soft_limit_chars: 70000\n", encoding="utf-8")
    assert regression.is_green(ws) is None and regression.is_stale(ws)   # конфигурация сменилась — прогон нужен заново
    regression.run_regression(ws)
    assert regression.is_green(ws) is True
    ws.templates.mkdir(exist_ok=True)
    (ws.templates / "окно.md.j2").write_text("новый шаблон", encoding="utf-8")
    assert regression.is_green(ws) is None


def test_regress_cli_предупреждает_о_пустом_корпусе(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    for f in regression.golden_dir(ws).glob("*.json"):
        f.unlink()
    r = runner.invoke(app, ["regress"])
    assert r.exit_code == 1 and "ПУСТ" in r.output and "КРАСНАЯ" in r.output


def test_retest_и_doctor_учитывают_устаревший_отчёт(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    regression.run_regression(ws)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nauto_retries_verify1: 1\n", encoding="utf-8")
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1 and "устарел" in r.output
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0 and "устарел" in r.output


# ============================================================ 2.9 решения.json


def test_resolutions_пересобираются_по_текущим_флагам(ws):
    n = 4
    ws.draft_path(n, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(n, 1).write_text("Бумага пахла табаком. Часы стояли.", encoding="utf-8")

    def sam(fid, quote):
        return Flag(flag_id=fid, type="самоволка", quote=quote, rule="вне брифа", kind="samovolka")

    verifier2.save_flags(ws, n, [sam("F-001", "Бумага пахла табаком"), sam("F-002", "Часы стояли")])
    review.build_review_pack(ws, n, 1)
    review.save_resolutions(ws, n, [Resolution(flag_id="F-001", decision="вычеркнуть"), Resolution(flag_id="F-002")])
    # повторный Э2 без самоволок (только нарушение): фантом F-002 не должен блокировать приёмку
    verifier2.save_flags(ws, n, [Flag(flag_id="F-010", type="бриф", quote="Часы стояли", rule="вне брифа")])
    review.build_review_pack(ws, n, 1)
    assert review.load_resolutions(ws, n) == [] and review.unresolved_samovolki(ws, n) == []
    # ещё один прогон: F-001 исчезла, появилась F-003
    verifier2.save_flags(ws, n, [sam("F-002", "Часы стояли"), sam("F-003", "Снег шёл")])
    review.build_review_pack(ws, n, 1)
    res = {r.flag_id: r for r in review.load_resolutions(ws, n)}
    assert set(res) == {"F-002", "F-003"} and review.unresolved_samovolki(ws, n) == ["F-002", "F-003"]
    # решения по оставшимся флагам сохраняются
    review.save_resolutions(ws, n, [Resolution(flag_id="F-002", decision="канонизировать", target_registry="эпистемика"), Resolution(flag_id="F-003")])
    verifier2.save_flags(ws, n, [sam("F-002", "Часы стояли")])
    review.build_review_pack(ws, n, 1)
    res = review.load_resolutions(ws, n)
    assert [(r.flag_id, r.decision) for r in res] == [("F-002", "канонизировать")]
    assert review.unresolved_samovolki(ws, n) == []


# ============================================================ 2.10 retest не трогает окно главы


def test_retest_собирает_окно_во_временную_папку(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = _chapter(ws, 1, "собрано", "сгенерировано")
    ws.window_path(1).write_text("СТАРОЕ ОКНО ГЛАВЫ В РАБОТЕ", encoding="utf-8")
    r = runner.invoke(app, ["пере-тест", "--chapter", "1"])
    assert r.exit_code == 0, r.output
    assert ws.window_path(1).read_text(encoding="utf-8") == "СТАРОЕ ОКНО ГЛАВЫ В РАБОТЕ"
    assert ChapterState(ws, 1).state == "сгенерировано"
    prompt = next((ws.root / "пере-тест").rglob("ПРОМПТ_раунд1.md"))
    assert "Каширин" in prompt.read_text(encoding="utf-8")
    assert not list((ws.root / "пере-тест").rglob("_сборка_окна"))
    assert st.draft == 1


# ============================================================ 2.11 мелочи


def test_verdict_и_flags_за_номером_черновика_и_авто_повтор_в_истории(ws):
    n = 5
    st = _chapter(ws, n, "собрано", "сгенерировано")
    d = ws.chapter_dir(n)
    (d / "вердикт.json").write_text(json.dumps({"chapter": n, "draft": 1, "checks": []}), encoding="utf-8")
    st.bump_retries()  # брак метрик → авто-повтор: вердикт черновика 1 сохраняется, петля в истории
    assert (d / "вердикт_1.json").exists()
    last = st.data["история"][-1]
    assert last["из"] == "сгенерировано" and last["в"] == "сгенерировано" and "авто-повтор" in last["команда"]
    st.set_draft(2)
    (d / "вердикт.json").write_text(json.dumps({"chapter": n, "draft": 2, "checks": []}), encoding="utf-8")
    st.transition("верифицировано-1", "verify1")
    assert json.loads((d / "вердикт_2.json").read_text(encoding="utf-8"))["draft"] == 2
    assert json.loads((d / "вердикт_1.json").read_text(encoding="utf-8"))["draft"] == 1  # не затёрт
    assert (d / "вердикт.json").exists()  # «текущий» — для совместимости
    verifier2.save_flags(ws, n, [])
    st.transition("верифицировано-2", "verify2")
    assert (d / "флаги_2.json").exists() and (d / "флаги.json").exists()


def test_атомарная_запись_не_портит_файл_при_сбое(tmp_path, monkeypatch):
    target = tmp_path / "состояние.yaml"
    guard.write_text(target, "состояние: собрано\n")

    def broken_replace(src, dst):
        raise OSError("диск переполнен")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        guard.write_text(target, "состояние: сгенерировано\n")
    assert target.read_text(encoding="utf-8") == "состояние: собрано\n"
    assert [p.name for p in tmp_path.iterdir()] == ["состояние.yaml"]  # временных файлов не осталось


def test_canonize_не_перезаписывает_отредактированный_пакет(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    n = 1
    _chapter(ws, n, "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке",
             "правки", "дифф-контроль", "принято")
    ws.draft_path(n, 1).write_text("Текст главы.", encoding="utf-8")
    assert runner.invoke(app, ["canonize", str(n)]).exit_code == 0
    batch = ws.chapter_dir(n) / "пакет_канона.md"
    batch.write_text(batch.read_text(encoding="utf-8") + "- РЕЕСТР эпистемика → | — | правка автора | — |\n", encoding="utf-8")
    r = runner.invoke(app, ["canonize", str(n)])
    assert r.exit_code == 1 and "правился автором" in r.output
    assert "правка автора" in batch.read_text(encoding="utf-8")
    r = runner.invoke(app, ["canonize", str(n), "--заново"])
    assert r.exit_code == 0, r.output
    assert "правка автора" not in batch.read_text(encoding="utf-8")


def test_rollback_без_to_по_цепочке_состояний(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    n = 7
    st = _chapter(ws, n, "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке",
                  "правки", "дифф-контроль")
    st.rollback("правки")
    # по истории «предыдущее» было бы «дифф-контроль» (вперёд) — откат по цепочке даёт «на-приёмке»
    r = runner.invoke(app, ["rollback", str(n), "-y"])
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, n).state == "на-приёмке"
    assert STATES.index("на-приёмке") == STATES.index("правки") - 1
    r = runner.invoke(app, ["rollback", "8", "-y"])
    assert r.exit_code == 1 and "некуда" in r.output


def test_test_id_санитизируется_как_имя_файла(ws):
    path = regression.add_test(
        ws, GoldenTest(test_id="../evil/имя теста: v2", fragment="Текст.", expected_flags=[])
    )
    assert path.parent == regression.golden_dir(ws) and path.name == "evil_имя_теста_v2.json"
    with pytest.raises(ValueError):
        regression.safe_file_stem("../")


def test_ugar_debug_поднимает_трейсбек(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["verify1", "9"])  # «не-начато» → недопустимо
    assert r.exit_code == 1 and "ОШИБКА" in r.output
    monkeypatch.setenv("KONVEYER_DEBUG", "1")
    r = runner.invoke(app, ["verify1", "9"])
    assert isinstance(r.exception, TransitionError)


# ============================================================ 4.6 адаптеры


class _GenaiError(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


def test_retryable_учитывает_code_ошибок_gemini():
    assert adapters._retryable(_GenaiError(400)) is False
    assert adapters._retryable(_GenaiError(403)) is False
    assert adapters._retryable(_GenaiError(503)) is True
    assert adapters._retryable(_GenaiError(429)) is True
    assert adapters._retryable(RuntimeError("сеть")) is True


def test_sdk_без_собственных_ретраев(ws, monkeypatch):
    captured: dict = {}

    class _Usage:
        input_tokens = 1
        output_tokens = 2

    class _Resp:
        content = [types.SimpleNamespace(type="text", text="ответ")]
        usage = _Usage()

    class Anthropic:
        def __init__(self, **kwargs):
            captured["anthropic"] = kwargs
            self.messages = types.SimpleNamespace(create=lambda **kw: _Resp())

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ключ")
    api = ApiConfig(retries=1, backoff_base_s=0.0)
    assert adapters.call_anthropic("s", "u", ModelConfig(provider="anthropic", model="m"), api, ws.logs, role="т") == "ответ"
    assert captured["anthropic"]["max_retries"] == 0

    class HttpOptions:
        def __init__(self, **kwargs):
            captured["http"] = kwargs

    class HttpRetryOptions:
        def __init__(self, **kwargs):
            captured["retry"] = kwargs

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            pass

    class Client:
        def __init__(self, **kwargs):
            self.models = types.SimpleNamespace(
                generate_content=lambda **kw: types.SimpleNamespace(text="проза", usage_metadata=None)
            )

    fake_types = types.ModuleType("google.genai.types")
    fake_types.HttpOptions, fake_types.HttpRetryOptions, fake_types.GenerateContentConfig = HttpOptions, HttpRetryOptions, GenerateContentConfig
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client, fake_genai.types = Client, fake_types
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    assert adapters.call_gemini("окно", ModelConfig(provider="gemini", model="g"), api, ws.logs) == "проза"
    assert captured["retry"] == {"attempts": 1} and "retry_options" in captured["http"]
