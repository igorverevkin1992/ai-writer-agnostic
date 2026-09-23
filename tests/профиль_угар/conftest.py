"""Тесты профиля эталона (УГАР): выполняются, когда доступна библиотека эталона (переменная окружения
KONVEYER_ЭТАЛОН) или на синтетических документах формата эталона через плагин-парсер профиля."""

import pytest

pytestmark = pytest.mark.skip(reason="тесты профиля эталона переносятся на плагин-парсер (этап 7)")


def pytest_collection_modifyitems(items):
    for item in items:
        if "профиль_угар" in str(item.fspath):
            item.add_marker(pytest.mark.skip(reason="профиль эталона: перенос на плагин (этап 7)"))
