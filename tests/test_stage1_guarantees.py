"""Этап 1 аудита: гарантии ТЗ — FR-C3 (окно без тайн), FR-K2/K3 (git в пределах библиотеки, откат по SHA,
guard для всех потоков), FR-E3 (цикл правок от базы приёмки), защита разметки от flag_id."""

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from konveyer import compiler, exporter, gitops, guard, verifier2
from tests import профиль
from tests.профиль import realcanon
from konveyer.cli import app
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution
from tests.общие import _git, _init_repo

REPO = Path(__file__).resolve().parent.parent


# пометки поглавника, адресованные читателю или инструменту (аудит 2, 1.10) — в окне их быть не должно
READER_MARKERS = (
    "читатель знает", "читатель узна", "читатель ещё не знает", "читателю-перечитывателю", "саспенс читателя",
    "матрица №", "→ т.", "ЗАКЛАДКА →", "⚠", "🔧", "реш. при прозе", "эхо в гл", "улика слоя 1 №", "арка-парабола",
)


# ------------------------------------------------------------------ FR-C3


# --------------------------------------------------- фильтр досье (юнит)


@pytest.fixture(autouse=True)
def _ugar_markers():
    with профиль.markers():
        yield


def test_фильтр_фраз_проверяет_все_ссылки_и_траектории():
    f = compiler._safe_sentences
    assert f("от искры (т.1) к браку", [], 1) == ""                       # траектория через тома
    assert f("коллега (т.1). Служит в МУРе с т.1.", [], 1) == "коллега (т.1). Служит в МУРе с т.1."
    assert f("знакомы с т.1; с т.8 — знание и молчание", [], 1) == ""     # вторая ссылка — будущее
    assert f("«мальчик из папки» → напарник (т.9–10)", [], 1) == ""
    assert f("до т.6 — коллега", [], 1) == "" and f("в томе 3 узнаёт", [], 1) == ""
    assert f("Служил в цикле II", [], 1) == ""


def test_фильтр_убирает_список_целиком_и_пометки_инструменту():
    f = compiler._safe_sentences
    text = ("Тройная идентичность: для МУРа — буржуазный спец; для ОГПУ — карманный инструмент; "
            "тайно — спящий актив сети. Рожд. ≈1871.")
    assert f(text, ["актив"], 1) == "Рожд. ≈1871."                          # обрывка «…инструмент;» нет
    assert f(text, [], 1) == text                                           # без маркера список цел
    assert f("надзиратель → инструмент → сын-по-выбору; вектор: расчёт", ["расчёт"], 1) == ""
    assert f("Закон: лозунги — хуже (держать в каждой сцене; инструмент обязан проверять корреляцию).", [], 1) \
        == "Закон: лозунги — хуже."
    assert f("канал присмотра (⚠)", [], 1) == "канал присмотра"
    assert f("куратор вербовки (⚠ уточнить при арке т.7)", [], 1) == "куратор вербовки"
    assert f("⚠ решить при арке. Спокоен.", [], 1) == "Спокоен."


def test_разбор_кто_знает_и_слияние_с_матрицей():
    names = {"Лемм", "Штерн", "Степан", "Заварзин"}
    reg = realcanon._parse_known_by("Степан; Штерн — гл. 45", names)
    assert reg == {"Степан": None, "Штерн": 45}
    assert realcanon._parse_known_by("Никто. Ответ — том 2", names) == {}
    assert realcanon._parse_known_by("Лемм; Заварзин узнает в томе 3", names) == {"Лемм": None}
    assert realcanon._merge_known(reg, {"Степан": 43, "Штерн": 45}) == {"Степан": 43, "Штерн": 45}
    assert realcanon._merge_known(reg, {}) == {"Степан": 0, "Штерн": 45}                 # нигде главы нет — всегда
    assert realcanon._merge_known({"Лемм": 17}, {"Лемм": 3, "Штерн": 7}) == {"Лемм": 3, "Штерн": 7}  # ранняя


def test_сопоставление_тайны_с_фактом_матрицы():
    from konveyer.schemas import MatrixFact

    def fact(fid, text):
        return MatrixFact(fact_id=fid, fact=text, subject="Читатель", from_chapter=1)

    matrix = [fact("М-12", "Посредника убила «третья рука» (не сеть, не ОГПУ)"),
              fact("М-14", "Первый подложный рапорт Степана (№6)"),
              fact("М-15", "Штерн укрыл подлог Степана")]
    assert realcanon._match_matrix_fact("Кто убил поляка-резидента («третья рука»)", matrix) is None  # 2 слова
    assert realcanon._match_matrix_fact("Степан совершил подлог в рапорте", matrix) == "М-14"          # основы
    assert realcanon._match_matrix_fact("Кто убил поляка (М-12)", matrix) == "М-12"                  # явная ссылка
    assert realcanon._match_matrix_fact("Кто убил поляка (факт 15)", matrix) == "М-15"


# ------------------------------------------------------------- flag_id / XSS


def test_flag_id_только_безопасные_символы():
    with pytest.raises(ValidationError, match="flag_id"):
        Flag(flag_id='x" onmouseover="alert(1)', type="т", quote="q", rule="r")
    with pytest.raises(ValidationError, match="target_registry"):
        Resolution(flag_id="F-001", decision="канонизировать", target_registry="3.1<script>")
    raw = json.dumps([{"flag_id": 'F"><img src=x onerror=alert(1)>', "type": "т", "quote": "q", "rule": "r",
                       "severity": "важно", "recommendation": "", "kind": "violation"}], ensure_ascii=False)
    flags = verifier2.parse_flags(raw)
    assert flags[0].flag_id == "F-001"


# ------------------------------------------------------------- guard / git


def test_guard_действует_во_всех_потоках(ws, library):
    errors: list[Exception] = []

    def worker():
        try:
            guard.write_text(library / "взлом.md", "x")
        except guard.CanonWriteError as e:
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert errors and not (library / "взлом.md").exists()


def test_git_только_в_пределах_библиотеки(ws, library):
    """Библиотека внутри репозитория кода: посторонние файлы не мешают и не попадают в коммит приёмки."""
    _init_repo(ws.root)
    (ws.root / "postoronniy.txt").write_text("вне библиотеки", encoding="utf-8")
    assert not gitops.dirty(library)  # грязь вне библиотеки — не грязь библиотеки
    assert gitops.commit_all(library, "[глава 9] пусто") is None
    (library / "Проза" / "Том1_Глава09.md").write_text("Глава.\n", encoding="utf-8")
    sha = gitops.commit_all(library, "[глава 9] приёмка")
    assert sha and _git(ws.root, "show", "--name-only", "--format=", sha).strip() == "Библиотека/Проза/Том1_Глава09.md"
    assert (ws.root / "postoronniy.txt").exists() and "postoronniy" in _git(ws.root, "status", "--porcelain")


def test_откат_не_ревертит_реверт_и_не_трогает_чужие_файлы(ws, library):
    _init_repo(ws.root)
    (library / "Проза" / "Том1_Глава09.md").write_text("Глава.\n", encoding="utf-8")
    sha = gitops.commit_all(library, "[глава 9] приёмка: записей 0")
    gitops.revert(library, sha)
    assert not (library / "Проза" / "Том1_Глава09.md").exists()
    # откачённая приёмка — не приёмка: повторный canonize --apply не должен её «находить» (4.1)
    assert gitops.find_chapter_commit(library, 9) is None
    (library / "Проза" / "Том1_Глава09.md").write_text("Глава заново.\n", encoding="utf-8")
    sha2 = gitops.commit_all(library, "[глава 9] приёмка: повторная")
    assert gitops.find_chapter_commit(library, 9) == sha2  # новая приёмка после отката — действующая
    # коммит, задевающий файл вне библиотеки, откатить нельзя
    (ws.root / "код.py").write_text("x", encoding="utf-8")
    (library / "Проза" / "Том1_Глава09.md").write_text("снова", encoding="utf-8")
    _git(ws.root, "add", "-A"); _git(ws.root, "commit", "-q", "-m", "[глава 9] смешанный")
    mixed = _git(ws.root, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="вне библиотеки"):
        gitops.revert(library, mixed)
    assert (ws.root / "код.py").exists() and not _git(ws.root, "status", "--porcelain")


# ------------------------------------------------------------- FR-E3 база


def test_цикл_правок_стартует_от_базы_приёмки(ws, library, monkeypatch):
    """черновик_1 на приёмке → правки дают черновик_2 с самоволием → повторный цикл идёт от черновик_1, а не от черновик_2."""
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    runner = CliRunner()
    n = 1
    st = ChapterState(ws, n)
    ws.chapter_dir(n).mkdir(parents=True, exist_ok=True)
    ws.draft_path(n, 1).write_text("Первая фраза. Вторая фраза. Третья фраза.\n", encoding="utf-8")
    for state, cmd in (("собрано", "compile"), ("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    st.data["черновик"] = 1; st._save()
    ws.chapter_dir(n).joinpath("вердикт.json").write_text(json.dumps({"chapter": 1, "draft": 1, "checks": []}), encoding="utf-8")
    ws.chapter_dir(n).joinpath("флаги.json").write_text("[]", encoding="utf-8")
    assert runner.invoke(app, ["review", str(n)]).exit_code == 0
    assert ChapterState(ws, n).data["база_приёмки"] == 1
    # пара применяется кодом (Р-023), указание уходит Писателю — от промежуточного текста
    (ws.chapter_dir(n) / "правки.md").write_text(
        "БЫЛО: Вторая фраза.\nСТАЛО: Другая фраза.\n\nУКАЗАНИЕ: оживить финал\n", encoding="utf-8"
    )
    # «Писатель» вносит правку и добавляет самоволие
    calls: list[int] = []

    def fake_apply(ws_, cfg, chapter, base_k, edits, new_k=None, base_text=None, **kw):
        calls.append(base_k)
        assert "Другая фраза." in base_text and [e.seq for e in edits] == [2]  # кодовая правка уже в тексте
        text = base_text.replace("Третья фраза.", "Третья фраза. Самовольная вставка.")
        from konveyer import writer
        writer._save_draft(ws_, chapter, new_k, text, cfg, mode="правки")
        return new_k

    from konveyer import writer
    monkeypatch.setattr(writer, "apply_edits", fake_apply)
    assert runner.invoke(app, ["apply-edits", str(n)]).exit_code == 0
    r = runner.invoke(app, ["diff-check", str(n)])
    assert "Самовольные" in r.output
    # второй цикл: автор поправил правки.md; база — по-прежнему черновик_1
    assert runner.invoke(app, ["apply-edits", str(n)]).exit_code == 0
    assert calls == [1, 1]
    r = runner.invoke(app, ["diff-check", str(n)])
    assert "Самовольные" in r.output  # самоволие видно и во втором цикле, не «отмыто»
    assert ChapterState(ws, n).draft == 3


def test_откат_зафиксированной_главы_по_sha_из_статуса(ws, library, monkeypatch):
    """canonize --apply пишет SHA в состояние.yaml; rollback ревертит его; повторный откат невозможен."""
    from konveyer import canonist, review
    from konveyer.config import Config

    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _init_repo(library)
    chapter = 1
    compiler.compile_window(ws, library, chapter)
    st = ChapterState(ws, chapter)
    st.transition("собрано", "compile")
    ws.draft_path(chapter, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(chapter, 1).write_text("Каширин нашёл записку утром возле хлебницы.", encoding="utf-8")
    st.set_draft(1)
    for state, cmd in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    verifier2.save_flags(ws, chapter, [])
    review.build_review_pack(ws, chapter, 1)
    st.transition("на-приёмке", "review")
    review.save_edits(ws, chapter, [])
    import shutil
    shutil.copyfile(ws.draft_path(chapter, 1), ws.draft_path(chapter, 2))
    st.set_draft(2)
    st.transition("правки", "apply-edits")
    from konveyer import verifier1
    verifier1.diff_check(ws, chapter, 1, 2, [])
    st.transition("дифф-контроль", "diff-check")
    st.transition("принято", "accept")
    canonist.build_batch(ws, Config(), chapter, 2)

    runner = CliRunner()
    r = runner.invoke(app, ["canonize", str(chapter), "--apply", "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, chapter)
    sha = st.data["коммит_приёмки"]
    assert st.state == "зафиксировано" and sha == gitops.head(library)
    assert (library / "Проза" / "Том1_Глава01.md").exists()

    assert (ws.corpus / "Том1_Глава01.txt").exists()  # принятая проза попала в корпус
    r = runner.invoke(app, ["rollback", str(chapter), "-y"])
    assert r.exit_code == 0, r.output
    st = ChapterState(ws, chapter)
    assert st.state == "принято" and "коммит_приёмки" not in st.data
    assert not (library / "Проза" / "Том1_Глава01.md").exists()
    # FR-SC-4: откат пересчитывает выгрузки и корпус, сбрасывает счётчики
    assert not (ws.corpus / "Том1_Глава01.txt").exists()
    index = json.loads((ws.corpus / exporter.CORPUS_INDEX).read_text(encoding="utf-8"))
    assert "Том1_Глава01.txt" not in index
    assert "Том1_Глава01" not in (ws.exports / "индекс.json").read_text(encoding="utf-8")
    assert st.data["авто_повторов"] == 0 and st.data["итераций_правок"] == 0
    # повторный откат из «принято» — обычный FSM-откат, реверт реверта невозможен
    r = runner.invoke(app, ["rollback", str(chapter), "--to", "собрано", "-y"])
    assert r.exit_code == 0, r.output
    assert not (library / "Проза" / "Том1_Глава01.md").exists()
    assert gitops.find_chapter_commit(library, chapter) is None  # приёмка откачена — действующей нет
