"""Тесты компилятора окна (FR-C1…FR-C6)."""

from konveyer import compiler


def _window(ws, library, chapter=1):
    path, breakdown = compiler.compile_window(ws, library, chapter)
    return path.read_text(encoding="utf-8"), breakdown


def test_детерминированность(ws, library):
    w1, _ = _window(ws, library)
    w2, _ = _window(ws, library)
    assert w1 == w2  # FR-C4: байт-в-байт


def test_обязательные_секции_и_запреты(ws, library):
    w, breakdown = _window(ws, library)
    assert "СТРОГО ЗАПРЕЩЕНО" in w  # FR-C6
    for section in [
        "роль и запреты", "регистр и стиль", "фокализация", "персонажи сцены",
        "что знает фокал", "техзадание — закладки", "бриф", "формат выдачи",
    ]:
        assert section in breakdown, section


def test_фильтрация_матрицы(ws, library):
    """FR-C3: факты, недоступные фокалу, в окно не попадают."""
    w, _ = _window(ws, library, chapter=1)
    assert "M-001" in w                      # узнал в гл. 1
    assert "M-002" not in w                  # узнаёт только в гл. 4
    assert "сторож жив" not in w             # M-003: НЕ знает (резерв)
    assert "M-004" not in w                  # факт другого субъекта


def test_закладки_только_главы(ws, library):
    w1, _ = _window(ws, library, chapter=1)
    assert "P-001" in w1 and "P-002" not in w1  # FR-C2
    w5, _ = _window(ws, library, chapter=5)
    assert "P-002" in w5 and "P-001" not in w5


def test_стоп_листы_участников_и_года(ws, library):
    w, _ = _window(ws, library, chapter=1)
    assert "Л-1" in w and "Л-2" in w and "Л-3" in w   # линии участников + общие
    assert "Э-1" in w                                  # 1995 < 1999
    assert "смартфон" in w                             # запрет включён в окно


def test_информрежим_как_не_упоминать(ws, library):
    w, _ = _window(ws, library, chapter=1)
    assert "НЕ упоминать (информрежим B-001)" in w  # FR-C3


def test_превышение_лимита_окна(ws, library):
    """FR-C5/Д-12: флаг с раскладкой по секциям."""
    compiler.compile_window(ws, library, 1, soft_limit_chars=100)
    flag = ws.chapter_dir(1) / "window_size_флаг.md"
    assert flag.exists()
    assert "раскладка" in flag.read_text(encoding="utf-8").lower()
