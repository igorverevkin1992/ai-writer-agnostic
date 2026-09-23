"""Этап 2: реестр метрик и нормы только из канона (FR-V1-1…FR-V1-7), языковой модуль (FR-V1-3), окно (FR-WN-3/5/7),
Писатель (FR-WR-1/4/5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import adapters, apilog, calibrate, compiler, exporter, gitops, lang, metrics, verifier1, writer
from konveyer.cli import app
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Brief, Norm

runner = CliRunner()
SAMPLE = ("Поезд ушёл без него. Ветер гнал по перрону обрывки газет, и старуха у кассы прятала лицо в платок. "
          "Он был мрачен. Спичка сломалась дважды.\n\n— Сынок, — сказал сосед и отвернулся.\n\nШпалы пахли мазутом.\n")


# ------------------------------------------------------------------ реестр и нормы


def test_реестр_метрик_документирован():
    doc = metrics.documentation()
    for mid in ("средняя_длина", "доля_коротких", "утечка_окна", "межглавные_повторы", "ttr_мин", "стоп_лексика"):
        assert mid in doc
    assert metrics.REGISTRY["короткая_фраза_порог"].kind == "параметр"
    assert metrics.unknown_norms(["средняя_длина", "чушь"]) == ["чушь"]
    r = runner.invoke(app, ["метрики"])
    assert r.exit_code == 0 and "V1.2a_средняя_длина" in r.output


def test_нормы_только_из_канона(ws):
    """Метрика без нормы не считается; неизвестная норма — ошибка валидации с перечнем доступных (FR-V1-2)."""
    norms = exporter.load_norms(ws.exports)
    brief = Brief(chapter=0)
    ids = {c.check_id for c in verifier1.analyze(SAMPLE, "", brief, norms, [])}
    assert "V1.2a_средняя_длина" in ids and "V1.3_был" in ids
    ids2 = {c.check_id for c in verifier1.analyze(SAMPLE, "", brief, {k: v for k, v in norms.items() if k != "средняя_длина"}, [])}
    assert "V1.2a_средняя_длина" not in ids2 and "V1.3_был" in ids2
    # без документа стиля — проверки молчат, а не падают
    assert [c.check_id for c in verifier1.analyze(SAMPLE, "", brief, {}, [])] == ["V1.5_стоп_лексика"]


def test_неизвестная_норма_ошибка_экспорта(ws, library):
    style = library / "02_Стиль_и_голос.md"
    style.write_text(style.read_text(encoding="utf-8").replace(
        "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |",
        "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |\n| чушь_метрика | нет такой | 1 | 2 | — | шт |"),
        encoding="utf-8")
    with pytest.raises(exporter.MarkupError, match="чушь_метрика") as e:
        exporter.run_export(library, ws.exports, ws.logs)
    assert "средняя_длина" in str(e.value)  # перечень доступных


def test_метрика_заданных_лексем(ws, library):
    """Плотность заданных лексем: норма «лексемы_<имя>» с перечнем слов в единице (FR-V1-1)."""
    style = library / "02_Стиль_и_голос.md"
    style.write_text(style.read_text(encoding="utf-8").replace(
        "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |",
        "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |\n"
        "| лексемы_ветер | плотность ветра | — | 1 | — | ветер, обрывки на 100 слов |"), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    norms = exporter.load_norms(ws.exports)
    assert "лексемы_ветер" in norms
    checks = {c.check_id: c for c in verifier1.analyze(SAMPLE, "", Brief(chapter=0), norms, [])}
    c = checks["V1.3_лексемы_ветер"]
    assert c.status == "FLAG" and "2 вхождений" in c.note


def test_метрики_калибровка(ws, library, tmp_path):
    """Эталонные значения по образцу + предложение коридоров + запись в стиль и журнал (FR-V1-7)."""
    L = lang.for_project(ws.root)
    v = calibrate.measure(SAMPLE, exporter.load_norms(ws.exports), L)
    n_words = len(L.words(SAMPLE))
    lengths = [len(L.words(s)) for s in L.split_sentences(SAMPLE)]
    assert v["объём_главы"] == n_words == 32 and v["максимум_длины"] == max(lengths) == 14
    assert v["средняя_длина"] == pytest.approx(sum(lengths) / len(lengths), abs=0.01)
    assert v["доля_диалога"] == pytest.approx(1 / 3, abs=0.01) and v["был_на_250"] == pytest.approx(250 / n_words, abs=0.1)
    samples = [(f"обр{i}", SAMPLE * (i + 1) + "Ещё одно предложение подлиннее, чем все остальные в образце. " * i) for i in range(4)]
    pr = calibrate.propose(samples, exporter.load_norms(ws.exports), L, exporter.load_stoplists(ws.exports))
    assert set(pr.corridors) >= {"средняя_длина", "объём_главы", "был_на_250", "доля_диалога"}
    assert pr.corridors["объём_главы"].min is not None and pr.corridors["был_на_250"].min is None
    assert "## Предложение" in pr.report and "средняя_длина" in pr.report
    gitops.init_repo(library)
    gitops._git(library, "config", "user.email", "t@t")
    gitops._git(library, "config", "user.name", "t")
    gitops.commit_all(library, "init")
    res = calibrate.apply(ws, Config(), library, pr, author_confirmed=True)
    assert res.commit
    style = (library / "02_Стиль_и_голос.md").read_text(encoding="utf-8")
    n = pr.corridors["объём_главы"]
    assert f"| объём_главы |" in style and f"| {n.min:g} | {n.max:g} |" in style
    journal = (library / "36_Журнал_решений.md").read_text(encoding="utf-8")
    assert "калиброваны" in journal and "## Р-0" in journal
    assert exporter.load_norms(ws.exports)["объём_главы"].min == n.min  # выгрузки пересобраны
    with pytest.raises(PermissionError):
        calibrate.apply(ws, Config(), library, pr, author_confirmed=False)


def test_cli_нормы(ws, library, monkeypatch, tmp_path):
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["нормы"])
    assert r.exit_code == 0 and "средняя_длина" in r.output
    f = tmp_path / "образец.md"
    f.write_text(SAMPLE, encoding="utf-8")
    r = runner.invoke(app, ["нормы", "--калибровать", str(f)])
    assert r.exit_code == 0 and "Предложение" in r.output and (ws.logs / "калибровка.md").exists(), r.output


# ------------------------------------------------------------------ языковой слой


def test_язык_сплиттер(tmp_path):
    L = lang.get()
    s = L.split_sentences("А. К. Иванов приехал на ул. Ленина 5 мая. Т. е. вечером. — Сынок, — сказал он. Всё.")
    assert s == ["А. К. Иванов приехал на ул. Ленина 5 мая.", "Т. е. вечером.", "— Сынок, — сказал он.", "Всё."]
    # контекстное сокращение: «г.» перед заглавной — конец фразы, перед строчной — нет
    assert L.split_sentences("Было в 1995 г. в мае.") == ["Было в 1995 г. в мае."]
    assert L.narration_only("— Сынок, — сказал сосед и отвернулся.\n\nШпалы пахли мазутом.") == "сказал сосед и отвернулся.\n\nШпалы пахли мазутом."
    assert L.stems("отец") == ["отец", "отц"] and L.lexemes("был") == {"был", "было", "были", "была"}
    # переопределение проекта: свои сокращения и лексемы (списки складываются)
    (tmp_path / "языки").mkdir()
    (tmp_path / "языки" / "ru.yaml").write_text("сокращения: [\"зав.\"]\nлексемы:\n  был: [бывало]\n", encoding="utf-8")
    Lp = lang.get("ru", tmp_path)
    assert Lp.split_sentences("Пришёл зав. складом. Ушёл.") == ["Пришёл зав. складом.", "Ушёл."]
    assert "бывало" in Lp.lexemes("был") and "был" in Lp.lexemes("был")
    with pytest.raises(ValueError, match="не поддерживается"):
        lang.get("xx")


def test_стоплист_реплики_персонажей(ws):
    norms = exporter.load_norms(ws.exports)
    stops = exporter.load_stoplists(ws.exports)
    rule = next(r for r in stops if r.kind == "лексика" and r.scope == "0.3")
    word = rule.items[0]
    brief = Brief(chapter=1, focal=rule.applies_to.get("focal", ""), year=1995)
    in_speech = f"Он вошёл.\n\n— Это {word}, — сказал сосед и ушёл.\n"
    in_narration = f"Он вошёл и подумал: {word}.\n"
    flags = lambda t: [c for c in verifier1.analyze(t, "", brief, norms, stops) if c.check_id == "V1.5_стоп_лексика" and c.status == "FLAG"]  # noqa: E731
    assert not flags(in_speech) and flags(in_narration)


# ------------------------------------------------------------------ окно


def test_окно_без_тайн(ws, library):
    """По всем главам: ни один маркер тайны, недоступной фокалу, не встречается в окне (FR-WN-3)."""
    bans = exporter.load_export(ws.exports, "infobans.json")
    for b in exporter.load_briefs(ws.exports):
        w = compiler.compile_window(ws, library, b.chapter)[0].read_text(encoding="utf-8").lower()
        for ban in bans:
            if not ban.get("secret"):
                continue
            known = ban.get("known_by", {}).get(b.focal)
            if known is not None and known <= b.chapter:
                continue
            for marker in ban.get("markers", []):
                assert marker.lower() not in w, f"гл. {b.chapter}: маркер «{marker}» тайны {ban['ban_id']} в окне"


def test_окно_деградация(ws, library):
    """Нет эпистемики (модуль выключен, документа нет) → секции нет, ошибок нет (FR-WN-7)."""
    from konveyer import manifest as manifest_mod

    man = manifest_mod.load(ws.root)
    man.модули["эпистемика"] = "выкл"
    for e in man.библиотека:
        if e.тип == "эпистемика":
            e.выключен = True
    manifest_mod.save(ws.root, man)
    exporter.run_export(library, ws.exports, ws.logs)
    w = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    assert "<!-- СЕКЦИЯ: что знает фокал -->" not in w and "M-001" not in w
    assert "<!-- СЕКЦИЯ: бриф -->" in w


def test_окно_хвост_прозы_исключён_из_повторов(ws, library):
    """Хвост принятой прозы предыдущей главы фокала попадает в окно цитатой и не считается утечкой (FR-WN-5)."""
    prose = library / "Проза" / "Том1_Глава01.md"
    prose.write_text("Каширин шёл домой и думал о записке, которую нашёл утром возле хлебницы на кухне.\n", encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    w = compiler.compile_window(ws, library, 5)[0].read_text(encoding="utf-8")  # гл. 5 — тоже Каширин
    assert compiler.TAIL_BEGIN in w and "возле хлебницы" in w
    brief = exporter.load_brief(ws.exports, 5)
    draft = "Каширин шёл домой и думал о записке, которую нашёл утром возле хлебницы на кухне. Гараж молчал.\n"
    leak = next(c for c in verifier1.analyze(draft, w, brief, exporter.load_norms(ws.exports), []) if c.check_id == "V1.6_утечка_окна")
    assert leak.status == "PASS"


# ------------------------------------------------------------------ Писатель


def _fake_writer(monkeypatch, seen: list):
    def fake(mc, api, system, user, logs_dir, *, role, chapter=None):
        seen.append({"system": system, "user": user, "role": role, "chapter": chapter})
        return f"Текст главы {chapter}.\n"
    monkeypatch.setattr(adapters, "call_model", fake)


def test_писатель_окно_единственный_вход(ws, library, monkeypatch):
    compiler.compile_window(ws, library, 1)
    seen: list = []
    _fake_writer(monkeypatch, seen)
    writer.write_chapter(ws, Config(), 1, 1)
    window = ws.window_path(1).read_text(encoding="utf-8")
    assert seen == [{"system": "", "user": window, "role": "писатель", "chapter": 1}]
    assert ws.draft_path(1, 1).read_text(encoding="utf-8") == "Текст главы 1.\n"
    meta = json.loads((ws.chapter_dir(1) / "черновик_1.meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "генерация" and "model" in meta and "params" in meta


def test_писатель_ручной_режим_совпадает(ws, library, monkeypatch):
    """Ручной режим: автор получает тот же промпт (окно.md побайтово), что ушёл бы модели (FR-WR-4)."""
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    compiler.compile_window(ws, library, 1)
    ChapterState(ws, 1).transition("собрано", "compile")
    r = runner.invoke(app, ["write", "1"])
    assert r.exit_code == 2 and "Ручной режим" in r.output and "окно" in r.output
    seen: list = []
    _fake_writer(monkeypatch, seen)
    writer.write_chapter(ws, Config(), 1, 1)
    assert seen[0]["user"] == ws.window_path(1).read_bytes().decode("utf-8")


def test_писатель_журнал_вызова(ws, monkeypatch):
    """Каждый вызов логируется: роль, модель, токены, стоимость, длительность, глава (FR-WR-5)."""
    from konveyer.config import ApiConfig, ModelConfig

    mc = ModelConfig(provider="anthropic", model="тест-модель", price_in_per_1m=1.0, price_out_per_1m=2.0)
    api = ApiConfig(retries=1)
    text = adapters._retry_call(lambda: ("ответ", 1000, 500), api, ws.logs, role="писатель", mc=mc, chapter=3)
    assert text == "ответ"
    entry = apilog.read_log(ws.logs)[-1]
    assert entry["role"] == "писатель" and entry["model"] == "тест-модель" and entry["chapter"] == 3
    assert entry["tokens_in"] == 1000 and entry["tokens_out"] == 500 and entry["cost_est"] == pytest.approx(0.002)
    assert entry["duration"] is not None and entry["volume"] == 1
