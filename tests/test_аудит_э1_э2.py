"""Аудит кластера Э1/Э2: нормы и вердикт (FR-V1-2, FR-V1-5), лексемные метрики (FR-V1-1), языковой слой (FR-V1-3),
стоп-листы по контексту (FR-V1-4), дифф-контроль (FR-V1-6), калибровка (FR-V1-7), Э2 (FR-V2-1…FR-V2-7)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from konveyer import exporter, lang, metrics, verifier1
from konveyer.cli import app
from konveyer.schemas import Brief, Norm

runner = CliRunner()
STYLE = "02_Стиль_и_голос.md"
ROW = "| повтор_нграмма | длина межглавного повтора | 5 | 5 | — | слов |"


def _style_add(library, rows: str) -> None:
    style = library / STYLE
    style.write_text(style.read_text(encoding="utf-8").replace(ROW, ROW + "\n" + rows), encoding="utf-8")


def _style_replace(library, old: str, new: str) -> None:
    style = library / STYLE
    text = style.read_text(encoding="utf-8")
    assert old in text
    style.write_text(text.replace(old, new), encoding="utf-8")


# ------------------------------------------------------------------ нормы: сторона брака


def test_брак_сторона_по_коридору():
    """Сторона порога брака — по положению относительно коридора, а не по наличию «мин» (FR-V1-2, FR-V1-5)."""
    upper = Norm(min=0.15, max=0.3, brak=0.5)
    assert metrics.brak_side(upper) == "верх"
    assert metrics.status_of(0.2, upper) == "PASS" and metrics.status_of(0.4, upper) == "FLAG"
    assert metrics.status_of(0.6, upper) == "BRAK"
    lower = Norm(min=9, max=12, brak=7)
    assert metrics.brak_side(lower) == "низ"
    assert metrics.status_of(10, lower) == "PASS" and metrics.status_of(8, lower) == "FLAG"
    assert metrics.status_of(6, lower) == "BRAK" and metrics.status_of(13, lower) == "FLAG"
    assert metrics.status_of(450, Norm(min=300, max=600, brak=900)) == "PASS"
    assert metrics.status_of(950, Norm(min=300, max=600, brak=900)) == "BRAK"
    assert metrics.brak_side(Norm(min=0.46, brak=0.4)) == "низ" and metrics.brak_side(Norm(max=1, brak=2)) == "верх"
    # одинокий брак и брак внутри коридора — сторона неопределима: брака нет, а экспорт сообщает
    assert metrics.brak_side(Norm(brak=7)) is None and metrics.status_of(3, Norm(brak=7)) == "PASS"
    assert metrics.brak_side(Norm(min=1, max=3, brak=2)) is None
    problems = dict(metrics.norm_problems({"а": Norm(brak=7), "б": Norm(min=1, max=3, brak=2), "в": lower}))
    assert "без мин/макс" in problems["а"] and "внутри коридора" in problems["б"] and "в" not in problems


def test_норма_только_брак_ошибка_экспорта(ws, library):
    _style_replace(library, "| ttr_мин | минимальный TTR в окне | 0.46 | — | — | доля |",
                   "| ttr_мин | минимальный TTR в окне | — | — | 0.4 | доля |")
    with pytest.raises(exporter.MarkupError, match="ttr_мин.*брак"):
        exporter.run_export(library, ws.exports, ws.logs)


def test_брак_внутри_коридора_ошибка_экспорта(ws, library):
    _style_replace(library, "| средняя_длина | средняя длина фразы | 9 | 12 | 7 | слов |",
                   "| средняя_длина | средняя длина фразы | 9 | 12 | 10 | слов |")
    with pytest.raises(exporter.MarkupError, match="средняя_длина.*внутри коридора"):
        exporter.run_export(library, ws.exports, ws.logs)


def test_норма_без_параметра_ошибка_экспорта(ws, library):
    """Норма объявлена, а норма-параметр отсутствует — не молчание, а ошибка с именем параметра (FR-V1-2)."""
    _style_replace(library, "| короткая_фраза_порог | порог «короткой» фразы | 6 | 6 | — | слов |\n", "")
    with pytest.raises(exporter.MarkupError, match="доля_коротких.*короткая_фраза_порог"):
        exporter.run_export(library, ws.exports, ws.logs)


def test_ttr_норма_без_мин_не_падает(ws):
    """Приёмка §7.5: проверки молчат, а не падают — норма ttr_мин без «мин» (только макс) допустима."""
    norms = exporter.load_norms(ws.exports)
    norms["ttr_окно_слов"] = Norm(min=10, max=10, unit="слов")
    text = "Слово " * 30
    for n in (Norm(max=0.9, unit="доля"), Norm(min=0.46, brak=0.4, unit="доля"), Norm(brak=0.4, unit="доля")):
        norms["ttr_мин"] = n
        checks = {c.check_id: c for c in verifier1.analyze(text, "", Brief(chapter=0), norms, [])}
        assert "V1.8b_ttr_окно" in checks
    assert checks["V1.8b_ttr_окно"].threshold.startswith("брак 0.4")
    norms["ttr_мин"] = Norm(min=0.46, brak=0.4, unit="доля")
    checks = {c.check_id: c for c in verifier1.analyze(text, "", Brief(chapter=0), norms, [])}
    assert checks["V1.8b_ttr_окно"].status == "BRAK" and "мин 0.46, брак 0.4" in checks["V1.8b_ttr_окно"].threshold


def test_ttr_окно_только_по_черновику(ws):
    """Минимум TTR — по окнам, захватывающим текст главы: низкое разнообразие принятых глав черновик
    не исправит, и БРАК за него — бессмысленный авто-повтор (FR-V1-5)."""
    (ws.corpus / "Том1_Глава01.txt").write_text("слово " * 200, encoding="utf-8")
    norms = exporter.load_norms(ws.exports)
    norms["ttr_окно_слов"] = Norm(min=50, max=50, unit="слов")
    norms["ttr_мин"] = Norm(min=0.5, brak=0.3, unit="доля")
    draft = " ".join(f"уникальное{i}" for i in range(120)) + "."
    c = next(c for c in verifier1.analyze(draft, "", Brief(chapter=2), norms, [], corpus_dir=ws.corpus)
             if c.check_id == "V1.8b_ttr_окно")
    assert c.status == "PASS" and "справочно: минимум по окнам принятых глав 0.020" in c.note


def test_объём_без_допуска_проверяется_по_коридору(ws):
    """Бриф задаёт объём, нормы «объём_допуск» нет — объём проверяется по коридору «объём_главы» (FR-V1-1)."""
    norms = {k: v for k, v in exporter.load_norms(ws.exports).items() if k != "объём_допуск"}
    norms["объём_главы"] = Norm(min=100, max=200, unit="слов")
    c = next(c for c in verifier1.analyze("Три слова тут.", "", Brief(chapter=0, volume_words=300), norms, [])
             if c.check_id == "V1.2e_объём")
    assert c.status == "FLAG" and "объём_допуск" in c.note and "300" in c.note
    norms["объём_допуск"] = Norm(max=0.15)
    ids = [c.check_id for c in verifier1.analyze("Три слова тут.", "", Brief(chapter=0, volume_words=300), norms, [])]
    assert ids.count("V1.2e_объём") == 1


# ------------------------------------------------------------------ лексемные метрики


def test_лексемные_метрики_живут_в_норме_а_не_в_реестре(ws, library, monkeypatch):
    """Список слов — из нормы при каждом прогоне: правка документа стиля действует сразу, реестр не засоряется (FR-V1-1)."""
    text = "Ветер дул. Дождь шёл. Ветер стих."
    n1 = {"лексемы_погода": Norm(max=1, unit="ветер на 100 слов")}
    n2 = {"лексемы_погода": Norm(max=1, unit="дождь на 100 слов")}
    c1 = next(c for c in verifier1.analyze(text, "", Brief(chapter=0), n1, []) if c.check_id == "V1.3_лексемы_погода")
    c2 = next(c for c in verifier1.analyze(text, "", Brief(chapter=0), n2, []) if c.check_id == "V1.3_лексемы_погода")
    assert c1.note.startswith("2 вхождений") and c2.note.startswith("1 вхождений")
    assert "лексемы_погода" not in metrics.REGISTRY
    assert metrics.unknown_norms(n1) == [] and metrics.unknown_norms({"лексемы_плохо": Norm(unit="доля")}) == ["лексемы_плохо"]
    # `konveyer нормы` описывает лексемную норму без предварительного экспорта в том же процессе
    _style_add(library, "| лексемы_ветер | плотность ветра | — | 1 | — | ветер, обрывки на 100 слов |")
    exporter.run_export(library, ws.exports, ws.logs)
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["нормы"])
    assert r.exit_code == 0 and "лексемы_ветер" in r.output and "плотность лексем ветер, обрывки" in r.output
    assert "неизвестная метрика" not in r.output


# ------------------------------------------------------------------ языковой слой


def test_стоп_лист_ловит_слова_с_ё():
    """Слово стоп-листа с «ё» находится в прозе с «ё» и с «е» (FR-V1-4, FR-V1-5)."""
    L = lang.get()
    assert metrics.find_items("Он сказал чёрт и ещё раз чёрт.", ["чёрт", "ещё"], L) == ["чёрт", "ещё"]
    assert metrics.find_items("Он сказал черт.", ["чёрт"], L) == ["чёрт"]
    assert metrics.find_items("Тётя серьёзно посмотрела.", ["тётя", "серьёзно"], L) == ["тётя", "серьёзно"]
    assert metrics.quote_sentences(["Чёрт возьми.", "Тихо."], {"черт"}, L) == ["Чёрт возьми."]
    assert not L.item_pattern("чёрт").search("чертёж")


def test_сплиттер_продолжение_фразы_и_сокращения():
    """Терминатор перед строчной буквой — не граница; контекстное сокращение не после числа; сокращение
    в начале фразы; инициалы против одиночной заглавной (FR-V1-3)."""
    L = lang.get()
    assert L.split_sentences("Он кивнул… потом отвернулся. Всё.") == ["Он кивнул… потом отвернулся.", "Всё."]
    assert L.split_sentences("Это было... давно. Да.") == ["Это было... давно.", "Да."]
    assert L.split_sentences("— Стой! — крикнул он. Она замерла.") == ["— Стой! — крикнул он.", "Она замерла."]
    assert L.split_sentences("Он жил в г. Москве. Потом уехал.") == ["Он жил в г. Москве.", "Потом уехал."]
    assert L.split_sentences("Это было в 1995 г. Москва спала.") == ["Это было в 1995 г.", "Москва спала."]
    assert L.split_sentences("Было в 1995 г. в мае.") == ["Было в 1995 г. в мае."]
    assert L.split_sentences("Завод им. Ленина стоял. Всё.") == ["Завод им. Ленина стоял.", "Всё."]
    assert L.split_sentences("Ул. Ленина. Конец.") == ["Ул. Ленина.", "Конец."]
    assert L.split_sentences("Потом группа Б. Конец.") == ["Потом группа Б.", "Конец."]
    assert L.split_sentences("Лемм А. Х. подписал и вышел.") == ["Лемм А. Х. подписал и вышел."]
    assert L.split_sentences("Цена 3.5 рубля. Ладно.") == ["Цена 3.5 рубля.", "Ладно."]


def test_речь_персонажа_после_атрибуции_не_повествование(ws):
    """После атрибуции реплика продолжается — она не речь повествователя, стоп-лист линии её не ловит (FR-V1-4)."""
    L = lang.get()
    assert L.narration_only("— Иди, — сказал он. — Отец ждёт тебя, сынок.") == "сказал он"
    assert L.narration_only("— Сынок, — сказал сосед, — иди домой, отец ждёт.") == "сказал сосед"
    assert L.narration_only("— Иди, — сказал он. Она вздохнула. — Ладно.") == "сказал он. Она вздохнула"
    assert L.narration_only("— Сынок!\n\nОн промолчал.") == "Он промолчал."
    from konveyer.schemas import StopRule

    rules = [StopRule(scope="0.3", rule_id="Л-1", items=["отец"], applies_to={"focal": "Штерн"}, action="запрет")]
    brief = Brief(chapter=1, focal="Штерн")
    text = "Штерн вошёл.\n\n— Иди, — сказал Бугаев. — Отец ждёт тебя, сынок.\n\nШтерн промолчал."
    checks = {c.check_id: c for c in verifier1.analyze(text, "", brief, {}, rules)}
    assert checks["V1.5_стоп_лексика"].status == "PASS"
    # цитаты нарушения — из повествования, а не из реплик персонажей
    text2 = "— Отец ждёт, — сказал сосед.\n\nОн вспомнил отца и промолчал."
    c = {c.check_id: c for c in verifier1.analyze(text2, "", brief, {}, rules)}["V1.5_стоп_лексика"]
    assert c.status == "FLAG" and c.quotes == ["Он вспомнил отца и промолчал."]


def test_абзацы_по_строкам_и_тире():
    """Текст с одинарными переносами: строка с тире реплики — абзац; без пустых строк — абзац на строку (FR-V1-1)."""
    L = lang.get()
    assert L.paragraphs("Он шёл.\n— Стой!\nОн встал.") == ["Он шёл.", "— Стой!", "Он встал."]
    assert L.paragraphs("Он шёл\nдолго.\n\n— Стой!\nОн встал.") == ["Он шёл долго.", "— Стой! Он встал."]
    assert L.paragraphs("Он шёл\nдолго.\n\nВсё.") == ["Он шёл долго.", "Всё."]
    text = "Он шёл.\n— Стой!\n— Иду.\nОн встал."
    checks = {c.check_id: c for c in verifier1.analyze(text, "", Brief(chapter=0), {"доля_диалога": Norm(min=0, max=1)}, [])}
    assert checks["V1.9a_доля_диалога"].actual == "0.5"


def test_основы_с_мягким_знаком():
    L = lang.get()
    assert L.stems("сеть") == ["сет"]
    for form in ("сеть", "сети", "сетью", "сетей"):
        assert L.item_pattern("сеть").search(f"в {form} города"), form
    assert not L.item_pattern("сеть").search("сетка") and not L.item_pattern("сын").search("сынок")


def test_язык_маркеры_документа_и_буквы_из_yaml(tmp_path):
    """Классы букв и маркеры документа-вставки — данные языка, а не константы кода (FR-V1-3)."""
    (tmp_path / "языки").mkdir()
    (tmp_path / "языки" / "ru.yaml").write_text("документ_начало: '>>> ДОК'\nдокумент_конец: '<<< ДОК'\n", encoding="utf-8")
    Lp = lang.get("ru", tmp_path)
    raw = "Проза.\n\n>>> ДОК\nРапорт.\n<<< ДОК\n\nЕщё проза."
    assert Lp.strip_document_inserts(raw) == "Проза.\n\n\nЕщё проза." and Lp.has_document_insert(raw)
    brief = Brief(chapter=1, documents=["№1: рапорт"])
    c = next(c for c in verifier1.analyze(raw, "", brief, {}, [], project_root=tmp_path) if c.check_id == "V1.11_документ_вставка")
    assert c.status == "PASS" and ">>> ДОК" in c.threshold
    assert lang.get().letter == "[А-Яа-яЁёA-Za-z]" and lang.get().doc_start == lang.DOC_START


# ------------------------------------------------------------------ стоп-листы: том, контекст проекта


def test_стоп_правило_ограничено_томом(ws, library):
    """Правила могут ограничиваться линией, годом и томом (FR-V1-4): колонка «тома» в документе языка/повествования."""
    from konveyer import compiler, declparse, verifier2
    from konveyer.schemas import StopRule

    assert declparse.conv_volumes("2") == {"volume": {"from": 2, "to": 2}}
    assert declparse.conv_volumes("1–2") == {"volume": {"from": 1, "to": 2}} and declparse.conv_volumes("с 3") == {"volume": {"from": 3}}
    assert declparse.conv_volumes("") == {} and declparse.conv_volumes("все") == {}
    rule = StopRule(scope="0.3", rule_id="Л-9", items=["дискурс"], applies_to={"volume": {"from": 2, "to": 2}}, action="запрет")
    assert metrics.stoplist_applies(rule, Brief(chapter=1, volume=1)) is False
    assert metrics.stoplist_applies(rule, Brief(chapter=1, volume=2)) is True
    doc = library / "03_Фокализация.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "| rule_id | фокал | слова/обороты | действие |\n|---|---|---|---|\n",
        "| rule_id | фокал | слова/обороты | тома | действие |\n|---|---|---|---|---|\n| Л-9 | все | дискурсивный | 2 | запрет |\n"
    ).replace("| Л-1 | Каширин | менталитет; харизма; депрессия | запрет |", "| Л-1 | Каширин | менталитет; харизма; депрессия | | запрет |")
     .replace("| Л-4 | Зоя | отец; папа | запрет |", "| Л-4 | Зоя | отец; папа | | запрет |")
     .replace("| Л-2 | Зоя | амбивалентный; экзистенциальный | запрет |", "| Л-2 | Зоя | амбивалентный; экзистенциальный | | запрет |")
     .replace("| Л-3 | все | нарратив; дискурс | запрет |", "| Л-3 | все | нарратив; дискурс | | запрет |"), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    stops = exporter.load_stoplists(ws.exports)
    r9 = next(r for r in stops if r.rule_id == "Л-9")
    assert r9.applies_to == {"all": True, "volume": {"from": 2, "to": 2}}
    assert next(r for r in stops if r.rule_id == "Л-3").applies_to == {"all": True}
    text = "Он думал про дискурсивный поворот и молчал."
    flags = lambda vol: [c for c in verifier1.analyze(text, "", Brief(chapter=1, focal="Каширин", volume=vol), {}, stops)  # noqa: E731
                         if c.check_id == "V1.5_стоп_лексика" and c.status == "FLAG"]
    assert not flags(1) and flags(2)
    # окно и Э2 показывают правило только главам своего тома
    w = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    assert "Л-9" not in w and "Л-3" in w
    from tests.test_этап3 import _to_review

    _to_review(ws, library, 1)
    system, user = verifier2.build_prompt(ws, 1, 1)
    assert "[Л-3]" in user and "[Л-9]" not in user


def test_check_и_регрессия_в_языке_проекта(ws, library, monkeypatch, tmp_path):
    """`check` и регрессия считают Э1 в контексте проекта — свои сокращения, документы главы (FR-V1-3, П-6)."""
    from konveyer import regression
    from konveyer.steps import quality

    (ws.root / "языки").mkdir()
    (ws.root / "языки" / "ru.yaml").write_text("сокращения: [\"зав.\"]\n", encoding="utf-8")
    text = "Пришёл зав. складом и долго молчал у ворот. Потом ушёл домой."
    f = tmp_path / "фрагмент.md"
    f.write_text(text, encoding="utf-8")
    monkeypatch.chdir(ws.root)
    seen = {}
    monkeypatch.setattr(quality, "_print_verdict", lambda v: seen.update({c.check_id: c for c in v.checks}))
    quality.check(f)
    assert seen["V1.2a_средняя_длина"].actual == "5.5"  # «зав.» — не конец фразы (без словаря проекта было бы 3.67)
    test = regression.GoldenTest(test_id="т", fragment=text, context_slice={"focal": "Каширин", "year": 1995},
                                 expected_flags=["V1.2a_средняя_длина"])
    caught, missed, extra = regression.run_e1_test(ws, test)
    assert "V1.2a_средняя_длина" in caught  # 5.5 < 7 — БРАК; регрессия считает тем же языком проекта


def test_документ_главы_из_реестра_без_брифа(ws):
    """V1.11 требует блок документа и когда документ назначен только реестром документов (FR-V1-1)."""
    brief = exporter.load_brief(ws.exports, 5)
    assert brief.documents
    brief.documents = []
    checks = {c.check_id: c for c in verifier1.analyze_text(ws, 5, "Проза без документа.", brief=brief, window_raw="")}
    assert checks["V1.11_документ_вставка"].status == "BRAK" and "№1" in checks["V1.11_документ_вставка"].note
    with_doc = "Проза.\n\n→ ДОКУМЕНТ\nРапорт.\n← КОНЕЦ ДОКУМЕНТА\n"
    checks = {c.check_id: c for c in verifier1.analyze_text(ws, 5, with_doc, brief=brief, window_raw="")}
    assert checks["V1.11_документ_вставка"].status == "PASS"
    checks = {c.check_id: c for c in verifier1.analyze_text(ws, 2, "Проза.", window_raw="")}
    assert "V1.11_документ_вставка" not in checks  # у главы 2 документа нет


# ------------------------------------------------------------------ регрессия


def test_регрессия_пересобирает_выгрузки(ws, monkeypatch):
    """`konveyer регрессия` на свежем проекте без выгрузок не падает — экспорт делается сам (FR-RG-2, П-5)."""
    import shutil

    shutil.rmtree(ws.exports)
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["регрессия"])
    assert "norms.json не найдена" not in r.output and (ws.exports / "norms.json").exists()
    assert "ЗЕЛЁНАЯ" in r.output, r.output


def test_золотой_тест_со_срезом_главы(ws, library, monkeypatch, tmp_path):
    """add-golden берёт срез контекста из брифа и окна главы, где ошибка поймана (FR-RG-1)."""
    from konveyer import compiler, regression

    compiler.compile_window(ws, library, 2)
    frag = tmp_path / "фрагмент.md"
    frag.write_text("Он вышел на платформу и остановился. Пиши прозу главы строго по этому окну и не выходи за бриф.\n",
                    encoding="utf-8")
    monkeypatch.chdir(ws.root)
    r = runner.invoke(app, ["add-golden", "красный_окно", str(frag), "--expect", "V1.6_утечка_окна", "--глава", "2", "--корпус"])
    assert r.exit_code == 0, r.output
    test = next(t for t in regression.load_tests(ws) if t.test_id == "красный_окно")
    ctx = test.context_slice
    assert ctx["chapter"] == 2 and ctx["focal"] == "Каширин" and ctx["year"] == 1995 and ctx["volume_words"] == 350
    assert ctx["not_knows"] and ctx["use_corpus"] is True and "<!-- СЕКЦИЯ: бриф -->" in ctx["window"]
    win = tmp_path / "окно.md"
    win.write_text("Пиши прозу главы строго по этому окну и не выходи за бриф.", encoding="utf-8")
    r = runner.invoke(app, ["add-golden", "красный_окно2", str(frag), "--expect", "V1.6_утечка_окна", "--окно", str(win), "--объём", "300"])
    assert r.exit_code == 0, r.output
    t2 = next(t for t in regression.load_tests(ws) if t.test_id == "красный_окно2")
    assert t2.context_slice["window"].startswith("Пиши прозу") and t2.context_slice["volume_words"] == 300
    caught, missed, extra = regression.run_e1_test(ws, t2)
    assert "V1.6_утечка_окна" in caught


# ------------------------------------------------------------------ дифф-контроль


def test_диффконтроль_короткое_стало_не_отмывает(ws):
    """Правка «Он» → «Она» не объясняет дописанное предложение со словом «Она» (FR-V1-6, FR-ED-3)."""
    from konveyer.schemas import Edit

    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "черновик_1.md").write_text("Он положил её в карман. Чай остыл. Зоя не звонила.", encoding="utf-8")
    (d / "черновик_2.md").write_text("Она положила её в карман. Чай остыл. Она ушла навсегда и больше не вернулась. Зоя не звонила.",
                                    encoding="utf-8")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Он ", after="Она ")])
    assert report.unauthorized == ["Она ушла навсегда и больше не вернулась."] and not report.clean
    assert report.applied_share == 1.0  # «Она положила» — та же правка с согласованием, не самоволка


def test_диффконтроль_полный_список_самоволий(ws):
    from konveyer.schemas import Edit

    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "черновик_1.md").write_text("Чай остыл.", encoding="utf-8")
    (d / "черновик_2.md").write_text("Чай остыл. " + " ".join(f"Новое предложение номер {i}." for i in range(25)), encoding="utf-8")
    report = verifier1.diff_check(ws, 1, 1, 2, [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл.")])
    assert len(report.unauthorized) == 25
    waived, missing = verifier1.waive_unauthorized(ws, 1, report, ["25"])
    assert waived == ["Новое предложение номер 24."] and not missing
