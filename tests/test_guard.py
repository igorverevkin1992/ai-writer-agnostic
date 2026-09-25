"""Критерий приёмки 3: ни один путь записи в библиотеку не обходит подтверждение автора."""

import pytest

from konveyer import guard


def test_запись_в_библиотеку_запрещена(ws, library):
    with pytest.raises(guard.CanonWriteError):
        guard.write_text(library / "31_Матрица_знаний.md", "взлом")
    with pytest.raises(guard.CanonWriteError):
        guard.append_text(library / "Проза" / "новая.md", "текст")


def test_запись_вне_библиотеки_разрешена(ws):
    guard.write_text(ws.root / "заметка.md", "ок")
    assert (ws.root / "заметка.md").read_text(encoding="utf-8") == "ок"


def test_сессия_канониста_открывает_запись(ws, library):
    with guard.canon_write_session():
        guard.write_text(library / "Проза" / "Том1_Глава99.md", "текст главы")
    assert (library / "Проза" / "Том1_Глава99.md").exists()
    # после выхода из сессии запись снова запрещена
    with pytest.raises(guard.CanonWriteError):
        guard.write_text(library / "Проза" / "Том1_Глава98.md", "текст")


def test_write_atomic_не_обходит_защиту(ws, library):
    """FR-SC-1: внутренняя запись guard тоже проверяет библиотеку — вне сессии записи она запрещена."""
    with pytest.raises(guard.CanonWriteError):
        guard._write_atomic(library / "Досье" / "взлом.md", "hack")
    assert not (library / "Досье" / "взлом.md").exists()
    assert not hasattr(guard, "write_atomic")


def test_симлинк_в_библиотеке_не_подменяется(ws, library):
    """FR-SC-1: ссылка внутри библиотеки на файл снаружи — всё равно библиотека: вне сессии запись запрещена,
    внутри сессии ссылка не заменяется обычным файлом; удаление записи библиотеки без сессии запрещено."""
    outside = ws.root / "снаружи.md"
    outside.write_text("x", encoding="utf-8")
    link = library / "ссылка.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("символьные ссылки недоступны")
    with pytest.raises(guard.CanonWriteError):
        guard.write_text(link, "подмена")
    with pytest.raises(guard.CanonWriteError):
        guard.remove(link)
    assert link.is_symlink() and outside.read_text(encoding="utf-8") == "x"
    with guard.canon_write_session(), pytest.raises(guard.CanonWriteError, match="ссылк"):
        guard.write_text(link, "подмена")
    assert link.is_symlink()
    # ссылка снаружи, ведущая внутрь библиотеки, — тоже библиотека
    door = ws.root / "дверь.md"
    door.symlink_to(library / "14_Мир.md")
    with pytest.raises(guard.CanonWriteError):
        guard.write_text(door, "подмена")


def test_сессия_записи_реентерабельна(ws, library):
    """Вложенная сессия не закрывает внешнюю; после выхода из внешней запись снова запрещена."""
    with guard.canon_write_session():
        with guard.canon_write_session():
            pass
        assert guard._allowed()
        guard.write_text(library / "Проза" / "Том1_Глава97.md", "текст")
    assert not guard._allowed()
    with pytest.raises(guard.CanonWriteError):
        guard.write_text(library / "Проза" / "Том1_Глава96.md", "текст")


def test_сообщение_ошибки_без_абсолютного_пути(ws, library):
    """FR-SC-9: в тексте запрета — путь относительно библиотеки, не путь машины автора."""
    with pytest.raises(guard.CanonWriteError) as e:
        guard.write_text(library / "Досье" / "новый.md", "x")
    assert str(library) not in str(e.value) and "библиотека/Досье/новый.md" in str(e.value)
