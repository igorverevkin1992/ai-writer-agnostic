"""Этап 4 аудита 2 — цикл автора: правки кодом (п. 19, Р-023), время такта по задачам (п. 23),
повторный Э2 после правок и варианты A/B (п. 24), точки отмены (cancel.py)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from konveyer import adapters, cancel, review, timing, verifier2, writer
from konveyer.cli import app
from konveyer.fsm import ChapterState
from konveyer.schemas import Edit

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cancel.clear()
    timing.current_job = None


def _edit(seq, before, after, note=""):
    return Edit(chapter=1, seq=seq, before=before, after=after, note=note)


def _chapter_at_review(ws, n=1, text="Первая фраза. Вторая фраза. Третья фраза.\n"):
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


def _chapter_generated(ws, n=1):
    """Глава «собрано» с окном — готова к write."""
    ws.chapter_dir(n).mkdir(parents=True, exist_ok=True)
    r = runner.invoke(app, ["compile", str(n)])
    assert r.exit_code == 0, r.output
    return ChapterState(ws, n)


# ------------------------------------------------------------ п. 19: правки кодом


def test_правки_кодом_найдено_не_найдено_дважды_удаление_указание():
    text = "Первая фраза. Вторая фраза. Третья фраза. Вторая фраза.\nЧетвёртая  фраза."
    res = writer.apply_edits_text(
        text,
        [
            _edit(1, "Первая фраза.", "Начальная фраза."),      # найдено один раз → код
            _edit(2, "Нет такой фразы.", "Что-то"),              # не найдено → Писателю
            _edit(3, "Вторая фраза.", "Другая фраза."),          # дважды → Писателю
            _edit(4, "Третья фраза.", ""),                       # пустое СТАЛО → удаление
            _edit(5, "", "переписать финал", note="свободное указание"),  # УКАЗАНИЕ → Писателю
            _edit(6, "Четвёртая фраза.", "Пятая фраза."),        # разные пробелы — дословно с точностью до пробелов
        ],
    )
    assert res.text == "Начальная фраза. Вторая фраза. Вторая фраза.\nПятая фраза."
    assert [e.seq for e in res.applied] == [1, 4, 6]
    assert [e.seq for e in res.remaining] == [2, 3, 5]
    assert "не найдено" in res.reasons[2] and "2 раза" in res.reasons[3] and res.reasons[5] == "свободное указание"
    assert res.needs_model
    assert not writer.apply_edits_text(text, [_edit(1, "Первая фраза.", "X")]).needs_model


def test_apply_edits_кодом_без_модели_и_чистый_дифф(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    (ws.chapter_dir(1) / "правки.md").write_text(
        "БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n\nБЫЛО: Третья фраза.\nСТАЛО:\n", encoding="utf-8"
    )
    called = []
    monkeypatch.setattr(adapters, "call_gemini", lambda *a, **k: called.append(1))
    r = runner.invoke(app, ["apply-edits", "1"])
    assert r.exit_code == 0, r.output
    assert "применено кодом 2, Писателю 0" in r.output and not called
    st = ChapterState(ws, 1)
    assert st.state == "правки" and st.draft == 2
    assert st.data.get("итераций_правок", 0) == 0  # бюджет FR-E3 не расходуется без модели
    assert ws.draft_path(1, 2).read_text(encoding="utf-8") == "Первая фраза. Другая фраза.\n"
    meta = json.loads((ws.chapter_dir(1) / "черновик_2.meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "правки (код)" and meta["применено_кодом"] == [1, 2]
    assert st.data["история"][-1]["команда"] == "apply-edits (код)"
    r = runner.invoke(app, ["diff-check", "1"])
    assert r.exit_code == 0, r.output
    assert "Дифф-контроль чист" in r.output
    report = json.loads((ws.chapter_dir(1) / "дифф.json").read_text(encoding="utf-8"))
    assert report["applied_share"] == 1.0 and not report["unauthorized"] and not report["not_applied"]


def test_apply_edits_смешанные_промпт_от_промежуточного_текста(ws, monkeypatch):
    """Дословная пара применена кодом, Писателю уходит только указание — от уже изменённого текста."""
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    (ws.chapter_dir(1) / "правки.md").write_text(
        "БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n\nУКАЗАНИЕ: оживить финал\n\nБЫЛО: Нет такой.\nСТАЛО: Есть такая.\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["apply-edits", "1"])  # без ключа — ручной режим, код 2
    assert r.exit_code == 2, r.output
    assert "Правок кодом: 1" in r.output and "Писателю: 2" in r.output
    prompt = (ws.chapter_dir(1) / "промпт_правок.md").read_text(encoding="utf-8")
    assert "Другая фраза." in prompt and "Вторая фраза." not in prompt.split("## ЧЕРНОВИК")[1]
    assert "оживить финал" in prompt and "Нет такой." in prompt
    assert "1. БЫЛО: Вторая фраза." not in prompt  # применённые кодом правки Писателю не уходят
    assert ChapterState(ws, 1).state == "на-приёмке"

    # с ключом (фейковый Писатель): итерация расходуется, meta помнит кодовые правки
    monkeypatch.setattr(adapters, "call_gemini", lambda prompt, *a, **k: prompt.split("## ЧЕРНОВИК")[1].strip() + " Финал.\n")
    r = runner.invoke(app, ["apply-edits", "1"])
    assert r.exit_code == 0, r.output
    assert "применено кодом 1, Писателю 2" in r.output
    st = ChapterState(ws, 1)
    assert st.data["итераций_правок"] == 1 and st.draft == 2
    meta = json.loads((ws.chapter_dir(1) / "черновик_2.meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "правки" and meta["применено_кодом"] == [1]


def test_apply_edits_лимит_итераций_не_мешает_правкам_кодом(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = _chapter_at_review(ws, 1)
    st.data["итераций_правок"] = 3
    st._save()
    (ws.chapter_dir(1) / "правки.md").write_text("БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n", encoding="utf-8")
    r = runner.invoke(app, ["apply-edits", "1"])
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, 1).state == "правки"
    # а свободное указание при исчерпанном бюджете — по-прежнему стоп FR-E3
    ChapterState(ws, 1).rollback("на-приёмке")
    st = ChapterState(ws, 1)
    st.data["итераций_правок"] = 3
    st._save()
    (ws.chapter_dir(1) / "правки.md").write_text("УКАЗАНИЕ: переписать\n", encoding="utf-8")
    r = runner.invoke(app, ["apply-edits", "1"])
    assert r.exit_code == 1 and "FR-E3" in r.output + (r.stderr or "")


# ------------------------------------------------------------ п. 23: время такта


def test_переходы_внутри_команды_помечены_задачей(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    hist_before = ChapterState(ws, 1).data["история"]
    assert all("задача" not in rec for rec in hist_before)  # прямые transition() в тестах — вне задачи
    (ws.chapter_dir(1) / "правки.md").write_text("", encoding="utf-8")
    assert runner.invoke(app, ["apply-edits", "1"]).exit_code == 0
    assert runner.invoke(app, ["diff-check", "1"]).exit_code == 0
    hist = ChapterState(ws, 1).data["история"]
    assert hist[-2]["задача"].startswith("apply_edits@") and hist[-1]["задача"].startswith("diff_check@")
    assert hist[-2]["задача"] != hist[-1]["задача"]  # две команды по одной — две задачи
    assert timing.current_job is None  # контекст закрыт


def test_run_объединяет_шаги_в_одну_задачу(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    review.save_edits(ws, 1, [])
    (ws.chapter_dir(1) / "правки.md").write_text("", encoding="utf-8")
    assert runner.invoke(app, ["run", "1"]).exit_code == 0
    hist = ChapterState(ws, 1).data["история"]
    assert hist[-1]["в"] == "дифф-контроль" and hist[-2]["в"] == "правки"
    assert hist[-1]["задача"] == hist[-2]["задача"] and hist[-1]["задача"].startswith("run@")
    machine, author = timing.chapter_times(hist)
    assert machine >= 0 and author >= 0
    # интервал между двумя переходами одной задачи — машинный
    kinds = [k for k, _, _ in timing.intervals(hist)]
    assert kinds[-1] == "машинное"


def test_сегодняшнее_авторское_время_по_всем_главам(ws):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for n, minutes in ((1, 10), (2, 5)):
        st = ChapterState(ws, n)
        ws.chapter_dir(n).mkdir(parents=True, exist_ok=True)
        st.data["история"] = [
            {"из": "не-начато", "в": "собрано", "время": (now - timedelta(minutes=minutes + 1)).isoformat(), "команда": "x"},
            {"из": "собрано", "в": "сгенерировано", "время": (now - timedelta(minutes=1)).isoformat(), "команда": "x"},
        ]
        st.data["состояние"] = "сгенерировано"
        st._save()
    # вчерашний интервал не считается
    st = ChapterState(ws, 3)
    ws.chapter_dir(3).mkdir(parents=True, exist_ok=True)
    st.data["история"] = [
        {"из": "не-начато", "в": "собрано", "время": (now - timedelta(days=2)).isoformat(), "команда": "x"},
        {"из": "собрано", "в": "сгенерировано", "время": (now - timedelta(days=2, minutes=-30)).isoformat(), "команда": "x"},
    ]
    st.data["состояние"] = "сгенерировано"
    st._save()
    assert timing.today_author_minutes(ws) == 15.0
    (ws.chapter_dir(4)).mkdir(parents=True, exist_ok=True)
    ws.status_path(4).write_text("", encoding="utf-8")  # битый состояние.yaml не роняет сводку
    assert timing.today_author_minutes(ws) == 15.0


# ------------------------------------------------------------ п. 24а: повторный Э2


def test_verify2_повторно_без_ключа_и_вручную(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_at_review(ws, 1)
    r = runner.invoke(app, ["verify2", "1", "--повторно"])
    assert r.exit_code == 1  # из «на-приёмке» нельзя — только после правок
    (ws.chapter_dir(1) / "правки.md").write_text("БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n", encoding="utf-8")
    assert runner.invoke(app, ["apply-edits", "1"]).exit_code == 0
    r = runner.invoke(app, ["verify2", "1", "--повторно"])
    assert r.exit_code == 2, r.output
    assert (ws.chapter_dir(1) / "промпт_э2_повторно.md").exists()
    assert "Другая фраза." in (ws.chapter_dir(1) / "промпт_э2_повторно.md").read_text(encoding="utf-8")
    assert not (ws.chapter_dir(1) / "флаги_повторно.json").exists()
    assert ChapterState(ws, 1).state == "правки"
    # ручной режим: голый список флагов → раздел в приёмка.md; флаги.json и решения не тронуты
    flags_before = (ws.chapter_dir(1) / "флаги.json").read_text(encoding="utf-8")
    (ws.chapter_dir(1) / "флаги_повторно.json").write_text(
        json.dumps([{
            "flag_id": "F-201", "type": "самоволие", "kind": "samovolka", "severity": "важно",
            "quote": "Другая фраза.", "rule": "нет в брифе", "recommendation": "решить",
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    r = runner.invoke(app, ["verify2", "1", "--повторно", "--manual"])
    assert r.exit_code == 0, r.output
    assert "самоволок: 1" in r.output and "не изменено" in r.output
    assert (ws.chapter_dir(1) / "флаги.json").read_text(encoding="utf-8") == flags_before
    assert review.load_resolutions(ws, 1) == []
    review_md = (ws.chapter_dir(1) / "приёмка.md").read_text(encoding="utf-8")
    assert "## Повторный Э2 после правок" in review_md and "F-201" in review_md
    assert review_md.index("Повторный Э2") < review_md.index("## ТЕКСТ")
    assert ChapterState(ws, 1).state == "правки"
    # повторный вызов заменяет раздел, а не дублирует
    черновик_k, flags = verifier2.load_flags_again(ws, 1)
    assert черновик_k == 2 and len(flags) == 1
    review.append_second_pass(ws, 1, 2, [])
    review_md = (ws.chapter_dir(1) / "приёмка.md").read_text(encoding="utf-8")
    assert review_md.count("## Повторный Э2 после правок") == 1 and "F-201" not in review_md and "## ТЕКСТ" in review_md


# ------------------------------------------------------------ п. 24б: варианты A/B


def test_write_варианты_без_ключа(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_generated(ws, 1)
    r = runner.invoke(app, ["write", "1", "--варианты", "2"])
    assert r.exit_code == 2, r.output
    assert "черновик_1.alt1.md" in r.output and "--manual --варианты 2" in r.output
    assert ChapterState(ws, 1).state == "собрано"
    # ручной режим: оба файла на месте → регистрируются, метрики рядом
    ws.draft_path(1, 1).write_text("Первая фраза. Вторая фраза.\n", encoding="utf-8")
    r = runner.invoke(app, ["write", "1", "--manual", "--варианты", "2"])
    assert r.exit_code == 1 and "черновик_1.alt1.md" in r.output + (r.stderr or "")
    (ws.chapter_dir(1) / "черновик_1.alt1.md").write_text("Иная первая фраза. Иная вторая фраза. Третья.\n", encoding="utf-8")
    r = runner.invoke(app, ["write", "1", "--manual", "--варианты", "2"])
    assert r.exit_code == 0, r.output
    summary = json.loads((ws.chapter_dir(1) / "варианты.json").read_text(encoding="utf-8"))
    assert [v["вариант"] for v in summary["варианты"]] == ["основной", "alt1"]
    assert "Метрики Э1 по вариантам" in r.output and "alt1" in r.output


def test_write_варианты_с_фейковым_писателем_и_выбор(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_generated(ws, 1)
    answers = iter(["Вариант А. Первая фраза.\n", "Вариант Б. Совсем другая фраза. И ещё одна.\n"])
    monkeypatch.setattr(adapters, "call_gemini", lambda *a, **k: next(answers))
    r = runner.invoke(app, ["write", "1", "--варианты", "2"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "сгенерировано" and st.draft == 1
    assert ws.draft_path(1, 1).read_text(encoding="utf-8").startswith("Вариант А")
    assert (ws.chapter_dir(1) / "черновик_1.alt1.md").read_text(encoding="utf-8").startswith("Вариант Б")
    meta_alt = json.loads((ws.chapter_dir(1) / "черновик_1.alt1.meta.json").read_text(encoding="utf-8"))
    assert meta_alt["вариант"] == "alt1" and meta_alt["вариантов"] == 2
    summary = json.loads((ws.chapter_dir(1) / "варианты.json").read_text(encoding="utf-8"))
    assert len(summary["варианты"]) == 2 and all("метрики" in v and "слов" in v for v in summary["варианты"])
    assert not (ws.chapter_dir(1) / "вердикт.json").exists()  # Э1 главы не запускался, FSM не менялся
    assert "варианты: 2" in st.data["история"][-1]["команда"]

    # выбор варианта: alt1 → черновик_1.md, прежний основной — alt0; состояние прежнее, обратимо
    r = runner.invoke(app, ["write", "1", "--выбрать", "alt1"])
    assert r.exit_code == 0, r.output
    assert ws.draft_path(1, 1).read_text(encoding="utf-8").startswith("Вариант Б")
    assert (ws.chapter_dir(1) / "черновик_1.alt0.md").read_text(encoding="utf-8").startswith("Вариант А")
    meta = json.loads((ws.chapter_dir(1) / "черновик_1.meta.json").read_text(encoding="utf-8"))
    assert meta["выбран"] == "alt1"
    st2 = ChapterState(ws, 1)
    assert st2.state == "сгенерировано" and len(st2.data["история"]) == len(st.data["история"])
    assert runner.invoke(app, ["write", "1", "--выбрать", "alt0"]).exit_code == 0
    assert ws.draft_path(1, 1).read_text(encoding="utf-8").startswith("Вариант А")
    r = runner.invoke(app, ["write", "1", "--выбрать", "alt7"])
    assert r.exit_code == 1 and "alt7" in r.output + (r.stderr or "")
    assert writer.existing_variants(ws, 1, 1) == ["основной", "alt0", "alt1"]


def test_отмена_между_вариантами(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_generated(ws, 1)

    def fake(*a, **k):
        cancel.request()  # автор нажал «Остановить» во время первого вызова
        return "Вариант А.\n"

    monkeypatch.setattr(adapters, "call_gemini", fake)
    r = runner.invoke(app, ["write", "1", "--варианты", "2"])
    assert r.exit_code == 2, r.output
    assert "остановлено автором" in r.output and "Traceback" not in r.output
    assert ChapterState(ws, 1).state == "собрано"  # последний завершённый шаг
    assert not cancel.requested()


# ------------------------------------------------------------ отмена в cmd_run


def test_отмена_такта_между_шагами(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    _chapter_generated(ws, 1)

    calls = []

    def fake_write(ws_, cfg, chapter, k):
        writer._save_draft(ws_, chapter, k, "Первая фраза. Вторая фраза.\n", cfg, mode="генерация")
        if not calls:
            cancel.request()  # автор нажал «Остановить» во время первой генерации
        calls.append(k)

    monkeypatch.setattr(writer, "write_chapter", fake_write)
    r = runner.invoke(app, ["run", "1"])
    assert r.exit_code == 2, r.output
    assert "остановлено автором" in r.output and "сгенерировано" in r.output and "Traceback" not in r.output
    assert ChapterState(ws, 1).state == "сгенерировано"  # write завершён, verify1 не начат
    # флаг сброшен: следующая команда идёт нормально
    r = runner.invoke(app, ["verify1", "1"])
    assert r.exit_code in (0, 1), r.output
    assert ChapterState(ws, 1).state in ("верифицировано-1", "сгенерировано")


def test_запрос_отмены_до_старта_команды_не_действует(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    cancel.request()  # «остаток» от прошлой задачи
    _chapter_generated(ws, 1)
    assert ChapterState(ws, 1).state == "собрано" and not cancel.requested()
