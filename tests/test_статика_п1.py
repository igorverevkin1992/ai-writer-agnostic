"""П-1 (статический тест): в движке нет констант серии. Имена персонажей, названия серий и реестров
эталона и демо-проекта могут жить только в данных (`konveyer/data/…`) и тестах, но не в коде, типах, модулях,
шаблонах и методиках движка."""

import re
from pathlib import Path

KONVEYER = Path(__file__).resolve().parent.parent / "konveyer"
PANEL_SRC = KONVEYER.parent / "panel" / "src"
PANEL_BUILD = KONVEYER / "data" / "панель"

# имена и названия эталонной серии и демо-проекта: ни одно не должно встречаться в движке
SERIES_TOKENS = [
    "УГАР", "Лемм", "Штерн", "Степан", "Заварзин", "Бугаев", "ОГПУ", "Лубянк",
    "Каширин", "Гуляев", "Пронин", "Зоя", "Гаражи",
    # номера документов эталона как области стоп-правил и служебные префиксы файлов эталона
    '"0.3"', '"0.4"', "ИНСТРУМЕНТ_", "Тест_Писателя",
]
ENGINE_SUFFIXES = {".py", ".j2", ".md", ".yaml", ".json"}
# панель: сверх имён серии — имя провайдера моделей, номера документов и решений эталона, служебные префиксы
# файлов эталонной библиотеки (всё это приходит с сервера из данных проекта/конфига, а не из текста панели)
PANEL_TOKENS = SERIES_TOKENS + ["Anthropic", "Gemini", "21_Круги_истории", "Р-020", "Р-021", "ИНСТРУМЕНТ_", "Тест_Писателя",
                                "документ 2.1", "проверку 4.4", '"3.1"', '"3.2"', '"3.3"', '"1.2"']

# Имена документов канона, ссылки на разделы и номера решений конкретной библиотеки (FR-SC-11): в коде движка
# (.py, .j2) их быть не может — только в каталоге типов (`имя_по_умолчанию`, формат ссылки журнала), профиле
# и проекте. «02 §5», «36_Журнал», «Р-020» — нумерация эталонной серии, а не движка.
SERIES_PATTERNS = {
    "имя документа вида NN_Имя": re.compile(r"(?<![\w.])\d{2}_[А-ЯЁ]"),
    "ссылка на раздел документа вида NN §N": re.compile(r"(?<![\w.])\d{2} §\s*\d"),
    "номер решения журнала вида Р-NNN / Р-№": re.compile(r"(?<![\w-])Р-(?:\d{2,3}|№)(?![\w-])"),
}
CODE_SUFFIXES = {".py", ".j2"}


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


def test_нет_имён_документов_и_номеров_решений_в_коде():
    """FR-SC-11: имена файлов документов, ссылки «NN §N» и номера решений — только в данных, не в коде."""
    offenders = []
    for path in _engine_files():
        if path.suffix not in CODE_SUFFIXES:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for what, rx in SERIES_PATTERNS.items():
                if rx.search(line):
                    offenders.append(f"{path.relative_to(KONVEYER)}:{i}: {what}: {line.strip()[:100]}")
    assert not offenders, "константы библиотеки-эталона в коде движка:\n" + "\n".join(offenders)
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


# зона данных (разбор, экспорт, схемы): ссылки на документы и решения эталонной серии («Р-016», «02 §5», «реестр 3.5»,
# «ИНСТРУМЕНТ_…») в коде недопустимы — откуда взята запись, говорят поля `file`/`source` из спецификации типа
DATA_ZONE = ["exporter.py", "declparse.py", "mdparse.py", "names.py", "schemas.py", "textutils.py", "catalog.py"]
ETALON_REF_PATTERNS = [r"Р-0\d\d", r"\b0[2-9] §", r"реестр \d\.\d", r"снапшот 3\.5", r"ИНСТРУМЕНТ_", r"36_Журнал",
                       r"FR-X\d", r"FR-K\d", r"FR-C\d"]


def test_зона_данных_без_ссылок_на_эталон():
    import re

    offenders = []
    for name in DATA_ZONE:
        text = (KONVEYER / name).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            for pat in ETALON_REF_PATTERNS:
                if re.search(pat, line):
                    offenders.append(f"{name}:{i}: /{pat}/: {line.strip()[:90]}")
    assert not offenders, "ссылки на эталон в зоне данных:\n" + "\n".join(offenders)


def test_номера_документов_эталона_не_в_движке():
    """«02 §5», «03 §…» — нумерация библиотеки эталона; в движке (в том числе в сообщениях автору) её нет."""
    import re

    offenders = [f"{p.relative_to(KONVEYER)}:{i}" for p in _engine_files()
                 for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
                 if re.search(r"\b0[2-9] §", line)]
    assert not offenders, offenders


def test_умолчания_схем_без_констант_серии():
    from konveyer.schemas import SCOPE_ALL, SCOPE_NARRATOR, Norm, StopRule

    assert Norm().source == ""
    assert StopRule(rule_id="x", items=[]).scope == SCOPE_NARRATOR and SCOPE_NARRATOR != SCOPE_ALL
    assert not any(ch.isdigit() for ch in SCOPE_NARRATOR + SCOPE_ALL)

