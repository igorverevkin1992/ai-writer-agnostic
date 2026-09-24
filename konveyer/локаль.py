"""Русские служебные надписи командной строки (NFR-7, П-8).

typer печатает заголовки справки, пометки параметров и ошибки разбора аргументов своими английскими строками.
Часть из них он берёт через `gettext` — `apply()` подменяет `gettext.gettext` словарём переводов и должна
выполниться ДО первого `import typer` (это делает `konveyer/__init__.py`). Остальное (подпись параметра
`--help`, тексты ошибок разбора) переводит `localize_typer()` после импорта typer — его вызывает `cli.py`."""

from __future__ import annotations

import gettext as _gettext
import re

_RU = {
    "Options": "Параметры", "Arguments": "Аргументы", "Commands": "Команды",
    "[required]": "[обязательно]", "required": "обязательно",
    "[default: {}]": "[по умолчанию: {}]", "default: {default}": "по умолчанию: {default}",
    "[env var: {}]": "[переменная окружения: {}]", "env var: {var}": "переменная окружения: {var}",
    "Error": "Ошибка", "Try [blue]'{command_path} {help_option}'[/] for help.": "Справка: [blue]'{command_path} {help_option}'[/].",
    "No such command {name!r}.": "Нет такой команды: {name!r}.", "Missing command.": "Не указана команда.",
    "Aborted!": "Прервано.", "Aborted.": "Прервано.", "(deprecated) ": "(устарело) ", "(dynamic)": "(вычисляется)",
}
_HELP_TEXT = "Показать эту справку и выйти."
_ERRORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Missing argument (.+?)\.?$"), r"Не указан аргумент \1."),
    (re.compile(r"^Missing option (.+?)\.?$"), r"Не указан параметр \1."),
    (re.compile(r"^Missing parameter: (.+)$"), r"Не указан параметр \1."),
    (re.compile(r"^Got unexpected extra argument(?:\(s\)|s)? \((.+)\)$"), r"Лишние аргументы: \1."),
    (re.compile(r"^No such option: (.+?)(?: \(Possible options: (.+)\))?$"), r"Нет такого параметра: \1 (возможно, вы имели в виду: \2)"),
    (re.compile(r"^Option (.+?) requires an argument\.?$"), r"Параметру \1 нужно значение."),
    (re.compile(r"^Option (.+?) requires (\d+) arguments\.?$"), r"Параметру \1 нужно значений: \2."),
    (re.compile(r"^Invalid value for (.+?): (.+)$"), r"Недопустимое значение для \1: \2"),
    (re.compile(r"^Invalid value: (.+)$"), r"Недопустимое значение: \1"),
    (re.compile(r"(.+?) is not a valid (?:int|integer)\.?$"), r"\1 — не целое число."),
    (re.compile(r"(.+?) is not a valid float\.?$"), r"\1 — не число."),
    (re.compile(r"(.+?) is not a valid boolean\.?$"), r"\1 — не да/нет."),
    (re.compile(r"(.+?) is not in the range (.+?)\.?$"), r"\1 вне диапазона \2."),
    (re.compile(r"(.+?) is not one of (.+?)\.?$"), r"\1 — допустимо только: \2."),
    (re.compile(r"(?:Path|File|Directory) (.+?) does not exist\.?$"), r"путь \1 не существует."),
    (re.compile(r"(?:Path|File|Directory) (.+?) is a directory\.?$"), r"\1 — папка, а нужен файл."),
    (re.compile(r"(?:Path|File|Directory) (.+?) is a file\.?$"), r"\1 — файл, а нужна папка."),
    (re.compile(r"(?:Path|File|Directory) (.+?) is not readable\.?$"), r"\1 недоступен для чтения."),
]

_original_gettext = _gettext.gettext


def _ru(message: str) -> str:
    return _RU.get(message, _original_gettext(message))


def apply() -> None:
    """Подменить gettext переводами (до импорта typer: его модули связывают `_` при импорте)."""
    if _gettext.gettext is not _ru:
        _gettext.gettext = _ru


def translate_error(message: str) -> str:
    """Текст ошибки разбора аргументов по-русски; неизвестные формулировки остаются как есть."""
    out = message
    for pat, rep in _ERRORS:
        if pat.search(out):
            out = pat.sub(rep, out)
    return re.sub(r" \(возможно, вы имели в виду: \)$", "", out)


def localize_typer() -> None:
    """Подпись параметра --help и тексты ошибок разбора — по-русски (вызывается после импорта typer)."""
    import typer.core
    import typer.main
    import typer.rich_utils

    # typer мог быть импортирован раньше пакета (например, тестами): подменяем `_` в его модулях и уже
    # вычисленные при импорте константы rich_utils
    for mod in (typer.core, typer.main, typer.rich_utils):
        if getattr(mod, "_", None) is not None:
            mod._ = _ru  # type: ignore[attr-defined]
    for const, english in (("DEPRECATED_STRING", "(deprecated) "), ("DEFAULT_STRING", "[default: {}]"),
                           ("ENVVAR_STRING", "[env var: {}]"), ("REQUIRED_LONG_STRING", "[required]"),
                           ("ARGUMENTS_PANEL_TITLE", "Arguments"), ("OPTIONS_PANEL_TITLE", "Options"),
                           ("COMMANDS_PANEL_TITLE", "Commands"), ("ERRORS_PANEL_TITLE", "Error"),
                           ("ABORTED_TEXT", "Aborted."), ("RICH_HELP", "Try [blue]'{command_path} {help_option}'[/] for help.")):
        if hasattr(typer.rich_utils, const):
            setattr(typer.rich_utils, const, _RU[english])

    for cls in (typer.core.TyperCommand, typer.core.TyperGroup):
        original = cls.get_help_option
        if getattr(original, "_konveyer_ru", False):
            continue

        def get_help_option(self, ctx, _original=original):
            opt = _original(self, ctx)
            if opt is not None:
                opt.help = _HELP_TEXT
            return opt

        get_help_option._konveyer_ru = True  # type: ignore[attr-defined]
        cls.get_help_option = get_help_option  # type: ignore[method-assign]

    fmt = typer.rich_utils.rich_format_error
    if getattr(fmt, "_konveyer_ru", False):
        return

    def rich_format_error(exc, _original=fmt):
        message = translate_error(exc.format_message())
        exc.format_message = lambda: message  # type: ignore[method-assign]
        return _original(exc)

    rich_format_error._konveyer_ru = True  # type: ignore[attr-defined]
    typer.rich_utils.rich_format_error = rich_format_error
