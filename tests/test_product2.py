"""Тесты третьего раунда улучшений: приёмка.html, оффлайн-дашборд, тайминг, find, snapshot."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from konveyer import htmlreview, review, search, snapshot, timing
from konveyer.cli import app
from konveyer.dashboard import build_dashboard
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution

runner = CliRunner()


# --- HTML-пакет приёмки (этап 3: «чтение с флагами» без сервера)


def test_review_html(ws):
    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Чай остыл давно. Зоя <б> молчала и ждала.", encoding="utf-8")
    from konveyer import verifier2

    verifier2.save_flags(
        ws, 1,
        [Flag(flag_id="F-001", type="самоволка", quote="Чай остыл давно.", rule="в брифе чая нет",
              recommendation="решить", kind="samovolka")],
    )
    review.save_resolutions(ws, 1, [Resolution(flag_id="F-001")])
    path = htmlreview.build_review_html(ws, 1, 1)
    html_text = path.read_text(encoding="utf-8")
    assert '<mark id="a-F-001"' in html_text            # цитата подсвечена и заякорена
    assert "БЕЗ РЕШЕНИЯ" in html_text                   # статус самоволки виден
    assert "&lt;б&gt;" in html_text                     # текст экранирован
    assert "http" not in html_text.split("<body>")[1]   # никаких внешних ресурсов
    # после решения статус меняется
    review.save_resolutions(ws, 1, [Resolution(flag_id="F-001", decision="вычеркнуть")])
    html_text = htmlreview.build_review_html(ws, 1, 1).read_text(encoding="utf-8")
    assert "решение: вычеркнуть" in html_text


def test_review_html_создаётся_командой(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 2)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2"]:
        st.transition(s)
    ws.draft_path(2, 1).write_text("Текст главы для приёмки.", encoding="utf-8")
    st.set_draft(1)
    r = runner.invoke(app, ["review", "2"])
    assert r.exit_code == 0, r.output
    assert (ws.chapter_dir(2) / "приёмка.html").exists()


# --- оффлайн-дашборд


def test_дашборд_без_сети(ws):
    st = ChapterState(ws, 1)
    st.transition("собрано")  # чтобы появился блок «Главы: состояние и время»
    (ws.logs / "метрики.jsonl").parent.mkdir(exist_ok=True)
    entries = [
        {"chapter": c, "V1.2a_средняя_длина": 10 + c, "правок_на_1000": 5.0, "слов": 300}
        for c in (1, 2, 3)
    ]
    (ws.logs / "метрики.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    path = build_dashboard(ws)
    text = path.read_text(encoding="utf-8")
    assert "<svg" in text and "Средняя длина фразы" in text
    assert "cdn." not in text and "http" not in text.split("<body>")[1]  # полностью локален
    assert "Данные таблицей" in text                                    # табличный дублёр
    assert "время такта" in text.lower() or "Время автора" in text      # блок глав


# --- тайминг такта (критерий приёмки 1)


_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _hist(*steps):
    out = []
    for offset_min, frm, to in steps:
        out.append({"из": frm, "в": to, "время": (_T0 + timedelta(minutes=offset_min)).isoformat(), "команда": "x"})
    return out


def test_время_автора_и_машины():
    """Аудит 2 (5.7): машинное — только между переходами одной задачи; всё остальное — ожидание автора,
    включая простой в «собрано»/«сгенерировано», когда шаги запускались по одному."""
    run1 = f"run@{(_T0 + timedelta(minutes=0)).isoformat()}"
    history = _hist(
        (0, "не-начато", "собрано"),
        (1, "собрано", "сгенерировано"),        # 1 мин: одна задача run → машинное
        (2, "сгенерировано", "верифицировано-1"),
        (3, "верифицировано-1", "верифицировано-2"),
        (4, "верифицировано-2", "на-приёмке"),  # ещё 3 мин машинного
        (34, "на-приёмке", "правки"),           # 30 мин: другая задача → авторское
        (35, "правки", "дифф-контроль"),        # 1 мин: команда по одной, без задачи → авторское
        (45, "дифф-контроль", "принято"),       # 10 мин авторской
    )
    for rec in history[:5]:
        rec["задача"] = run1
    history[5]["задача"] = f"apply-edits@{(_T0 + timedelta(minutes=34)).isoformat()}"
    machine_s, author_s = timing.chapter_times(history)
    assert machine_s == 4 * 60
    assert author_s == 41 * 60
    assert timing.fmt_minutes(author_s) == "41.0 мин"


def test_время_делится_по_старту_задачи():
    """Переход другой задачи с известным стартом: до старта — авторское, после — машинное."""
    history = _hist((0, "собрано", "сгенерировано"), (12, "сгенерировано", "верифицировано-1"))
    history[1]["задача"] = f"verify1@{(_T0 + timedelta(minutes=10)).isoformat()}"
    machine_s, author_s = timing.chapter_times(history)
    assert author_s == 10 * 60 and machine_s == 2 * 60
    # без задачи вовсе — всё авторское (старое поведение «машинное по состоянию» отменено)
    assert timing.chapter_times(_hist((0, "собрано", "сгенерировано"), (5, "сгенерировано", "верифицировано-1"))) == (0.0, 5 * 60)


def test_тайминг_в_карточке_главы(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 3)
    st.data["история"] = _hist((0, "не-начато", "собрано"), (2, "собрано", "сгенерировано"))
    st.data["состояние"] = "сгенерировано"
    st._save()
    r = runner.invoke(app, ["status", "3"])
    assert r.exit_code == 0 and "Время такта" in r.output


# --- поиск по канону


def test_find_типизированная_выдача(ws, library):
    hits = search.grouped(search.find(ws.exports, library, "гараж"))
    assert "факт" in hits or "закладка" in hits
    assert "проза" in hits  # «за гаражами» в прозе
    by_plant = search.find(ws.exports, library, "P-001")
    assert any(h.kind == "закладка" and "т1 гл1" in h.text for h in by_plant)
    rule = search.find(ws.exports, library, "менталитет")
    assert any(h.kind == "правило" and h.ref == "Л-1" for h in rule)


def test_find_команда(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["find", "записка"])
    assert r.exit_code == 0 and "M-001" in r.output
    r = runner.invoke(app, ["find", "зюзюблик"])
    assert "ничего не найдено" in r.output


# --- снапшот тома


def test_snapshot(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    path = snapshot.build_snapshot(ws, 1)
    text = path.read_text(encoding="utf-8")
    assert "Каширин" in text and "M-001" in text
    assert "НЕ знает: сторож жив" in text            # эпистемика среза
    assert "[P-001]" in text and "Глава 1" in text
    with pytest.raises(FileNotFoundError):
        snapshot.build_snapshot(ws, 9)
    r = runner.invoke(app, ["snapshot", "1"])
    assert r.exit_code == 0 and "Срез тома 1" in r.output
