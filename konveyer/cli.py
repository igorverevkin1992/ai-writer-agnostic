"""CLI универсального конвейера книжной серии (интерфейсы из реестра модулей 4.2; язык — русский, NFR-2).

Каждый шаг такта исполним отдельной командой (FR-O2): отказ любого компонента
не блокирует такт — артефакты человекочитаемы, ручной режим всегда возможен (NFR-3).

Тонкая обёртка над ядром `konveyer/steps/*` (аудит 2, п. 30): здесь только регистрация команд typer
(имена, опции, панели справки), вызов функции ядра и перевод её исключений в сообщения и коды
возврата (`_friendly`). Логика шагов, тексты сообщений и подтверждения — в ядре; typer в ядре нет.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import typer

from . import cancel, steps
from .steps import canon, edits as edits_mod, onboarding as onboarding_steps, overview, quality, setup, tact, volume as volume_steps
from .steps.common import NEXT_STEP as NEXT_STEP  # noqa: F401 — совместимость: `from konveyer.cli import NEXT_STEP`
from .steps.common import _chapter_flags_summary as _chapter_flags_summary  # noqa: F401
from .steps.common import _ctx, _ensure_dir as _ensure_dir, _is_git_url as _is_git_url  # noqa: F401
from .steps.common import _print_variants as _print_variants, _print_verdict as _print_verdict  # noqa: F401
from .steps.common import _sha256 as _sha256  # noqa: F401
from .steps.canon import _compile_window_to as _compile_window_to  # noqa: F401

app = typer.Typer(
    name="konveyer",
    help="КОНВЕЙЕР — производственный такт главы (ТЗ v1.0).",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def version_string() -> str:
    """Версия конвейера: из метаданных установленного пакета, иначе из konveyer/__init__.py."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("konveyer")
    except PackageNotFoundError:
        from . import __version__

        return __version__


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"konveyer {version_string()}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", "-V", help="Версия конвейера.", callback=_version_callback, is_eager=True),
) -> None:
    """КОНВЕЙЕР — производственный такт главы (ТЗ v1.0)."""


def _fail(message: str) -> None:
    typer.secho(f"ОШИБКА: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _manual(e: steps.ManualMode) -> None:
    typer.secho(f"⚠ {e.reason}", fg=typer.colors.YELLOW)
    typer.echo(f"Ручной режим (NFR-3): {e.hint}")
    raise typer.Exit(code=2)


def _opt(value, default):
    """Прямой вызов typer-команды из кода без аргумента оставляет typer.OptionInfo — он истинен;
    такие значения считаем неуказанными. Ядру (`konveyer/steps`) это не нужно: его функции вызываются
    как обычные; оставлено для совместимости."""
    return default if isinstance(value, typer.models.OptionInfo) else value


_NOT_A_JOB = {"cmd_panel"}


def _friendly(fn):
    """Единый обработчик ошибок команд: исключения ядра (`StepError` и семейство, `cancel.Cancelled`,
    `TransitionError`, `StatusFileError`, `MarkupError`, …) — читаемое сообщение и код возврата вместо
    трейсбека. KONVEYER_DEBUG=1 — полный трейсбек для программных ошибок (2.11).

    Внешняя команда — одна задача для учёта времени такта (`steps.job_context`): вложенные команды
    (`run` → `write` → …) наследуют задачу. Остановка автором (`cancel.Cancelled`) — сообщение без
    трейсбека, код выхода 2 (как ручной режим: глава на последнем завершённом шаге)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # сервер панели живёт часами — сам он не задача: задачи заводят команды, которые он вызывает
        job_name = None if fn.__name__ in _NOT_A_JOB else fn.__name__.removeprefix("cmd_")
        with steps.job_context(job_name) as outermost:
            try:
                return fn(*args, **kwargs)
            except (typer.Exit, typer.Abort):
                raise  # собственные коды выхода — не ошибка
            except steps.Rejected as e:  # автор не подтвердил (Д-8)
                raise typer.Abort() if e.abort else typer.Exit()
            except steps.StepExit as e:  # шаг сам всё напечатал и просит код возврата
                raise typer.Exit(code=e.code)
            except steps.ManualMode as e:
                if os.environ.get("KONVEYER_DEBUG") == "1":
                    raise
                _manual(e)
            except cancel.Cancelled as e:
                if not outermost:
                    raise  # до внешней команды: она печатает и завершает
                typer.secho(f"⏹ {e}", fg=typer.colors.YELLOW)
                raise typer.Exit(code=2)
            except steps.StepError as e:  # ожидаемая ошибка шага — всегда без трейсбека
                _fail(str(e))
            except steps.EXPECTED_ERRORS as e:
                if os.environ.get("KONVEYER_DEBUG") == "1":
                    raise
                _fail(str(e))

    return wrapper


# ------------------------------------------------------------------ этап 1


@app.command("export", rich_help_panel="Такт главы")
@_friendly
def cmd_export() -> None:
    """Перегенерировать все выгрузки из MD-библиотеки (FR-X1…FR-X3)."""
    tact.export()


@app.command("compile", rich_help_panel="Такт главы")
@_friendly
def cmd_compile(chapter: int) -> None:
    """Собрать окно контекста главы N (FR-C1…FR-C6). Экспорт выполняется автоматически (риск R-5)."""
    tact.compile(chapter)


@app.command("write", rich_help_panel="Такт главы")
@_friendly
def cmd_write(
    chapter: int,
    manual: bool = typer.Option(
        False, "--manual", help="Зарегистрировать черновик, сохранённый вручную как черновик_{k+1}.md (NFR-3)."
    ),
    variants: int = typer.Option(
        1, "--варианты", "--variants", min=1, max=4,
        help="A/B: столько вызовов Писателя по одному окну → черновик_k.md, черновик_k.alt1.md …; метрики Э1 рядом (варианты.json).",
    ),
    choose: str | None = typer.Option(
        None, "--выбрать", "--choose", help="Сделать вариант (alt1, alt0 — прежний основной) текущим черновик_k.md; состояние не меняется.",
    ),
) -> None:
    """Отправить окно Писателю, сохранить черновик_k.md (FR-W1). `--варианты 2` — A/B, `--выбрать alt1` — выбор варианта."""
    tact.write(chapter, manual=manual, variants=variants, choose=choose)


@app.command("verify1", rich_help_panel="Такт главы")
@_friendly
def cmd_verify1(chapter: int) -> None:
    """Формальные проверки Э1 (FR-V1.*). Брак метрик → авто-повтор генерации (≤2, §5.4)."""
    tact.verify1(chapter)


@app.command("verify2", rich_help_panel="Такт главы")
@_friendly
def cmd_verify2(
    chapter: int,
    manual: bool = typer.Option(False, "--manual", help="Принять флаги.json, заполненный вручную (NFR-3)."),
    taste: bool = typer.Option(False, "--вкус", "--taste", help="Дополнительно: советы по вкусу автора (02 §6.1) — не блокируют приёмку."),
    again: bool = typer.Option(
        False, "--повторно", "--после-правок", "--again",
        help="Повторный Э2 по текущему черновику после правок (из «правки»/«дифф-контроль»): совещательно — "
             "флаги_повторно.json и раздел в приёмка.md; FSM, флаги.json и решения не меняются.",
    ),
) -> None:
    """Смысловые проверки Э2 (FR-V2.*). `--повторно` — второй прогон после правок (advisory)."""
    tact.verify2(chapter, manual=manual, taste=taste, again=again)


@app.command("review", rich_help_panel="Такт главы")
@_friendly
def cmd_review(chapter: int) -> None:
    """Пакет приёмки автора: приёмка.md + правки.md + решения.json (FR-E1)."""
    tact.review(chapter)


@app.command("apply-edits", rich_help_panel="Такт главы")
@_friendly
def cmd_apply_edits(
    chapter: int,
    manual: bool = typer.Option(False, "--manual", help="Черновик с правками сохранён вручную как черновик_{k+1}.md."),
) -> None:
    """Внесение правок: дословные БЫЛО/СТАЛО — кодом (Р-023), свободные указания — Писателем (FR-W2, FR-E3)."""
    tact.apply_edits(chapter, manual=manual)


@app.command("diff-check", rich_help_panel="Такт главы")
@_friendly
def cmd_diff_check(
    chapter: int,
    author_fix: bool = typer.Option(
        False, "--авторская-правка", "--author-fix", help="Текущий черновик правил сам автор — расхождения не самоволия."
    ),
    fragments: list[str] | None = typer.Option(
        None, "--фрагмент", "--fragment",
        help="С --авторская-правка: снять только эти самоволия (номер в списке или подстрока текста); можно несколько раз.",
    ),
) -> None:
    """Дифф-контроль до/после правок (FR-V1.10, FR-E3)."""
    tact.diff_check(chapter, author_fix=author_fix, fragments=fragments)


@app.command("accept", rich_help_panel="Такт главы")
@_friendly
def cmd_accept(chapter: int, yes: bool = typer.Option(False, "--yes", "-y", help="Подтверждение без вопроса.")) -> None:
    """Приёмка главы автором (FR-E4): только из «дифф-контроль: чисто», с явным подтверждением."""
    tact.accept(chapter, yes=yes, confirm=typer.confirm)


@app.command("canonize", rich_help_panel="Такт главы")
@_friendly
def cmd_canonize(
    chapter: int,
    apply: bool = typer.Option(False, "--apply", help="Применить подписанный пакет (правки MD + export + git-коммит)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
    redo: bool = typer.Option(False, "--заново", "--redo", help="Пересобрать пакет, даже если автор его уже правил (правки пропадут)."),
) -> None:
    """Канонист: пакет записей в канон (FR-K1); применение — только после подписи (FR-K2)."""
    tact.canonize(chapter, apply=apply, yes=yes, redo=redo, confirm=typer.confirm)


# ------------------------------------------------------------- сервисные


@app.command("status", rich_help_panel="Обзор")
@_friendly
def cmd_status(
    chapter: int | None = typer.Argument(None, help="Номер главы — подробная карточка."),
    volume: int | None = typer.Option(None, "--том", "--volume", help="Том (по умолчанию — текущий из конфиг.yaml)."),
) -> None:
    """Состояния глав и следующий шаг (FR-D2); `konveyer status N` — карточка главы; `--том N` — главы тома N."""
    overview.status(chapter, volume=volume)


@app.command("resolve", rich_help_panel="Правки и решения")
@_friendly
def cmd_resolve(
    chapter: int,
    flag_id: str | None = typer.Argument(None, help="ID самоволки (например F-001)."),
    decision: str | None = typer.Argument(None, help="«вычеркнуть», «канонизировать» или «отклонить»."),
    registry: str | None = typer.Option(None, "--реестр", "--registry", help="Целевой реестр (имя типа: эпистемика, закладки, континуити…)."),
    reason: str = typer.Option("", "--причина", "--reason", help="Причина отклонения флага (обязательна для «отклонить»)."),
) -> None:
    """Решения по флагам без ручной правки JSON (FR-RV-2): самоволку — вычеркнуть или канонизировать, любой флаг — отклонить с причиной.

    Без аргументов — список; с флагом и решением — записывает решение.
    """
    edits_mod.resolve(chapter, flag_id, decision, registry=registry, reason=reason)


@app.command("edits", rich_help_panel="Правки и решения")
@_friendly
def cmd_edits(chapter: int) -> None:
    """Предпросмотр правок: как парсер понял правки.md (без вызова Писателя)."""
    edits_mod.edits(chapter)


@app.command("check", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_check(
    file: Path = typer.Argument(..., help="Файл с текстом для проверки Э1."),
    chapter: int | None = typer.Option(None, "--глава", "--chapter", help="Взять контекст (фокал/год/объём) из брифа главы."),
    focal: str = typer.Option("", "--фокал", "--focal"),
    year: int | None = typer.Option(None, "--год", "--year"),
    volume_words: int | None = typer.Option(None, "--объём", "--volume"),
) -> None:
    """Прогнать проверки Э1 по произвольному файлу — вне такта и FSM (ручной режим, NFR-3)."""
    quality.check(file, chapter=chapter, focal=focal, year=year, volume_words=volume_words)


@app.command("нормы", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_norms(
    calibrate: bool = typer.Option(False, "--калибровать", "--calibrate", help="Посчитать метрики по образцам и предложить коридоры."),
    files: list[Path] = typer.Argument(None, help="Файлы прозы-образцов (пусто — принятые главы корпуса)."),
    approve: bool = typer.Option(False, "--утвердить", "--approve", help="Записать предложенные нормы в стиль и журнал решений."),
    yes: bool = typer.Option(False, "--yes", "-y", "--да", help="Подтверждение без вопроса."),
) -> None:
    """Нормы стиля: показать; --калибровать — коридоры по образцам автора (FR-V1-7); --утвердить — записать в канон."""
    if not calibrate and not approve:
        quality.norms()
        return
    quality.norms(calibrate_files=list(files) if files else None, approve=approve, yes=yes,
                  confirm=lambda q: typer.confirm(q), from_corpus=not files)


@app.command("учёт", rich_help_panel="Обзор")
@_friendly
def cmd_accounting(volume: int | None = typer.Option(None, "--том", "--volume", help="Номер тома (по умолчанию — текущий).")) -> None:
    """Стоимость и время по главам, тому и ролям; прогноз остатка тома (FR-CT-3, FR-EC-4)."""
    overview.accounting(volume)


@app.command("метрики", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_metrics() -> None:
    """Реестр метрик Э1: идентификаторы норм, что считают, единицы, зависимости (FR-V1-1)."""
    quality.metrics_doc()


@app.command("diff", rich_help_panel="Правки и решения")
@_friendly
def cmd_diff(
    chapter: int,
    k1: int | None = typer.Argument(None, help="Номер первого черновика (по умолчанию предпоследний)."),
    k2: int | None = typer.Argument(None, help="Номер второго (по умолчанию текущий)."),
) -> None:
    """Дифф черновиков главы (по умолчанию — два последних)."""
    edits_mod.diff(chapter, k1, k2)


@app.command("log", rich_help_panel="Обзор")
@_friendly
def cmd_log(n: int = typer.Option(15, "-n", help="Сколько последних вызовов показать.")) -> None:
    """Последние API-вызовы: роль, модель, токены, стоимость (журнал §6.3)."""
    overview.log(n)


@app.command("panel", rich_help_panel="Обзор")
@_friendly
def cmd_panel(
    port: int = typer.Option(8765, "--port", help="Порт локального сервера."),
    open_browser: bool = typer.Option(True, "--открыть/--не-открывать", "--open/--no-open"),
) -> None:
    """Панель (этап 3): такт целиком в браузере — очередь, чтение с флагами,
    правки, решения, дифф, приёмка, дашборд, журнал. Только 127.0.0.1, без облака."""
    from . import server as server_mod

    ws, cfg, lib = _ctx()
    try:
        srv = server_mod.serve(ws, cfg, lib, port)
    except OSError as e:
        _fail(f"порт {port} занят или недоступен ({e}) — укажите другой: `konveyer panel --port 8766`.")
    url = f"http://127.0.0.1:{port}/"
    typer.secho(f"Панель запущена: {url} (остановка — Ctrl+C)", fg=typer.colors.GREEN)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nПанель остановлена.")
    finally:
        srv.server_close()


@app.command("find", rich_help_panel="Обзор")
@_friendly
def cmd_find(query: str) -> None:
    """Поиск по канону и выгрузкам: факты, закладки, правила, брифы, досье, проза."""
    overview.find(query)


@app.command("circles", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_circles(
    scope: str = typer.Argument("всё", help="книга | акты | главы | всё"),
    chapter: int | None = typer.Option(None, "--глава", "--chapter", help="Только одна глава (для охвата «главы»)."),
    redo: bool = typer.Option(False, "--заново", "--redo", help="Пересчитать уже существующие круги."),
    to_canon: bool = typer.Option(
        False, "--в-канон", "--to-canon", help="Внести черновики кругов в документ 2.1 библиотеки и закоммитить (Д-8)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Круги истории (8 шагов) — каркас драматургии (Р-020): книга → четыре акта → главы; черновики в драматургия/."""
    quality.circles(scope, chapter=chapter, redo=redo, to_canon=to_canon, yes=yes, confirm=typer.confirm)


@app.command("lint", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_lint(
    llm: bool = typer.Option(False, "--llm", help="Дополнительно: смысловые противоречия моделью (по вызову на документ)."),
    files: list[str] = typer.Option([], "--файл", "--file", help="Только эти документы для модельного слоя (путь внутри библиотеки)."),
    watch: bool = typer.Option(False, "--watch", "--следить", help="Следить за библиотекой и перепроверять при каждом изменении."),
    max_calls: int = typer.Option(40, "--лимит", "--max-calls", help="Предел оплачиваемых вызовов модели за прогон (--llm)."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Код возврата 1 при ошибках канона (для скриптов); панель вызывает --no-strict."),
) -> None:
    """Проверка канона на противоречия и ошибки логики повествования (машинный слой; --llm — модель)."""
    errors = canon.lint(llm=llm, files=files, watch=watch, max_calls=max_calls)
    if not watch and errors and strict:
        raise typer.Exit(code=1)


@app.command("snapshot", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_snapshot(volume: int | None = typer.Argument(None, help="Номер тома (по умолчанию — текущий).")) -> None:
    """Черновик снапшота тома (реестр 3.5): кто что знает, закладки, хронология.
    В канон снапшот вносит `konveyer volume close N`."""
    canon.snapshot(volume)


volume_app = typer.Typer(
    help="Тома (аудит 2, п. 27): сводка тома, закрытие тома (снапшот 3.5, тег, рукопись, статистика), переключение текущего тома.",
    no_args_is_help=True,
)
app.add_typer(volume_app, name="volume", rich_help_panel="Канон и бэкап")


@volume_app.command("status")
@_friendly
def cmd_volume_status(
    volume: int | None = typer.Argument(None, help="Номер тома (по умолчанию — текущий)."),
) -> None:
    """Сводка тома: главы по состояниям, слова принятых глав, метрики Э1 по актам, стоимость по журналы/api.jsonl."""
    volume_steps.volume_status(volume)


@volume_app.command("close")
@_friendly
def cmd_volume_close(
    volume: int | None = typer.Argument(None, help="Номер тома (по умолчанию — текущий)."),
    again: bool = typer.Option(False, "--заново", "--again", help="Переписать уже существующий снапшот 35_Снапшот_ТомN.md и тег."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Подтверждение без вопросов (снапшот в канон; переключение тома — только явным ответом)."),
    next_volume: bool | None = typer.Option(None, "--следующий/--без-переключения", help="Переключить config.volume на N+1 без вопроса / не переключать."),
) -> None:
    """Закрыть том: все главы «зафиксировано» → снапшот 3.5 в библиотеку (35_Снапшот_ТомN.md, коммит) →
    тег `том-N` → рукопись рукопись/ТомN.md (+ .docx при python-docx) → статистика → переход к тому N+1."""
    volume_steps.volume_close(volume, again=again, yes=yes, next_volume=next_volume, confirm=typer.confirm)


@volume_app.command("open")
@_friendly
def cmd_volume_open(volume: int = typer.Argument(..., help="Номер тома, над которым идёт работа.")) -> None:
    """Переключить текущий том рабочей области (конфиг.yaml: volume) — с проверкой, что документы тома есть."""
    volume_steps.volume_open(volume)


@app.command("doctor", rich_help_panel="Обзор")
@_friendly
def cmd_doctor() -> None:
    """Диагностика установки и готовности конвейера (NFR-1)."""
    overview.doctor()


@app.command("rollback", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_rollback(
    chapter: int,
    to: str | None = typer.Option(None, "--to", help="Целевое состояние (§5.4); без него — на один шаг назад."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Откат главы в предыдущее состояние (сценарий Г); без --to — на шаг назад по цепочке состояний §5.4."""
    canon.rollback(chapter, to=to, yes=yes, confirm=typer.confirm)


@app.command("regress", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_regress(llm: bool = typer.Option(False, "--llm", help="Включить тесты Э2.")) -> None:
    """Прогон регрессионного корпуса золотых тестов (FR-R2)."""
    quality.regress(llm=llm)


@app.command("add-golden", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_add_golden(
    test_id: str,
    fragment_file: Path,
    expect: list[str] = typer.Option([], "--expect", help="Ожидаемый флаг (check_id), можно несколько раз."),
    focal: str = typer.Option("", "--focal"),
    year: int | None = typer.Option(None, "--year"),
    echelon: str = typer.Option("Э1", "--echelon"),
) -> None:
    """Добавить золотой тест из пойманной автором ошибки (FR-R1)."""
    quality.add_golden(test_id, fragment_file, expect=expect, focal=focal, year=year, echelon=echelon)


@app.command("dashboard", rich_help_panel="Обзор")
@_friendly
def cmd_dashboard(
    open_browser: bool = typer.Option(False, "--открыть", "--open", help="Открыть в браузере."),
) -> None:
    """Собрать дашборд.html (FR-D1)."""
    path = overview.dashboard()
    if open_browser:
        import webbrowser

        webbrowser.open(path.as_uri())


@app.command("run", rich_help_panel="Такт главы")
@_friendly
def cmd_run(chapter: int) -> None:
    """Такт целиком с паузами на шагах автора (FR-O1): review, accept, canonize."""
    tact.run(chapter)


@app.command("пере-тест", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_retest(
    chapter: int = typer.Option(1, "--chapter", help="Глава для свежего брифа пакета."),
    fix: bool = typer.Option(False, "--зафиксировать", "--fix", help="Зафиксировать результаты (требует зелёной регрессии, FR-R3)."),
) -> None:
    """Пере-тест моделей (сценарий В, Д-10): пакет раунда 1 протокола отбора; прогон полуручной."""
    canon.retest(chapter=chapter, fix=fix)


@app.command("canon-commit", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_canon_commit(
    message: str = typer.Option(..., "-m", "--message", help="Сообщение коммита (изменение норм — со ссылкой Р-№)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Правка канона автором (сценарий Б): валидация структуры, перегенерация выгрузок, коммит."""
    canon.canon_commit(message, yes=yes, confirm=typer.confirm)


@app.command("library-split", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_library_split(
    target: str | None = typer.Option(None, "--в", "--to", help="Куда перенести (по умолчанию ../Библиотека рядом с рабочей областью)."),
    show: bool = typer.Option(False, "--показать", "--dry-run", help="Только план, ничего не менять."),
    with_history: bool = typer.Option(False, "--с-историей", "--with-history",
                                      help="Перенести историю папки в новый репозиторий (git subtree split)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Вынести библиотеку канона в отдельный git-репозиторий рядом с рабочей областью (аудит 2, п. 28):
    перенос папки, git init + первый коммит, library_dir в конфиг.yaml, .gitignore в прежнем репозитории."""
    canon.library_split(target=target, show=show, with_history=with_history, yes=yes, confirm=typer.confirm)


@app.command("backup", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_backup(
    folder: str | None = typer.Argument(None, help="Папка архива для --архив (по умолчанию backup_dir из конфиг.yaml, иначе ../архивы)."),
    push: bool = typer.Option(False, "--push", help="Отправить библиотеку во все удалённые места (после y)."),
    archive: bool = typer.Option(False, "--архив", "--archive", help="Zip рабочей области (главы/, журналы/, круги, снапшоты, корпус, конфиг.yaml)."),
    add_remote: tuple[str, str] | None = typer.Option(
        None, "--добавить-remote", "--add-remote", metavar="ИМЯ URL|ПАПКА",
        help="Добавить удалённое место библиотеки: URL или локальная папка (внешний диск; создаётся как bare-репозиторий)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Сохранность (NFR-6): состояние копий; --push — во все remotes; --архив — zip рабочей области;
    --добавить-remote — второе место хранения (папка на внешнем диске = без облака, §1.3 ТЗ)."""
    canon.backup(folder, push=push, archive=archive, add_remote=add_remote, yes=yes, confirm=typer.confirm)


@app.command("init", rich_help_panel="Настройка")
@_friendly
def cmd_init(
    demo: bool = typer.Option(False, "--демо", "--demo", help="Развернуть демо-библиотеку и золотые тесты — играбельный пример."),
) -> None:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, папки (NFR-1)."""
    setup.init(demo=demo)


@app.command("импорт", rich_help_panel="Настройка")
@_friendly
def cmd_import(source: str = typer.Argument(..., help="Файл, папка или .zip с материалами автора.")) -> None:
    """Импортировать материалы в сырьё/: копии оригиналов, извлечения в Markdown, индекс (раздел 5.1)."""
    try:
        onboarding_steps.import_materials(source)
    except FileNotFoundError as e:
        _fail(str(e))


@app.command("онбординг", rich_help_panel="Настройка")
@_friendly
def cmd_onboarding(
    apply: bool = typer.Option(False, "--применить", "--apply", help="Применить решения: документы в библиотеку, манифест, коммит."),
    model: bool = typer.Option(False, "--модель", "--model", help="Подключить модельный слой (роль «архивариус»)."),
    decision: list[str] = typer.Option(None, "--решение", "-р", help="Решение автора: файл=принять|тип:<имя>|сырьё|отклонить|разбить."),
    yes: bool = typer.Option(False, "--yes", "-y", "--да", help="Подтверждение без вопроса."),
    no_commit: bool = typer.Option(False, "--без-коммита", help="Записать документы без git-коммита."),
) -> None:
    """Предложение «файл → тип» по сырью с предпросмотром разбора; с --применить — нормализация в библиотеку."""
    try:
        if apply:
            if decision:
                for d in decision:
                    f, dec = d.split("=", 1)
                    from .onboarding import propose as _propose

                    _propose.set_decision(_ctx()[0], f.strip(), dec.strip())
            onboarding_steps.apply_onboarding(yes, confirm=lambda q: typer.confirm(q), commit=not no_commit)
        else:
            onboarding_steps.propose_types(use_model=model, decisions=decision)
    except (ValueError, KeyError, RuntimeError, PermissionError) as e:
        _fail(str(e))


project_app = typer.Typer(
    help="Проект серии: создание папки, манифеста и стартового комплекта (этап 1 жизненного цикла).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="проект", rich_help_panel="Настройка")


@project_app.command("создать")
@_friendly
def cmd_project_create(
    path: str | None = typer.Argument(None, help="Папка нового проекта (пустая или несуществующая)."),
    name: str | None = typer.Option(None, "--имя", "--name", help="Название серии."),
    volumes: int | None = typer.Option(None, "--томов", "--volumes", help="Сколько томов в плане."),
    modules: str | None = typer.Option(None, "--модули", "--modules", help="Модули через запятую (пусто — умолчания)."),
    methodic: str | None = typer.Option(None, "--методика", "--methodic", help="Методика драматургии."),
    profile: str | None = typer.Option(None, "--профиль", "--profile", help="Профиль серии (имя из data/профили или папка)."),
    no_starter: bool = typer.Option(False, "--без-комплекта", help="Не создавать стартовый комплект документов."),
    no_git: bool = typer.Option(False, "--без-git", help="Не инициализировать git в библиотеке."),
    yes: bool = typer.Option(False, "--да", "-y", "--yes", help="Без вопросов: умолчания для всего, что не задано."),
) -> None:
    """Создать проект серии: папки по раскладке, проект.yaml, стартовый комплект, git библиотеки."""
    try:
        setup.project_create(path, name, volumes, modules, methodic, profile, starter=not no_starter, git=not no_git,
                             yes=yes, prompt=lambda q, d: typer.prompt(q, default=d))
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        _fail(str(e))


def main() -> None:  # точка входа для python -m konveyer.cli
    app()


if __name__ == "__main__":
    main()
