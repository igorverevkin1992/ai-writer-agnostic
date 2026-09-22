"""Калибровочные юнит-тесты счётчиков (10.1): точность значений, не PASS/FAIL.

Эталон — макет Том1_Глава04_МАКЕТ.md с заранее известными значениями
(в реальной библиотеке подставляются замеренные значения реального макета).
"""

from importlib import resources
from pathlib import Path

from konveyer import textutils

MAKET = Path(
    str(resources.files("konveyer").joinpath("data/демо/Библиотека/Проза/Том1_Глава04_МАКЕТ.md"))
)


def test_калибровка_макета():
    text = textutils.narrator_text(MAKET.read_text(encoding="utf-8"))
    sentences = textutils.split_sentences(text)
    lengths = [len(textutils.words(s)) for s in sentences]
    assert len(sentences) == 10
    assert sum(lengths) == 55
    assert sum(lengths) / len(lengths) == 5.5  # средняя длина фразы
    short = sum(1 for x in lengths if x <= 6)
    assert short == 7 and short / len(lengths) == 0.7  # доля ≤6 слов
    tokens = textutils.normalize(text)
    assert sum(1 for t in tokens if t in {"был", "было", "были", "была"}) == 2  # «был» = 2


def test_документ_вставка_исключается():
    """Д-7: документ-вставка не попадает в метрики повествователя."""
    raw = MAKET.read_text(encoding="utf-8")
    assert "Справка" in raw
    assert "Справка" not in textutils.narrator_text(raw)


def test_сокращения_не_рвут_предложения():
    text = "Он жил на ул. Ленина против депо. Там было тихо."
    assert len(textutils.split_sentences(text)) == 2


def test_прямая_речь_и_многоточие():
    text = "— Ты видел? — спросила Зоя. Он кивнул… Потом отвернулся."
    sents = textutils.split_sentences(text)
    assert len(sents) == 4


def test_нграммы_и_ttr():
    tokens = textutils.normalize("Ветер гнал по перрону обрывки газет")
    assert tokens == ["ветер", "гнал", "по", "перрону", "обрывки", "газет"]
    assert list(textutils.ngrams(tokens, 5)) == [
        ("ветер", "гнал", "по", "перрону", "обрывки"),
        ("гнал", "по", "перрону", "обрывки", "газет"),
    ]
    assert textutils.ttr(["а", "б", "а", "в"]) == 0.75
