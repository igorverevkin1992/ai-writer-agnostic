"""Реальная библиотека канона «УГАР» (Библиотека/ в репозитории): парсер под канон (Д-1).

Проверяет критерий приёмки этапа 1 на живых данных: окно главы 5, собранное
`compile`, семантически эквивалентно эталону v1.1 (Тест_Писателя/ПРОМПТ_Глава5.md);
калибровка счётчиков на реальном макете гл. 4 (10.1).
"""

import json
import shutil
from pathlib import Path

import pytest

from konveyer import compiler, exporter, guard, textutils, verifier1
from konveyer.paths import Workspace

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"

pytestmark = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture
def real(tmp_path):
    (tmp_path / "конфиг.yaml").write_text(f'library_dir: "{LIBRARY.as_posix()}"\n', encoding="utf-8")
    ws = Workspace(tmp_path)
    guard.set_library_dir(LIBRARY)
    exporter.run_export(LIBRARY, ws.exports, ws.logs)
    return ws


def test_нормы_из_прозы_регламента(real):
    norms = exporter.load_norms(real.exports)
    n = norms["средняя_длина"]
    assert (n.min, n.max, n.brak) == (9, 12, 7) and "Р-015" in n.source
    assert norms["доля_коротких"].min == 0.30 and norms["доля_коротких"].max == 0.45
    assert norms["был_на_250"].max == 1 and norms["ttr_мин"].min == 0.46
    assert norms["усилители_на_1000"].max == 2 and "Р-016" in norms["усилители_на_1000"].source
    assert "ТЗ" in norms["утечка_нграмма"].source  # порог из ТЗ, пока канон не переопределил


def test_поглавник_всего_тома(real):
    briefs = exporter.load_briefs(real.exports)
    assert len(briefs) == 46 and all(b.year == 1926 and b.volume == 1 for b in briefs)
    b5 = exporter.load_brief(real.exports, 5)
    assert b5.focal == "Степан" and "18.04" in b5.date
    assert b5.scenes and "кабинет Лемма" in b5.scenes[0] and "Лемм" in b5.participants
    assert exporter.load_brief(real.exports, 22).focal == "Лемм"  # «Заварзин глазами Лемма»
    assert not any("Читатель узнаёт" in beat for beat in b5.beats)  # мета читателя Писателю не идёт


def test_стоп_листы_и_усилители(real):
    stop = exporter.load_stoplists(real.exports)
    stern = [r for r in stop if r.applies_to.get("focal") == "Штерн"]
    assert stern and set(stern[0].items) == {"отец", "сын", "семья", "папа"}  # без «Разрешено»
    dated = {w: r.applies_to["year"]["before"] for r in stop if "year" in r.applies_to for w in r.items}
    assert dated["пятилетка"] == 1928 and dated["стахановец"] == 1935
    forever = {w for r in stop if r.scope == "0.4" and r.kind == "лексика" and "all" in r.applies_to for w in r.items}
    assert {"стукач", "разборка", "беспредел"} <= forever and not any("любые" in w for w in forever)
    ints = [r for r in stop if r.kind == "усилитель"]
    assert ints and "предельно" in ints[0].items


def test_матрица_закладки_тайны_досье(real):
    matrix = exporter.load_matrix(real.exports)
    stepan_knows = {f.fact_id for f in matrix if f.subject == "Степан" and f.from_chapter is not None and f.from_chapter <= 5}
    assert stepan_knows == {"М-04"}
    lemm = {f.fact_id: f.from_chapter for f in matrix if f.subject == "Лемм"}
    assert lemm["М-01"] == 0 and lemm["М-02"] == 3 and lemm["М-14"] is None

    plants = exporter.load_plants(real.exports)
    zola = next(p for p in plants if "золе" in p.what)
    assert zola.chapters == [6] and {"vol": 6} in zola.fires
    ch4 = {p.what.split(" (")[0] for p in plants if 4 in p.chapters}
    assert ch4 == {"Часы Штерна", "Почтовый канал Штерна"}  # §7 реестра (Р-033), сходится со сквозным контролем
    assert not any("картотека" in p.what and p.plant_id.startswith("П-") for p in plants)  # дедуп с §7

    bans = exporter.load_infobans(real.exports)
    secrets = [b for b in bans if b.secret]
    assert len(secrets) == 10
    assert next(b for b in bans if "сын Лемма" in b.text).until_chapter == 46
    # §7 «НЕ упоминается в томе 1» — открытый запрет на весь том, не тайна (аудит 1.5)
    assert [(b.ban_id, b.until_volume) for b in bans if not b.secret] == [("З-07", 1)]

    names = sorted(d.name for d in exporter.load_dossiers(real.exports))
    assert names == ["Ася", "Бугаев", "Заварзин", "Ковров", "Лемм", "Мередит", "Ольга", "Ремез", "Степан", "Штерн"]
    lemm_d = next(d for d in exporter.load_dossiers(real.exports) if d.name == "Лемм")
    assert "Штерн" in lemm_d.relations and "на «Вы»" in lemm_d.speech


def test_известные_имена_из_досье_и_участники_по_действию(real):
    """Аудит 1.6/1.11: имена всех карточек досье известны; «Британец» и дубль «Куратор» — нет;
    участник события — только участвующий в действии; Мередит приходит в гл. 41 из арки досье."""
    known = exporter._known_names(LIBRARY)
    assert {"Мередит", "Ковров", "Ремез", "Куратор ОГПУ", "Ольга"} <= known
    assert "Британец" not in known and "Куратор" not in known
    briefs = {b.chapter: b for b in exporter.load_briefs(real.exports)}
    assert briefs[41].participants == ["Мередит"]
    assert "Степан" not in briefs[46].participants          # «последний рапорт Степана на столе»
    assert briefs[7].participants == ["Куратор ОГПУ"]      # «по спецу Лемму» — не участник
    assert briefs[6].participants == []                     # «после ухода Степана»
    assert briefs[44].participants == []                    # «нестыковка маршрута Лемма»
    assert briefs[8].participants == ["Лемм"]               # «слежка за Леммом»; приказ куратора — до сцены
    # участники по действию сохранены там, где событие — единственный источник (гл. 10–46)
    assert briefs[10].participants == ["Заварзин"] and briefs[42].participants == ["Заварзин"]
    assert briefs[25].participants == ["Степан"] and briefs[30].participants == ["Лемм"]
    assert briefs[38].participants == ["Лемм"] and briefs[2].participants == ["Куратор ОГПУ", "Лемм"]
    bans = {b.ban_id: b for b in exporter.load_infobans(real.exports) if b.secret}
    assert "Куратор ОГПУ" in bans["Т-03"].known_by and "Куратор" not in bans["Т-03"].known_by
    # досье Мередита с «Опознавательным кодом» — в окне гл. 41 (реплики-якоря финала т.2 вычищены)
    w41 = compiler.compile_window(real, LIBRARY, 41)[0].read_text(encoding="utf-8")
    sec = w41[w41.index("### Мередит"): w41.index("<!-- СЕКЦИЯ: что знает фокал")]
    assert "Опознавательный код: 1. Перстень-печатка на левой руке. 2. Перчатка, снимаемая только с одной руки." in sec
    assert "Ф-1927" not in sec and "кому служите" not in sec
    w5 = compiler.compile_window(real, LIBRARY, 5)[0].read_text(encoding="utf-8")
    assert "Опознавательный код" not in w5  # у карточек без секции строки нет


def test_окно_главы_5_эквивалентно_эталону(real):
    """Критерий этапа 1: разделы эталона v1.1 присутствуют, тайны не утекают."""
    path, breakdown = compiler.compile_window(real, LIBRARY, 5)
    w = path.read_text(encoding="utf-8")
    etalon = (LIBRARY / "Тест_Писателя/ПРОМПТ_Глава5.md").read_text(encoding="utf-8")
    # 1. регистр и стиль — из 02 (разделы 1–4, 6.1) и нормы
    assert "Идиоматическая естественность выше образности" in w and "средняя_длина" in w
    # 2. фокализация — общие законы и линия
    assert "Одна сцена — одна голова" in w and "Фокал: Степан" in w
    # 3. персонажи сцены — досье участников
    assert "### Степан" in w and "### Лемм" in w and "### Штерн" not in w
    # 4. что знает фокал — только доступное; содержание тайн не раскрыто (FR-C3)
    assert "М-04" in w and "сын Лемма" not in w and "Подлог 1913" not in w
    # 5. бриф — сцена из поглавника
    assert "обыск стола по приказу куратора" in w
    # 6. формат выдачи и СТРОГО ЗАПРЕЩЕНО
    assert "СТРОГО ЗАПРЕЩЕНО" in w and "Формат выдачи" in w
    # лексика эпохи и усилители из эталона доступны Писателю
    for word in ("пятилетка", "стукач", "разборка", "предельно"):
        assert word in w, word
    assert len(w) < 80_000
    # детерминизм на реальных данных
    assert compiler.compile_window(real, LIBRARY, 5)[0].read_text(encoding="utf-8") == w
    assert "550" in etalon and "700–800 слов" in w  # объём: эталон 550–800, канон Р-019 700–800


def test_калибровка_реального_макета(real):
    """10.1: ожидаемые ТЗ ≈592 слова, средняя ≈7,2 (±0,2), ≤6 ≈51%, «был» 2 — замер на файле макета.

    Замер после правки сплиттера (этап 3 аудита, Д-2): 605 слов, 83 предложения, средняя 7,29,
    ≤6 слов — 47% (до правки инициалы «А. К. Штерн.» давали три лишних предложения: 85, 7,12)."""
    raw = (LIBRARY / "Проза/Том1_Глава04_МАКЕТ.md").read_text(encoding="utf-8")
    text = textutils.narrator_text(raw)
    lens = textutils.sentence_lengths(text)
    tokens = textutils.normalize(text)
    avg = sum(lens) / len(lens)
    assert 585 <= len(tokens) <= 620
    assert abs(avg - 7.2) <= 0.2 and abs(avg - 7.29) < 0.01 and len(lens) == 83
    assert 0.45 <= sum(1 for x in lens if x <= 6) / len(lens) <= 0.53
    assert sum(1 for t in tokens if t in {"был", "было"}) == 4  # был 1 + было 3 (файл макета v2)
    assert "Статус: на приёмке" not in text  # заголовки-метаданные исключены


def test_э1_по_принятой_главе_5(real):
    """Принятая гл. 5 (Р-018) против норм Р-015: Э1 честно показывает расхождение."""
    (real.chapter_dir(5)).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LIBRARY / "Проза/Том1_Глава05.md", real.draft_path(5, 1))
    compiler.compile_window(real, LIBRARY, 5)
    verdict = verifier1.run_verify1(real, 5, 1)
    by_id = {c.check_id: c for c in verdict.checks}
    assert by_id["V1.2a_средняя_длина"].status == "BRAK"       # 6,2 < 7 — телеграф по Р-015
    assert by_id["V1.2a_средняя_длина"].actual == "6.2"        # абзацы по Д-2 (до этапа 3 аудита: 5,78)
    assert by_id["V1.4_усилители"].status == "FLAG"            # «предельно ясно» и др.
    assert by_id["V1.5_стоп_лексика"].status == "PASS"
    assert by_id["V1.6_утечка_окна"].status == "PASS"
    assert by_id["V1.2e_объём"].status == "PASS"               # 752 слова в коридоре 700–800 (Р-019)
    assert "700" in by_id["V1.2e_объём"].threshold


# ------------------------------------------------ аудит 2, 1.1/1.2: дозы §5, документы §6, колонка читателя


def test_дозы_и_документы_реестра(real):
    doses = exporter.load_doses(real.exports)
    assert [(d.dose_id, d.chapter) for d in doses] == [("№1", 12), ("№2", 22), ("№3", 32)]
    d1, d2, d3 = doses
    assert d1.trigger.startswith("Чужая сфабрикованная бумага") and "печь в мае" in d1.reader_gets
    assert d1.reader_not_gets == "Ни слова о подлоге, ни слова о судьбе сына"
    assert d3.reader_gets.startswith("Полная картина") and "живым медиком" in d3.reader_not_gets
    assert all(d.form.startswith("Единственная разрешённая форма прошлого") for d in doses)
    # правило доз: общая фраза — каждой дозе, адресная «в дозе №1» — только дозе №1
    assert all(d.rule.startswith("интонация — протокольная") for d in doses)
    assert "«Печь в мае» появляется только в дозе №1 и в гл. 46" in d1.rule
    assert "печь в мае" not in d2.rule.lower() and "печь в мае" not in d3.rule.lower()

    docs = exporter.load_documents(real.exports)
    assert [(d.number, d.after_chapter) for d in docs] == [(1, 2), (2, 8), (3, 14), (4, 21), (5, 35), (6, 44)]
    assert docs[0].style == "Чистый комсомольский канцелярит, лозунги" and docs[0].kind == "рапорты Степана"
    assert "нестыковка маршрута" in docs[5].divergence and "гл. 39–41" in docs[5].divergence
    assert all(d.form.startswith("Первое лицо внутри третьего") for d in docs)
    assert docs[2].scale == "№1–3 — «доношу до вашего сведения», штампы, страдательный залог"
    assert docs[3].scale.startswith("№4–6 — появляются точные детали") and "тому 4" not in docs[3].scale

    briefs = {b.chapter: b for b in exporter.load_briefs(real.exports)}
    assert briefs[1].reader_learns.startswith("Червонец — не эпизод")
    assert "Доза 1913 №1" in briefs[12].reader_learns and "полная картина подлога" in briefs[32].reader_learns
    assert "Документ №3" in briefs[14].reader_learns
    manifest = json.loads((real.exports / "индекс.json").read_text(encoding="utf-8"))
    assert {"doses.json", "documents.json"} <= set(manifest["files"])


def _sections(window: str) -> dict[str, str]:
    parts = compiler.SECTION_RE.split(window)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_секции_дозы_и_документа_только_в_своих_главах(real):
    """Доза — только в окне своей главы (в 32 — будущее относительно фокала других глав, FR-C3),
    документ — только в окне главы «После гл.»; «печь в мае» из правила доз — в 12 и 46."""
    briefs = exporter.load_briefs(real.exports)
    windows = {b.chapter: compiler.compile_window(real, LIBRARY, b.chapter)[0].read_text(encoding="utf-8") for b in briefs}
    with_dose = {ch for ch, w in windows.items() if "доза прошлого" in _sections(w)}
    with_doc = {ch for ch, w in windows.items() if "документ-вставка" in _sections(w)}
    assert with_dose == {12, 22, 32} and with_doc == {2, 8, 14, 21, 35, 44}
    for ch, w in windows.items():
        if ch != 32:
            assert "Полная картина: улики против сына" not in w, ch
        if ch != 12:
            assert "Чужая сфабрикованная бумага" not in w, ch
        if ch != 44:
            assert "изъята нестыковка маршрута" not in w, ch
    # «печь в мае» из правила доз — в окнах 12 и 46; гл. 6 несёт собственную пометку поглавника
    # «рифма «печь в мае» ещё не предъявлена» в бите сцены 6.2 (запрет, не предъявление)
    pech = {ch for ch, w in windows.items() if "печь в мае" in w.lower()}
    assert pech - {6} == {12, 46}
    assert "ещё не предъявлена" in windows[6]
    assert not any("### Документы-вставки" in w for w in windows.values())  # старый список брифа заменён секцией

    s12 = _sections(windows[12])["доза прошлого"]
    assert "### Доза №1" in s12 and "- Триггер: Чужая сфабрикованная бумага в деле «Треста»" in s12
    assert "- Чего НЕ получает: Ни слова о подлоге" in s12
    assert (
        "- Правило доз: интонация — протокольная, без самооправданий; сентимент запрещён (регистр Кучера). "
        "«Печь в мае» появляется только в дозе №1 и в гл. 46" in s12
    )
    s22 = _sections(windows[22])["доза прошлого"]
    assert "### Доза №2" in s22 and "печь в мае" not in s22.lower()

    s44 = _sections(windows[44])["документ-вставка"]
    assert "## Документ-вставка №6 (рапорты Степана)" in s44 and "- Положение: после главы" in s44
    assert "- Стиль: Внешне безупречный, спокойный — впервые «профессиональный»" in s44
    assert "- Языковая шкала: №4–6 — появляются точные детали" in s44 and "№1–3" not in s44
    assert "Первое лицо внутри третьего" in s44
    s14 = _sections(windows[14])["документ-вставка"]
    assert "## Документ-вставка №3" in s14 and "- Стиль: Вымученный, под давлением куратора" in s14
    assert "- Языковая шкала: №1–3 — «доношу до вашего сведения»" in s14
    # гл. 2: §6 и строка «→ ДОКУМЕНТ №1» поглавника слиты в один документ
    s2 = _sections(windows[2])["документ-вставка"]
    assert s2.count("## Документ-вставка") == 1 and "- По поглавнику: первый рапорт" in s2 and "- Стиль: Чистый" in s2
    # колонка «Что нового знает читатель» Писателю не передаётся
    assert "Матрёшка: игра внутри игры" not in windows[12] and "находит изъятие сам" not in windows[44]
