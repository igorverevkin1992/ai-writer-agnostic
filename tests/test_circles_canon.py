"""Круг истории как несущий каркас (Р-020): документ 2.1 ↔ выгрузка, окно, Э2, внесение в канон."""

import json
import subprocess

from konveyer import circles, compiler, exporter, realcanon, verifier2
from konveyer.config import Config
from konveyer.schemas import Act, CircleStep, StoryCircle

ACTS = [Act(act=1, title="МОКРОЕ ДЕЛО", from_chapter=1, to_chapter=2, parts="I", steps="1–2")]


def _sample() -> list[StoryCircle]:
    names = circles.STEP_NAMES
    book = StoryCircle(scope="книга", title="Книга (том целиком)", summary="сыщик против аппарата", weak_spot="шаг 6",
                       steps=[CircleStep(n=i + 1, name=names[i], text=f"том шаг {i + 1}", chapters=f"гл. {i + 1}")
                              for i in range(8)])
    part = StoryCircle(scope="акт", key=1, summary="первый акт",
                       steps=[CircleStep(n=1, name="Ты", text="Лемм у сейфа", chapters="гл. 1"),
                              CircleStep(n=2, name="Потребность", text="сирота рядом", chapters="гл. 1–2")])
    ch = StoryCircle(scope="глава", key=1, summary="осмотр", steps=[
        CircleStep(n=1, name="Ты", text="утро, контора", chapters="сц. 1.1, начало"),
        CircleStep(n=8, name="Изменение", text="метод показан", chapters="сц. 1.2, финал"),
    ])
    return [book, part, ch]


def test_документ_2_1_туда_и_обратно(tmp_path):
    doc = circles.render_canon_doc(_sample(), ACTS)
    assert "## Круг тома" in doc and "## Круг акта 1 «МОКРОЕ ДЕЛО» (гл. 1–2)" in doc and "## Круг главы 1" in doc
    assert "| 1 | «МОКРОЕ ДЕЛО» | 1–2 | I | 1–2 |" in doc
    path = tmp_path / "21_Круги_истории_Том1.md"
    path.write_text(doc, encoding="utf-8")
    acts = realcanon.parse_acts(path)
    assert [(a.act, a.from_chapter, a.to_chapter, a.parts) for a in acts] == [(1, 1, 2, "I")]
    parsed = realcanon.parse_circles(path)
    assert [(c.scope, c.key) for c in parsed] == [("книга", None), ("акт", 1), ("глава", 1)]
    book, part, ch = parsed
    assert book.summary == "сыщик против аппарата" and book.weak_spot == "шаг 6"
    assert [(s.from_chapter, s.to_chapter) for s in part.steps] == [(1, 1), (1, 2)]
    assert ch.steps[1].chapters == "сц. 1.2, финал" and ch.steps[1].from_chapter is None
    assert part.title == "Акт 1 «МОКРОЕ ДЕЛО»"
    # шаги тома, на которые приходится глава 2
    assert [s.n for s in book.steps_for_chapter(2)] == [2]
    assert [s.n for s in part.steps_for_chapter(2)] == [2]


def test_диапазоны_глав():
    assert realcanon.chapter_range("гл. 10–18") == (10, 18)
    assert realcanon.chapter_range("гл. 5") == (5, 5)
    assert realcanon.chapter_range("гл. 1, 3") == (1, 3)
    assert realcanon.chapter_range("сц. 5.1, финал") == (None, None)


def test_окно_и_э2_без_каркаса(ws, library):
    path, breakdown = compiler.compile_window(ws, library, 1)
    w = path.read_text(encoding="utf-8")
    assert "драматургия" in breakdown and "в канон ещё не внесён" in w
    assert exporter.load_circles(ws.exports) == []


def test_окно_и_э2_с_каркасом(ws, library):
    sample = _sample()
    (library / "21_Круги_истории_Том1.md").write_text(circles.render_canon_doc(sample, ACTS), encoding="utf-8")
    exporter.run_export(library, ws.exports, ws.logs)
    assert len(exporter.load_circles(ws.exports)) == 3 and len(exporter.load_acts(ws.exports)) == 1

    path, _ = compiler.compile_window(ws, library, 1)
    w = path.read_text(encoding="utf-8")
    assert "## Драматургия главы" in w
    assert "- Том: шаг 1 «Ты» (гл. 1) — том шаг 1" in w
    assert "- Акт 1 «МОКРОЕ ДЕЛО»: шаг 1 «Ты» (гл. 1) — Лемм у сейфа" in w
    assert "- Круг главы: осмотр" in w and "8. Изменение (сц. 1.2, финал) — метод показан" in w
    assert "в канон ещё не внесён" not in w
    # слабое место — заметка аналитика, Писателю не показывается
    assert "Слабое место" not in w
    # детерминизм окна сохраняется (FR-C4)
    assert compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8") == w

    ws.chapter_dir(1).mkdir(parents=True, exist_ok=True)
    ws.draft_path(1, 1).write_text("Текст.\n", encoding="utf-8")
    system, user = verifier2.build_prompt(ws, 1, 1)
    assert "## Драматургия: каркас круга истории" in user and "Слабое место (по оценке аналитика): шаг 6" not in user
    assert "Том: шаг 1 «Ты»" in user
    assert "8. **Драматургия**" in system and "драматургия" in system


def _init_git(lib):
    for args in (["init"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(lib), *args], check=True, capture_output=True)


def test_внесение_в_канон(ws, library, monkeypatch):
    _init_git(library)
    book, part, ch = _sample()
    circles.save_circle(ws, "книга", None, json.loads(book.model_dump_json()))
    circles.save_circle(ws, "глава", 1, json.loads(ch.model_dump_json()))
    assert circles.canon_status(ws) == {"книга": "не в каноне", "глава_01": "не в каноне"}

    path, commit = circles.commit_to_canon(ws, Config(), library)
    assert path.name == circles.CANON_DOC and len(commit) >= 7
    canon = exporter.load_circles(ws.exports)
    assert [(c.scope, c.key) for c in canon] == [("книга", None), ("глава", 1)]
    assert circles.canon_status(ws) == {"книга": "в каноне", "глава_01": "в каноне"}
    log = subprocess.run(["git", "-C", str(library), "log", "-1", "--format=%s"], capture_output=True, text=True, encoding="utf-8").stdout
    assert "круги истории" in log and "Р-020" in log

    # правка черновика → «отличается от канона»; повторное внесение заменяет только его, книга остаётся
    ch.steps[0].text = "иначе"
    circles.save_circle(ws, "глава", 1, json.loads(ch.model_dump_json()))
    assert circles.canon_status(ws)["глава_01"] == "отличается от канона"
    circles.commit_to_canon(ws, Config(), library)
    canon = {(c.scope, c.key): c for c in exporter.load_circles(ws.exports)}
    assert canon[("глава", 1)].steps[0].text == "иначе" and ("книга", None) in canon

    # окно главы теперь несёт каркас
    w = compiler.compile_window(ws, library, 1)[0].read_text(encoding="utf-8")
    assert "- Том: шаг 1 «Ты»" in w and "Круг главы" in w

    # грязный git библиотеки — отказ (защита от полузаписи)
    (library / "мусор.md").write_text("x", encoding="utf-8")
    try:
        circles.commit_to_canon(ws, Config(), library)
        raise AssertionError("ожидался отказ")
    except RuntimeError as e:
        assert "чистого git" in str(e)


def test_вложенность_материалов(ws, library):
    """Акт строится внутри шагов тома, глава — внутри шагов акта и тома (черновики поверх канона)."""
    book, part, ch = _sample()
    circles.save_circle(ws, "книга", None, json.loads(book.model_dump_json()))
    title, material = circles.build_material(ws, "глава", 1)
    assert "## Каркас уровня выше" in material and "Том: шаг 1 «Ты» (гл. 1) — том шаг 1" in material
    assert "Круг главы" not in material  # свой круг главы в материал не входит


def test_без_таблицы_актов_акты_равны_частям(ws, library):
    """Демо-библиотека без 2.1: актов нет, окно собирается без каркаса; с частями — акты = части."""
    assert exporter.load_acts(ws.exports) == []
    parts = [{"part": 1, "title": "А", "period": "", "from_chapter": 1, "to_chapter": 3},
             {"part": 2, "title": "Б", "period": "", "from_chapter": 4, "to_chapter": 6}]
    from konveyer import exporter as ex
    orig = ex.export_parts
    try:
        ex.export_parts = lambda lib, volume=1: parts
        acts = ex.export_acts(library)
    finally:
        ex.export_parts = orig
    assert [(a.act, a.title, a.parts, a.to_chapter) for a in acts] == [(1, "А", "I", 3), (2, "Б", "II", 6)]
