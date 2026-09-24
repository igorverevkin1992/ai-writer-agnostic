"""Этап 5 (7.11 ТЗ): линтер канона по модулям — нулевой шум на демо, каждый класс проверок ловится на испорченной
копии и ничего лишнего, выключенный модуль молчит, кэш по отпечатку, модельный слой ограничен."""

from __future__ import annotations

from pathlib import Path

import pytest

from konveyer import catalog, guard, lint, manifest as manifest_mod
from konveyer.config import Config, ModelConfig
from konveyer.steps import setup

PLAN = "23_Поглавник_Том1.md"
MATRIX = "31_Матрица_знаний.md"


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


def test_линтер_чистый_канон_молчит(ws, library):
    report = lint.run_lint(library, ws.exports, ws.logs)
    assert report.errors == 0 and report.warnings == 0 and report.notes == 0, [f.message for f in report.findings]
    # каждая проверка объявлена модулем или типом ровно теми кодами, что реализованы (FR-LT-1)
    impl = {c for codes, _ in lint.CHECKS for c in codes}
    declared = catalog.all_lint_codes(catalog.load_modules(None)) - {"РАЗМ-1", "ЛИНТ-0", "МОДЕЛЬ"}
    assert declared <= impl and impl <= declared | {"АКТ-1", "ЧАСТЬ-1"}, (declared - impl, impl - declared)


# по одному внедрённому противоречию на класс проверок (FR-LT-2): ожидаемый код ловится, лишнего нет.
# Список — данные демо (`противоречия.yaml`, NFR-9): тот же, что вносит `konveyer init --демо --противоречия`
CASES = {f"{c['код']} {c['что']}": c for c in setup.demo_contradictions()}


@pytest.mark.parametrize("case", list(CASES))
def test_линтер_каждый_класс_ловится(ws, library, case):
    item = CASES[case]
    code = item["код"]
    baseline = _codes(lint.run_lint(library, ws.exports, ws.logs))
    with guard.canon_write_session():
        setup.apply_contradiction(ws.root, library, item)
    report = lint.run_lint(library, ws.exports, ws.logs, use_cache=False)
    codes = _codes(report)
    assert code in codes, [f.message for f in report.findings]
    # следствия внедрённого противоречия в смежных реестрах (перекрёстные ссылки) — не шум
    extra = codes - baseline - {code} - set(item.get("следствия") or [])
    assert not extra, f"лишние находки: {[f.message for f in report.findings if f.code in extra]}"
    for f in report.findings:
        if f.code == code:
            assert f.file and "Что сделать" in f.message  # файл и подсказка (FR-LT-1, FR-LT-6)


def test_линтер_все_противоречия_демо_разом(ws, library):
    """NFR-9: учебный набор противоречий вносится целиком (`init --демо --противоречия`) и ловится весь."""
    with guard.canon_write_session():
        codes = setup.apply_contradictions(ws.root, library)
    found = _codes(lint.run_lint(library, ws.exports, ws.logs, use_cache=False))
    assert set(codes) <= found, sorted(set(codes) - found)


def test_линтер_выключенный_модуль_молчит(ws, library):
    _edit(library / "32_Реестр_закладок.md", "| P-002 | царапины на замке гаража №14 | т1 гл5 | т1 гл6; т2 |",
          "| P-002 | царапины на замке гаража №14 | т1 гл5 | т1 гл2; т2 |")
    assert "ЗАКЛ-2" in _codes(lint.run_lint(library, ws.exports, ws.logs, use_cache=False))
    man = manifest_mod.load(ws.root)
    man.модули["закладки"] = "выкл"
    manifest_mod.save(ws.root, man)
    report = lint.run_lint(library, ws.exports, ws.logs, use_cache=False)
    assert "ЗАКЛ-2" not in _codes(report) and not any(c.startswith("ЗАКЛ") for c in _codes(report))
    assert report.errors == 0


def test_линтер_кэш_по_отпечатку(ws, library):
    r1 = lint.run_lint(library, ws.exports, ws.logs)
    r2 = lint.run_lint(library, ws.exports, ws.logs)
    assert r2.ts == r1.ts and r2.fingerprint == r1.fingerprint  # тот же канон — прежний отчёт
    _edit(library / PLAN, "- Дата: 12 июня 1995", "- Дата: 12 июля 1995")
    r3 = lint.run_lint(library, ws.exports, ws.logs)
    assert r3.fingerprint != r1.fingerprint and "ХРОН-2" in _codes(r3)
    r4 = lint.run_lint(library, ws.exports, ws.logs, use_cache=False)
    assert r4.ts != r3.ts and _codes(r4) == _codes(r3)


def test_линтер_модельный_слой_ограничен(ws, library, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    docs = [library / PLAN, library / MATRIX, library / "36_Журнал_решений.md"]
    with pytest.raises(ValueError, match="лимит"):
        lint.run_lint_llm(ws, Config(), library, docs, max_calls=2)
    cfg = Config(canonist=ModelConfig(provider="anthropic", model="м", price_in_per_1m=1000.0, price_out_per_1m=1000.0))
    with pytest.raises(ValueError, match="бюджета"):
        lint.run_lint_llm(ws, cfg, library, docs, max_cost_usd=0.01)
    findings, prompts = lint.run_lint_llm(ws, Config(), library, docs, max_calls=5, max_cost_usd=100.0)
    assert not findings and len(prompts) == 3  # без ключа — промпты для ручного прогона


def test_линтер_правка_только_через_сессию_записи(ws, library):
    from konveyer import guard
    from konveyer.schemas import LintFix

    fix = LintFix(file=PLAN, line=5, old="- Дата: 12 июня 1995", new="- Дата: 13 июня 1995")
    with pytest.raises(PermissionError):
        lint.apply_fix(library, fix)
    with guard.canon_write_session():
        lint.apply_fix(library, fix)
    assert "13 июня 1995" in (library / PLAN).read_text(encoding="utf-8")
