"""Служебные надписи командной строки — по-русски (NFR-7, П-8): заголовки справки, пометки параметров,
подпись --help и ошибки разбора аргументов; автодополнение оболочки с английской справкой отключено."""

import re

from typer.testing import CliRunner

from konveyer import локаль
from konveyer.cli import app

runner = CliRunner()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_справка_без_английских_надписей():
    out = _plain(runner.invoke(app, ["--help"]).output)
    for english in ("Options", "Commands", "Show this message", "Install completion", "--install-completion"):
        assert english not in out, english
    assert "Параметры" in out and "Показать эту справку и выйти." in out
    out = _plain(runner.invoke(app, ["том", "--help"]).output)
    assert "Команды" in out and "Commands" not in out
    out = _plain(runner.invoke(app, ["собрать", "--help"]).output)
    assert "Аргументы" in out and "[обязательно]" in out and "[required]" not in out and "Arguments" not in out
    out = _plain(runner.invoke(app, ["панель", "--help"]).output)
    assert "[по умолчанию: 8765]" in out and "[default:" not in out


def test_ошибки_разбора_аргументов_по_русски():
    r = runner.invoke(app, ["собрать"])
    out = _plain(r.output + (r.stderr or ""))
    assert r.exit_code != 0 and "Не указан аргумент" in out and "Missing argument" not in out and "Справка:" in out
    r = runner.invoke(app, ["собрать", "abc"])
    out = _plain(r.output + (r.stderr or ""))
    assert "Недопустимое значение" in out and "не целое число" in out and "Invalid value" not in out
    r = runner.invoke(app, ["нет-такой-команды"])
    out = _plain(r.output + (r.stderr or ""))
    assert "Нет такой команды" in out and "No such command" not in out
    r = runner.invoke(app, ["собрать", "1", "2"])
    out = _plain(r.output + (r.stderr or ""))
    assert "Лишние аргументы" in out and "unexpected extra" not in out


def test_перевод_ошибок_словарём():
    assert локаль.translate_error("Missing argument 'CHAPTER'.") == "Не указан аргумент 'CHAPTER'."
    assert локаль.translate_error("Invalid value for 'CHAPTER': 'x' is not a valid integer.") == \
        "Недопустимое значение для 'CHAPTER': 'x' — не целое число."
    assert локаль.translate_error("No such option: --foo") == "Нет такого параметра: --foo"
    assert локаль.translate_error("что-то своё") == "что-то своё"
