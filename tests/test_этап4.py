"""Этап 4 (7.13–7.16, 9, 12 ТЗ): адаптеры и роли, ручной провайдер, ошибки биллинга, ключи, шаблоны,
регрессия, пере-тест и пины, сохранность, учёт времени и денег."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import accounting, adapters, apilog, backup as backup_mod, compiler, gitops, llmjson, pins, regression, timing, verifier2
from konveyer.cli import app
from konveyer.config import ApiConfig, Config, ModelConfig
from konveyer.fsm import ChapterState
from konveyer.steps import canon as canon_steps

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


# ------------------------------------------------------------------ 9. адаптеры


def test_адаптер_повторы_и_таймаут(ws, monkeypatch):
    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("сеть упала")
        return "ок", 10, 5

    api = ApiConfig(retries=3, backoff_base_s=0.0, timeout_s=7)
    mc = ModelConfig(provider="anthropic", model="м")
    assert adapters._retry_call(flaky, api, ws.logs, role="писатель", mc=mc, chapter=1) == "ок" and calls["n"] == 3
    log = apilog.read_log(ws.logs)
    assert len(log) == 3 and log[0]["error"].startswith("сеть") and log[-1]["tokens_in"] == 10
    calls["n"] = -10  # всегда падает → ручной режим с готовым промптом
    with pytest.raises(adapters.ManualModeNeeded) as e:
        adapters._retry_call(flaky, ApiConfig(retries=2, backoff_base_s=0.0), ws.logs, role="писатель", mc=mc, chapter=1)
    assert "ручн" in (e.value.hint + e.value.reason).lower()


def test_адаптер_ручной_режим_промпт(ws, library):
    """Ручной провайдер: промпт сохранён, вызов сразу переводит в ручной режим (FR-AD-1, FR-WR-4)."""
    compiler.compile_window(ws, library, 1)
    cfg = Config(writer=ModelConfig(provider="ручной", model="—"))
    with pytest.raises(adapters.ManualModeNeeded, match="ручной провайдер"):
        adapters.call_model(cfg.writer, cfg.api, "", "промпт", ws.logs, role="писатель", chapter=1)
    ChapterState(ws, 1).transition("собрано", "compile")
    r = runner.invoke(app, ["write", "1"])
    assert r.exit_code == 2 and "Ручной режим" in r.output


def test_адаптер_ошибка_биллинга_распознана():
    class Http(Exception):
        def __init__(self, status, msg):
            super().__init__(msg)
            self.status_code = status

    assert adapters.classify_error(Http(402, "payment required")) == "биллинг"
    assert adapters.classify_error(Http(429, "quota exceeded")) == "квота"
    assert adapters.classify_error(Http(401, "invalid api key")) == "доступ"
    assert adapters.classify_error(Http(503, "overloaded")) == "сеть"
    assert "биллинг" in adapters.explain_error(Http(402, "payment required"), "Писатель").lower() or \
           "оплат" in adapters.explain_error(Http(402, "payment required"), "Писатель").lower()
    assert not adapters._retryable(Http(402, "x")) and adapters._retryable(Http(503, "x"))


def test_адаптер_json_с_обрамлением():
    raw = "Вот флаги:\n```json\n[{\"a\": 1}]\n```\nи хвост текста."
    assert llmjson.extract_json(raw, list) == [{"a": 1}]
    assert llmjson.extract_json("{\"x\": [1, 2]} спасибо", dict) == {"x": [1, 2]}
    with pytest.raises(ValueError):
        llmjson.extract_json("никакого JSON тут нет", list)


def test_адаптер_неразбираемый_ответ_сохранён(ws, library, monkeypatch):
    compiler.compile_window(ws, library, 1)
    ws.draft_path(1, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    monkeypatch.setattr(adapters, "call_role", lambda *a, **k: "Ответ без JSON, извините")
    with pytest.raises(ValueError, match="сохранён целиком"):
        verifier2.run_verify2(ws, Config(), 1, 1)
    assert (ws.chapter_dir(1) / "ответ_э2_сырой.md").read_text(encoding="utf-8") == "Ответ без JSON, извините"


def test_адаптер_ключи_не_в_журналах(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-123456")
    assert "sk-ant-secret" not in adapters.mask_secrets("ошибка: key sk-ant-secret-123456 rejected; ANTHROPIC_API_KEY=sk-ant-secret-123456")

    def boom():
        raise RuntimeError("401 invalid ANTHROPIC_API_KEY sk-ant-secret-123456")

    with pytest.raises(adapters.ManualModeNeeded):
        adapters._retry_call(boom, ApiConfig(retries=1), ws.logs, role="писатель", mc=ModelConfig(provider="anthropic", model="м"), chapter=1)
    log_text = (ws.logs / "api.jsonl").read_text(encoding="utf-8")
    assert "sk-ant-secret" not in log_text
    gi = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi


def test_шаблон_проекта_переопределяет_движковый(ws, library):
    ws.draft_path(1, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    (ws.root / "промпты").mkdir(exist_ok=True)
    (ws.root / "промпты" / "верификатор2_система.md").write_text("СВОЙ ПРОМПТ Э2 для «{{ series }}» {{ max_flags }}", encoding="utf-8")
    system, user = verifier2.build_prompt(ws, 1, 1, Config())
    assert system.startswith("СВОЙ ПРОМПТ Э2 для «Гаражи» 12")
    # остальные шаблоны — из движка; отпечаток шаблонов входит в отчёт регрессии (FR-AD-8)
    h1 = regression.environment_hashes(ws)["шаблоны"]
    (ws.root / "промпты" / "верификатор2_система.md").write_text("ДРУГОЙ", encoding="utf-8")
    assert regression.environment_hashes(ws)["шаблоны"] != h1


# ------------------------------------------------------------------ 7.13 регрессия


def test_регрессия_пополнение_и_отпечаток(ws, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    frag = ws.root / "фрагмент.md"
    frag.write_text("Он был. Было тихо. Была ночь. Были все.", encoding="utf-8")
    r = runner.invoke(app, ["add-golden", "красный_новый", str(frag), "--expect", "V1.3_был", "--focal", "Каширин", "--year", "1995"])
    assert r.exit_code == 0, r.output
    report = regression.run_regression(ws)
    assert any(t["test_id"] == "красный_новый" and "V1.3_был" in t["поймано"] for t in report["результаты"])
    assert report["зелёная"] and set(report["хэши"]) >= {"конфиг.yaml", "шаблоны", "norms.json"}
    # смена конфигурации → отчёт устарел; красная регрессия блокирует фиксацию пере-теста (FR-RG-3)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nwindow_soft_limit_chars: 70000\n", encoding="utf-8")
    assert regression.is_stale(ws)
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1 and "фиксация retest запрещена" in r.output


# ------------------------------------------------------------------ 7.14 пере-тест и пины


def test_перетест_пакет_сравнения_и_ручной_прогон(ws, library, monkeypatch):
    seen = []

    def fake(mc, api, system, user, logs, *, role, chapter=None):
        seen.append((mc.model, role))
        if mc.provider == "gemini":
            raise adapters.ManualModeNeeded("нет ключа", "прогоните вручную")
        return "Каширин шёл по перрону. Ветер гнал обрывки газет.\n"

    monkeypatch.setattr(adapters, "call_model", fake)
    dest = canon_steps.retest(chapter=1)
    assert (dest / "ПРОМПТ_раунд1.md").exists()
    prompt = (dest / "ПРОМПТ_раунд1.md").read_text(encoding="utf-8")
    assert "ОКНО КОНТЕКСТА" in prompt and not (ws.root / "главы" / "001" / "окно.md").exists()
    answers = sorted(p.name for p in dest.glob("ответ_*.md"))
    assert answers == ["ответ_claude-sonnet-4-5.md"]  # anthropic-роли — одна модель, прогнана один раз
    results = (dest / "РЕЗУЛЬТАТЫ.md").read_text(encoding="utf-8")
    assert "Ручной прогон: gemini-3.1-pro" in results and "claude-sonnet-4-5" in results
    summary = (dest / "СВОДКА.md").read_text(encoding="utf-8")
    assert "| claude-sonnet-4-5 |" in summary and "V1.2a_средняя_длина" in summary
    # ручной прогон: положили ответ — сводка пересчиталась
    (dest / "ответ_gemini-3.1-pro.md").write_text("Короткая фраза. Ещё одна.\n", encoding="utf-8")
    dest2 = canon_steps.retest(chapter=1)
    assert dest2 == dest and "| gemini-3.1-pro |" in (dest / "СВОДКА.md").read_text(encoding="utf-8")


def test_смена_пина_без_перетеста_предупреждает(ws, library, monkeypatch):
    cfg = Config()
    pins.record(ws, cfg, note="тест")
    assert pins.warn_if_changed(ws, cfg) is None
    changed = Config(writer=ModelConfig(provider="gemini", model="gemini-другая"))
    warning = pins.warn_if_changed(ws, changed)
    assert warning and "без пере-теста" in warning and "gemini-другая" in warning
    log = (ws.logs / pins.CHANGES).read_text(encoding="utf-8")
    assert log.count("\n") == 1 and "gemini-другая" in log
    pins.warn_if_changed(ws, changed)
    assert (ws.logs / pins.CHANGES).read_text(encoding="utf-8").count("\n") == 1  # один раз на отпечаток
    # при генерации — предупреждение в выводе
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nwriter: {provider: gemini, model: gemini-другая}\n", encoding="utf-8")
    compiler.compile_window(ws, library, 1)
    ChapterState(ws, 1).transition("собрано", "compile")
    r = runner.invoke(app, ["write", "1"])
    assert "без пере-теста" in r.output


def test_доктор_пин_не_найден_и_не_проверен(ws, monkeypatch):
    from tests.test_stage10_safety import _fake_anthropic, _fake_genai

    r = runner.invoke(app, ["doctor"])
    assert "~ модель gemini-3.1-pro (Писатель): нет GEMINI_API_KEY" in r.output  # не проверено
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _fake_genai(monkeypatch, set())
    _fake_anthropic(monkeypatch, {"claude-sonnet-4-5"})
    r = runner.invoke(app, ["doctor"])
    assert "✗ модель gemini-3.1-pro (Писатель): модель «gemini-3.1-pro» не найдена" in r.output  # не найдена
    assert "режиме без обучения" in r.output


# ------------------------------------------------------------------ 7.15 сохранность


def test_бэкап_второе_место_и_bare_папка(ws, library, tmp_path):
    from tests.test_stage6_reliability import _init_repo

    _init_repo(library)
    r = runner.invoke(app, ["doctor"])
    assert "удалённых копий: 0 (нужно ≥2)" in r.output
    target = tmp_path / "внешний_диск" / "библиотека.git"
    r = runner.invoke(app, ["backup", "--добавить-remote", "диск", str(target)])
    assert r.exit_code == 0, r.output
    assert gitops.is_bare_repo(target) and "диск" in gitops.remotes(library)


def test_архив_ротация_и_после_приёмки(ws, library, tmp_path):
    from tests.test_stage6_reliability import _accepted_chapter, _init_repo

    _init_repo(library)
    dest = tmp_path / "архивы"
    (ws.root / "конфиг.yaml").write_text(f"library_dir: Библиотека\nbackup_dir: \"{dest.as_posix()}\"\nbackup_keep: 2\n", encoding="utf-8")
    cfg = Config(backup_dir=str(dest), backup_keep=2)
    for _ in range(3):
        backup_mod.make_archive(ws, cfg)
    assert len(backup_mod.list_archives(dest)) == 2
    _accepted_chapter(ws, library, 1)
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0 and "Архив рабочей области" in r.output, r.output
    assert len(backup_mod.list_archives(dest)) == 2 and "глава-1" in gitops.tags(library)


# ------------------------------------------------------------------ 7.16 учёт


def test_учёт_стоимость_по_ценам_конфига(ws):
    mc = ModelConfig(provider="anthropic", model="м", price_in_per_1m=3.0, price_out_per_1m=15.0)
    adapters._retry_call(lambda: ("x", 100_000, 10_000), ApiConfig(retries=1), ws.logs, role="писатель", mc=mc, chapter=2)
    adapters._retry_call(lambda: ("x", 50_000, 0), ApiConfig(retries=1), ws.logs, role="верификатор-2", mc=mc, chapter=2)
    acc = accounting.volume_account(ws, 1)
    assert acc.chapters[2].cost == pytest.approx(0.3 + 0.15 + 0.15) and acc.by_role["писатель"] == pytest.approx(0.45)
    assert acc.cost == pytest.approx(sum(float(r["cost_est"]) for r in apilog.read_log(ws.logs)))
    assert accounting.estimate_before(mc, 3000, 1000) == pytest.approx(3000 / 3 * 3 / 1e6 + 1000 * 15 / 1e6)
    assert accounting.today_cost(ws) == pytest.approx(0.6)
    # пороги (FR-EC-2)
    cfg = Config(thresholds={"chapter_cost_usd": 0.5, "daily_cost_usd": 0.5})
    w = accounting.warnings(ws, cfg, 2)
    assert any("стоимость главы 2" in x for x in w) and any("расход за сутки" in x for x in w)
    assert accounting.warnings(ws, Config(), 2) == []


def test_учёт_перерыв_по_порогу_и_переход_внутри_задачи():
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    hist = [
        {"время": t0.isoformat(), "из": "не-начато", "в": "собрано", "задача": f"compile@{t0.isoformat()}"},
        {"время": (t0 + timedelta(minutes=30)).isoformat(), "из": "собрано", "в": "сгенерировано",
         "задача": f"write@{(t0 + timedelta(minutes=25)).isoformat()}"},
        {"время": (t0 + timedelta(minutes=30 + 150)).isoformat(), "из": "сгенерировано", "в": "верифицировано-1",
         "задача": f"verify1@{(t0 + timedelta(minutes=30 + 150)).isoformat()}"},
    ]
    timing.set_pause_threshold(120)
    kinds = [(k, round(s / 60)) for k, s, _ in timing.intervals(hist)]
    assert ("авторское", 25) in kinds and ("машинное", 5) in kinds and ("перерыв", 150) in kinds
    timing.set_pause_threshold(180)
    kinds = [(k, round(s / 60)) for k, s, _ in timing.intervals(hist)]
    assert ("авторское", 150) in kinds
    timing.set_pause_threshold(120)


def test_прогноз_остатка_тома(ws):
    for n in (1, 2):
        st = ChapterState(ws, n)
        st.data["состояние"] = "зафиксировано"
        st._save()
    mc = ModelConfig(provider="anthropic", model="м", price_in_per_1m=1.0, price_out_per_1m=1.0)
    for n, tokens in ((1, 100_000), (2, 300_000)):
        adapters._retry_call(lambda t=tokens: ("x", t, 0), ApiConfig(retries=1), ws.logs, role="писатель", mc=mc, chapter=n)
    acc = accounting.volume_account(ws, 1)
    f = acc.forecast()
    assert acc.chapters_total == 6 and f["осталось_глав"] == 4 and f["стоимость"] == pytest.approx(0.2 * 4)
    r = runner.invoke(app, ["учёт"])
    assert r.exit_code == 0 and "Прогноз остатка тома" in r.output and "Осталось глав: 4" in r.output
    assert (ws.logs / "учёт_том1.md").exists()
