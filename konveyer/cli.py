"""CLI универсального конвейера книжной серии (раздел 8.1 ТЗ: FR-CL-1…FR-CL-5; язык — русский, NFR-7).

Каждый шаг такта исполним отдельной командой (FR-CL-1): отказ любого компонента
не блокирует такт — артефакты человекочитаемы, ручной режим всегда возможен (NFR-4).

Тонкая обёртка над ядром `konveyer/steps/*`: здесь только регистрация команд typer
(имена, опции, панели справки), вызов функции ядра и перевод её исключений в сообщения и коды
возврата (`_friendly`). Логика шагов, тексты сообщений и подтверждения — в ядре; typer в ядре нет.

Русский интерфейс целиком (FR-CL-5, NFR-7): русские имена команд и опций с латинскими синонимами,
русская справка (заголовки разделов, «--справка», без автодополнения оболочки) и русские сообщения
об ошибках разбора командной строки в едином формате «ОШИБКА: … . …» с кодом возврата 1 (FR-CL-3):
код 2 остаётся только за ручным режимом.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from pathlib import Path

import typer
from typer import core as typer_core

try:  # typer ≥ 0.27 несёт click внутри себя
    from typer._click import exceptions as click_exc
except ImportError:  # pragma: no cover — typer < 0.27 с отдельным click
    from click import exceptions as click_exc  # type: ignore[no-redef]

from . import cancel, steps
from .steps import canon, edits as edits_mod, onboarding as onboarding_steps, overview, quality, setup, tact, volume as volume_steps
from .steps.common import COMMAND_NAMES, NEXT_STEP as NEXT_STEP  # noqa: F401 — совместимость: `from konveyer.cli import NEXT_STEP`
from .steps.common import _chapter_flags_summary as _chapter_flags_summary  # noqa: F401
from .steps.common import _ctx, _ensure_dir as _ensure_dir, _is_git_url as _is_git_url  # noqa: F401
from .steps.common import _print_variants as _print_variants, _print_verdict as _print_verdict  # noqa: F401
from .steps.common import _sha256 as _sha256  # noqa: F401
from .steps.canon import _compile_window_to as _compile_window_to  # noqa: F401

# ------------------------------------------------------------------ русская справка и ошибки разбора (FR-CL-3, FR-CL-5, NFR-7)

HELP_OPTION_NAMES = ["--справка", "--help", "-h"]
OPTIONS_METAVAR = "[ОПЦИИ]"
SUBCOMMAND_METAVAR = "КОМАНДА [АРГУМЕНТЫ]..."
USAGE_PREFIX = "Использование: "
HELP_OPTION_TEXT = "Показать справку и выйти."

try:  # заголовки и пометки богатой справки typer — по-русски
    from typer import rich_utils as _rich_utils

    _rich_utils.ARGUMENTS_PANEL_TITLE = "Аргументы"
    _rich_utils.OPTIONS_PANEL_TITLE = "Опции"
    _rich_utils.COMMANDS_PANEL_TITLE = "Команды"
    _rich_utils.ERRORS_PANEL_TITLE = "Ошибка"
    _rich_utils.DEFAULT_STRING = "[по умолчанию: {}]"
    _rich_utils.ENVVAR_STRING = "[переменная окружения: {}]"
    _rich_utils.REQUIRED_LONG_STRING = "[обязательно]"
    _rich_utils.DEPRECATED_STRING = "(устарело) "
    _rich_utils.ABORTED_TEXT = "Прервано."
    _rich_utils.RICH_HELP = "Справка: '{command_path} {help_option}'."
except ImportError:  # pragma: no cover — typer без rich: простая справка click
    pass

_NoArgsIsHelpError = getattr(click_exc, "NoArgsIsHelpError", None)
_ABORT_ERRORS = tuple({typer.Abort, getattr(click_exc, "Abort", typer.Abort)})

# английские сообщения разбора click → русские (FR-CL-3); значение в кавычках click даёт как repr
_USAGE_TRANSLATIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^No such command '(.+?)'\.(?: Did you mean (.+?)\?)?$"), "нет команды «{g1}»{maybe}"),
    (re.compile(r"^Missing command\.?$"), "не указана команда"),
    (re.compile(r"^Got unexpected extra arguments?(?:\(s\))? \((.+)\)$"), "лишние аргументы: {g1}"),
    (re.compile(r"^Option '(.+?)' requires an argument\.?$"), "опции «{g1}» нужно значение"),
    (re.compile(r"^Option '(.+?)' requires (\d+) arguments\.?$"), "опции «{g1}» нужно значений: {g2}"),
    (re.compile(r"^Option '(.+?)' does not take a value\.?$"), "опция «{g1}» не принимает значения"),
    (re.compile(r"^'?(.+?)'? is not a valid (?:int|integer|int range)\.?$"), "«{g1}» — не целое число"),
    (re.compile(r"^'?(.+?)'? is not a valid (?:float|float range)\.?$"), "«{g1}» — не число"),
    (re.compile(r"^'?(.+?)'? is not a valid boolean\b.*$"), "«{g1}» — не да/нет"),
    (re.compile(r"^'?(.+?)'? is not in the range (.+?)\.?$"), "{g1} вне диапазона {g2}"),
    (re.compile(r"^'?(.+?)'? is not one of (.+?)\.?$"), "«{g1}» не из списка: {g2}"),
    (re.compile(r"^(?:Path|File|Directory) '(.+?)' does not exist\.?$"), "путь «{g1}» не существует"),
]


def _translate_usage(message: str) -> str:
    """Русский текст ошибки разбора; незнакомую формулировку оставляем как есть."""
    text = (message or "").strip()
    for rx, template in _USAGE_TRANSLATIONS:
        m = rx.match(text)
        if not m:
            continue
        groups = {f"g{i}": (g or "") for i, g in enumerate(m.groups(), start=1)}
        groups["maybe"] = f" (может быть, {m.group(2)}?)" if rx.groups >= 2 and m.group(2) else ""
        return template.format(**groups)
    return text


def _param_name(param) -> str:
    """Имя параметра для сообщения: русская длинная опция, иначе первая; аргумент — его имя."""
    opts = list(getattr(param, "opts", []) or [])
    longs = [o for o in opts if o.startswith("--")]
    cyr = [o for o in longs if not o.isascii()]
    if cyr or longs or opts:
        return (cyr or longs or opts)[0]
    return str(getattr(param, "name", "") or "")


def _usage_error_text(e: click_exc.UsageError) -> str:
    """«ОШИБКА: <что случилось>. <что сделать>» для ошибок разбора командной строки (FR-CL-3)."""
    ctx = getattr(e, "ctx", None)
    param = getattr(e, "param", None)
    if isinstance(e, click_exc.NoSuchOption):
        what = f"нет опции «{e.option_name}»"
        if getattr(e, "possibilities", None):
            what += f" (может быть, {', '.join(e.possibilities)}?)"
    elif isinstance(e, click_exc.MissingParameter):
        kind = "аргумент" if getattr(param, "param_type_name", "") == "argument" else "опция"
        what = f"не указан{'' if kind == 'аргумент' else 'а'} обязательн{'ый' if kind == 'аргумент' else 'ая'} {kind} {_param_name(param)}"
    elif isinstance(e, click_exc.BadParameter):
        kind = "аргумента" if getattr(param, "param_type_name", "") == "argument" else "опции"
        where = f" {kind} «{_param_name(param)}»" if param is not None else ""
        what = f"недопустимое значение{where}: {_translate_usage(e.message)}"
    else:
        what = _translate_usage(getattr(e, "message", str(e)))
    help_hint = f"См. `{ctx.command_path} {HELP_OPTION_NAMES[0]}`." if ctx is not None else f"См. `konveyer {HELP_OPTION_NAMES[0]}`."
    return f"{what}. {help_hint}"


class _RussianHelpMixin:
    """Русские элементы справки, общие для группы и команд: строка использования и опция справки."""

    def format_usage(self, ctx, formatter) -> None:
        pieces = self.collect_usage_pieces(ctx)  # type: ignore[attr-defined]
        formatter.write_usage(ctx.command_path, " ".join(pieces), prefix=USAGE_PREFIX)

    def get_help_option_names(self, ctx) -> list[str]:
        names = set(super().get_help_option_names(ctx))  # type: ignore[misc]
        return [n for n in HELP_OPTION_NAMES if n in names] + sorted(names.difference(HELP_OPTION_NAMES))

    def get_help_option(self, ctx):
        opt = super().get_help_option(ctx)  # type: ignore[misc]
        if opt is not None:
            opt.help = HELP_OPTION_TEXT
        return opt


class Команда(_RussianHelpMixin, typer_core.TyperCommand):
    """Команда с русской справкой."""


class Группа(_RussianHelpMixin, typer_core.TyperGroup):
    """Группа команд с русской справкой и русскими ошибками разбора (FR-CL-3): опечатка в аргументах —
    «ОШИБКА: …», код 1; вызов без аргументов — справка, код 0; отказ/прерывание — «Прервано.», код 1."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        try:
            rv = super().main(args=args, prog_name=prog_name, complete_var=complete_var, standalone_mode=False, **extra)
        except click_exc.UsageError as e:
            if _NoArgsIsHelpError is not None and isinstance(e, _NoArgsIsHelpError):
                typer.echo(e.ctx.get_help())
                code = 0
            else:
                typer.secho(f"ОШИБКА: {_usage_error_text(e)}", fg=typer.colors.RED, err=True)
                code = 1
        except click_exc.ClickException as e:
            e.show()
            code = e.exit_code
        except _ABORT_ERRORS:
            typer.secho("Прервано.", fg=typer.colors.YELLOW, err=True)
            code = 1
        else:
            code = rv if isinstance(rv, int) else 0
        if standalone_mode:
            sys.exit(code)
        return code


app = typer.Typer(
    name="konveyer",
    cls=Группа,
    help="КОНВЕЙЕР — производственный такт главы (ТЗ v1.0).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    options_metavar=OPTIONS_METAVAR,
    subcommand_metavar=SUBCOMMAND_METAVAR,
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
    version: bool = typer.Option(False, "--версия", "--version", "-V", help="Версия конвейера.", callback=_version_callback, is_eager=True),
) -> None:
    """КОНВЕЙЕР — производственный такт главы (ТЗ v1.0)."""


def _hide_paths(message: str) -> str:
    """FR-SC-9: абсолютные пути библиотеки и рабочей области в сообщениях об ошибках — словами."""
    from . import guard
    from .paths import find_workspace

    roots: list = [(guard._library_dir_raw, "библиотека"), (guard._library_dir, "библиотека")]
    try:
        roots.append((find_workspace().root, "рабочая область"))
    except OSError:
        pass
    return steps.hide_paths(message, roots)


def _fail(message: str) -> None:
    typer.secho(f"ОШИБКА: {_hide_paths(message)}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _manual(e: steps.ManualMode) -> None:
    typer.secho(f"⚠ {_hide_paths(e.reason)}", fg=typer.colors.YELLOW)
    typer.echo(f"Ручной режим (NFR-4): {_hide_paths(e.hint)}")
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
    трейсбека. KONVEYER_DEBUG=1 — полный трейсбек для программных ошибок.

    Внешняя команда — одна задача для учёта времени такта (`steps.job_context`): вложенные команды
    (`run` → `write` → …) наследуют задачу. Остановка автором (`cancel.Cancelled`) — сообщение без
    трейсбека, код выхода 2 (как ручной режим: глава на последнем завершённом шаге)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # сервер панели живёт часами — сам он не задача: задачи заводят команды, которые он вызывает
        job_name = None if fn.__name__ in _NOT_A_JOB else fn.__name__.removeprefix("cmd_")
        try:
            with steps.job_context(job_name) as outermost:
                try:
                    return fn(*args, **kwargs)
                except (typer.Exit, typer.Abort):
                    raise  # собственные коды выхода — не ошибка
                except steps.Rejected as e:  # автор не подтвердил (Д-17): сознательный отказ — код 0
                    if e.abort:
                        typer.secho("Отменено автором.", fg=typer.colors.YELLOW, err=True)
                    raise typer.Exit(code=e.code)
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
        except steps.StepError as e:  # замок проекта занят другой задачей (FR-TK-7)
            _fail(str(e))

    return wrapper


# ------------------------------------------------------------------ общие параметры команд (FR-CL-4, FR-CL-5)


def _chapter():
    """Позиционный номер главы — один и тот же у всех команд такта."""
    return typer.Argument(..., metavar="ГЛАВА", help="Номер главы.")


def _yes():
    """Флаг «без вопросов» у каждой команды с подтверждением автора (FR-CL-4): `--да`, синонимы `--yes`, `-y`."""
    return typer.Option(False, "--да", "--yes", "-y", help="Подтверждение без вопроса (для скриптов).")


def _manual_flag(text: str):
    """Флаг ручного режима у долгих команд (FR-CL-4, FR-WR-4): `--вручную`, синоним `--manual`."""
    return typer.Option(False, "--вручную", "--manual", help=text)


# ------------------------------------------------------------------ такт главы (раздел 7 ТЗ)


@app.command("export", rich_help_panel="Такт главы")
@_friendly
def cmd_export() -> None:
    """Перегенерировать все выгрузки из MD-библиотеки (FR-EX-1…FR-EX-5)."""
    tact.export()


@app.command("compile", rich_help_panel="Такт главы")
@_friendly
def cmd_compile(chapter: int = _chapter()) -> None:
    """Собрать окно контекста главы (FR-WN-1…FR-WN-7). Экспорт выполняется автоматически (риск R-8)."""
    tact.compile(chapter)


@app.command("write", rich_help_panel="Такт главы")
@_friendly
def cmd_write(
    chapter: int = _chapter(),
    manual: bool = _manual_flag("Зарегистрировать черновик, сохранённый вручную как черновик_{k+1}.md (FR-WR-4)."),
    variants: int = typer.Option(
        1, "--варианты", "--variants", min=1, max=4,
        help="A/B: столько вызовов Писателя по одному окну → черновик_k.md, черновик_k.alt1.md …; метрики Э1 рядом (варианты.json).",
    ),
    choose: str | None = typer.Option(
        None, "--выбрать", "--choose", help="Сделать вариант (alt1, alt0 — прежний основной) текущим черновик_k.md; состояние не меняется.",
    ),
) -> None:
    """Отправить окно Писателю, сохранить черновик_k.md (FR-WR-1). `--варианты 2` — A/B (FR-WR-2), `--выбрать alt1` — выбор варианта."""
    tact.write(chapter, manual=manual, variants=variants, choose=choose)


@app.command("verify1", rich_help_panel="Такт главы")
@_friendly
def cmd_verify1(
    chapter: int = _chapter(),
    accept_brak: bool = typer.Option(
        False, "--принять-брак", "--accept-brak",
        help="Решение автора: принять текст вопреки браку Э1 (после исчерпания авто-повторов); нужна --причина.",
    ),
    reason: str = typer.Option("", "--причина", "--reason", help="Причина решения автора (пишется в историю главы и журнал решений)."),
) -> None:
    """Машинные проверки Э1 (FR-V1-5). Брак метрик → авто-повтор генерации (лимит в конфиге), затем вердикт автору."""
    tact.verify1(chapter, accept_brak=accept_brak, reason=reason)


@app.command("verify2", rich_help_panel="Такт главы")
@_friendly
def cmd_verify2(
    chapter: int = _chapter(),
    manual: bool = _manual_flag("Принять флаги.json, заполненный вручную — ответ модели как есть (FR-TK-6, FR-RL-3)."),
    taste: bool = typer.Option(False, "--вкус", "--taste", help="Дополнительно: советы по вкусу автора (FR-V2-7) — не блокируют приёмку; с --manual принимает вкус.json."),
    again: bool = typer.Option(
        False, "--повторно", "--после-правок", "--again",
        help="Повторный Э2 по текущему черновику после правок (из «правки»/«дифф-контроль»): совещательно — "
             "флаги_повторно.json и раздел в приёмка.md; FSM, флаги.json и решения не меняются.",
    ),
) -> None:
    """Смысловые проверки Э2 (FR-V2-1…FR-V2-7). `--повторно` — второй прогон после правок (совещательно)."""
    tact.verify2(chapter, manual=manual, taste=taste, again=again)


@app.command("review", rich_help_panel="Такт главы")
@_friendly
def cmd_review(chapter: int = _chapter()) -> None:
    """Пакет приёмки автора: приёмка.md + правки.md + решения.json (FR-RV-1)."""
    tact.review(chapter)


@app.command("apply-edits", rich_help_panel="Такт главы")
@_friendly
def cmd_apply_edits(
    chapter: int = _chapter(),
    manual: bool = _manual_flag("Черновик с правками сохранён вручную как черновик_{k+1}.md."),
) -> None:
    """Внесение правок: дословные БЫЛО/СТАЛО — кодом (FR-ED-1), свободные указания — Писателем (FR-ED-2)."""
    tact.apply_edits(chapter, manual=manual)


@app.command("diff-check", rich_help_panel="Такт главы")
@_friendly
def cmd_diff_check(
    chapter: int = _chapter(),
    author_fix: bool = typer.Option(
        False, "--авторская-правка", "--author-fix", help="Текущий черновик правил сам автор — расхождения не самоволия."
    ),
    fragments: list[str] | None = typer.Option(
        None, "--фрагмент", "--fragment",
        help="С --авторская-правка: снять только эти самоволия (номер в списке или подстрока текста); можно несколько раз.",
    ),
) -> None:
    """Дифф-контроль до/после правок (FR-V1-6, FR-ED-3); не чист — глава возвращается в «правки»."""
    tact.diff_check(chapter, author_fix=author_fix, fragments=fragments)


@app.command("accept", rich_help_panel="Такт главы")
@_friendly
def cmd_accept(chapter: int = _chapter(), yes: bool = _yes()) -> None:
    """Приёмка главы автором (FR-RV-4): только из «дифф-контроль: чисто», с явным подтверждением."""
    tact.accept(chapter, yes=yes, confirm=typer.confirm)


@app.command("canonize", rich_help_panel="Такт главы")
@_friendly
def cmd_canonize(
    chapter: int = _chapter(),
    apply: bool = typer.Option(False, "--применить", "--apply", help="Применить подписанный пакет (правки MD + экспорт + git-коммит)."),
    yes: bool = _yes(),
    redo: bool = typer.Option(False, "--заново", "--redo", help="Пересобрать пакет, даже если автор его уже правил (правки пропадут)."),
    manual: bool = typer.Option(False, "--manual", "--ручной", help="Ручной режим: собрать пакет из ответа модели в главы/N/ответ_канониста.json (FR-RL-3)."),
    answer: Path | None = typer.Option(None, "--ответ", "--answer", help="Файл с JSON-ответом Канониста (ручной режим)."),
) -> None:
    """Канонист: пакет записей в канон (FR-CN-1); применение — только после подписи (FR-CN-2)."""
    tact.canonize(chapter, apply=apply, yes=yes, redo=redo, confirm=typer.confirm, manual=manual, answer=answer)


# ------------------------------------------------------------- сервисные


@app.command("status", rich_help_panel="Обзор")
@_friendly
def cmd_status(
    chapter: int | None = typer.Argument(None, metavar="ГЛАВА", help="Номер главы — подробная карточка."),
    volume: int | None = typer.Option(None, "--том", "--volume", help="Том (по умолчанию — текущий из манифеста)."),
) -> None:
    """Состояния глав и следующий шаг (§7.2); `konveyer статус N` — карточка главы; `--том N` — главы тома N."""
    overview.status(chapter, volume=volume)


@app.command("resolve", rich_help_panel="Правки и решения")
@_friendly
def cmd_resolve(
    chapter: int = _chapter(),
    flag_id: str | None = typer.Argument(None, metavar="ФЛАГ", help="ID самоволки (например F-001)."),
    decision: str | None = typer.Argument(None, metavar="РЕШЕНИЕ", help="«вычеркнуть», «канонизировать» или «отклонить»."),
    registry: str | None = typer.Option(None, "--реестр", "--registry", help="Целевой реестр (имя типа: эпистемика, закладки, континуити…)."),
    reason: str = typer.Option("", "--причина", "--reason", help="Причина отклонения флага (обязательна для «отклонить»)."),
    all_: bool = typer.Option(False, "--все", "--all", help="Одно решение для всех самоволок без решения: `решение N вычеркнуть --все`."),
) -> None:
    """Решения по флагам без ручной правки JSON (§7.7): самоволку — вычеркнуть или канонизировать, любой флаг — отклонить с причиной.

    Без аргументов — список; с флагом и решением — записывает решение; `--все` — решение для всех самоволок без решения.
    """
    if all_:
        edits_mod.resolve_all(chapter, decision or flag_id or "", registry=registry)
        return
    edits_mod.resolve(chapter, flag_id, decision, registry=registry, reason=reason)


@app.command("edits", rich_help_panel="Правки и решения")
@_friendly
def cmd_edits(chapter: int = _chapter()) -> None:
    """Предпросмотр правок: как парсер понял правки.md (без вызова Писателя, FR-ED-4)."""
    edits_mod.edits(chapter)


@app.command("check", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_check(
    file: Path = typer.Argument(..., metavar="ФАЙЛ", help="Файл с текстом для проверки Э1."),
    chapter: int | None = typer.Option(None, "--глава", "--chapter", help="Взять контекст (фокал/год/объём) из брифа главы."),
    focal: str = typer.Option("", "--фокал", "--focal", help="Фокальный персонаж (для проверок фокализации и стоп-листов линии)."),
    year: int | None = typer.Option(None, "--год", "--year", help="Год действия (для проверок лексики эпохи)."),
    volume_words: int | None = typer.Option(None, "--объём", "--words", help="Плановый объём в словах (для проверки объёма)."),
) -> None:
    """Прогнать проверки Э1 по произвольному файлу — вне такта и FSM (ручной режим, FR-WR-4)."""
    quality.check(file, chapter=chapter, focal=focal, year=year, volume_words=volume_words)


@app.command("нормы", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_norms(
    calibrate: bool = typer.Option(False, "--калибровать", "--calibrate", help="Посчитать метрики по образцам и предложить коридоры."),
    files: list[Path] = typer.Argument(None, metavar="ФАЙЛЫ", help="Файлы прозы-образцов (пусто — принятые главы корпуса)."),
    approve: bool = typer.Option(False, "--утвердить", "--approve", help="Записать предложенные нормы в стиль и журнал решений."),
    yes: bool = _yes(),
) -> None:
    """Нормы стиля: показать; --калибровать — коридоры по образцам автора (FR-V1-7); --утвердить — записать в канон."""
    if not calibrate and not approve:
        quality.norms()
        return
    quality.norms(calibrate_files=list(files) if files else None, approve=approve, yes=yes,
                  confirm=lambda q: typer.confirm(q), from_corpus=not files)


@app.command("типы", rich_help_panel="Настройка проекта")
@_friendly
def cmd_types(
    docs: bool = typer.Option(False, "--документация", "--docs", help="Записать «Соглашения типов» и «Реестр метрик» в папку."),
    out: Path = typer.Option(Path("docs"), "--куда", "--out", help="Папка для сгенерированных документов."),
) -> None:
    """Каталог типов документов и модулей (с переопределениями проекта); --документация — сгенерировать docs/*.md (NFR-10)."""
    from . import catalog, guard, metrics as metrics_mod
    from .paths import find_workspace

    root: Path | None
    try:
        root = find_workspace().root
    except FileNotFoundError:
        root = None
    types, modules = catalog.load_types(root), catalog.load_modules(root)
    if not docs:
        for name in sorted(types):
            t = types[name]
            typer.echo(f"{name}: {t.purpose or '—'} · модули: {', '.join(t.feeds) or '—'}"
                       + (" · обязателен для такта" if t.required_for_tact else ""))
        typer.echo("модули: " + ", ".join(sorted(modules)))
        return
    out.mkdir(parents=True, exist_ok=True)
    guard.write_text(out / "Соглашения_типов.md", catalog.documentation(types, modules))
    guard.write_text(out / "Реестр_метрик.md", metrics_mod.documentation())
    typer.echo(f"записано: {out / 'Соглашения_типов.md'}, {out / 'Реестр_метрик.md'}")


@app.command("учёт", rich_help_panel="Обзор")
@_friendly
def cmd_accounting(volume: int | None = typer.Option(None, "--том", "--volume", help="Номер тома (по умолчанию — текущий).")) -> None:
    """Стоимость и время по главам, тому и ролям; прогноз остатка тома (FR-CT-3)."""
    overview.accounting(volume)


@app.command("метрики", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_metrics() -> None:
    """Реестр метрик Э1: идентификаторы норм, что считают, единицы, зависимости (FR-V1-1)."""
    quality.metrics_doc()


@app.command("diff", rich_help_panel="Правки и решения")
@_friendly
def cmd_diff(
    chapter: int = _chapter(),
    k1: int | None = typer.Argument(None, metavar="ЧЕРНОВИК1", help="Номер первого черновика (по умолчанию предпоследний)."),
    k2: int | None = typer.Argument(None, metavar="ЧЕРНОВИК2", help="Номер второго (по умолчанию текущий)."),
) -> None:
    """Дифф черновиков главы (по умолчанию — два последних)."""
    edits_mod.diff(chapter, k1, k2)


@app.command("log", rich_help_panel="Обзор")
@_friendly
def cmd_log(n: int = typer.Option(15, "--число", "--last", "-n", help="Сколько последних вызовов показать.")) -> None:
    """Последние API-вызовы: роль, модель, токены, стоимость (журнал FR-CT-1)."""
    overview.log(n)


@app.command("panel", rich_help_panel="Обзор")
@_friendly
def cmd_panel(
    port: int = typer.Option(8765, "--порт", "--port", help="Порт локального сервера."),
    open_browser: bool = typer.Option(True, "--открыть/--не-открывать", "--open/--no-open", help="Открыть панель в браузере после запуска."),
) -> None:
    """Панель (FR-PN-1…FR-PN-7): такт целиком в браузере — очередь, чтение с флагами,
    правки, решения, дифф, приёмка, дашборд, журнал. Только 127.0.0.1, без облака."""
    from . import server as server_mod

    ws, cfg, lib = _ctx()
    try:
        srv = server_mod.serve(ws, cfg, lib, port)
    except OSError as e:
        _fail(f"порт {port} занят или недоступен ({e}) — укажите другой: `konveyer панель --порт 8766`.")
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
def cmd_find(query: str = typer.Argument(..., metavar="ЗАПРОС", help="Что искать (подстрока).")) -> None:
    """Поиск по канону и выгрузкам: факты, закладки, правила, брифы, досье, проза."""
    overview.find(query)


@app.command("circles", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_circles(
    scope: str = typer.Argument("всё", metavar="ОХВАТ", help="книга | акты | главы | всё"),
    chapter: int | None = typer.Option(None, "--глава", "--chapter", help="Только одна глава (для охвата «главы»)."),
    redo: bool = typer.Option(False, "--заново", "--redo", help="Пересчитать уже существующие каркасы."),
    to_canon: bool = typer.Option(
        False, "--в-канон", "--to-canon",
        help="Внести черновики каркасов в документ каркасов тома (тип «каркасы») и закоммитить — по подтверждению автора (FR-DR-4).",
    ),
    yes: bool = _yes(),
    accept: str | None = typer.Option(None, "--принять", "--accept", help="Принять ответ модели из файла как каркас: книга | акт_N | глава_NN."),
    answer_file: Path | None = typer.Option(None, "--ответ", "--answer", help="Файл с ответом модели (JSON) для --принять."),
) -> None:
    """Каркасы драматургии по методике проекта (FR-DR-1…FR-DR-6): том → акты → главы; шаги — по методике
    каждого уровня; черновики аналитика в драматургия/, в канон — только с --в-канон; --принять — ответ модели
    из ручного прогона."""
    quality.circles(scope, chapter=chapter, redo=redo, to_canon=to_canon, yes=yes, confirm=typer.confirm,
                    accept=accept, answer_file=answer_file)


@app.command("lint", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_lint(
    llm: bool = typer.Option(False, "--модель", "--llm", help="Дополнительно: смысловые противоречия моделью (по вызову на документ, FR-LT-3)."),
    files: list[str] = typer.Option([], "--файл", "--file", help="Только эти документы для модельного слоя (путь внутри библиотеки)."),
    watch: bool = typer.Option(False, "--следить", "--watch", help="Следить за библиотекой и перепроверять при каждом изменении (FR-LT-5)."),
    max_calls: int = typer.Option(40, "--лимит", "--max-calls", help="Предел оплачиваемых вызовов модели за прогон (--модель)."),
    budget: float | None = typer.Option(
        None, "--бюджет", "--budget", metavar="USD",
        help="Бюджет модельного слоя в долларах по ценам конфига: оценка выше бюджета — отказ до первого вызова (--модель).",
    ),
    strict: bool = typer.Option(
        True, "--строго/--не-строго", "--strict/--no-strict",
        help="Код возврата 1 при ошибках канона (для скриптов); панель вызывает --не-строго.",
    ),
    answers: list[str] = typer.Option([], "--ответ", "--answer", help="Ответ модели, полученный вручную по сохранённому промпту: документ=файл_ответа."),
    fix: bool = typer.Option(False, "--исправить", "--fix", help="Применить механические исправления отчёта (с подтверждением; --файл — только эти документы)."),
    yes: bool = _yes(),
) -> None:
    """Проверка канона на противоречия и ошибки логики повествования (машинный слой, FR-LT-1…FR-LT-2; --модель — модельный
    слой; --ответ — принять ответ модели по документу из ручного прогона)."""
    errors = canon.lint(llm=llm, files=files, watch=watch, max_calls=max_calls, answers=answers, budget=budget,
                        fix=fix, yes=yes, confirm=typer.confirm)
    if not watch and errors and strict:
        raise typer.Exit(code=1)


@app.command("snapshot", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_snapshot(volume: int | None = typer.Argument(None, metavar="ТОМ", help="Номер тома (по умолчанию — текущий).")) -> None:
    """Черновик снапшота тома (срез мира и знаний на конец тома): кто что знает, закладки, хронология.
    В канон снапшот вносит `konveyer том закрыть N`; раньше закрытия — правкой библиотеки и `konveyer канон-коммит`."""
    canon.snapshot(volume)


volume_app = typer.Typer(
    help="Тома (FR-VL-1…FR-VL-3): сводка тома, закрытие тома (рукопись, статистика, снапшот тома в канон, тег), переключение текущего тома.",
    no_args_is_help=True,
)
app.add_typer(volume_app, name="volume", rich_help_panel="Канон и бэкап", hidden=True)


@volume_app.command("status")
@_friendly
def cmd_volume_status(
    volume: int | None = typer.Argument(None, metavar="ТОМ", help="Номер тома (по умолчанию — текущий)."),
) -> None:
    """Сводка тома (FR-VL-3): главы по состояниям, слова принятых глав, метрики Э1 по актам, стоимость по журналу API."""
    volume_steps.volume_status(volume)


@volume_app.command("close")
@_friendly
def cmd_volume_close(
    volume: int | None = typer.Argument(None, metavar="ТОМ", help="Номер тома (по умолчанию — текущий)."),
    again: bool = typer.Option(False, "--заново", "--again", help="Переписать уже существующий снапшот тома и тег."),
    yes: bool = typer.Option(False, "--да", "--yes", "-y", help="Подтверждение без вопросов (снапшот в канон; переключение тома — только явным ответом)."),
    next_volume: bool | None = typer.Option(
        None, "--следующий/--без-переключения", "--next/--no-next", help="Переключить текущий том на N+1 без вопроса / не переключать.",
    ),
) -> None:
    """Закрыть том (FR-VL-2): все главы «зафиксировано» → рукопись рукопись/ТомN.md (+ .docx при python-docx)
    и статистика → снапшот тома в библиотеку (коммит) → тег `том-N` → переход к тому N+1 по подтверждению."""
    volume_steps.volume_close(volume, again=again, yes=yes, next_volume=next_volume, confirm=typer.confirm)


@volume_app.command("open")
@_friendly
def cmd_volume_open(volume: int = typer.Argument(..., metavar="ТОМ", help="Номер тома, над которым идёт работа.")) -> None:
    """Переключить текущий том рабочей области (FR-VL-1) — с проверкой, что документы тома есть."""
    volume_steps.volume_open(volume)


@app.command("doctor", rich_help_panel="Обзор")
@_friendly
def cmd_doctor() -> None:
    """Диагностика установки и готовности конвейера (FR-LC-2, FR-RT-3, FR-BK-1)."""
    overview.doctor()


@app.command("rollback", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_rollback(
    chapter: int = _chapter(),
    to: str | None = typer.Option(None, "--в", "--to", metavar="СОСТОЯНИЕ", help="Целевое состояние (FR-TK-2); без него — на один шаг назад."),
    yes: bool = _yes(),
) -> None:
    """Откат главы в предыдущее состояние (FR-SC-4, сценарий Е); без --в — на шаг назад по цепочке состояний."""
    canon.rollback(chapter, to=to, yes=yes, confirm=typer.confirm)


@app.command("regress", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_regress(llm: bool = typer.Option(False, "--модель", "--llm", help="Включить тесты Э2 (вызовы модели).")) -> None:
    """Прогон регрессионного корпуса золотых тестов (FR-RG-2)."""
    quality.regress(llm=llm)


@app.command("add-golden", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_add_golden(
    test_id: str = typer.Argument(..., metavar="ИДЕНТИФИКАТОР", help="Идентификатор золотого теста."),
    fragment_file: Path = typer.Argument(..., metavar="ФАЙЛ", help="Файл с фрагментом текста."),
    expect: list[str] = typer.Option([], "--ожидать", "--expect", help="Ожидаемый флаг (идентификатор проверки), можно несколько раз."),
    ignore: list[str] = typer.Option([], "--игнорировать", "--ignore", help="Флаг, срабатывание которого не считать «лишним» (нормы длин на коротком фрагменте)."),
    focal: str = typer.Option("", "--фокал", "--focal", help="Фокальный персонаж фрагмента."),
    year: int | None = typer.Option(None, "--год", "--year", help="Год действия фрагмента."),
    echelon: str = typer.Option("Э1", "--эшелон", "--echelon", help="Эшелон проверок: Э1 или Э2."),
    chapter: int | None = typer.Option(None, "--глава", "--chapter", help="Взять срез контекста из брифа и окна главы N."),
    window_file: Path | None = typer.Option(None, "--окно", "--window", help="Файл окна для проверки утечки окна."),
    use_corpus: bool = typer.Option(False, "--корпус", "--corpus", help="Проверять против корпуса принятых глав."),
    volume_words: int | None = typer.Option(None, "--объём", "--volume-words", help="Объём брифа, слов."),
) -> None:
    """Добавить золотой тест из пойманной автором ошибки (FR-RG-1)."""
    quality.add_golden(test_id, fragment_file, expect=expect, focal=focal, year=year, echelon=echelon, chapter=chapter,
                       window_file=window_file, use_corpus=use_corpus, volume_words=volume_words, ignore=ignore)


@app.command("dashboard", rich_help_panel="Обзор")
@_friendly
def cmd_dashboard(
    open_browser: bool = typer.Option(False, "--открыть", "--open", help="Открыть в браузере."),
) -> None:
    """Собрать дашборд.html: главы, состояния, стоимость и время (обзор без панели)."""
    path = overview.dashboard()
    if open_browser:
        import webbrowser

        webbrowser.open(path.as_uri())


@app.command("run", rich_help_panel="Такт главы")
@_friendly
def cmd_run(chapter: int = _chapter()) -> None:
    """Такт целиком с паузами на шагах автора (FR-CL-1): приёмка, принять, канон."""
    tact.run(chapter)


@app.command("пере-тест", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_retest(
    chapter: int = typer.Option(1, "--chapter", "--глава", help="Глава для свежего брифа пакета."),
    fix: bool = typer.Option(False, "--зафиксировать", "--fix", help="Зафиксировать пины по результатам (требует зелёной регрессии и пакета с ответами, FR-RG-3, FR-RT-2)."),
    no_pack: bool = typer.Option(False, "--без-пакета", "--no-pack", help="С --зафиксировать: зафиксировать пины без пакета сравнения (решение автора записывается в черновик журнала)."),
) -> None:
    """Пере-тест моделей (FR-RT-1, Д-19): пакет сравнения на свежем брифе главы — ответы доступных моделей,
    метрики Э1 и флаги Э2 в сводке; недоступные модели — промпт для ручного прогона."""
    canon.retest(chapter=chapter, fix=fix, no_pack=no_pack)


@app.command("отбор", rich_help_panel="Качество и регрессия")
@_friendly
def cmd_selection(
    chapter: int = typer.Option(1, "--глава", "--chapter", help="Глава для свежего брифа пакета."),
    fix: bool = typer.Option(False, "--зафиксировать", "--fix", help="Зафиксировать выбранные модели (требует зелёной регрессии, FR-RG-3)."),
) -> None:
    """Отборочный тест Писателя (этап 8 жизненного цикла): пакет сравнения моделей на свежем брифе — то же,
    что `пере-тест`; ответы моделей кладутся файлами, сводка метрик Э1 и флагов Э2 — в папке пакета."""
    canon.retest(chapter=chapter, fix=fix)


@app.command("canon-commit", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_canon_commit(
    message: str = typer.Option(..., "--сообщение", "--message", "-m", help="Сообщение коммита (изменение норм — со ссылкой на запись журнала решений)."),
    yes: bool = _yes(),
) -> None:
    """Правка канона автором (сценарий Г): валидация структуры, перегенерация выгрузок, коммит."""
    canon.canon_commit(message, yes=yes, confirm=typer.confirm)


@app.command("library-split", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_library_split(
    target: str | None = typer.Option(None, "--в", "--to", help="Куда перенести (по умолчанию ../Библиотека рядом с рабочей областью)."),
    show: bool = typer.Option(False, "--показать", "--dry-run", help="Только план, ничего не менять."),
    with_history: bool = typer.Option(False, "--с-историей", "--with-history",
                                      help="Перенести историю папки в новый репозиторий (git subtree split)."),
    yes: bool = _yes(),
) -> None:
    """Вынести библиотеку канона в отдельный git-репозиторий рядом с рабочей областью (FR-BK-4):
    перенос папки, git init + первый коммит, library_dir в конфиг.yaml, .gitignore в прежнем репозитории."""
    canon.library_split(target=target, show=show, with_history=with_history, yes=yes, confirm=typer.confirm)


@app.command("backup", rich_help_panel="Канон и бэкап")
@_friendly
def cmd_backup(
    folder: str | None = typer.Argument(None, metavar="ПАПКА", help="Папка архива для --архив (по умолчанию backup_dir из конфиг.yaml, иначе ../архивы)."),
    push: bool = typer.Option(False, "--отправить", "--push", help="Отправить библиотеку во все удалённые места (после подтверждения)."),
    archive: bool = typer.Option(False, "--архив", "--archive", help="Zip рабочей области: проект.yaml, конфиг.yaml, главы/, журналы/, круги, снапшоты, рукопись, регрессия, пере-тест, онбординг, сырьё, переопределения проекта; библиотека — если не под git (выгрузки/ не входят: пересчитываются)."),
    add_remote: tuple[str, str] | None = typer.Option(
        None, "--добавить-remote", "--add-remote", metavar="ИМЯ URL|ПАПКА",
        help="Добавить удалённое место библиотеки: URL или локальная папка (внешний диск; создаётся как bare-репозиторий)."
    ),
    yes: bool = _yes(),
) -> None:
    """Сохранность (FR-BK-1…FR-BK-4): состояние копий; --отправить — во все remotes; --архив — zip рабочей области;
    --добавить-remote — второе место хранения (папка на внешнем диске = без облака, §1.3 ТЗ)."""
    canon.backup(folder, push=push, archive=archive, add_remote=add_remote, yes=yes, confirm=typer.confirm)


@app.command("init", rich_help_panel="Настройка проекта")
@_friendly
def cmd_init(
    demo: bool = typer.Option(False, "--демо", "--demo", help="Развернуть демо-библиотеку и золотые тесты — играбельный пример."),
    contradictions: bool = typer.Option(False, "--противоречия", "--contradictions",
                                        help="Вместе с --демо: внести в копию демо-канона заведомые противоречия — учебный набор для линтера."),
) -> None:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, .gitignore, папки (NFR-1); --демо --противоречия —
    демо-проект с заведомыми противоречиями для знакомства с линтером (NFR-9)."""
    setup.init(demo=demo, contradictions=contradictions)


@app.command("импорт", rich_help_panel="Онбординг")
@_friendly
def cmd_import(
    sources: list[str] = typer.Argument(..., metavar="ПУТЬ...", help="Файлы, папки или .zip с материалами автора (можно несколько)."),
) -> None:
    """Импортировать материалы в сырьё/: копии оригиналов, извлечения в Markdown, индекс (§5.1)."""
    try:
        onboarding_steps.import_materials(sources)
    except FileNotFoundError as e:
        _fail(str(e))


@app.command("импорт-прозы", rich_help_panel="Онбординг")
@_friendly
def cmd_import_prose(
    files: list[Path] = typer.Argument(..., metavar="ФАЙЛЫ", help="Готовые главы: .md/.txt как есть, .docx/.pdf/.rtf — через извлечение."),
    volume: int | None = typer.Option(None, "--том", "--volume", help="Том, к которому относятся главы (по умолчанию — текущий)."),
    start: int | None = typer.Option(None, "--с-главы", "--from-chapter", min=1,
                                     help="Нумеровать главы по порядку файлов начиная с N (иначе номер берётся из имени файла)."),
    no_calibrate: bool = typer.Option(False, "--без-калибровки", "--no-calibrate", help="Не предлагать коридоры норм по импортированной прозе."),
    no_commit: bool = typer.Option(False, "--без-коммита", "--no-commit", help="Записать главы без git-коммита."),
    yes: bool = _yes(),
) -> None:
    """Сценарий В «Серия, частично написанная»: готовая проза → в корпус (документы типа «проза» библиотеки, одной
    сессией записи в канон), нормы калибруются по ней, из неё предзаполняются словарь имён и заготовка континуити,
    спорные факты выносятся списком (онбординг/проза_*.md)."""
    onboarding_steps.import_prose([str(f) for f in files], volume=volume, start_chapter=start, yes=yes,
                                  confirm=typer.confirm, commit=not no_commit, calibrate=not no_calibrate)


@app.command("онбординг", rich_help_panel="Онбординг")
@_friendly
def cmd_onboarding(
    apply: bool = typer.Option(False, "--применить", "--apply", help="Применить решения: документы в библиотеку, манифест, коммит."),
    model: bool | None = typer.Option(None, "--модель/--без-модели", "--model/--no-model",
                                      help="Модельный слой (роль «архивариус»); по умолчанию — из конфига (onboarding_model_layer)."),
    decision: list[str] = typer.Option(None, "--решение", "--decision", "-р",
                                       help="Решение автора: файл=принять|тип:<имя>|сырьё|отклонить|разбить|склеить:<файл>|"
                                            "колонка:<поле>=<заголовок>|источник|канон."),
    answer: list[str] = typer.Option(None, "--ответ", help="Ответ Архивариуса, полученный вручную: файл_сырья=путь_к_ответу (FR-RL-3)."),
    yes: bool = _yes(),
    no_commit: bool = typer.Option(False, "--без-коммита", "--no-commit", help="Записать документы без git-коммита."),
) -> None:
    """Предложение «файл → тип» по сырью с предпросмотром разбора; с --применить — нормализация в библиотеку."""
    try:
        if apply:
            onboarding_steps.apply_decisions(decision)
            onboarding_steps.apply_onboarding(yes, confirm=lambda q: typer.confirm(q), commit=not no_commit)
        else:
            onboarding_steps.propose_types(use_model=model, decisions=decision, answers=answer)
    except (ValueError, KeyError, RuntimeError, PermissionError, FileNotFoundError) as e:
        _fail(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))


project_app = typer.Typer(
    help="Проект серии: создание папки, манифеста и стартового комплекта (этап 1 жизненного цикла).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="проект", rich_help_panel="Настройка проекта")


@project_app.command("создать")
@_friendly
def cmd_project_create(
    path: str | None = typer.Argument(None, metavar="ПАПКА", help="Папка нового проекта (пустая или несуществующая)."),
    name: str | None = typer.Option(None, "--имя", "--name", help="Название серии."),
    volumes: int | None = typer.Option(None, "--томов", "--volumes", help="Сколько томов в плане."),
    modules: str | None = typer.Option(None, "--модули", "--modules", help="Модули через запятую (пусто — умолчания)."),
    methodic: str | None = typer.Option(None, "--методика", "--methodic", help="Методика драматургии."),
    profile: str | None = typer.Option(None, "--профиль", "--profile", help="Профиль серии (имя из data/профили или папка)."),
    no_starter: bool = typer.Option(False, "--без-комплекта", "--no-starter", help="Не создавать стартовый комплект документов."),
    no_git: bool = typer.Option(False, "--без-git", "--no-git", help="Не инициализировать git в библиотеке."),
    yes: bool = typer.Option(False, "--да", "--yes", "-y", help="Без вопросов: умолчания для всего, что не задано."),
) -> None:
    """Создать проект серии: папки по раскладке, проект.yaml, стартовый комплект, git библиотеки."""
    try:
        setup.project_create(path, name, volumes, modules, methodic, profile, starter=not no_starter, git=not no_git,
                             yes=yes, prompt=lambda q, d: typer.prompt(q, default=d))
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        _fail(str(e))


@project_app.command("индекс")
@_friendly
def cmd_project_index(
    yes: bool = typer.Option(False, "--да", "-y", "--yes", help="Без вопроса."),
    no_commit: bool = typer.Option(False, "--без-коммита", help="Записать индекс, но не коммитить."),
) -> None:
    """Пересобрать индекс библиотеки из манифеста (FR-DM-3): после правки проект.yaml руками."""
    try:
        setup.project_index(yes, confirm=lambda q: typer.confirm(q), commit=not no_commit)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))


# ------------------------------------------------------------------ русские имена команд, латинские синонимы (FR-CL-5)

# основное имя — русское (видно в справке), латинское — скрытый синоним; команды, объявленные по-русски, получают
# латинский синоним из этой же таблицы
SYNONYMS = COMMAND_NAMES


def _register_synonyms(typer_app: typer.Typer) -> None:
    """Каждая команда доступна под русским и латинским именем: основное имя (в справке) — русское."""
    existing = {c.name for c in typer_app.registered_commands}
    for info in list(typer_app.registered_commands):
        other = SYNONYMS.get(info.name or "")
        if not other or other in existing:
            continue
        latin_primary = (info.name or "").isascii()
        alias = typer.models.CommandInfo(
            name=other, cls=info.cls, context_settings=info.context_settings, callback=info.callback, help=info.help,
            epilog=info.epilog, short_help=info.short_help, options_metavar=info.options_metavar,
            add_help_option=info.add_help_option, no_args_is_help=info.no_args_is_help,
            hidden=not latin_primary, deprecated=info.deprecated, rich_help_panel=info.rich_help_panel,
        )
        if latin_primary:
            info.hidden = True  # латинское имя остаётся синонимом, в справке — русское
        typer_app.registered_commands.append(alias)
        existing.add(other)


_register_synonyms(app)
_VOLUME_SYNONYMS = {"status": "статус", "close": "закрыть", "open": "открыть"}


def _register_group_synonyms(typer_app: typer.Typer, table: dict[str, str]) -> None:
    existing = {c.name for c in typer_app.registered_commands}
    for info in list(typer_app.registered_commands):
        other = table.get(info.name or "")
        if other and other not in existing:
            alias = typer.models.CommandInfo(name=other, callback=info.callback, help=info.help, hidden=False,
                                             rich_help_panel=info.rich_help_panel)
            info.hidden = True
            typer_app.registered_commands.append(alias)
            existing.add(other)


_register_group_synonyms(volume_app, _VOLUME_SYNONYMS)
app.add_typer(volume_app, name="том", rich_help_panel="Канон и бэкап", hidden=False)


def _russify(typer_app: typer.Typer) -> None:
    """Русская справка у всех команд и групп (в том числе у синонимов): класс команды с русской строкой
    использования и опцией справки, русский заполнитель опций."""
    def _plain(value):  # значение без обёртки Default(...) typer
        return value.value if isinstance(value, typer.models.DefaultPlaceholder) else value

    for info in typer_app.registered_commands:
        if _plain(info.cls) in (None, typer_core.TyperCommand):
            info.cls = Команда
        if _plain(info.options_metavar) == "[OPTIONS]":
            info.options_metavar = OPTIONS_METAVAR
    for group in typer_app.registered_groups:
        sub = group.typer_instance
        if sub is None:
            continue
        for holder in (group, sub.info):
            if _plain(holder.cls) in (None, typer_core.TyperGroup):
                holder.cls = Группа
            if _plain(holder.options_metavar) == "[OPTIONS]":
                holder.options_metavar = OPTIONS_METAVAR
            if _plain(holder.subcommand_metavar) is None:
                holder.subcommand_metavar = SUBCOMMAND_METAVAR
        _russify(sub)


_russify(app)


def main() -> None:  # точка входа для python -m konveyer.cli
    app()


if __name__ == "__main__":
    main()
