"""Смоук-тесты CLI (FR-O2: каждый шаг — отдельная команда; NFR-2: интерфейс русский)."""

from typer.testing import CliRunner

from konveyer import dashboard
from konveyer.cli import app

runner = CliRunner()


def test_export_compile_status(ws, monkeypatch):
    monkeypatch.chdir(ws.root)
    assert runner.invoke(app, ["export"]).exit_code == 0
    r = runner.invoke(app, ["compile", "1"])
    assert r.exit_code == 0 and "Окно собрано" in r.output
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "собрано" in r.output


def test_ошибка_структуры_читаемая(ws, library, monkeypatch):
    monkeypatch.chdir(ws.root)
    path = library / "31_Матрица_знаний.md"
    path.write_text(path.read_text(encoding="utf-8") + "| x |\n", encoding="utf-8")
    r = runner.invoke(app, ["export"])
    assert r.exit_code == 1
    out = r.output + (r.stderr or "")
    assert "31_Матрица_знаний.md:14:" in out  # файл и строка (FR-EX-3)
    assert "не разобран" in out


def _out(r) -> str:
    """stdout и stderr вызова (CliRunner разных версий click делит их по-разному)."""
    try:
        return r.output + (r.stderr or "")
    except ValueError:
        return r.output


def test_verify1_требует_состояния(ws, monkeypatch):
    """Глава ещё не сгенерирована: ошибка шага — код 1, «ОШИБКА: …», без трейсбека (FR-CL-3)."""
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["verify1", "1"])
    out = _out(r)
    assert r.exit_code == 1 and out.lstrip().startswith("ОШИБКА:"), out
    assert "Traceback" not in out and (r.exception is None or isinstance(r.exception, SystemExit))


ENGLISH_HELP_PHRASES = (
    "Usage:", "Options", "Arguments", "Commands", "Show this message", "Install completion", "[required]",
    "[default:", "[OPTIONS]", "COMMAND [ARGS]",
)


def _all_help_invocations() -> list[list[str]]:
    calls: list[list[str]] = [["--help"], ["--справка"], ["-h"]]
    for info in app.registered_commands:
        calls.append([info.name, "--справка"])
    for group in app.registered_groups:
        calls.append([group.name, "--справка"])
        for info in group.typer_instance.registered_commands:
            calls.append([group.name, info.name, "--справка"])
    return calls


def test_cli_справка_по_русски(monkeypatch):
    """Справка каждой команды без английских элементов typer/click (FR-CL-5, NFR-7): заголовки разделов,
    строка использования, опция справки; автодополнения оболочки нет."""
    monkeypatch.setenv("COLUMNS", "200")
    for args in _all_help_invocations():
        r = runner.invoke(app, args)
        out = _out(r)
        assert r.exit_code == 0, (args, out)
        assert "Использование:" in out and "справка" in out, (args, out)
        for phrase in ENGLISH_HELP_PHRASES:
            assert phrase not in out, (args, phrase, out)
    r = runner.invoke(app, ["--help"])
    assert "--install-completion" not in r.output and "--show-completion" not in r.output


def test_cli_ошибки_разбора_по_русски_код_1(ws, monkeypatch):
    """Опечатка в аргументах — не «ручной режим»: код 1 и «ОШИБКА: … . См. …» по-русски (FR-CL-3);
    вызов без аргументов — справка, код 0."""
    monkeypatch.chdir(ws.root)
    cases = {
        ("собрать", "abc"): "не целое число",
        ("несуществующая",): "нет команды",
        ("канон-коммит",): "не указана обязательная опция",
        ("собрать", "1", "2"): "лишние аргументы",
        ("написать", "1", "--варианты", "9"): "вне диапазона",
        ("написать", "1", "--нет-такой"): "нет опции",
        ("том", "открыть"): "не указан обязательный аргумент",
    }
    for args, expected in cases.items():
        r = runner.invoke(app, list(args))
        out = _out(r)
        assert r.exit_code == 1, (args, r.exit_code, out)
        line = next(ln for ln in out.splitlines() if ln.startswith("ОШИБКА:"))
        assert expected in line and "--справка" in line, (args, line)
        assert "Traceback" not in out and "Invalid value" not in out and "No such" not in out and "Missing" not in out, out
    for args in ([], ["том"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 0 and "Использование:" in r.output, (args, _out(r))


def test_дашборд_строится(ws):
    path = dashboard.build_dashboard(ws)
    assert path.exists() and "КОНВЕЙЕР" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ русские имена опций, --да, справка без внутренних ссылок


def _click_commands() -> list[tuple[str, object]]:
    """(путь команды, click-команда) для всех команд и подкоманд, включая скрытые латинские синонимы."""
    import typer.main as typer_main

    root = typer_main.get_command(app)
    out: list[tuple[str, object]] = []
    for name, cmd in root.commands.items():
        subs = getattr(cmd, "commands", None)
        if subs:
            out.extend((f"{name} {sub}", c) for sub, c in subs.items())
        else:
            out.append((name, cmd))
    return out


def test_cli_опции_по_русски():
    """У каждой опции есть русское длинное имя (FR-CL-5) и описание; у аргументов — русский заполнитель.
    `--объём` команды «проверка» не путается с `--том`: латинский синоним — `--words`, не `--volume`."""
    for path, cmd in _click_commands():
        for p in cmd.params:
            names = list(p.opts) + list(p.secondary_opts)
            if p.param_type_name == "option":
                assert any(n.startswith("--") and not n.isascii() for n in names), (path, names)
                assert (p.help or "").strip(), (path, names)
            else:
                assert p.metavar and not p.metavar.isascii(), (path, p.name, p.metavar)
    check = dict(_click_commands())["проверка"]
    words = next(p for p in check.params if "--объём" in p.opts)
    assert "--words" in words.opts and "--volume" not in words.opts
    assert any("--волюм" not in p.opts and "--том" in p.opts for p in dict(_click_commands())["статус"].params)


def test_cli_флаг_да_у_каждой_команды_с_подтверждением():
    """Каждая опасная команда (с подтверждением автора) принимает `--да` и синонимы `--yes`/`-y` (FR-CL-4, FR-CL-5)."""
    import inspect

    infos = list(app.registered_commands) + [i for g in app.registered_groups for i in g.typer_instance.registered_commands]
    with_confirm = {info.name for info in infos if "confirm=" in inspect.getsource(inspect.unwrap(info.callback))}
    assert {"принять", "канон", "каркас", "откат", "канон-коммит", "библиотека-отделить", "бэкап", "закрыть", "нормы", "онбординг"} <= with_confirm
    for path, cmd in _click_commands():
        name = path.split()[-1]
        if name not in with_confirm:
            continue
        yes = next((p for p in cmd.params if p.name == "yes"), None)
        assert yes is not None, path
        assert {"--да", "--yes", "-y"} <= set(yes.opts), (path, yes.opts)
        assert (yes.help or "").strip(), path


INTERNAL_HELP_TOKENS = (
    "аудит", "этап 3", "п. 27", "п. 28", "Р-0", "Д-8", "FR-K", "FR-E1", "FR-E3", "FR-E4", "FR-V1.", "FR-V2.", "FR-W1",
    "FR-X1", "FR-C1", "FR-D1", "FR-D2", "FR-R1", "FR-R2", "FR-R3", "FR-O1", "FR-O2", "8 шагов", "четыре акта", "документ 2.1",
    "3.5", "02 §6.1",
)


def test_cli_справка_без_внутренних_ссылок(monkeypatch):
    """Справка для автора: без номеров внутренних аудитов и этапов разработки, без устаревших идентификаторов
    требований и без констант эталонной серии (число шагов, актов, имя документа) — П-1, FR-CL-2."""
    monkeypatch.setenv("COLUMNS", "200")
    for args in _all_help_invocations():
        out = _out(runner.invoke(app, args))
        for tok in INTERNAL_HELP_TOKENS:
            assert tok not in out, (args, tok)


# ------------------------------------------------------------------ отбор, бюджет модельного слоя, решения онбординга, документация


def test_cli_отбор_это_пакет_пере_теста(ws, monkeypatch):
    """`konveyer отбор` (этап 8 жизненного цикла) собирает пакет сравнения моделей, как `пере-тест`."""
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = runner.invoke(app, ["отбор", "--глава", "1"])
    assert r.exit_code == 0, _out(r)
    packs = sorted((ws.root / "пере-тест").iterdir())
    assert packs and (packs[-1] / "ПРОМПТ_раунд1.md").exists()
    assert runner.invoke(app, ["select", "--справка"]).exit_code == 0


def test_cli_линтер_бюджет_модельного_слоя(ws, monkeypatch):
    """FR-LT-3: модельный слой ограничен лимитом вызовов и бюджетом — оценка по фактическому размеру документов
    выше бюджета даёт отказ до первого вызова; бюджет печатается рядом с оценкой."""
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (ws.root / "конфиг.yaml").write_text(
        "library_dir: Библиотека\nlinter: {provider: anthropic, model: м, price_in_per_1m: 1000.0, price_out_per_1m: 1000.0}\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["линтер", "--модель", "--бюджет", "0.001", "--не-строго"])
    out = _out(r)
    assert r.exit_code == 1 and "выше бюджета" in out and "--бюджет" in out, out
    assert not (ws.logs / "линтер_промпты").exists()  # отказ до первого вызова: промпты не готовились
    r = runner.invoke(app, ["линтер", "--модель", "--бюджет", "-1"])
    assert r.exit_code == 1 and "отрицательн" in _out(r)
    r = runner.invoke(app, ["линтер", "--модель", "--бюджет", "1000", "--не-строго"])
    out = _out(r)
    assert r.exit_code == 0 and "бюджет 1000.00 $" in out and "≈ $" in out, out
    assert (ws.logs / "линтер_промпты").exists()  # без ключа — ручной режим: промпты сохранены


def test_cli_онбординг_решение_без_равно(ws, monkeypatch):
    """`--решение` без «=» и для неизвестного файла — понятная ошибка по-русски, а не текст исключения Python."""
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["онбординг", "--применить", "--решение", "заметки.md", "-y"])
    out = _out(r)
    assert r.exit_code == 1 and "ОШИБКА: решение задаётся как файл=" in out and "unpack" not in out, out
    r = runner.invoke(app, ["онбординг", "--применить", "--решение", "x=принять", "-y"])
    out = _out(r)
    assert r.exit_code == 1 and "ОШИБКА: файла «x» нет в предложении" in out, out


def test_документация_совпадает_с_каталогом(tmp_path, monkeypatch):
    """NFR-10: docs/ сгенерированы из каталога типов и реестра метрик — изменение YAML без перегенерации не пройдёт."""
    from pathlib import Path

    from konveyer import catalog, metrics

    repo = Path(__file__).resolve().parent.parent
    types, modules = catalog.load_types(None), catalog.load_modules(None)
    assert catalog.documentation(types, modules) == (repo / "docs" / "Соглашения_типов.md").read_text(encoding="utf-8")
    assert metrics.documentation() == (repo / "docs" / "Реестр_метрик.md").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["типы", "--документация", "--куда", str(tmp_path / "д")])
    assert r.exit_code == 0 and (tmp_path / "д" / "Соглашения_типов.md").exists() and (tmp_path / "д" / "Реестр_метрик.md").exists()
    assert (tmp_path / "д" / "Реестр_метрик.md").read_text(encoding="utf-8") == metrics.documentation()


def test_cli_импорт_прозы_сценарий_в(ws, library, monkeypatch):
    """Сценарий В (§4.2): готовая проза → документы типа «проза» (дословно, через одну сессию записи в канон),
    корпус пересобран, коридоры норм предложены, словарь имён и континуити предзаполнены черновиками,
    спорные факты — списком с пометкой «⚠ решение автора»; существующая глава не перезаписывается."""
    monkeypatch.chdir(ws.root)
    src = ws.root / "черновики"
    src.mkdir()
    text = ("Пронин пришёл к киоску раньше первого поезда. Зоя открыла окно. Виктор Семёнович Кузнецов стоял "
            "у кассы и молчал. Потом Кузнецов ушёл.\n\nВторой абзац главы с Прониным и Зоей.\n")
    (src / "Глава 9.md").write_text(text, encoding="utf-8")
    (src / "Глава 3.md").write_text("Другой текст.\n", encoding="utf-8")
    old3 = (library / "Проза" / "Том1_Глава03.md").read_text(encoding="utf-8")
    r = runner.invoke(app, ["импорт-прозы", str(src / "Глава 9.md"), str(src / "Глава 3.md"), "--да", "--без-коммита"])
    out = _out(r)
    assert r.exit_code == 0, out
    assert (library / "Проза" / "Том1_Глава09.md").read_text(encoding="utf-8") == text  # дословно (FR-ON-15)
    assert (library / "Проза" / "Том1_Глава03.md").read_text(encoding="utf-8") == old3 and "не перезаписан" in out
    assert (ws.corpus / "Том1_Глава09.txt").exists()  # корпус пересобран экспортом
    names = (ws.root / "онбординг" / "проза_имена.md").read_text(encoding="utf-8")
    assert "| Зоя | 9 | да |" in names and "| Пронин | 9 | да |" in names
    assert "| Виктор Семёнович | 9 | нет | ⚠ решение автора" in names and "Потом Кузнецов" not in names
    disputed = (ws.root / "онбординг" / "проза_спорное.md").read_text(encoding="utf-8")
    assert "«Виктор Семёнович»" in disputed and "⚠ решение автора" in disputed
    cont = (ws.root / "онбординг" / "проза_континуити.md").read_text(encoding="utf-8")
    assert "| дата | событие | главы | примечание |" in cont and "т.1 гл.9" in cont and "Виктор Семёнович Кузнецов стоял" in cont
    assert (ws.logs / "калибровка.md").exists() and "Утвердить коридоры" in out
    # повтор: вносить нечего — ошибка, а не перезапись
    r = runner.invoke(app, ["импорт-прозы", str(src / "Глава 9.md"), "--да", "--без-коммита"])
    assert r.exit_code == 1 and "вносить нечего" in _out(r)
    # нумерация по порядку и отказ автора (код 0, ничего не записано)
    (src / "текст.md").write_text("Глава без номера.\n", encoding="utf-8")
    r = runner.invoke(app, ["импорт-прозы", str(src / "текст.md")])
    assert r.exit_code == 1 and "номер главы не распознан" in _out(r)
    r = runner.invoke(app, ["импорт-прозы", str(src / "текст.md"), "--с-главы", "11", "--без-калибровки"], input="n\n")
    assert r.exit_code == 0 and not (library / "Проза" / "Том1_Глава11.md").exists()
    r = runner.invoke(app, ["импорт-прозы", str(src / "текст.md"), "--с-главы", "11", "--без-калибровки", "-y", "--без-коммита"])
    assert r.exit_code == 0 and (library / "Проза" / "Том1_Глава11.md").exists(), _out(r)
