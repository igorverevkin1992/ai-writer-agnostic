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
