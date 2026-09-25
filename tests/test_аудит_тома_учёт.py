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


# ------------------------------------------------------------- сохранность


def test_архив_без_git_и_состав_архива(ws, library, tmp_path):
    """B2-3, B2-5, B5-14, B2-25: архив делается без git и содержит манифест, сырьё, онбординг, переопределения,
    пере-тест и саму библиотеку (когда она не под git); выгрузки и .env — нет."""
    import zipfile

    for rel, text in (("сырьё/оригинал.md", "# оригинал автора\n"), ("онбординг/предложение.json", "{}"),
                      ("промпты/писатель.md", "свой промпт"), ("пере-тест/20260101/СВОДКА.md", "# сводка"),
                      ("главы/001/окно.md", "окно"), (".env", "KEY=секрет"), (".env.example", "KEY=")):
        p = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    dest = tmp_path / "архивы"
    r = runner.invoke(app, ["бэкап", "--архив", str(dest)])
    assert r.exit_code == 0 and "Архив рабочей области" in r.output and "не под git" in r.output, r.output
    from konveyer import backup as backup_mod

    names = set(zipfile.ZipFile(backup_mod.latest_archive(dest)).namelist())
    for must in ("проект.yaml", "конфиг.yaml", ".env.example", "сырьё/оригинал.md", "онбординг/предложение.json",
                 "промпты/писатель.md", "пере-тест/20260101/СВОДКА.md", "главы/001/окно.md",
                 "Библиотека/02_Стиль_и_голос.md", "Библиотека/Проза/Том1_Глава03.md"):
        assert must in names, must
    assert ".env" not in names and not any(n.startswith("выгрузки/") for n in names)
    # библиотека под git — своими копиями хранится, в архив не входит
    _init_repo(library)
    r = runner.invoke(app, ["бэкап", "--архив", str(dest)])
    assert r.exit_code == 0, r.output
    names = set(zipfile.ZipFile(backup_mod.latest_archive(dest)).namelist())
    assert not any(n.startswith("Библиотека/") for n in names) and "главы/001/окно.md" in names
    # --push без git — понятный отказ, а не трейсбек
    r = runner.invoke(app, ["бэкап", "--push", "-y"])
    assert r.exit_code == 1 and "нет удалённых" in r.output, r.output


def test_бэкап_push_отправляет_теги(ws, library, tmp_path):
    """B2-4: во второе место хранения уходят теги приёмок и томов, а не только ветка (FR-BK-1, FR-BK-3)."""
    _init_repo(library)
    _fixed_chapter(ws, library, 1)
    assert "глава-1" in gitops.tags(library)
    bare = tmp_path / "резерв.git"
    r = runner.invoke(app, ["бэкап", "--добавить-remote", "резерв", str(bare)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["бэкап", "--push", "-y"])
    assert r.exit_code == 0 and "✓ резерв" in r.output, r.output
    assert "глава-1" in _git(bare, "tag", "--list").split()
    assert _git(bare, "rev-list", "-n", "1", "глава-1") == _git(library, "rev-list", "-n", "1", "глава-1")


def test_library_split_перепривязывает_главы_всех_томов(ws, library, tmp_path, monkeypatch):
    """B2-26: после переезда библиотеки SHA приёмок снимаются/перепривязываются и у глав других томов."""
    from konveyer import backup as backup_mod
    from konveyer.config import load_config

    root = ws.root
    _init_repo(root)  # библиотека — подпапка репозитория рабочей области («shared»)
    for v, n in ((1, 2), (2, 1)):
        st = ChapterState(ws.for_volume(v), n)
        st.data["состояние"] = "зафиксировано"
        st.data["коммит_приёмки"] = "deadbeef" * 5
        st._save()
    assert backup_mod.volumes_present(ws) == [1, 2]
    cfg = load_config(ws)
    plan = backup_mod.plan_split(ws, cfg, library, tmp_path / "Библиотека_новая")
    assert plan.fixed_chapters == [(1, 2), (2, 1)]
    assert any("т.2 гл. 1" in line for line in plan.lines())
    notes = backup_mod.split_library(ws, cfg, plan)
    assert any("т.2 гл. 1" in n for n in notes)
    for v, n in ((1, 2), (2, 1)):
        data = ChapterState(ws.for_volume(v), n).data
        assert "коммит_приёмки" not in data and data["коммит_приёмки_до_переезда"].startswith("deadbeef")


def test_архивы_одной_секунды_упорядочены(ws, tmp_path, monkeypatch):
    """Два архива в одну секунду: второй считается новее (latest_archive) и ротация удаляет первый."""
    from konveyer import backup as backup_mod
    from konveyer.config import Config

    class FrozenDT(backup_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 1, 12, 0, 0)

    monkeypatch.setattr(backup_mod, "datetime", FrozenDT)
    dest = tmp_path / "архивы"
    first, _ = backup_mod.make_archive(ws, Config(), dest, keep=0)
    second, _ = backup_mod.make_archive(ws, Config(), dest, keep=0)
    assert first != second and backup_mod.latest_archive(dest) == second
    removed = backup_mod.rotate(dest, 1)
    assert removed == [first] and backup_mod.list_archives(dest) == [second]


# ------------------------------------------------------------- регрессия


def test_регрессия_поймано_пропущено_лишние(ws):
    """FR-RG-2 / B2-27: три множества отчёта; `ignore_flags` убирает шум норм длин из «лишних»."""
    from konveyer import regression
    from konveyer.schemas import GoldenTest

    for f in regression.golden_dir(ws).glob("*.json"):
        f.unlink()
    frag = "Вечер был долгим. Небо было низким. В доме было холодно. Каширин был мрачен. Все были об одном."
    regression.add_test(ws, GoldenTest(test_id="без_игнора", fragment=frag, context_slice={"focal": "Каширин", "year": 1995},
                                       expected_flags=["V1.3_был", "V1.5_стоп_лексика"]))
    regression.add_test(ws, GoldenTest.model_validate({
        "id": "с_игнором", "фрагмент": frag, "срез_контекста": {"focal": "Каширин", "year": 1995},
        "ожидаемые_флаги": ["V1.3_был"], "игнорировать_флаги": ["V1.2a_средняя_длина", "V1.2b_доля_коротких", "V1.2e_объём"],
    }))
    report = regression.run_regression(ws)
    by_id = {r["test_id"]: r for r in report["результаты"]}
    assert by_id["без_игнора"]["поймано"] == ["V1.3_был"] and by_id["без_игнора"]["пропущено"] == ["V1.5_стоп_лексика"]
    assert "V1.2b_доля_коротких" in by_id["без_игнора"]["лишние"]
    assert by_id["с_игнором"] == {"test_id": "с_игнором", "поймано": ["V1.3_был"], "пропущено": [], "лишние": []}
    assert not report["зелёная"] and report["провалено"] == ["без_игнора"]


def test_демо_корпус_без_лишних_флагов(ws):
    """D2-21: у стартового корпуса «лишние» пусты — золотой тест различает «поймал ровно то» и «сработало всё подряд»."""
    from konveyer import regression

    report = regression.run_regression(ws)
    assert report["зелёная"]
    assert all(r["лишние"] == [] for r in report["результаты"] if not r.get("skipped")), report["результаты"]


def test_регрессия_красная_блокирует_смену_модели(ws):
    """D1-15 / B2-28: свежий, но КРАСНЫЙ отчёт запрещает `пере-тест --зафиксировать`; доктор говорит «КРАСНАЯ»."""
    from konveyer import regression
    from konveyer.schemas import GoldenTest

    regression.add_test(ws, GoldenTest(test_id="ложный", fragment="Он вышел из дома и пошёл к станции.",
                                       context_slice={"focal": "Каширин", "year": 1995}, expected_flags=["V1.5_стоп_лексика"]))
    r = runner.invoke(app, ["регрессия"])
    assert r.exit_code == 1 and "КРАСНАЯ" in r.output, r.output
    assert regression.is_green(ws) is False and not regression.is_stale(ws)
    r = runner.invoke(app, ["пере-тест", "--зафиксировать", "--без-пакета"])
    assert r.exit_code == 1 and "КРАСНАЯ" in r.output, r.output
    assert not (ws.logs / "пины.json").exists()
    r = runner.invoke(app, ["доктор"])
    assert "регрессия КРАСНАЯ" in r.output


def test_регрессия_отпечаток_только_конфигурация_проверок(ws):
    """B2-6: переключение тома, папка архива, пороги расходов отчёт не устаревают; модели, e2-параметры, лимит окна — да."""
    from konveyer import regression
    from konveyer.config import set_volume

    regression.run_regression(ws)
    assert regression.is_green(ws) is True
    set_volume(ws, 2)
    assert regression.is_green(ws) is True and not regression.is_stale(ws)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nvolume: 2\nbackup_dir: ../архивы\n"
                                          "пороги: {стоимость_главы: 1.0}\n", encoding="utf-8")
    assert regression.is_green(ws) is True
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nvolume: 2\ne2_max_flags: 5\n", encoding="utf-8")
    assert regression.is_green(ws) is None and regression.is_stale(ws)
    regression.run_regression(ws)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nvolume: 2\ne2_max_flags: 5\n"
                                          "writer: {provider: gemini, model: другая}\n", encoding="utf-8")
    assert regression.is_stale(ws)


def test_регрессия_э2_без_api_промпт_и_ответ_файлом(ws):
    """B4-32: без API промпт Э2 сохраняется, ответ файлом принимается, нечитаемый ответ — «не разобран» с сырым файлом."""
    from konveyer import regression

    report = regression.run_regression(ws, llm=True)
    e2 = next(r for r in report["результаты"] if r["test_id"] == "красный_дс_сентенции_э2")
    assert "API недоступен" in e2["skipped"] and "промпты" in e2["skipped"]
    prompt = ws.regression / "промпты" / "красный_дс_сентенции_э2.md"
    assert prompt.exists() and "<текст_главы>" in prompt.read_text(encoding="utf-8")
    answers = ws.regression / "ответы"
    answers.mkdir()
    (answers / "красный_дс_сентенции_э2.json").write_text("тут не JSON", encoding="utf-8")
    report = regression.run_regression(ws)  # ответ есть — тест выполняется и без --llm
    e2 = next(r for r in report["результаты"] if r["test_id"] == "красный_дс_сентенции_э2")
    assert "не разобран" in e2["skipped"] and (answers / "красный_дс_сентенции_э2_сырой.md").exists()
    (answers / "красный_дс_сентенции_э2.json").write_text(json.dumps([
        {"flag_id": "F-001", "type": "бриф", "kind": "violation", "quote": "Жизнь, думал Каширин", "rule": "сентенция вне брифа"}
    ], ensure_ascii=False), encoding="utf-8")
    report = regression.run_regression(ws)
    e2 = next(r for r in report["результаты"] if r["test_id"] == "красный_дс_сентенции_э2")
    assert e2["поймано"] == ["бриф"] and not e2["пропущено"] and report["зелёная"]


# ------------------------------------------------------------- пере-тест


def _fake_models(monkeypatch, *, e2_json: str | None = None):
    from konveyer import adapters

    seen = []

    def fake(mc, api, system, user, logs, *, role, chapter=None, role_key=None):
        seen.append((mc.model, role, mc.params.get("t")))
        if mc.provider == "gemini":
            raise adapters.ManualModeNeeded("нет ключа", "прогоните вручную")
        if role.startswith("верификатор-2"):
            return e2_json if e2_json is not None else "[]"
        return "Каширин шёл по перрону. Ветер гнал обрывки газет.\n"

    monkeypatch.setattr(adapters, "call_model", fake)
    return seen


def test_перетест_флаги_э2_в_сводке(ws, library, monkeypatch):
    """B2-13: флаги Э2 по ответам считаются автоматически; без API сохраняется промпт Э2."""
    from konveyer.steps import canon as canon_steps

    seen = _fake_models(monkeypatch, e2_json=json.dumps([
        {"flag_id": "F-001", "type": "бриф", "kind": "samovolka", "quote": "Ветер гнал", "rule": "вне брифа"}]))
    dest = canon_steps.retest(chapter=1)
    assert dest.name.endswith("_гл1") and (dest / "пакет.json").exists()
    assert any(r.startswith("верификатор-2") for _, r, _ in seen)
    flags = json.loads((dest / "флаги_claude-sonnet-4-5.json").read_text(encoding="utf-8"))
    assert flags[0]["type"] == "бриф"
    summary = (dest / "СВОДКА.md").read_text(encoding="utf-8")
    assert "флаги Э2" in summary and "1 (самоволок 1)" in summary
    # ответ ручного прогона положен — при повторе для него тоже считается Э2 (тут API «ручной» → промпт)
    (dest / "ответ_gemini-3.1-pro.md").write_text("Короткая фраза. Ещё одна.\n", encoding="utf-8")
    monkeypatch.setattr("konveyer.adapters.call_model", lambda *a, **k: (_ for _ in ()).throw(
        __import__("konveyer.adapters", fromlist=["ManualModeNeeded"]).ManualModeNeeded("нет ключа", "вручную")))
    dest2 = canon_steps.retest(chapter=1)
    assert dest2 == dest and (dest / "э2_промпт_gemini-3.1-pro.md").exists()
    assert "вручную (э2_промпт)" in (dest / "СВОДКА.md").read_text(encoding="utf-8")


def test_перетест_папка_по_главе_и_дедупликация_по_пину(ws, library, monkeypatch):
    """B2-21: пакеты разных глав одного дня не смешиваются; одна модель с разными параметрами — два ответа."""
    from konveyer.steps import canon as canon_steps

    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nwriter: {provider: anthropic, model: m, params: {t: 1}}\n"
        "verifier2: {provider: anthropic, model: m, params: {t: 2}}\ncanonist: {provider: anthropic, model: m, params: {t: 2}}\n",
        encoding="utf-8",
    )
    seen = _fake_models(monkeypatch)
    d1 = canon_steps.retest(chapter=1)
    d2 = canon_steps.retest(chapter=2)
    assert d1 != d2 and d1.name.endswith("_гл1") and d2.name.endswith("_гл2")
    assert sorted(p.name for p in d1.glob("ответ_*.md")) == ["ответ_m.md", "ответ_m_верификатор2.md"]
    writer_calls = [s for s in seen if s[1].startswith("пере-тест")]
    assert {s[2] for s in writer_calls} == {1, 2} and len([s for s in writer_calls if s[1] == "пере-тест (писатель)"]) == 2


def test_перетест_фиксация_требует_пакет_и_пишет_приватность(ws, library, monkeypatch):
    """B2-22 / A5-22: фиксация без пакета с ответом Писателя — отказ; с пакетом — пины, приватность, ссылка на сводку."""
    from konveyer.steps import canon as canon_steps

    assert runner.invoke(app, ["регрессия"]).exit_code == 0
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1 and "нет пакета" in r.output, r.output
    _fake_models(monkeypatch)
    dest = canon_steps.retest(chapter=1)
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 1, r.output  # ответ есть только у anthropic-ролей, Писатель — gemini (ручной прогон не сделан)
    (dest / "ответ_gemini-3.1-pro.md").write_text("Ответ ручного прогона.\n", encoding="utf-8")
    r = runner.invoke(app, ["пере-тест", "--зафиксировать"])
    assert r.exit_code == 0, r.output
    pins = json.loads((ws.logs / "пины.json").read_text(encoding="utf-8"))
    assert pins["пакет"] == dest.name and pins["приватность"]["писатель"]["провайдер"] == "gemini"
    assert pins["приватность"]["писатель"]["режим_без_обучения"] is True
    entry = (dest / "журнал_запись.md").read_text(encoding="utf-8")
    assert "СВОДКА.md" in entry and "режим без обучения — да" in entry and "gemini/gemini-3.1-pro" in entry
    # без пакета — только явно, и это видно в черновике журнала
    import shutil

    shutil.rmtree(ws.root / "пере-тест")
    r = runner.invoke(app, ["пере-тест", "--зафиксировать", "--без-пакета"])
    assert r.exit_code == 0, r.output
    entry = next((ws.root / "пере-тест").rglob("журнал_запись.md")).read_text(encoding="utf-8")
    assert "без пакета сравнения" in entry


# ------------------------------------------------------------- крайние случаи учёта и журналов


def test_история_с_временем_без_зоны_не_роняет_учёт():
    """Запись истории без зоны (правка руками, старый формат) считается UTC, а не роняет `intervals` TypeError."""
    from konveyer import timing

    hist = [{"время": "2026-05-01T10:00:00", "из": "а", "в": "б"},
            {"время": "2026-05-01T10:05:00+00:00", "из": "б", "в": "в"}]
    assert [(k, round(s)) for k, s, _ in timing.intervals(hist)] == [("авторское", 300)]
    assert timing.chapter_times(hist) == (0.0, 300.0)


def test_обрывок_строки_журнала_api_пропускается(ws):
    """Прерванная запись в api.jsonl не роняет учёт, сводку тома и дашборд (П-5)."""
    from konveyer import accounting, apilog, dashboard

    _log(ws, role="писатель", cost=0.1, chapter=1)
    with (ws.logs / "api.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"role": "пис')
    assert len(apilog.read_log(ws.logs)) == 1
    assert accounting.volume_account(ws, 1, chapters_total=0).cost == pytest.approx(0.1)
    (ws.logs / "метрики.jsonl").write_text('{"chapter": 1, "V1.2a_средняя_длина": 10}\n{"chapter": 2, "V1.2a_ср', encoding="utf-8")
    assert "Токены по ролям" in dashboard.render_dashboard(ws)


def test_коммит_приёмки_узнаётся_по_трейлеру(ws, library):
    """A5-27: ручной канон-коммит с темой «[глава N] …» приёмкой не считается; коммит приёмки несёт трейлер."""
    _init_repo(library)
    style = library / "02_Стиль_и_голос.md"
    style.write_text(style.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    r = runner.invoke(app, ["canon-commit", "-m", "[глава 1] поправил опечатку", "-y"])
    assert r.exit_code == 0, r.output
    assert gitops.find_chapter_commit(library, 1) is None
    _fixed_chapter(ws, library, 1)
    sha = ChapterState(ws, 1).data["коммит_приёмки"]
    assert gitops.find_chapter_commit(library, 1) == sha
    assert "Конвейер-приёмка: глава 1" in _git(library, "log", "-1", "--format=%B", sha)
    r = runner.invoke(app, ["rollback", "1", "-y"])
    assert r.exit_code == 0, r.output
    assert gitops.find_chapter_commit(library, 1) is None  # откат новее приёмки


def test_бюджет_модельного_слоя_линтера(ws, library, monkeypatch):
    """C3-28: `линтер --llm --бюджет` и `бюджет_линтера` конфига доходят до run_lint_llm как max_cost_usd."""
    from konveyer import lint as lint_mod

    seen = {}

    def fake_llm(ws_, cfg, lib, files=None, max_calls=None, max_cost_usd=None):
        seen["budget"] = max_cost_usd
        return [], []

    monkeypatch.setattr(lint_mod, "run_lint_llm", fake_llm)
    monkeypatch.setattr(lint_mod, "resolve_library_files", lambda lib, files: [library / "02_Стиль_и_голос.md"])
    r = runner.invoke(app, ["lint", "--llm", "--бюджет", "0.5", "--no-strict"])
    assert r.exit_code == 0 and seen["budget"] == 0.5 and "бюджет 0.50 $" in r.output, r.output
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nбюджет_линтера: 1.25\n", encoding="utf-8")
    r = runner.invoke(app, ["lint", "--llm", "--no-strict"])
    assert r.exit_code == 0 and seen["budget"] == 1.25, r.output


# ------------------------------------------------------------- адаптеры и роли (тесты)


def test_роли_аналитик_линтер_архивариус_независимы(ws, monkeypatch):
    """D1-33: русские ключи `модели: {аналитик…}` дают ролям свои модели; не заданные наследуют Канониста;
    вызов роли уходит именно в её модель."""
    from konveyer import adapters
    from konveyer.config import Config, load_config

    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nмодели:\n  канонист: {provider: anthropic, model: к}\n"
        "  аналитик: {provider: anthropic, model: а, режим_без_обучения: нет}\n  линтер: {provider: anthropic, model: л}\n",
        encoding="utf-8",
    )
    cfg = load_config(ws)
    assert cfg.role("аналитик").model == "а" and cfg.role("линтер").model == "л" and cfg.role("архивариус").model == "к"
    assert cfg.role("analyst").model == "а" and cfg.analyst.no_training is False
    assert Config().role("аналитик") is Config().canonist or Config().role("аналитик").model == Config().canonist.model
    seen = []
    monkeypatch.setattr(adapters, "call_model", lambda mc, api, s, u, logs, *, role, chapter=None, role_key=None: seen.append((role, mc.model)) or "ok")
    for role in ("аналитик", "линтер", "архивариус"):
        adapters.call_role(cfg, role, "s", "u", ws.logs)
    assert seen == [("аналитик", "а"), ("линтер", "л"), ("архивариус", "к")]
    r = runner.invoke(app, ["доктор"])
    assert "роли с обучением — аналитик" in r.output


def test_адаптер_таймаут_доходит_до_sdk_и_таймаут_повторяется(ws, monkeypatch):
    """B4-18 / D1-32: timeout_s передаётся клиентам SDK; TimeoutError — «сеть» → повтор → ручной режим."""
    import sys
    import types

    from konveyer import adapters
    from konveyer.config import ApiConfig, ModelConfig

    captured: dict = {}

    class Anthropic:
        def __init__(self, **kwargs):
            captured["anthropic"] = kwargs
            self.messages = types.SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(TimeoutError("timed out")))

    mod = types.ModuleType("anthropic")
    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ключ")
    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)
    api = ApiConfig(retries=2, backoff_base_s=0.0, timeout_s=7)
    assert adapters.classify_error(TimeoutError("timed out")) == "сеть" and adapters._retryable(TimeoutError("x"))
    with pytest.raises(adapters.ManualModeNeeded):
        adapters.call_anthropic("s", "u", ModelConfig(provider="anthropic", model="m"), api, ws.logs, role="писатель", chapter=1)
    assert captured["anthropic"]["timeout"] == 7.0 and captured["anthropic"]["max_retries"] == 0
    log = [r for r in __import__("konveyer.apilog", fromlist=["read_log"]).read_log(ws.logs) if r.get("error")]
    assert len(log) == 2  # два повтора — оба с ошибкой таймаута

    class HttpOptions:
        def __init__(self, **kwargs):
            captured["http"] = kwargs

    class Client:
        def __init__(self, **kwargs):
            self.models = types.SimpleNamespace(generate_content=lambda **kw: types.SimpleNamespace(text="проза", usage_metadata=None))

    fake_types = types.ModuleType("google.genai.types")
    fake_types.HttpOptions = HttpOptions
    fake_types.HttpRetryOptions = lambda **kw: kw
    fake_types.GenerateContentConfig = lambda **kw: None
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client, fake_genai.types = Client, fake_types
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    for name, m in (("google", fake_google), ("google.genai", fake_genai), ("google.genai.types", fake_types)):
        monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.setenv("GEMINI_API_KEY", "ключ")
    assert adapters.call_gemini("окно", ModelConfig(provider="gemini", model="g"), api, ws.logs) == "проза"
    assert captured["http"]["timeout"] == 7 * 1000


def test_доктор_отличает_не_найдена_от_не_проверено_без_сети(ws, monkeypatch):
    """D1-32 / FR-RT-3: ключ есть, сети нет → «не проверено», а не «не найдена»."""
    import sys
    import types

    from tests.test_stage10_safety import _fake_genai, _with_spec
    from konveyer import adapters
    from konveyer.config import ModelConfig

    class Anthropic:
        def __init__(self, **kwargs):
            self.models = types.SimpleNamespace(retrieve=lambda model_id: (_ for _ in ()).throw(ConnectionError("нет сети")))

    mod = _with_spec(types.ModuleType("anthropic"))
    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    _fake_genai(monkeypatch, set())
    ok, note = adapters.probe_model(ModelConfig(provider="anthropic", model="claude-sonnet-4-5"))
    assert ok is None and note.startswith("не проверено: ConnectionError")
    r = runner.invoke(app, ["доктор"])
    assert "~ модель claude-sonnet-4-5" in r.output and "не проверено: ConnectionError" in r.output
    assert "✗ модель gemini-3.1-pro (Писатель): модель «gemini-3.1-pro» не найдена" in r.output


def test_демо_корпус_э2_по_каждому_чек_листу(ws):
    """A4-29 (§7.6): в демо есть золотой тест Э2 на каждый включённый чек-лист и «зелёный» контроль фактуры;
    промпт теста несёт срез канона и «НЕ знает» фокала, так что тест самодостаточен."""
    from konveyer import catalog, regression
    from konveyer.config import Config

    tests = {t.test_id: t for t in regression.load_tests(ws) if t.echelon == "Э2"}
    types = {"фокализация", "эпистемика", "информрежим", "закладка", "континуити", "анахронизм", "самоволка", "бриф"}
    assert types <= {f for t in tests.values() for f in t.expected_flags}
    assert tests["зелёный_э2_фактура"].expected_flags == []
    mods = catalog.load_modules(ws.root)
    checks = {c for m in mods.values() for c in m.e2_checks}
    assert {"фокализация", "эпистемика", "информрежим", "закладки", "континуити", "анахронизмы", "самоволки", "бриф"} <= checks
    system, user = regression.e2_prompt(ws, Config(), tests["красный_э2_эпистемика"])
    assert "Фокал НЕ знает: что сторож жив" in user and "<текст_главы>" in user
    _, user = regression.e2_prompt(ws, Config(), tests["красный_э2_континуити"])
    assert "## СРЕЗ КАНОНА" in user and "шрам на левой брови" in user
