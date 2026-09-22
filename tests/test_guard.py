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
