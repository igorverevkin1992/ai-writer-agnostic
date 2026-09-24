"""Имена и перечисления глав (`konveyer.names`): склонения, ложные срабатывания, диапазоны, детерминизм (П-6)."""

from __future__ import annotations

from konveyer import names


def test_диапазоны_глав_в_любом_регистре_и_словом():
    assert names.chapter_range("Гл. 1–3") == (1, 3)
    assert names.chapter_range("главы 1–3") == (1, 3)
    assert names.chapter_range("Глава 5") == (5, 5)
    assert names.chapter_range("гл. 1, 3") == (1, 3)
    assert names.chapter_range("сц. 5.1") == (None, None)


def test_перечисление_глав_раскрывает_диапазон():
    assert names.chapters_listed("гл. 5–7") == [5, 6, 7]
    assert names.chapters_listed("Гл. 9, 27") == [9, 27]
    assert names.chapters_listed("гл. 29 или 40") == [29, 40]
    assert names.chapters_listed("[Читатель: гл.31–32]") == [31, 32]


def test_имена_на_мягкий_знак_и_й_в_косвенных_падежах():
    assert names.find_names("Игорь пришёл", {"Игорь"}) == ["Игорь"]
    assert names.find_names("Он позвал Андрея", {"Андрей"}) == ["Андрей"]
    assert names.find_names("с Сергеем", {"Сергей"}) == ["Сергей"]
    assert names.find_names("у Дмитрия", {"Дмитрий"}) == ["Дмитрий"]
    assert names.find_names("к Любови", {"Любовь"}) == ["Любовь"]
    assert names.find_names("Игорем", {"Игорь"}) == ["Игорь"]


def test_короткие_основы_не_ловят_обычные_слова():
    assert names.find_names("Он склонился над столом", {"Надя"}) == []
    assert names.find_names("любой человек", {"Люба"}) == []
    assert names.find_names("я верю в это", {"Вера"}) == []
    assert names.find_names("в аду", {"Ада"}) == []
    assert names.find_names("Иванов пришёл", {"Иван"}) == []
    # а сами имена — во всех падежах
    assert names.find_names("Надя и Вера пришли к Любе", {"Надя", "Вера", "Люба"}) == ["Вера", "Люба", "Надя"]
    assert names.find_names("Он подошёл к Наде", {"Надя"}) == ["Надя"]


def test_имя_в_прозе_требует_заглавной_буквы():
    assert names.find_names("верёвка", {"Вера"}) == []
    assert names.find_names("Вера", {"Вера"}) == ["Вера"]
    # имя-роль из нескольких слов находится и со строчной («к куратору отдела»)
    assert names.find_names("пошёл к куратору отдела", {"Куратор отдела"}) == ["Куратор отдела"]


def test_нормализация_имени():
    known = {"Иванов", "Куратор отдела", "Зоя"}
    assert names.normalize_name("Иванова", known) == "Иванов"
    assert names.normalize_name("куратор", known) == "Куратор отдела"
    assert names.normalize_name("Зои", known) == "Зоя"
    # неизвестное имя возвращается целиком, а не первым словом
    assert names.normalize_name("Мария Петровна", known) == "Мария Петровна"
    assert names.normalize_name("", known) == ""


def test_нормализация_детерминирована_при_равной_длине():
    """П-6: два имени одинаковой длины, оба подходящих, — побеждает первое по алфавиту, не по порядку множества."""
    for _ in range(20):
        assert names.normalize_name("Зой", {"Зоя", "Зой"}) == "Зой"
        assert names.normalize_name("Зоя", {"Зой", "Зоя"}) == "Зоя"  # точное совпадение — прежде основ
        assert names.normalize_name("Зое", {"Зоя", "Зой"}) == "Зой"  # обе основы подходят: первое по алфавиту


def test_участники_действия():
    known = {"Каширин", "Зоя", "Читатель"}
    acting = names.find_acting_names("Каширин идёт к Зое; читатель узнаёт", known, pseudo={"Читатель"})
    assert acting == ["Зоя", "Каширин"]
    assert names.find_acting_names("рапорт Зои лежит на столе", known) == []


def test_split_items_не_делит_внутри_скобок():
    assert names.split_items("а; б (в; г); д.") == ["а", "б (в; г)", "д"]
