"""Этап 5 второго аудита (п. 28, 29, 31): сохранность, пины, гигиена релизов.

Раскладки библиотеки в `doctor`, переезд `library-split`, архив рабочей области, второй remote как
bare-папка, теги приёмки `глава-N`/`глава-N-2`, `--version`, сверка пинов с API на фейковых клиентах."""

import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import adapters, backup, gitops
from konveyer.cli import app
from konveyer.config import Config, ModelConfig
from konveyer.fsm import ChapterState

from tests.общие import _accepted_chapter, _git, _init_repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch, ws):
    monkeypatch.chdir(ws.root)


def _with_spec(mod: types.ModuleType) -> types.ModuleType:
    """importlib.util.find_spec (doctor) требует __spec__ у модулей из sys.modules."""
    import importlib.machinery

    mod.__spec__ = importlib.machinery.ModuleSpec(mod.__name__, None)
    return mod


# ------------------------------------------------------------- п. 28: три раскладки в doctor


def test_doctor_библиотека_не_под_git(ws):
    assert backup.layout(ws.root / "Библиотека", ws.root).kind == "no-git"
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "библиотека под git" in r.output and "корень собственного" not in r.output


def test_doctor_библиотека_собственный_репозиторий(ws, library):
    _init_repo(library)
    lay = backup.layout(library, ws.root)
    assert lay.kind == "own" and lay.ok is True
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "библиотека — корень собственного репозитория" in r.output


def test_doctor_библиотека_внутри_репозитория_кода(ws, library):
    # рабочая область = репозиторий кода конвейера (pyproject + konveyer/), библиотека — его подпапка
    (ws.root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (ws.root / "konveyer").mkdir()
    _init_repo(ws.root)
    lay = backup.layout(library, ws.root)
    assert lay.kind == "shared" and lay.code_repo and lay.prefix == "Библиотека/" and lay.ok is None
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "библиотека внутри репозитория кода" in r.output
    assert "git pull" in r.output and "library-split" in r.output


def test_doctor_библиотека_внутри_чужого_репозитория(ws, library):
    _init_repo(ws.root)  # репозиторий рабочей области без кода конвейера
    lay = backup.layout(library, ws.root)
    assert lay.kind == "shared" and not lay.code_repo
    assert "чужого репозитория" in lay.label


# ------------------------------------------------------------- п. 28: library-split


def test_library_split_показать_ничего_не_меняет(ws, library):
    _init_repo(ws.root)
    before = sorted(p.name for p in library.iterdir())
    r = runner.invoke(app, ["library-split", "--показать"])
    assert r.exit_code == 0, r.output
    assert "План переезда" in r.output and "library_dir" in r.output and ".gitignore" in r.output
    assert "Ничего не изменено" in r.output
    assert sorted(p.name for p in library.iterdir()) == before
    assert not (ws.root.parent / "Библиотека").exists()
    assert (ws.root / "конфиг.yaml").read_text(encoding="utf-8") == "library_dir: Библиотека\n"


def test_library_split_переносит_и_настраивает(ws, library, tmp_path):
    _init_repo(ws.root)
    _accepted_chapter(ws, library, 1)  # глава с пакетом; статус без коммита приёмки
    target = tmp_path / "куда" / "Библиотека"
    r = runner.invoke(app, ["library-split", "--в", str(target), "-y"])
    assert r.exit_code == 0, r.output
    assert not library.exists() and (target / "02_Стиль_и_голос.md").exists()
    # новый репозиторий с первым коммитом и чистым деревом
    assert gitops.is_repo(target) and gitops.prefix(target) == "" and not gitops.dirty(target)
    assert "отдельный репозиторий" in _git(target, "log", "-1", "--format=%s")
    # конфиг.yaml указывает на новое место, конвейер его находит
    cfg_text = (ws.root / "конфиг.yaml").read_text(encoding="utf-8")
    assert 'library_dir: "куда/Библиотека"' in cfg_text  # относительно рабочей области
    from konveyer.config import library_dir, load_config
    from konveyer.paths import Workspace

    assert library_dir(Workspace(ws.root), load_config(Workspace(ws.root))).resolve() == target.resolve()
    # прежний репозиторий: запись в .gitignore, папка удалена
    assert "/Библиотека/" in (ws.root / ".gitignore").read_text(encoding="utf-8")
    assert "закоммитьте" in r.output and "удалённые копии" in r.output.lower()
    # конвейер работает из нового места
    assert runner.invoke(app, ["export"]).exit_code == 0
    assert runner.invoke(app, ["doctor"]).exit_code == 0


def test_library_split_отказы_до_действий(ws, library, tmp_path):
    _init_repo(ws.root)
    occupied = tmp_path / "занято"
    occupied.mkdir()
    r = runner.invoke(app, ["library-split", "--в", str(occupied), "--показать"])
    assert r.exit_code == 1 and "уже существует" in r.output
    r = runner.invoke(app, ["library-split", "--в", str(library / "внутрь"), "--показать"])
    assert r.exit_code == 1 and "внутри самой библиотеки" in r.output
    assert library.exists() and not (ws.root.parent / "Библиотека").exists()


def test_library_split_уже_отдельный(ws, library):
    _init_repo(library)
    r = runner.invoke(app, ["library-split", "--показать"])
    assert r.exit_code == 1 and "уже корень собственного" in r.output


def test_library_split_не_уезжает_с_незакоммиченными(ws, library):
    _init_repo(ws.root)
    (library / "02_Стиль_и_голос.md").write_text("правка\n", encoding="utf-8")
    r = runner.invoke(app, ["library-split", "-y"])
    assert r.exit_code == 1 and "незакоммиченные" in r.output
    assert library.exists()


def test_library_split_снимает_недействительные_sha_приёмки(ws, library, tmp_path):
    _init_repo(ws.root)
    _accepted_chapter(ws, library, 1)
    st = ChapterState(ws, 1)
    st.data["коммит_приёмки"] = "deadbeef" * 5
    st._save()
    target = tmp_path / "нов"
    r = runner.invoke(app, ["library-split", "--в", str(target), "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, 1)
    assert "коммит_приёмки" not in st.data and st.data["коммит_приёмки_до_переезда"].startswith("deadbeef")
    assert "остался в прежней истории" in r.output


@pytest.mark.skipif(not gitops.has_subtree(Path.cwd()), reason="git subtree недоступен")
def test_library_split_с_историей(ws, library, tmp_path):
    _init_repo(ws.root)
    _accepted_chapter(ws, library, 1)
    assert runner.invoke(app, ["canonize", "1", "--apply", "-y"]).exit_code == 0
    old_sha = ChapterState(ws, 1).data["коммит_приёмки"]
    target = tmp_path / "с_историей"
    r = runner.invoke(app, ["library-split", "--в", str(target), "--с-историей", "-y"])
    assert r.exit_code == 0, r.output
    log = _git(target, "log", "--format=%s")
    assert "[глава 1]" in log and "init" in log  # история папки перенесена
    new_sha = ChapterState(ws, 1).data["коммит_приёмки"]
    assert new_sha != old_sha and new_sha == gitops.find_chapter_commit(target, 1)
    assert not library.exists() and (target / "02_Стиль_и_голос.md").exists()


# ------------------------------------------------------------- п. 29: архив рабочей области


def test_backup_архив_создаёт_zip_и_ротирует(ws, library, tmp_path):
    _init_repo(library)
    (ws.root / "конфиг.yaml").write_text("library_dir: Библиотека\nbackup_keep: 2\n", encoding="utf-8")
    ws.chapter_dir(1).mkdir(parents=True)
    (ws.chapter_dir(1) / "черновик_1.md").write_text("текст", encoding="utf-8")
    (ws.chapter_dir(1) / "мусор.tmp").write_text("x", encoding="utf-8")
    dest = tmp_path / "бэкап"
    for _ in range(3):
        r = runner.invoke(app, ["backup", "--архив", str(dest)])
        assert r.exit_code == 0, r.output
        assert "Архив рабочей области" in r.output
    archives = backup.list_archives(dest)
    assert len(archives) == 2  # backup_keep = 2
    assert all(p.name.startswith("рабочая_область_") and p.suffix == ".zip" for p in archives)
    names = zipfile.ZipFile(archives[-1]).namelist()
    assert "главы/001/черновик_1.md" in names and "конфиг.yaml" in names and "проект.yaml" in names  # манифест — в архиве
    assert "Восстановление:" in r.output  # процедура восстановления называется в выводе бэкапа
    assert any(n.startswith("регрессия/золотые/") for n in names)
    assert not any(n.endswith(".tmp") for n in names)
    assert not list(dest.glob("*.tmp"))
    # backup без флагов — строка про архив
    r = runner.invoke(app, ["backup", str(dest)])
    assert r.exit_code == 0 and "Архив рабочей области: 0.0 дн. назад" in r.output


def test_backup_без_архивов_подсказывает(ws, library):
    _init_repo(library)
    r = runner.invoke(app, ["backup"])
    assert r.exit_code == 0, r.output
    assert "ещё не делался" in r.output and "--архив" in r.output


def test_архив_после_canonize_apply_если_задан_backup_dir(ws, library, tmp_path):
    _init_repo(library)
    dest = tmp_path / "авто"
    (ws.root / "конфиг.yaml").write_text(f"library_dir: Библиотека\nbackup_dir: '{dest.as_posix()}'\n", encoding="utf-8")
    _accepted_chapter(ws, library, 1)
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    assert "Архив рабочей области" in r.output and len(backup.list_archives(dest)) == 1
    r = runner.invoke(app, ["doctor"])
    assert "архив рабочей области: 0.0 дн. назад" in r.output


def test_doctor_архив_не_настроен(ws):
    r = runner.invoke(app, ["doctor"])
    assert "архив рабочей области: ещё не делался" in r.output and "backup_dir" in r.output


# ------------------------------------------------------------- п. 28в: второй remote как bare-папка


def test_backup_добавить_remote_папка(ws, library, tmp_path):
    _init_repo(library)
    bare = tmp_path / "внешний_диск" / "Библиотека.git"
    r = runner.invoke(app, ["backup", "--добавить-remote", "диск", str(bare)])
    assert r.exit_code == 0, r.output
    assert "Создан bare-репозиторий" in r.output and gitops.is_bare_repo(bare)
    assert gitops.remotes(library) == ["диск"] and gitops.remote_url(library, "диск") == str(bare)
    # отправка работает
    r = runner.invoke(app, ["backup", "--push", "-y"])
    assert r.exit_code == 0, r.output
    assert "✓ диск" in r.output
    assert _git(bare, "log", "-1", "--format=%s") == "init"
    # повтор с тем же именем — отказ; существующая не-bare папка — отказ
    assert runner.invoke(app, ["backup", "--добавить-remote", "диск", str(bare)]).exit_code == 1
    plain = tmp_path / "просто_папка"
    plain.mkdir()
    r = runner.invoke(app, ["backup", "--добавить-remote", "ещё", str(plain)])
    assert r.exit_code == 1 and "не bare-репозиторий" in r.output


def test_backup_добавить_remote_url_не_создаёт_папку(ws, library, tmp_path):
    _init_repo(library)
    r = runner.invoke(app, ["backup", "--добавить-remote", "github", "https://example.invalid/автор/библиотека.git"])
    assert r.exit_code == 0, r.output
    assert gitops.remote_url(library, "github") == "https://example.invalid/автор/библиотека.git"
    assert not (ws.root / "https:").exists()


# ------------------------------------------------------------- п. 28г: теги приёмки


def test_тег_главы_после_приёмки_и_после_отката(ws, library):
    _init_repo(library)
    _accepted_chapter(ws, library, 1)
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    assert "Тег канона: глава-1" in r.output
    sha1 = ChapterState(ws, 1).data["коммит_приёмки"]
    assert gitops.tags(library) == ["глава-1"]
    assert _git(library, "rev-list", "-n", "1", "глава-1") == sha1
    # откат тег не трогает
    assert runner.invoke(app, ["rollback", "1", "-y"]).exit_code == 0
    assert gitops.tags(library) == ["глава-1"]
    # повторная приёмка — глава-1-2
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    assert "Тег канона: глава-1-2" in r.output
    sha2 = ChapterState(ws, 1).data["коммит_приёмки"]
    assert sha2 != sha1 and _git(library, "rev-list", "-n", "1", "глава-1-2") == sha2
    assert sorted(gitops.tags(library)) == ["глава-1", "глава-1-2"]


def test_тег_идемпотентен_и_не_ломает_приёмку(ws, library, monkeypatch):
    _init_repo(library)
    _accepted_chapter(ws, library, 1)
    assert runner.invoke(app, ["canonize", "1", "--apply", "-y"]).exit_code == 0
    sha = ChapterState(ws, 1).data["коммит_приёмки"]
    assert gitops.tag_chapter(library, 1, sha) == "глава-1"  # повтор на тот же SHA — тот же тег, без -2
    assert gitops.tags(library) == ["глава-1"]
    # git tag сорвался — приёмка всё равно проходит (восстановление по коммиту с предупреждением)
    st = ChapterState(ws, 1)
    st.data["состояние"] = "принято"
    st._save()
    monkeypatch.setattr(gitops, "tag", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет прав")))
    _git(library, "tag", "-d", "глава-1")
    r = runner.invoke(app, ["canonize", "1", "--apply", "-y"])
    assert r.exit_code == 0, r.output
    assert "Тег главы не поставлен" in r.output and ChapterState(ws, 1).state == "зафиксировано"


# ------------------------------------------------------------- п. 31: --version и сверка пинов


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and r.output.startswith("konveyer ") and r.output.strip().count(".") >= 2
    import os

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent), "PYTHONUTF8": "1"}
    out = subprocess.run([sys.executable, "-m", "konveyer", "-V"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert out.returncode == 0 and out.stdout.startswith("konveyer ")


def _fake_anthropic(monkeypatch, known: set[str]):
    class NotFoundError(Exception):
        status_code = 404

    class _Models:
        def retrieve(self, model_id):
            if model_id not in known:
                raise NotFoundError("not found")
            return types.SimpleNamespace(id=model_id, display_name=f"Имя {model_id}")

    class Anthropic:
        def __init__(self, **kwargs):
            assert kwargs.get("max_retries") == 0 and kwargs["timeout"] <= adapters.PROBE_TIMEOUT_S
            self.models = _Models()

        def messages(self):  # генераций быть не должно
            raise AssertionError("doctor не должен генерировать")

    mod = _with_spec(types.ModuleType("anthropic"))
    mod.Anthropic, mod.NotFoundError = Anthropic, NotFoundError
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def _fake_genai(monkeypatch, known: set[str]):
    class APIError(Exception):
        def __init__(self, code):
            self.code = code

    class _Models:
        def get(self, *, model):
            if model not in known:
                raise APIError(404)
            return types.SimpleNamespace(name=f"models/{model}", display_name=f"Имя {model}")

        def generate_content(self, **kw):
            raise AssertionError("doctor не должен генерировать")

    class Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    types_mod = _with_spec(types.ModuleType("google.genai.types"))
    types_mod.HttpOptions = lambda **kw: kw
    genai = _with_spec(types.ModuleType("google.genai"))
    genai.Client, genai.types = Client, types_mod
    google = _with_spec(types.ModuleType("google"))
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)


def test_probe_model_без_ключей_и_sdk(monkeypatch):
    ok, note = adapters.probe_model(ModelConfig(provider="anthropic", model="claude-x"))
    assert ok is None and "ANTHROPIC_API_KEY" in note
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # SDK не импортируется
    ok, note = adapters.probe_model(ModelConfig(provider="anthropic", model="claude-x"))
    assert ok is None and "SDK" in note
    ok, note = adapters.probe_model(ModelConfig(provider="другой", model="m"))
    assert ok is None and "провайдер" in note


def test_probe_model_на_фейковых_клиентах(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    _fake_anthropic(monkeypatch, {"claude-sonnet-4-5"})
    _fake_genai(monkeypatch, {"gemini-3.1-pro"})
    assert adapters.probe_model(ModelConfig(provider="anthropic", model="claude-sonnet-4-5")) == (True, "Имя claude-sonnet-4-5")
    ok, note = adapters.probe_model(ModelConfig(provider="anthropic", model="claude-2"))
    assert ok is False and "не найдена" in note
    assert adapters.probe_model(ModelConfig(provider="gemini", model="gemini-3.1-pro"))[0] is True
    ok, note = adapters.probe_model(ModelConfig(provider="gemini", model="gemini-1.0"))
    assert ok is False and "не найдена" in note


def test_doctor_сверяет_пины(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    _fake_anthropic(monkeypatch, {"claude-sonnet-4-5"})
    _fake_genai(monkeypatch, set())  # Писатель снят из API
    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nwriter: {provider: gemini, model: gemini-3.1-pro}\n"
        "verifier2: {provider: anthropic, model: claude-sonnet-4-5}\ncanonist: {provider: anthropic, model: claude-sonnet-4-5}\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "✗ модель gemini-3.1-pro (Писатель): модель «gemini-3.1-pro» не найдена" in r.output
    assert "пере-тест" in r.output
    assert "✓ модель claude-sonnet-4-5 (Верификатор-2/Канонист): есть в API" in r.output
    assert r.output.count("модель claude-sonnet-4-5") == 1  # один пин — одна проверка


def test_doctor_без_ключей_пины_не_проверены(ws):
    r = runner.invoke(app, ["doctor"])
    assert "~ модель gemini-3.1-pro (Писатель): нет GEMINI_API_KEY" in r.output
    assert "~ модель claude-sonnet-4-5 (Верификатор-2/Канонист): нет ANTHROPIC_API_KEY" in r.output


def test_thinking_config_писателя_пробрасывается_как_есть():
    """конфиг.yaml корня репозитория задаёт thinking_config в форме google-genai; адаптер отдаёт params без правок."""
    import yaml

    root_cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "konveyer" / "data" / "конфиг.пример.yaml").read_text(encoding="utf-8"))
    cfg = Config.model_validate(root_cfg)
    assert cfg.writer.params["thinking_config"]["thinking_level"] in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}
    pytest.importorskip("google.genai")
    from google.genai import types as gtypes

    gcc = gtypes.GenerateContentConfig(**cfg.writer.params)
    assert gcc.thinking_config is not None and gcc.thinking_config.thinking_level == cfg.writer.params["thinking_config"]["thinking_level"]
