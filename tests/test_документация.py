"""Документация не расходится с кодом (NFR-10, 14.3.1): каждая команда и флаг из Запуск.md/README.md/CLAUDE.md
существуют в CLI, списки форматов, extras, шаблонов и содержимого архива совпадают с реализацией."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from typer.main import get_command

from konveyer import backup
from konveyer.cli import app
from konveyer.onboarding import extract

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "Запуск.md").read_text(encoding="utf-8")
DOCS = {name: (ROOT / name).read_text(encoding="utf-8") for name in ("Запуск.md", "README.md", "CLAUDE.md")}
CLI = get_command(app)


def _mentions() -> list[tuple[str, str]]:
    """Все `konveyer …` из обратных кавычек и блоков кода документации: (документ, текст команды)."""
    out = []
    for name, text in DOCS.items():
        for m in re.finditer(r"`(konveyer [^`\n]+)`", text):
            out.append((name, m.group(1)))
        for block in re.findall(r"```\n(.*?)```", text, re.S):
            for line in block.splitlines():
                line = line.split("#")[0].strip()
                for part in re.split(r"\s*(?:&&|\|\|)\s*", line):
                    if part.startswith("konveyer "):
                        out.append((name, part))
    return out


def _resolve(words: list[str]):
    """Команда (и подкоманда) typer по словам вызова; None — не команда (например, `konveyer[llm]`)."""
    cmd = CLI
    i = 1
    while i < len(words) and hasattr(cmd, "commands"):
        w = words[i]
        if w.startswith("-") or w not in cmd.commands:
            break
        cmd = cmd.commands[w]
        i += 1
    return cmd if cmd is not CLI else None, words[i:]


@pytest.mark.parametrize("doc,call", _mentions())
def test_команды_и_флаги_документации_существуют(doc, call):
    words = call.replace("…", "").split()
    if words[0] != "konveyer" or len(words) < 2 or words[1] in ("--help", "<команда>"):
        return
    cmd, rest = _resolve(words)
    assert cmd is not None, f"{doc}: команды «{words[1]}» нет в CLI: {call}"
    if hasattr(cmd, "commands") and rest and not rest[0].startswith("-") and rest[0] not in ("N", "<путь>"):
        pytest.fail(f"{doc}: у «{cmd.name}» нет подкоманды «{rest[0]}»: {call}")
    known = {opt for p in cmd.params for opt in getattr(p, "opts", []) + getattr(p, "secondary_opts", [])}
    for w in rest:
        if w.startswith("--"):
            flag = w.split("=")[0]
            assert flag in known, f"{doc}: у «{' '.join(words[1:len(words) - len(rest)])}» нет флага {flag}: {call}"


def test_форматы_импорта_в_руководстве():
    listed = set(re.findall(r"`(\.[a-z]+)`", GUIDE.split("## 3.")[1].split("## 4.")[0]))
    assert listed <= extract.SUPPORTED, listed - extract.SUPPORTED
    assert {".md", ".docx", ".pdf", ".xlsx", ".rtf", ".html", ".tsv"} <= listed


def test_extras_и_папка_главы_в_руководстве():
    extras = set(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"])
    for name in re.findall(r"konveyer\[([a-z,]+)\]", GUIDE):
        assert set(name.split(",")) <= extras, name
    from konveyer.paths import Workspace

    assert Workspace(ROOT).chapter_dir(5).name == "005" and "главы/005/" in GUIDE


def test_архив_шаблоны_и_ручной_режим_в_руководстве():
    section = GUIDE.split("## 8.")[1].split("## 9.")[0]
    for item in backup.ARCHIVE_ITEMS:
        assert item.replace(".yaml", "") in section, item
    templates = {p.name for p in (ROOT / "konveyer" / "шаблоны").iterdir()}
    for name in re.findall(r"`([а-яё0-9_]+(?:_система)?\.md(?:\.j2)?)`", GUIDE.split("## 11.")[1]):
        if name.endswith("_система.md") or name.endswith(".j2"):
            assert name in templates, name
    manual = GUIDE.split("## 9.")[1].split("## 10.")[0]
    for role in ("Писатель", "Верификатор-2", "Канонист", "Аналитик", "Линтер", "Архивариус"):
        assert role in manual, role
    for prompt in ("промпт_э2.md", "промпт_правок.md", "промпт_канониста.md", "промпт_вкуса.md"):
        assert prompt in manual, prompt
