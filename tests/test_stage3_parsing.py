"""Этап 3 аудита: точность разбора канона и проверок Э1 (АУДИТ.md 1.5, 3.1–3.7).

Юнит-тесты — на мини-документах; тесты на реальной библиотеке (Библиотека/)
пропускаются, если она не подключена (как в tests/test_real_canon.py).
"""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from konveyer import compiler, exporter, guard, realcanon, textutils, verifier1
from konveyer.cli import app
from konveyer.paths import Workspace
from konveyer.schemas import Brief

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture
def real(tmp_path):
    (tmp_path / "конфиг.yaml").write_text(f'library_dir: "{LIBRARY.as_posix()}"\n', encoding="utf-8")
    ws = Workspace(tmp_path)
    guard.set_library_dir(LIBRARY)
    exporter.run_export(LIBRARY, ws.exports, ws.logs)
    return ws


# ------------------------------------------------------------ 3.1 / 1.5 закладки §7


def test_главы_из_перечислений_закладок():
    assert realcanon.chapters_listed("Гл. 9, 27") == [9, 27]
    assert realcanon.chapters_listed("Гл. 29 или 40, деталью без акцента") == [29, 40]
    assert realcanon.chapters_listed("Доза №1 (гл. 12), гл. 46") == [12, 46]
    assert realcanon.chapters_listed("Пролог, гл. 41") == [41]


def test_тома_выстрела_диапазоны_и_оговорки():
    assert realcanon.volumes_listed("Иерархия предметов томов 2–5; наследник линии — Брокер, том 10") == [2, 3, 4, 5, 10]
    # «в томе 1 любая ниточка…» — оговорка, не выстрел
    assert realcanon.volumes_listed("Резерв тома 6 (дуэль отца и сына) — в томе 1 любая ниточка к этому взломает слой 2") == [6]
    assert realcanon.volumes_listed("Том 2 (оппонент), том 11 (галерея)") == [2, 11]


def test_не_упоминается_в_томе_становится_запретом(tmp_path):
    reg = tmp_path / "УГАР_Том1_Реестр_информационного_режима.md"
    reg.write_text(
        "# «УГАР». Том 1 (1926)\n\n## 7. Реестр дальних закладок\n\n"
        "| Закладка | Где лежит | Где стреляет |\n|---|---|---|\n"
        "| Картотека | Гл. 9, 27 | Тома 9–10 |\n"
        "| Красный Крест / Ватикан | НЕ упоминается в томе 1 | Резерв тома 6 — в томе 1 любая ниточка взломает слой |\n",
        encoding="utf-8",
    )
    plants = realcanon.parse_plants_registry(reg)
    assert plants[0].chapters == [9, 27] and [f["vol"] for f in plants[0].fires] == [9, 10]
    assert plants[1].chapters == [] and [f["vol"] for f in plants[1].fires] == [6]
    bans = realcanon.parse_plant_bans(reg)
    assert len(bans) == 1 and bans[0].secret is False and bans[0].until_volume == 1
    assert bans[0].ban_id == "З-02" and "Красный Крест" in bans[0].text
    assert compiler.ban_active(bans[0], Brief(chapter=30, volume=1))
    assert not compiler.ban_active(bans[0], Brief(chapter=1, volume=2))


@real_only
def test_реестр_закладки_все_главы_и_окна_27_40(real):
    by_id = {p.plant_id: p for p in exporter.load_plants(real.exports)}
    assert by_id["З-02"].chapters == [29] and [f["vol"] for f in by_id["З-02"].fires] == [2, 3, 4, 5, 10]
    assert by_id["З-03"].chapters == [9, 27]
    assert by_id["З-08"].chapters == [34, 40]
    assert [f["vol"] for f in by_id["З-07"].fires] == [6]  # «в томе 1» — не выстрел
    w27 = compiler.compile_window(real, LIBRARY, 27)[0].read_text(encoding="utf-8")
    w40 = compiler.compile_window(real, LIBRARY, 40)[0].read_text(encoding="utf-8")
    assert "[З-03] Картотека Лемма" in w27
    assert "[З-08] «Третья рука»" in w40 and "[З-02]" not in w40  # З-02 — только гл. 29 (Р-028)
    w29 = compiler.compile_window(real, LIBRARY, 29)[0].read_text(encoding="utf-8")
    assert "[З-02]" in w29
    # 1.5: Красный Крест / Ватикан — «НЕ упоминать» в каждом окне тома
    assert "НЕ упоминать (информрежим З-07): Красный Крест / Ватикан" in w27


# --------------------------------------------------------------- 3.2 сплиттер Д-2


def test_сплиттер_абзацы_инициалы_заголовки():
    assert textutils.split_sentences("Подписал: судебный медик А. К. Штерн. Он вышел.") == [
        "Подписал: судебный медик А. К. Штерн.", "Он вышел.",
    ]
    soft = "Свет ударит на брусчатку Большого \nГнездниковского\n переулка. Он шагнул."
    assert textutils.split_sentences(soft) == ["Свет ударит на брусчатку Большого Гнездниковского переулка.", "Он шагнул."]
    assert textutils.split_sentences("Глава пятая\n\nСтепан провернул ключ.") == ["Степан провернул ключ."]
    assert textutils.split_sentences("Он ушёл.\n\nЧасть II\n\nОна осталась.") == ["Он ушёл.", "Она осталась."]
    # абзацы без пустой строки между ними остаются отдельными предложениями по терминаторам
    assert len(textutils.split_sentences("Один абзац.\nВторой абзац.")) == 2


def test_однобуквенные_сокращения_по_контексту():
    assert textutils.split_sentences("Он читал на с. 12 и в д. пятом. Она с. Он молчал.") == [
        "Он читал на с. 12 и в д. пятом.", "Она с.", "Он молчал.",
    ]
    # «им.» контекстное: местоимение «им» в конце фразы не склеивает предложения; «ул.» — безусловное
    assert textutils.split_sentences("Всё решено им. Он ушёл на ул. Ленина.") == ["Всё решено им.", "Он ушёл на ул. Ленина."]
    assert textutils.split_sentences("Завод им. тов. Ленина стоял.") == ["Завод им. тов. Ленина стоял."]
    assert len(textutils.split_sentences("Он пришёл в 1926 г. Она ушла.")) == 2  # «г.» перед заглавной — конец фразы


@real_only
def test_калибровка_сплиттера_на_реальной_прозе():
    """Перекалибровка 10.1 после правки Д-2: макет гл. 4 — 83 предложения, средняя 7,29
    (ТЗ ждёт ≈7,2 ± 0,2); принятая гл. 5 — 6,20 (аудит: 5,78 из-за мягких переносов)."""
    t4 = textutils.narrator_text((LIBRARY / "Проза/Том1_Глава04_МАКЕТ.md").read_text(encoding="utf-8"))
    sents = textutils.split_sentences(t4)
    assert "Подписал: судебный медик А. К. Штерн." in sents
    lens = textutils.sentence_lengths(t4)
    assert len(lens) == 83 and abs(sum(lens) / len(lens) - 7.29) < 0.01
    t5 = textutils.narrator_text((LIBRARY / "Проза/Том1_Глава05.md").read_text(encoding="utf-8"))
    sents5 = textutils.split_sentences(t5)
    assert "Глава пятая" not in sents5
    assert any("Большого Гнездниковского переулка" in s for s in sents5)
    lens5 = textutils.sentence_lengths(t5)
    assert abs(sum(lens5) / len(lens5) - 6.20) < 0.01


# ------------------------------------------------------- 3.3 стоп-лексика по основам


def test_стоп_лексика_по_основам_слов():
    text = "Он вспомнил отца, написал сыну и подумал о семье. Папе — ни слова."
    assert verifier1._find_items(text, ["отец", "сын", "семья", "папа"]) == ["отец", "сын", "семья", "папа"]
    # «семь» (число) и «папка» — другие слова, не формы «семья»/«папа»
    assert verifier1._find_items("Семь папок лежали в столе.", ["семья", "папа"]) == []
    # обороты из нескольких слов — дословно
    assert verifier1._find_items("объект движется по улице", ["объект движется"]) == ["объект движется"]
    assert verifier1._find_items("объекты движутся", ["объект движется"]) == []
    assert verifier1.word_stems("отец") == ["отец", "отц"] and verifier1.word_stems("сын") == ["сын"]


def test_флаг_слова_анахронизмов_по_статусу(tmp_path):
    p04 = tmp_path / "04_Языковой_канон.md"
    p04.write_text(
        "# 0.4\n\n## Е. Стоп-лист анахронизмов\n\n"
        "**Запрещено до соответствующего года:** пятилетка (до 1928).\n"
        "**Запрещено навсегда (современный слой):** разборка, висяк ⚠ (датировка; синоним: «глухое дело», глухарь ⚠) · "
        "любые кальки телепроцедурала 🔧 запрет.\n"
        "**Ложные друзья (существовали, но иначе):** блат ✓ · мент ⚠ (только из уст среды) · липа (подделка) ✓.\n\n"
        "## Ж. Правила\n",
        encoding="utf-8",
    )
    rules = realcanon.parse_anachronisms(p04)
    flag = next(r for r in rules if r.action == "флаг")
    assert flag.rule_id == "0.4-Е-⚠" and flag.items == ["глухарь", "мент"] and flag.applies_to == {"all": True}
    banned = {w for r in rules if r.action == "запрет" for w in r.items}
    assert {"висяк", "разборка", "пятилетка"} <= banned and "глухарь" not in banned


@real_only
def test_э1_стоп_лексика_реального_канона(real):
    norms = exporter.load_norms(real.exports)
    stop = exporter.load_stoplists(real.exports)
    flag_rule = next(r for r in stop if r.rule_id == "0.4-Е-⚠")
    assert set(flag_rule.items) == {"глухарь", "мент"} and flag_rule.action == "флаг"
    text = "Он вспомнил отца и подумал о сыне. Глухарь висел на стене, мент молчал."
    checks = verifier1.analyze(text, "", Brief(chapter=7, focal="Штерн", year=1926), norms, stop, corpus_dir=real.corpus)
    v15 = [c for c in checks if c.check_id == "V1.5_стоп_лексика"]
    found = {c.rule_source.split()[0]: c.actual for c in v15}
    assert found["0.3-Штерн"] == "отец; сын" and found["0.4-Е-⚠"] == "глухарь; мент"
    assert all(c.quotes for c in v15)  # цитаты находятся и по словоформе


# ---------------------------------------------------------------- 3.4 own_stem в check


@real_only
def test_check_не_сравнивает_главу_с_собой(real, monkeypatch):
    monkeypatch.chdir(real.root)
    runner = CliRunner()
    r = runner.invoke(app, ["check", str(LIBRARY / "Проза/Том1_Глава05.md"), "--глава", "5"])
    assert r.exit_code == 0, r.output
    line = next(ln for ln in r.output.splitlines() if "V1.7_межглавные_повторы" in ln)
    assert "[PASS]" in line, line  # принятая гл. 5 лежит в корпусе — с самой собой не сравнивается


# ------------------------------------------------------ 3.5 участники из реестра


def test_имена_в_событии_по_основе_и_регистру():
    known = {"Заварзин", "Лемм", "Куратор", "Куратор ОГПУ", "Ася", "Читатель", "Штерн"}
    found = realcanon.find_names("Заварзин глазами Лемма · вызов к куратору ОГПУ; Асю видели; читатель знает", known)
    assert found == ["Ася", "Заварзин", "Куратор ОГПУ", "Лемм"]
    assert realcanon.find_names("по приказу куратора", known) == ["Куратор ОГПУ"]  # единственное полное имя
    assert realcanon.find_names("Штерном подписано", known) == ["Штерн"]
    # без дубля «Куратор» в известных именах «куратор» всё равно находится (аудит 1.6)
    assert realcanon.find_names("вызов к куратору", {"Куратор ОГПУ", "Лемм"}) == ["Куратор ОГПУ"]
    assert realcanon.normalize_name("куратор ОГПУ", {"Куратор ОГПУ", "Лемм"}) == "Куратор ОГПУ"
    assert realcanon.normalize_name("куратор", {"Куратор ОГПУ", "Лемм"}) == "Куратор ОГПУ"
    assert realcanon.normalize_name("Лемма", {"Куратор ОГПУ", "Лемм"}) == "Лемм"


# ------------------------------------------------------ 1.11 ложные участники события


def test_участники_события_только_по_действию():
    known = {"Степан", "Лемм", "Заварзин", "Куратор ОГПУ", "Ася", "Штерн"}
    acting = realcanon.find_acting_names
    # родительный при существительном и дательный адресата — упоминание, не участие
    assert acting("Один, ночь, последний рапорт Степана на столе", known) == []
    assert acting("предлагает себя верификатором данных по спецу Лемму", known) == []
    assert acting("Та же ночь после ухода Степана: печь, огонь", known) == []
    assert acting("фраза, сказанная однажды ночью только Степану", known) == []
    assert acting("Из отчёта изъята нестыковка маршрута Лемма", known) == []
    # именительный, перечисление, предлог совместного действия, дополнение глагола — участие
    assert acting("Ася и Степан: первый личный разговор", known) == ["Ася", "Степан"]
    assert acting("Вызов к Заварзину. Принудительное внедрение", known) == ["Заварзин"]
    assert acting("Штерн выходит на куратора ОГПУ: предлагает себя", known) == ["Куратор ОГПУ", "Штерн"]
    assert acting("Разбор с Заварзиным: нити обрублены", known) == ["Заварзин"]
    assert acting("Слежка за Леммом по приказу куратора", known) == ["Лемм"]
    assert acting("Лемм ведёт Степана как инструмент", known) == ["Лемм", "Степан"]
    assert acting("Степан прикрывает Лемма на встрече", known) == ["Лемм", "Степан"]
    assert acting("Заварзин глазами Лемма · Заварзин теряет протекцию", known) == ["Заварзин"]


def test_имена_из_таблицы_03_без_оговорок(tmp_path):
    p03 = tmp_path / "03_Правила.md"
    p03.write_text(
        "# 03\n\n## Фокальные персонажи по томам\n\n| Тома | Фокальные линии | Без фокала |\n|---|---|---|\n"
        "| 1 | Лемм ~45% · Степан | Ася, Заварзин, все оппоненты |\n"
        "| 11 | Степан · Ольга (единственный том) | Британец — никогда не фокален во всей серии |\n\n"
        "## Персональные запреты линий\n\n**Штерн (тома 1–5):**\n- стоп-лист: «отец».\n",
        encoding="utf-8",
    )
    names = realcanon.focal_names(p03)
    assert {"Лемм", "Степан", "Ася", "Заварзин", "Ольга", "Штерн"} <= names
    assert "Британец" not in names  # ячейка-предложение «Имя — …» — оговорка, не список имён


def test_досье_имена_карточек_код_и_присутствие_по_арке(tmp_path):
    d = tmp_path / "Досье"
    d.mkdir()
    f = d / "Ковров_Мередит.md"
    f.write_text(
        "# Досье 1.3: МИТЯ КОВРОВ (рабочее имя, Р-007)\n## Профиль\nРожд. ≈1912.\n## Арка по томам\n"
        "Т.7: вербовка; проверка делом.\n\n"
        "# Досье 1.3: АРТУР МЕРЕДИТ (рабочее имя, Р-013)\n## Профиль\nРожд. ≈1887. Офицер SIS.\n"
        "## Опознавательный код (закладка пролог → т.2 → т.11)\n1. Перстень-печатка на левой руке. "
        "2. Перчатка, снимаемая только с одной руки.\n"
        "## Арка\nТ.1: две немые сцены (пролог; кабаре, гл. 41). Т.2: дуэль с Леммом (гл. 3, 5). Т.11: галерея.\n\n"
        "# Досье 1.3: ОЛЬГА ЛЕММ\n## Профиль\nДочь.\n",
        encoding="utf-8",
    )
    known = {"Лемм", "Ольга", "Степан"}
    assert realcanon.dossier_names([f], known) == {"Ковров", "Мередит", "Ольга"}
    cards = {c.name: c for c in realcanon.parse_dossiers_real([f], known | {"Ковров", "Мередит"})}
    assert cards["Мередит"].code.startswith("1. Перстень-печатка на левой руке")
    assert cards["Ковров"].code == "" and cards["Ольга"].name == "Ольга"
    assert realcanon.dossier_presence([f], 1, known) == {"Мередит": [41]}
    assert realcanon.dossier_presence([f], 2, known) == {"Мередит": [3, 5]}
    briefs = [Brief(chapter=41, volume=1, focal="Лемм"), Brief(chapter=40, volume=1, focal="Лемм")]
    realcanon.enrich_from_dossiers(briefs, [f], known)
    assert briefs[0].participants == ["Мередит"] and briefs[1].participants == []


@real_only
def test_участники_реестра_и_досье_в_окне(real):
    briefs = {b.chapter: b for b in exporter.load_briefs(real.exports)}
    assert "Заварзин" in briefs[22].participants and briefs[22].focal == "Лемм"
    assert briefs[2].participants.count("Куратор ОГПУ") == 1 and "Куратор" not in briefs[2].participants
    assert "Куратор ОГПУ" in briefs[7].participants
    assert "Заварзин" in briefs[10].participants
    w22 = compiler.compile_window(real, LIBRARY, 22)[0].read_text(encoding="utf-8")
    assert "### Заварзин" in w22 and "### Лемм" in w22


# ---------------------------------------------- 3.6 документы и поля сцены поглавника


def test_поглавник_документы_и_поле_входит_выходит(tmp_path):
    p23 = tmp_path / "23_Поглавник.md"
    p23.write_text(
        "# Поглавник\n\n## Гл. 2 · 13 апреля · фокал СТЕПАН\n\n"
        "**Сц. 2.2.** Явочная комната, вечер · Степан, куратор ОГПУ · вербовка · "
        "входит: тревога; выходит: согласие · кладём: механика рапорта.\n"
        "**→ ДОКУМЕНТ №1** (после главы): первый рапорт — чистый канцелярит.\n",
        encoding="utf-8",
    )
    brief = Brief(chapter=2, focal="Степан")
    realcanon.enrich_from_poglavnik([brief], p23, {"Степан", "Куратор ОГПУ", "Куратор"})
    assert brief.documents == ["№1 (после главы): первый рапорт — чистый канцелярит."]
    assert brief.scenes == ["Явочная комната, вечер · Степан, куратор ОГПУ · вербовка · входит: тревога; выходит: согласие"]
    assert brief.beats == []  # «кладём» — закладки сцены, не биты (аудит 2, 1.10)
    assert brief.scene_cards[0].plants == ["механика рапорта"]
    assert brief.participants == ["Куратор ОГПУ"]


@real_only
def test_документы_вставки_в_окне(real):
    b2 = exporter.load_brief(real.exports, 2)
    assert b2.documents and b2.documents[0].startswith("№1 (после главы): первый рапорт")
    assert "входит: гордость назначением; выходит: первые заметки" in b2.scenes[0]
    w2 = compiler.compile_window(real, LIBRARY, 2)[0].read_text(encoding="utf-8")
    assert "## Документ-вставка №1" in w2 and "- По поглавнику: первый рапорт — чистый канцелярит" in w2
    assert w2.count("Документ-вставка №1") == 1  # §6 реестра и строка поглавника слиты, не продублированы
    assert exporter.load_brief(real.exports, 8).documents[0].startswith("№2")
    assert exporter.load_brief(real.exports, 5).documents == []


# ------------------------------------------------------------------- 3.7 мелочи


def test_континуити_ссылки_и_даты(tmp_path):
    p33 = tmp_path / "33_Континуити.md"
    p33.write_text(
        "# 3.3\n\n- Штерн: почерк мелкий · т.1 гл.4 · канон (заряжено на экспертизу т.8 — почерк как улика)\n"
        "- Сторож **Клюев** · т.1 гл.1, 4 · канон\n"
        "- Кепка Степана · сквозная деталь (гл.4: снял-надел; гл.5) · канон\n"
        "- Убийца Клюева = линия «третьей руки»? · НЕ фиксировано — при арке т.2 ⚠\n",
        encoding="utf-8",
    )
    ev = realcanon.parse_continuity_bullets(p33)
    assert [(e.date, e.chapters) for e in ev] == [
        ("т.1 гл.4", "4"), ("т.1 гл.1, 4", "1, 4"), ("гл.4", "4, 5"), (realcanon.NO_SOURCE, ""),
    ]
    assert all(e.date for e in ev) and ev[1].event == "Сторож Клюев"


def test_отношения_досье_без_тире(tmp_path):
    d = tmp_path / "Досье"
    d.mkdir()
    (d / "Ася.md").write_text(
        "# Досье 1.3: АСЯ ГРИНБЕРГ\n## Отношения\n"
        "[[Степан]] — от уважения к любви. [[Александр-сын]], [[Бугаев]] («своя, из выдвиженок»).\n",
        encoding="utf-8",
    )
    ася = realcanon.parse_dossiers_real([d / "Ася.md"], {"Ася", "Степан", "Бугаев"})[0]
    assert ася.relations["Степан"] == "от уважения к любви"
    assert ася.relations["Бугаев"] == "«своя, из выдвиженок»"
    assert ася.relations["Александр-сын"] == "(связь отмечена без пояснения)"


def test_ttr_корпус_по_тому_и_части(tmp_path):
    for name in ("Том1_Глава03", "Том1_Глава12", "Том2_Глава01", "прочее"):
        (tmp_path / f"{name}.txt").write_text("слово\n", encoding="utf-8")
    scope = [f.stem for f in verifier1.corpus_scope(tmp_path, 1, (1, 9))]
    assert scope == ["Том1_Глава03", "прочее"]
    assert [f.stem for f in verifier1.corpus_scope(tmp_path, 1, None)] == ["Том1_Глава03", "Том1_Глава12", "прочее"]


@real_only
def test_континуити_и_отношения_реального_канона(real):
    ev = exporter.load_continuity(real.exports)
    assert all(e.date for e in ev)
    assert next(e for e in ev if "почерк мелкий" in e.event).chapters == "4"  # «т.8» в скобках — статус
    assert next(e for e in ev if "Клюев" in e.event and "Сторож" in e.event).chapters == "1, 4"
    ася = next(d for d in exporter.load_dossiers(real.exports) if d.name == "Ася")
    assert ася.relations["Бугаев"] == "«своя, из выдвиженок»"
    (real.chapter_dir(5)).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LIBRARY / "Проза/Том1_Глава05.md", real.draft_path(5, 1))
    compiler.compile_window(real, LIBRARY, 5)
    ttr = next(c for c in verifier1.run_verify1(real, 5, 1).checks if c.check_id == "V1.8b_ttr_окно")
    # окно TTR скользит по всему тому (часть короче 10 000 слов — проверка не срабатывала бы никогда),
    # сама проверяемая глава в корпус не входит
    assert "том 1" in ttr.note and "Том1_Глава05" not in ttr.note
    assert "справочно: том короче окна" in ttr.actual and ttr.status == "PASS"


# ---------------------------------------- аудит 2, 1.1: §5 дозы и §6 документы — синтетика и демо


def test_дозы_и_документы_без_разделов_и_в_демо(tmp_path, ws):
    reg = tmp_path / "УГАР_Том1_Реестр_информационного_режима.md"
    reg.write_text(
        "# «УГАР». Том 1 (1926)\n\n## 7. Реестр дальних закладок\n\n"
        "| Закладка | Где лежит | Где стреляет |\n|---|---|---|\n| Часы | Гл. 4 | Том 2 |\n",
        encoding="utf-8",
    )
    assert realcanon.parse_doses(reg) == [] and realcanon.parse_documents(reg) == []
    # демо-библиотека без реестра информрежима: выгрузки — пустые списки, не ошибка
    assert exporter.load_doses(ws.exports) == [] and exporter.load_documents(ws.exports) == []
    manifest = json.loads((ws.exports / "индекс.json").read_text(encoding="utf-8"))
    assert {"doses.json", "documents.json"} <= set(manifest["files"])


def test_разбор_доз_правило_по_адресу_и_шкала_документов(tmp_path):
    reg = tmp_path / "УГАР_Том2_Реестр_информационного_режима.md"
    reg.write_text(
        "# «УГАР». Том 2 (1927)\n\n## 5. Две дозы прошлого (канал воспоминаний)\n\nФорма дозы.\n\n"
        "| Доза | Глава | Триггер | Что получает читатель | Чего НЕ получает |\n|---|---|---|---|---|\n"
        "| №1 | 3 | Письмо | Был брат | Судьбу брата |\n| №2 | 9 | Фото | Брат жив | Где он |\n\n"
        "Правило доз: тон сухой. «Окно» — только в дозе №2 и в гл. 40. Никакого сентимента.\n\n---\n\n"
        "## 6. Реестр документов (письма Ольги)\n\nПисьмо верстается как письмо.\n\n"
        "| № | После гл. | Стиль | Расхождение с правдой, которую видел читатель |\n|---|---|---|---|\n"
        "| 1 | 3 | Сухой | Нет |\n| 2 | 9 | Живой | **Умолчание:** нет даты |\n\n"
        "Языковая шкала писем (для написания): №1–1 — штампы; №2–2 — живые детали. К тому 5 — как у Лемма.\n",
        encoding="utf-8",
    )
    doses = realcanon.parse_doses(reg)
    assert [(d.dose_id, d.chapter, d.volume, d.form) for d in doses] == [("№1", 3, 2, "Форма дозы."), ("№2", 9, 2, "Форма дозы.")]
    assert doses[0].rule == "тон сухой. Никакого сентимента."
    assert doses[1].rule == "тон сухой. «Окно» — только в дозе №2 и в гл. 40. Никакого сентимента."
    docs = realcanon.parse_documents(reg)
    assert [(d.number, d.after_chapter, d.volume, d.kind) for d in docs] == [(1, 3, 2, "письма Ольги"), (2, 9, 2, "письма Ольги")]
    assert docs[0].scale == "№1–1 — штампы" and docs[1].scale == "№2–2 — живые детали"
    assert docs[1].divergence == "**Умолчание:** нет даты" and docs[0].form == "Письмо верстается как письмо."


def test_документ_главы_сливает_реестр_и_поглавник(tmp_path):
    exports = tmp_path / "выгрузки"
    exports.mkdir()
    (exports / "documents.json").write_text(
        json.dumps([{"number": 1, "after_chapter": 2, "volume": 1, "kind": "рапорты", "style": "Сухой",
                     "divergence": "Нет", "form": "", "scale": "№1–3 — штампы"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (exports / "doses.json").write_text("[]", encoding="utf-8")
    brief = Brief(chapter=2, focal="Степан", documents=["№1 (после главы): первый рапорт."])
    docs = compiler.chapter_documents(exports, brief)
    assert len(docs) == 1 and docs[0]["note"] == "первый рапорт." and docs[0]["style"] == "Сухой"
    assert docs[0]["position"] == "после главы" and docs[0]["scale"] == "№1–3 — штампы"
    assert compiler.chapter_documents(exports, Brief(chapter=3, focal="Степан")) == []
    # документ только из поглавника (реестра §6 нет) — секция всё равно есть
    only23 = compiler.chapter_documents(exports, Brief(chapter=7, focal="Лемм", documents=["№9 (в середине главы): записка"]))
    assert only23 == [{"number": 9, "kind": "", "position": "в середине главы", "note": "записка",
                       "style": "", "divergence": "", "scale": "", "form": ""}]
    assert compiler.chapter_doses(exports, brief) == []
    assert compiler.chapter_doses(tmp_path / "нет", brief) == []  # старые выгрузки без doses.json — не падаем
