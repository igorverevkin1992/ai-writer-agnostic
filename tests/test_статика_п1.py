"""П-1 (статический тест): в движке нет констант серии. Имена персонажей, названия серий и реестров
эталона и демо-проекта могут жить только в данных (`konveyer/data/…`) и тестах, но не в коде, типах, модулях,
шаблонах и методиках движка."""

from pathlib import Path

KONVEYER = Path(__file__).resolve().parent.parent / "konveyer"

# имена и названия эталонной серии и демо-проекта: ни одно не должно встречаться в движке
SERIES_TOKENS = [
    "УГАР", "Лемм", "Штерн", "Степан", "Заварзин", "Бугаев", "ОГПУ", "Лубянк",
    "Каширин", "Гуляев", "Пронин", "Зоя", "Гаражи",
]
ENGINE_SUFFIXES = {".py", ".j2", ".md", ".yaml", ".json"}


def _engine_files() -> list[Path]:
    out = []
    for p in KONVEYER.rglob("*"):
        if not p.is_file() or p.suffix not in ENGINE_SUFFIXES:
            continue
        if "data" in p.relative_to(KONVEYER).parts or "__pycache__" in p.parts:
            continue
        out.append(p)
    return out


def test_нет_серийных_констант():
    offenders = []
    for path in _engine_files():
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            for tok in SERIES_TOKENS:
                if tok in line:
                    offenders.append(f"{path.relative_to(KONVEYER)}:{i}: «{tok}»: {line.strip()[:90]}")
    assert not offenders, "константы серии в движке:\n" + "\n".join(offenders)


def test_серийные_маркеры_окна_живут_в_профиле():
    """Слова-маркеры поглавника эталона («цикл», «матрица №», «арка-парабола») объявлены типом профиля, не кодом."""
    from konveyer import compiler

    base = (compiler._TOOL_NOTE_BASE + compiler._READER_MARK_BASE + compiler._FUTURE_BASE).lower()
    for tok in ("цикл", "матриц", "парабол", "эхо"):
        assert tok not in base, tok
    profile = KONVEYER / "data" / "профили" / "угар" / "типы" / "маркеры_угар.yaml"
    assert "цикл" in profile.read_text(encoding="utf-8")
