"""П-1 / FR-SC-11 (статический тест): в движке нет констант серии. Имена персонажей, названия серий, имена
документов канона, маркеры тайн и номера решений эталона могут жить только в данных проекта или профиля
(`konveyer/data/демо`, `konveyer/data/профили`) и в тестах, но не в коде, типах, модулях, методиках, шаблонах,
языковом слое, исходниках панели и её собранном бандле."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KONVEYER = REPO / "konveyer"
PANEL_SRC = REPO / "panel" / "src"
PANEL_BUNDLE = KONVEYER / "data" / "панель"
PROFILE = KONVEYER / "data" / "профили" / "угар"

# имена и названия эталонной серии и демо-проекта: ни одно не должно встречаться в движке
SERIES_TOKENS = [
    "УГАР", "Лемм", "Штерн", "Степан", "Заварзин", "Бугаев", "ОГПУ", "Лубянк", "Ася ", "Асе ", "Мередит", "Ковров",
    "Ремез", "Клюев", "Холодов", "Континенталь", "Сретенк",
    "Каширин", "Гуляев", "Пронин", "Зоя", "Гаражи",
    # имена документов и реестров эталона
    "Поглавник", "Реестр_информационного", "информационного режима", "Эпистемическая_матрица", "Континуити_трекер",
    "Генеральная_хронология", "Стилевой_регламент", "Правила_фокализации", "Языковой_канон", "Хроника_1926",
    "Концепция_цикла", "Тест_Писателя",
    # маркеры тайн эталона
    "сын Лемма", "Подлог 1913", "третья рука",
]
# номера решений журнала эталона (Р-015, Р-020…); Р-001 — первое решение любого проекта (стартовый комплект)
ETALON_DECISION_RE = re.compile(r"Р-0(?!01\b)\d\d\b")
ENGINE_SUFFIXES = {".py", ".j2", ".md", ".yaml", ".yml", ".json", ".txt"}
PANEL_SUFFIXES = {".ts", ".tsx", ".css", ".html", ".js"}


def _engine_files() -> list[Path]:
    out = []
    for p in KONVEYER.rglob("*"):
        if not p.is_file() or p.suffix not in ENGINE_SUFFIXES or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(KONVEYER).parts
        if rel[:2] in (("data", "демо"), ("data", "профили")):
            continue  # данные серий — допустимые исключения (FR-SC-11)
        out.append(p)
    return out


def _panel_sources() -> list[Path]:
    return [p for p in PANEL_SRC.rglob("*") if p.is_file() and p.suffix in PANEL_SUFFIXES]


def _bundle_files() -> list[Path]:
    return [p for p in PANEL_BUNDLE.rglob("*") if p.is_file() and p.suffix in PANEL_SUFFIXES]


def _offenders(files: list[Path], base: Path) -> list[str]:
    out = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), start=1):
            hits = [tok for tok in SERIES_TOKENS if tok in line] + ETALON_DECISION_RE.findall(line)
            for tok in hits:
                pos = line.find(tok.strip())
                out.append(f"{path.relative_to(base)}:{i}: «{tok.strip()}»: …{line[max(0, pos - 40):pos + 60].strip()}…")
    return out


def test_нет_серийных_констант():
    offenders = _offenders(_engine_files(), KONVEYER)
    assert not offenders, "константы серии в движке:\n" + "\n".join(offenders)


def test_нет_серийных_констант_в_исходниках_панели():
    assert _panel_sources(), "исходники панели не найдены"
    offenders = _offenders(_panel_sources(), PANEL_SRC)
    assert not offenders, "константы серии в панели:\n" + "\n".join(offenders)


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _panel_sources_newer_than_bundle() -> bool:
    """Исходники панели изменены после сборки: правки не закоммичены или закоммичены позже бандла."""
    if _git("status", "--porcelain", "--", str(PANEL_SRC.relative_to(REPO))):
        return True
    src = _git("log", "-1", "--format=%ct", "--", str(PANEL_SRC.relative_to(REPO)))
    bundle = _git("log", "-1", "--format=%ct", "--", str(PANEL_BUNDLE.relative_to(REPO)))
    return src.isdigit() and bundle.isdigit() and int(src) > int(bundle)


def test_нет_серийных_констант_в_бандле_панели():
    """Собранная панель поставляется автору любой серии. Проверяется актуальный бандл: если исходники панели
    изменены после сборки, бандл устарел — его пересобирает `cd panel && npm run build` (CI это требует)."""
    if _panel_sources_newer_than_bundle():
        pytest.skip("бандл панели старше исходников panel/src — пересоберите панель (npm run build)")
    offenders = _offenders(_bundle_files(), PANEL_BUNDLE)
    assert not offenders, "константы серии в бандле панели:\n" + "\n".join(offenders)


def test_серийные_маркеры_окна_живут_в_профиле():
    """Слова-маркеры поглавника эталона («цикл», «матрица №», «арка-парабола») объявлены типом профиля, не кодом."""
    from konveyer import compiler

    base = (compiler._TOOL_NOTE_BASE + compiler._READER_MARK_BASE + compiler._FUTURE_BASE).lower()
    for tok in ("цикл", "матриц", "парабол", "эхо"):
        assert tok not in base, tok
    assert "цикл" in (PROFILE / "типы" / "маркеры_угар.yaml").read_text(encoding="utf-8")


def test_имена_документов_эталона_только_в_профиле():
    """Сигнатуры классификации с именами документов эталона лежат в типах профиля, а базовый каталог их не знает."""
    profile_text = "\n".join(p.read_text(encoding="utf-8") for p in (PROFILE / "типы").glob("*.yaml"))
    assert "Поглавник" in profile_text or "информационного режима" in profile_text
