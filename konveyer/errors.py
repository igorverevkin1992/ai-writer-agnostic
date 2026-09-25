"""Исключения ядра шагов: ядро (`konveyer/steps/*`) не знает typer — оно бросает обычные
исключения, а интерфейсы (`cli.py`, `server.py`) переводят их в код возврата и статус задачи.

Соответствие прежним кодам typer:

* `StepError` — ожидаемая ошибка шага: «ОШИБКА: …», код 1 (прежний `_fail`);
* `ManualMode` — API недоступен: «⚠ причина» + «Ручной режим: подсказка», код 2 (прежний `_manual`);
  это тот же класс, что `adapters.ManualModeNeeded`;
* `Rejected` — автор не подтвердил действие: без сообщения, код 0 (прежний `typer.Exit()`);
  с `abort=True` — «Отменено автором.», код 1 (прежний `typer.Abort()`);
* `StepExit` — шаг сам всё напечатал и просит код возврата (брак Э1 после авто-повторов — 1,
  круги без API — 2; прежний `typer.Exit(code=…)` после сообщения).
"""

from __future__ import annotations

from pathlib import Path


class StepError(RuntimeError):
    """Ожидаемая ошибка шага: читаемое сообщение вместо трейсбека, код возврата 1."""

    code: int = 1


class ManualMode(StepError):
    """API недоступен — конвейер деградирует в ручной режим (§1.3, NFR-4); код возврата 2."""

    code = 2

    def __init__(self, reason: str, hint: str):
        super().__init__(f"{reason}\n\nРучной режим: {hint}")
        self.reason = reason
        self.hint = hint


class Rejected(StepError):
    """Автор не подтвердил действие (Д-17): ничего не печатается; код 0, с `abort` — 1 («Отменено автором.»)."""

    def __init__(self, message: str = "", *, abort: bool = False):
        super().__init__(message)
        self.abort = abort
        self.code = 1 if abort else 0


class StepExit(StepError):
    """Шаг завершён с кодом `code`, сообщение уже напечатано самим шагом."""

    def __init__(self, code: int):
        super().__init__("")
        self.code = code


def describe(e: StepError) -> str:
    """Текст, который интерфейс показывает автору (без цвета); пустая строка — показывать нечего."""
    if isinstance(e, ManualMode):
        return f"⚠ {e.reason}\nРучной режим: {e.hint}"
    if isinstance(e, (Rejected, StepExit)):
        return ""
    return f"ОШИБКА: {e}"


def hide_paths(message: str, roots: list[tuple[Path | None, str]]) -> str:
    """Сообщение без абсолютных путей машины автора (FR-SC-9): каждый корень (библиотека, рабочая область)
    заменяется словом. Порядок — от длинного к короткому, чтобы вложенный путь библиотеки не превратился
    в «рабочая область/Библиотека»; Windows-варианты с удвоенными «\\» (repr исключения) тоже заменяются."""
    pairs: list[tuple[str, str]] = []
    for root, word in roots:
        if root is None:
            continue
        root = Path(root)
        forms = {str(root), root.as_posix()}
        try:
            forms |= {str(root.resolve()), root.resolve().as_posix()}
        except OSError:
            pass
        forms |= {v.replace("\\", "\\\\") for v in forms if "\\" in v}
        pairs += [(v, word) for v in forms if v and v not in ("/", ".")]
    for variant, word in sorted(pairs, key=lambda p: -len(p[0])):
        message = message.replace(variant + "/", word + "/").replace(variant + "\\", word + "/").replace(variant, word)
    return message
