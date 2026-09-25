"""Регрессионные тесты второго аудита (глубокое ревью)."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from konveyer import adapters, canonist, exporter, llmjson, review, verifier1, verifier2
from konveyer.cli import app
from konveyer.config import ApiConfig, Config, ModelConfig
from konveyer.fsm import ChapterState
from konveyer.schemas import Edit, Norm

runner = CliRunner()


# --- свободные указания не блокируют приёмку (FR-V1.10 / FR-E4)


def test_указание_не_блокирует_приёмку(ws):
    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "черновик_1.md").write_text("Чай остыл. Жизнь как расписание. Зоя молчала.", encoding="utf-8")
    (d / "черновик_2.md").write_text("Чай остыл. Зоя молчала.", encoding="utf-8")
    edits = [Edit(chapter=1, seq=1, before="", after="убрать сентенцию про жизнь", note="свободное указание")]
    report = verifier1.diff_check(ws, 1, 1, 2, edits)
    assert report.not_applied == []          # указание не считается «не внесённым»
    assert report.unverifiable == [1]
    assert report.applied_share == 1.0


# --- защита от двойного применения пакета канониста


def _init_git(lib, identity=True):
    subprocess.run(["git", "-C", str(lib), "init"], check=True, capture_output=True)
    if identity:
        for k, v in (("user.email", "a@b.c"), ("user.name", "Автор")):
            subprocess.run(["git", "-C", str(lib), "config", k, v], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(lib), "add", "-A"], check=True, capture_output=True)
    # начальный коммит: при identity=False — разовой -c-подстановкой,
    # чтобы репозиторий был чистым, но БЕЗ сохранённого user.email
    subprocess.run(
        ["git", "-C", str(lib), "-c", "user.email=tmp@tmp", "-c", "user.name=tmp", "commit", "-m", "начало"],
        check=True, capture_output=True,
    )


def _prep_batch(ws, chapter=1):
    d = ws.chapter_dir(chapter)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(chapter, 1).write_text("Текст главы.", encoding="utf-8")
    (d / "пакет_канона.json").write_text("{}", encoding="utf-8")
    (d / "пакет_канона.md").write_text("# Пакет\n", encoding="utf-8")


def test_apply_batch_отказ_при_грязном_git(ws, library):
    _init_git(library)
    (library / "02_Стиль_и_голос.md").write_text(
        (library / "02_Стиль_и_голос.md").read_text(encoding="utf-8") + "\nправка\n", encoding="utf-8"
    )
    _prep_batch(ws)
    with pytest.raises(RuntimeError, match="двойного применения"):
        canonist.apply_batch(ws, Config(), library, 1, 1)
    assert not (library / "Проза" / "Том1_Глава01.md").exists()  # ничего не записано


def test_apply_batch_отказ_без_git_identity(ws, library, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ws.root / "нет-такого-файла"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(ws.root / "нет-такого-файла"))
    _init_git(library, identity=False)
    _prep_batch(ws)
    with pytest.raises(RuntimeError, match="git не настроен"):
        canonist.apply_batch(ws, Config(), library, 1, 1)
    assert not (library / "Проза" / "Том1_Глава01.md").exists()


# --- Э2: истёкшие запреты информрежима не попадают в промпт


def test_информбан_фильтруется_по_тому(ws):
    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.", encoding="utf-8")
    bans = json.loads((ws.exports / "infobans.json").read_text(encoding="utf-8"))
    for b in bans:
        if b["ban_id"] == "B-002":
            b["until_volume"] = 0  # «истёк» до тома 1
    (ws.exports / "infobans.json").write_text(json.dumps(bans, ensure_ascii=False), encoding="utf-8")
    _, user = verifier2.build_prompt(ws, 1, 1)
    assert "B-001" in user and "B-002" not in user


# --- V1.2e: объём_допуск, заданный через «мин», не роняет проверку


def test_объём_допуск_через_мин(ws):
    norms = exporter.load_norms(ws.exports)
    norms["объём_допуск"] = Norm(min=0.15, unit="доля", source="02 §5")
    checks = verifier1.analyze(
        "Три слова здесь.", "", __import__("konveyer.schemas", fromlist=["Brief"]).Brief(
            chapter=1, year=1995, focal="Каширин", volume_words=300
        ),
        norms, exporter.load_stoplists(ws.exports),
    )
    v = [c for c in checks if c.check_id == "V1.2e_объём"][0]
    assert v.status == "BRAK" and "15%" in v.threshold


# --- сброс бюджета авто-повторов при новой генерации (§5.4)


def test_сброс_авто_повторов_при_write(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 1)
    st.transition("собрано")
    st.data["авто_повторов"] = 3
    st._save()
    ws.window_path(1).parent.mkdir(parents=True, exist_ok=True)
    ws.window_path(1).write_text("окно", encoding="utf-8")

    def fake_write(ws_, cfg_, chapter_, k_):
        p = ws_.draft_path(chapter_, k_)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Новый черновик главы.", encoding="utf-8")

    from konveyer import writer

    monkeypatch.setattr(writer, "write_chapter", fake_write)
    r = runner.invoke(app, ["write", "1"])
    assert r.exit_code == 0, r.output
    st2 = ChapterState(ws, 1)
    assert st2.data["авто_повторов"] == 0 and st2.state == "сгенерировано"


# --- ручной apply-edits не тратит бюджет итераций FR-E3


def test_manual_apply_edits_не_тратит_итерации(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 2)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    ws.draft_path(2, 0)
    d = ws.chapter_dir(2)
    ws.draft_path(2, 1).write_text("Черновик два.", encoding="utf-8")
    st.set_draft(1)
    # свободное указание — нужна модель (дословную пару применил бы код, Р-023)
    (d / "правки.md").write_text("УКАЗАНИЕ: добавить в конец слово «правкой».\n", encoding="utf-8")

    # автоматический запуск без API: код 2, бюджет не потрачен
    r = runner.invoke(app, ["apply-edits", "2"])
    assert r.exit_code == 2
    assert ChapterState(ws, 2).data.get("итераций_правок", 0) == 0

    ws.draft_path(2, 2).write_text("Черновик два с правкой.", encoding="utf-8")
    r = runner.invoke(app, ["apply-edits", "2", "--manual"])
    assert r.exit_code == 0, r.output
    assert ChapterState(ws, 2).data.get("итераций_правок", 0) == 0  # ручной — не итерация Писателя


# --- атомарность экспорта: ошибка структуры не оставляет смешанных выгрузок


def test_export_атомарен_при_ошибке(ws, library):
    before = {
        name: (ws.exports / name).read_text(encoding="utf-8")
        for name in ("norms.json", "briefs.json", "индекс.json")
    }
    # ломаем поглавник (разбирается ПОСЛЕ норм)
    p23 = library / "23_Поглавник_Том1.md"
    p23.write_text("# 23. Поглавник\n\nни одной секции «Глава N»\n", encoding="utf-8")
    # и меняем нормы, чтобы проверить, что новая версия НЕ записана
    p02 = library / "02_Стиль_и_голос.md"
    p02.write_text(p02.read_text(encoding="utf-8").replace("| — | 1 |", "| — | 9 |"), encoding="utf-8")
    with pytest.raises(exporter.ExportErrors, match="Поглавник"):
        exporter.run_export(library, ws.exports, ws.logs)
    for name, text in before.items():
        assert (ws.exports / name).read_text(encoding="utf-8") == text, name


# --- извлечение JSON из ответа модели со скобками в прозе


def test_llmjson_скобки_в_прозе():
    raw = 'Найдено [2] нарушения по чек-листу:\n[{"flag_id": "F-001", "type": "бриф", "quote": "х", "rule": "y"}]'
    flags = verifier2.parse_flags(raw)
    assert len(flags) == 1 and flags[0].flag_id == "F-001"

    raw2 = 'Итог (см. [чек-лист 4.1]):\n```json\n{"facts": [], "samovolki": []}\n```'
    assert llmjson.extract_json(raw2, dict) == {"facts": [], "samovolki": []}

    with pytest.raises(ValueError):
        llmjson.extract_json("никакого джейсона [тут] нет", dict)


# --- ретраи только на временных ошибках (§6.3)


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


def test_нет_ретраев_на_401(ws):
    calls = []

    def fn():
        calls.append(1)
        raise _HttpError(401)

    mc = ModelConfig(provider="x", model="m")
    with pytest.raises(adapters.ManualModeNeeded):
        adapters._retry_call(fn, ApiConfig(retries=3, backoff_base_s=0.0), ws.logs, role="т", mc=mc, chapter=None)
    assert len(calls) == 1  # 401 — без повторов


def test_ретраи_на_500(ws):
    calls = []

    def fn():
        calls.append(1)
        raise _HttpError(503)

    mc = ModelConfig(provider="x", model="m")
    with pytest.raises(adapters.ManualModeNeeded):
        adapters._retry_call(fn, ApiConfig(retries=3, backoff_base_s=0.0), ws.logs, role="т", mc=mc, chapter=None)
    assert len(calls) == 3


# --- повторный diff-check из состояния «дифф-контроль» (подтверждение автора)


def test_повторный_diff_check_разрешён(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    st = ChapterState(ws, 3)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке"]:
        st.transition(s)
    ws.draft_path(3, 1).write_text("Первый вариант текста главы.", encoding="utf-8")
    ws.draft_path(3, 2).write_text("Совсем другой текст главы.", encoding="utf-8")
    st.set_draft(2)
    st.data["база_правок"] = 1
    st._save()
    review.save_edits(ws, 3, [])
    st.transition("правки")

    r = runner.invoke(app, ["diff-check", "3"])
    assert r.exit_code == 0 and "Самовольные изменения" in r.output
    # подтверждение авторской правки повторным прогоном из «дифф-контроль»
    r = runner.invoke(app, ["diff-check", "3", "--author-fix"])
    assert r.exit_code == 0, r.output
    data = json.loads((ws.chapter_dir(3) / "дифф.json").read_text(encoding="utf-8"))
    assert data["unauthorized"] == []
