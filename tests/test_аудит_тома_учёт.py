"""Аудит кластера «тома и учёт»: откат зафиксированной главы (FR-TK-4, FR-SC-2, FR-SC-4), коммит канона
в подпапке репозитория (FR-CN-2), тома (FR-VL-2, FR-VL-3), сохранность (FR-BK-1…4), регрессия (FR-RG-2…4),
пере-тест (FR-RT-1, FR-RT-2), учёт и экономика (FR-CT-3, FR-EC-2, FR-EC-3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_stage6_reliability import _accepted_chapter, _git, _init_repo
from konveyer import gitops
from konveyer.cli import app
from konveyer.fsm import ChapterState
from konveyer.mdparse import MarkupError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _fixed_chapter(ws, library, n: int = 1) -> ChapterState:
    _accepted_chapter(ws, library, n)
    r = runner.invoke(app, ["canonize", str(n), "--apply", "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, n)
    assert st.state == "зафиксировано" and st.data.get("коммит_приёмки")
    return st


# ------------------------------------------------------------- откат зафиксированной главы


def test_фсм_откат_сбрасывает_счётчики(ws, library):
    """A3-26 / A5-4: выход из «зафиксировано» обнуляет счётчики и снимает поля цикла приёмки (FR-TK-4)."""
    _init_repo(library)
    st = _fixed_chapter(ws, library, 1)
    st.data["авто_повторов"] = 3
    st.data["итераций_правок"] = 2
    st.data["пакет_хэш"] = "abc"
    st.data["база_приёмки"] = 2
    st._save()
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert st.state == "принято"
    assert st.data["авто_повторов"] == 0 and st.data["итераций_правок"] == 0
    for key in ("коммит_приёмки", "пакет_хэш", "база_приёмки"):
        assert key not in st.data, key
    assert st.data["история"][-1]["из"] == "зафиксировано" and st.data["история"][-1]["в"] == "принято"
    assert "выгрузки и корпус пересчитаны" in r.output


def test_откат_зафиксированной_требует_чистого_git(ws, library):
    """A5-3: грязная библиотека или чужие проиндексированные файлы блокируют откат ДО revert'а (FR-SC-2)."""
    _init_repo(library)
    _fixed_chapter(ws, library, 1)
    head = gitops.head(library)
    style = library / "02_Стиль_и_голос.md"
    style.write_text(style.read_text(encoding="utf-8") + "\nправка автора\n", encoding="utf-8")
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 1 and "незакоммиченные изменения" in r.output, r.output
    assert gitops.head(library) == head and ChapterState(ws, 1).state == "зафиксировано"
    _git(library, "checkout", "--", ".")
    # проиндексированный посторонний файл — тоже отказ, а не захват в revert-коммит
    (library / "Досье" / "чужое.md").write_text("# Чужое\n", encoding="utf-8")
    _git(library, "add", "Досье/чужое.md")
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 1 and gitops.head(library) == head, r.output
    _git(library, "reset", "-q")
    (library / "Досье" / "чужое.md").unlink()
    # чистая библиотека — откат проходит, revert-коммит трогает только файлы приёмки
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert _git(library, "log", "-1", "--format=%s").startswith("Revert")
    assert "чужое" not in _git(library, "show", "--stat", "--format=", "HEAD")


def test_откат_сообщает_о_непересчитанных_выгрузках(ws, library, monkeypatch):
    """A5-5: итоговая строка отката соответствует факту пересчёта выгрузок."""
    from konveyer.steps import canon as canon_steps

    _init_repo(library)
    _fixed_chapter(ws, library, 1)

    def broken(*a, **k):
        raise MarkupError(Path("02_Стиль.md"), 3, "сломанная таблица")

    monkeypatch.setattr(canon_steps.exporter, "run_export", broken)
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert "не пересчитаны" in r.output and "выгрузки и корпус пересчитаны" not in r.output
    assert ChapterState(ws, 1).state == "принято"


# ------------------------------------------------------------- коммит канона в подпапке репозитория


def test_commit_all_не_захватывает_индекс_вне_библиотеки(tmp_path):
    """C3-7: библиотека — подпапка репозитория; проиндексированный файл автора вне неё в коммит канона не попадает."""
    root = tmp_path / "проект"
    lib = root / "Библиотека"
    lib.mkdir(parents=True)
    (lib / "01_Мир.md").write_text("# Мир\n", encoding="utf-8")
    (root / "заметки_автора.md").write_text("заметки\n", encoding="utf-8")
    _init_repo(root)
    (root / "заметки_автора.md").write_text("заметки, ещё\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "заметки_автора.md"], check=True, capture_output=True)
    (lib / "01_Мир.md").write_text("# Мир\n\nновая строка\n", encoding="utf-8")
    (lib / "02_Стиль.md").write_text("# Стиль\n", encoding="utf-8")
    sha = gitops.commit_all(lib, "[глава 1] приёмка")
    assert sha
    files = _git(root, "show", "--stat", "--format=", "HEAD")
    assert "Библиотека/01_Мир.md" in files and "Библиотека/02_Стиль.md" in files
    assert "заметки_автора.md" not in files
    assert gitops.staged_files(lib) == ["заметки_автора.md"]  # индекс автора цел
    # revert при непустом индексе — отказ до каких-либо действий
    with pytest.raises(RuntimeError, match="в индексе git уже есть файлы"):
        gitops.revert(lib, sha)
    assert _git(root, "rev-parse", "HEAD") == sha


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(path: Path) -> dict:
    return json.loads(_read(path))


# ------------------------------------------------------------- тома: сводка, закрытие, учёт


def _log(ws, *, role: str, cost: float, chapter: int | None, volume: int = 1, tokens_out: int = 0) -> None:
    from konveyer import apilog

    apilog.current_volume = volume
    apilog.log_call(ws.logs, role=role, model="м", tokens_in=1000, tokens_out=tokens_out, cost_est=cost, chapter=chapter)
    apilog.current_volume = 1


def test_учёт_тома_сходится_с_журналом(ws, library):
    """B2-2: стоимость тома = сумма ВСЕХ строк журнала тома, включая вызовы без главы; `том статус` и `учёт` совпадают."""
    from konveyer import accounting, apilog, volume as volume_mod

    _log(ws, role="писатель", cost=0.10, chapter=1)
    _log(ws, role="линтер канона", cost=0.25, chapter=None)
    _log(ws, role="верификатор-2 (регрессия)", cost=0.05, chapter=None)
    _log(ws, role="писатель", cost=9.0, chapter=1, volume=2)  # чужой том — не считается
    acc = accounting.volume_account(ws, 1, library=library)
    expected = sum(float(r["cost_est"]) for r in apilog.read_log(ws.logs) if r["volume"] == 1)
    assert acc.cost == pytest.approx(expected) == pytest.approx(0.40)
    assert acc.other_cost == pytest.approx(0.30) and acc.other_calls == 2 and acc.calls == 3
    assert acc.by_role == {"писатель": 0.10, "линтер": 0.25, "верификатор2": 0.05}
    text = accounting.render(acc)
    assert accounting.OUTSIDE in text and "стоимость тома: 0.40 $" in text
    stats = volume_mod.volume_stats(ws, library, 1)
    assert stats.cost == pytest.approx(acc.cost) and stats.calls == acc.calls
    r = runner.invoke(app, ["учёт"])
    assert r.exit_code == 0 and "стоимость тома: 0.40 $" in r.output, r.output
    r = runner.invoke(app, ["том", "статус"])
    assert r.exit_code == 0 and "$0.40" in r.output and "Время автора" in r.output, r.output


def test_нормализация_ролей_журнала():
    """B4-31: имена ролей журнала приводятся к ролям конфига, подроль в скобках отбрасывается."""
    from konveyer.accounting import normalize_role

    assert normalize_role("писатель (правки)") == "писатель"
    assert normalize_role("верификатор-2 (повторно)") == "верификатор2"
    assert normalize_role("вкус") == "верификатор2"
    assert normalize_role("аналитик драматургии") == "аналитик"
    assert normalize_role("линтер канона") == "линтер"
    assert normalize_role("архивариус") == "архивариус"
    assert normalize_role("пере-тест (писатель)") == "писатель"
    assert normalize_role("writer") == "писатель"
    assert normalize_role(None) == "—"


def test_том_статус_и_учёт_чужого_тома(ws, library):
    """B2-7: сводка не текущего тома берёт поглавник из документов тома, а не нули из чужих выгрузок."""
    from konveyer import accounting, volume as volume_mod

    stats = volume_mod.volume_stats(ws, library, 2)
    assert stats.chapters_total == 4  # демо: том 2 — четыре главы в поглавнике
    acc = accounting.volume_account(ws, 2, library=library)
    assert acc.chapters_total == 4
    r = runner.invoke(app, ["том", "статус", "2"])
    assert r.exit_code == 0 and "глав в поглавнике: 4" in r.output, r.output
    r = runner.invoke(app, ["учёт", "--том", "2"])
    assert r.exit_code == 0 and "Глав в плане: 4" in r.output, r.output
    # рабочие выгрузки остались тома 1
    from konveyer import exporter

    assert exporter.export_volume(ws.exports) == 1
    # тома, которого нет в библиотеке, — «поглавник недоступен», а не «0 глав»
    r = runner.invoke(app, ["том", "статус", "7"])
    assert r.exit_code == 0 and "поглавник недоступен" in r.output and "глав в поглавнике: 0" not in r.output, r.output
    r = runner.invoke(app, ["учёт", "--том", "7"])
    assert r.exit_code == 0 and "Прогноз недоступен" in r.output and "Осталось глав: 0" not in r.output, r.output


def test_учёт_без_выгрузок_без_нулей(ws, library):
    """B2-19: без выгрузок `учёт` сначала делает экспорт; если поглавник недоступен — так и говорит."""
    import shutil

    shutil.rmtree(ws.exports)
    r = runner.invoke(app, ["учёт"])
    assert r.exit_code == 0 and "Глав в плане: 6" in r.output, r.output


def _all_fixed(ws, library) -> None:
    for n in range(1, 7):
        _accepted_chapter(ws, library, n)
        r = runner.invoke(app, ["canonize", str(n), "--apply", "-y"])
        assert r.exit_code == 0, r.output


def test_том_закрытие_требует_git(ws, library):
    """B2-17: без git закрытие тома отказывает до записи, как приёмка главы."""
    for n in range(1, 7):
        st = ChapterState(ws, n)
        st.data["состояние"] = "зафиксировано"
        st._save()
    r = runner.invoke(app, ["том", "закрыть", "1", "-y"])
    assert r.exit_code == 1 and "не под git" in r.output, r.output
    assert not list(library.glob("*Снапшот*")) and not (ws.root / "рукопись").exists()


def test_том_закрытие_отказывает_без_прозы_зафиксированной_главы(ws, library):
    """B2-8: удалённая проза зафиксированной главы останавливает закрытие ДО снапшота, а не даёт неполную рукопись."""
    _init_repo(library)
    _all_fixed(ws, library)
    (library / "Проза" / "Том1_Глава06.md").unlink()
    _git(library, "add", "-A")
    _git(library, "commit", "-q", "-m", "потеряна проза")
    head = gitops.head(library)
    r = runner.invoke(app, ["том", "закрыть", "1", "-y", "--без-переключения"])
    assert r.exit_code == 1 and "гл. 6" in r.output and "нет документа прозы" in r.output, r.output
    assert gitops.head(library) == head and not list(library.glob("*Снапшот*")) and "том-1" not in gitops.tags(library)
    assert not (ws.root / "рукопись" / "Том1.md").exists()


def test_том_сбой_рукописи_не_трогает_канон(ws, library):
    """B2-9: рукопись и статистика собираются до снапшота — их сбой оставляет канон и теги нетронутыми."""
    from konveyer import volume as volume_mod

    _init_repo(library)
    _all_fixed(ws, library)
    head = gitops.head(library)

    def boom(*a, **k):
        raise OSError("рукопись/: нет прав на запись")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(volume_mod, "build_manuscript", boom)
        r = runner.invoke(app, ["том", "закрыть", "1", "-y", "--без-переключения"])
    assert r.exit_code != 0 and "нет прав" in r.output, r.output
    assert gitops.head(library) == head and not list(library.glob("*Снапшот*")) and "том-1" not in gitops.tags(library)
    r = runner.invoke(app, ["том", "закрыть", "1", "-y", "--без-переключения"])  # повтор без --заново проходит
    assert r.exit_code == 0 and "Том 1 закрыт" in r.output, r.output
    assert "том-1" in gitops.tags(library)
    stats = (ws.root / "рукопись" / "Том1_статистика.md").read_text(encoding="utf-8")
    assert "Время автора" in stats and "Глав в поглавнике: 6" in stats


def test_том_закрыть_с_yes_без_терминала(ws, library):
    """B2-10: `-y` без --следующий/--без-переключения не задаёт вопрос (нет терминала → был Abort, код 1)."""
    _init_repo(library)
    _all_fixed(ws, library)
    r = runner.invoke(app, ["том", "закрыть", "1", "-y"], input=None)
    assert r.exit_code == 0 and "Aborted" not in r.output, r.output
    assert "Том 1 закрыт" in r.output and "том открыть 2" in r.output
    from konveyer.config import load_config

    assert load_config(ws).volume == 1


def test_вопрос_о_переключении_без_терминала_это_нет():
    from konveyer.steps.volume import _ask

    class Abort(RuntimeError):
        pass

    def eof(_prompt):
        raise EOFError

    def abort(_prompt):
        raise Abort()

    assert _ask(eof, "?") is False and _ask(abort, "?") is False and _ask(None, "?") is False
    assert _ask(lambda p: True, "?") is True


def test_числовые_метрики_статистики_из_реестра():
    """B2-35: подписи и состав числовых метрик статистики тома — из реестра метрик, не из локального словаря."""
    from konveyer import metrics, volume as volume_mod

    checks = volume_mod.numeric_checks()
    assert "V1.2a_средняя_длина" in checks and "V1.9a_доля_диалога" in checks
    assert checks["V1.2a_средняя_длина"].startswith("средняя_длина")
    assert set(checks) <= {m.check_id for m in metrics.REGISTRY.values()}


def test_расход_за_сутки_по_локальной_дате(ws):
    """B2-20: «сегодня» для расхода за сутки — локальная дата, как для минут автора в timing."""
    from datetime import datetime, timedelta, timezone

    from konveyer import accounting, guard

    plus3 = timezone(timedelta(hours=3))
    row = {"ts": "2026-05-01T23:30:00+00:00", "role": "писатель", "model": "м", "cost_est": 0.5, "chapter": 1, "volume": 1}
    guard.append_text(ws.logs / "api.jsonl", json.dumps(row) + "\n")
    same_instant_plus3 = datetime(2026, 5, 2, 2, 30, tzinfo=plus3)  # тот же момент, другая запись
    assert accounting.today_cost(ws, same_instant_plus3) == pytest.approx(0.5)
    assert accounting.today_cost(ws, datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)) == 0.0


def test_ориентиры_экономики_из_конфига_и_журнала(ws, library):
    """B2-14: ориентиры FR-EC-3 — глава/том/серия/доля Писателя — считаются из цен конфига, поглавника и плана томов."""
    from konveyer import accounting
    from konveyer.config import Config, ModelConfig

    priced = ModelConfig(provider="anthropic", model="м", price_in_per_1m=3.0, price_out_per_1m=15.0)
    cfg = Config(writer=priced, verifier2=priced, canonist=priced)
    _log(ws, role="писатель", cost=0.8, chapter=1, tokens_out=1500)
    _log(ws, role="канонист", cost=0.2, chapter=1)
    acc = accounting.volume_account(ws, 1, library=library)
    g = accounting.guidelines(ws, cfg, acc)
    assert g.out_tokens == 1500 and g.out_source.startswith("среднее по журналу")
    assert g.generation and g.chapter_simple and g.chapter_real and g.chapter_real > g.chapter_simple
    assert g.chapters_total == 6 and g.volume == pytest.approx(g.chapter_real * 6, abs=0.01)
    assert g.volumes_total == 2 and g.series == pytest.approx(g.volume * 2, abs=0.01)
    assert g.writer_share_fact == pytest.approx(0.8) and 0 < g.writer_share_est < 1
    text = accounting.render(acc, cfg, ws)
    assert "серия из 2 т." in text and "Доля Писателя" in text and "13 000" not in text
    # без цен — ориентиры честно не считаются
    g0 = accounting.guidelines(ws, Config(), acc)
    assert g0.generation is None and "не заданы" in "\n".join(accounting.render_guidelines(g0))


def test_русские_ключи_конфига_сохранности_и_цен(ws):
    """B2-34: ключи сохранности, цен и ориентиров — по-русски, латинские остаются синонимами."""
    from konveyer.config import Config, load_config

    (ws.root / "конфиг.yaml").write_text(
        "библиотека: Библиотека\nпапка_архива: ../архивы_тест\nхранить_архивов: 3\nмест_хранения_мин: 1\n"
        "автор_коммита: \"Автор <a@b.c>\"\nориентиры: {окно_знаков: 9000, выход_токенов: 700}\n"
        "модели:\n  писатель: {провайдер: Anthropic, модель: м, цена_вход_1м: 3, цена_выход_1м: 15}\n",
        encoding="utf-8",
    )
    cfg = load_config(ws)
    assert cfg.backup_dir == "../архивы_тест" and cfg.backup_keep == 3 and cfg.backup_remotes_min == 1
    assert cfg.commit_author == "Автор <a@b.c>" and cfg.guidelines.window_chars == 9000 and cfg.guidelines.out_tokens == 700
    assert cfg.writer.provider == "anthropic" and cfg.writer.price_in_per_1m == 3.0 and cfg.writer.price_out_per_1m == 15.0
    assert Config(writer={"provider": " Ручной ", "model": "—"}).writer.manual
    assert not Config(writer={"provider": "gemeni", "model": "м"}).writer.known_provider
