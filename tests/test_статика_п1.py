"""П-1 (статический тест): в движке нет констант серии. Имена персонажей, названия серий и реестров
эталона и демо-проекта могут жить только в данных (`konveyer/data/…`) и тестах, но не в коде, типах, модулях,
шаблонах и методиках движка."""

from pathlib import Path

KONVEYER = Path(__file__).resolve().parent.parent / "konveyer"
PANEL_SRC = KONVEYER.parent / "panel" / "src"
PANEL_BUILD = KONVEYER / "data" / "панель"

# имена и названия эталонной серии и демо-проекта: ни одно не должно встречаться в движке
SERIES_TOKENS = [
    "УГАР", "Лемм", "Штерн", "Степан", "Заварзин", "Бугаев", "ОГПУ", "Лубянк",
    "Каширин", "Гуляев", "Пронин", "Зоя", "Гаражи",
]
ENGINE_SUFFIXES = {".py", ".j2", ".md", ".yaml", ".json"}
# панель: сверх имён серии — имя провайдера моделей, номера документов и решений эталона, служебные префиксы
# файлов эталонной библиотеки (всё это приходит с сервера из данных проекта/конфига, а не из текста панели)
PANEL_TOKENS = SERIES_TOKENS + ["Anthropic", "Gemini", "21_Круги_истории", "Р-020", "Р-021", "ИНСТРУМЕНТ_", "Тест_Писателя",
                                "документ 2.1", "проверку 4.4", '"3.1"', '"3.2"', '"3.3"', '"1.2"']


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


def _panel_files() -> list[Path]:
    out = [p for p in PANEL_SRC.rglob("*") if p.is_file() and p.suffix in (".ts", ".tsx", ".css")]
    out += [p for p in PANEL_BUILD.rglob("*") if p.is_file() and p.suffix in (".js", ".html", ".css")]
    return out


def test_нет_серийных_констант_в_панели():
    """П-1 для панели: исходники panel/src и собранный бандл konveyer/data/панель без имён серии, провайдера,
    номеров документов и решений эталона — всё это панель получает от сервера."""
    files = _panel_files()
    assert any(p.suffix == ".tsx" for p in files) and any(p.suffix == ".js" for p in files)
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for tok in PANEL_TOKENS:
            pos = text.find(tok)
            if pos >= 0:
                offenders.append(f"{path.name}: «{tok}»: …{text[max(0, pos - 40):pos + 40]!r}…")
    assert not offenders, "константы серии в панели:\n" + "\n".join(offenders)


def test_серийные_маркеры_окна_живут_в_профиле():
    """Слова-маркеры поглавника эталона («цикл», «матрица №», «арка-парабола») объявлены типом профиля, не кодом."""
    from konveyer import compiler

    base = (compiler._TOOL_NOTE_BASE + compiler._READER_MARK_BASE + compiler._FUTURE_BASE).lower()
    for tok in ("цикл", "матриц", "парабол", "эхо"):
        assert tok not in base, tok
    profile = KONVEYER / "data" / "профили" / "угар" / "типы" / "маркеры_угар.yaml"
    assert "цикл" in profile.read_text(encoding="utf-8")
