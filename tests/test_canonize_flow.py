"""Сквозной оффлайн-такт (без API): review → правки → канонизация → атомарный коммит.

Проверяет критерии этапа 2: приёмка порождает корректный атомарный коммит;
запись в библиотеку — только через канониста (FR-CN-2, П-3); корпус пересчитан.
"""

import json
import subprocess

import pytest

from konveyer import canonist, compiler, gitops, review
from konveyer.config import Config
from konveyer.fsm import ChapterState
from konveyer.schemas import Flag, Resolution


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _git(lib, *args):
    subprocess.run(["git", "-C", str(lib), *args], check=True, capture_output=True)


def test_полный_такт_с_коммитом(ws, library):
    _git(library, "init")
    _git(library, "config", "user.email", "автор@example.com")
    _git(library, "config", "user.name", "Автор")
    _git(library, "add", "-A")
    _git(library, "commit", "-m", "канон: начальное состояние")

    cfg = Config()
    chapter = 1

    # машинные шаги такта (черновик кладём руками — API в тесте нет, ручной режим)
    compiler.compile_window(ws, library, chapter)
    st = ChapterState(ws, chapter)
    st.transition("собрано", "compile")
    draft = ws.draft_path(chapter, 1)
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(
        "Каширин нашёл записку утром возле хлебницы на кухонном столе. Бумага пахла чужим табаком и сыростью подъезда.",
        encoding="utf-8",
    )
    st.set_draft(1)
    st.transition("сгенерировано", "write")
    st.transition("верифицировано-1", "verify1")

    # флаги Э2 с самоволкой (вручную, как в ручном режиме)
    from konveyer import verifier2

    verifier2.save_flags(
        ws,
        chapter,
        [
            Flag(
                flag_id="F-001", type="самоволка", quote="Бумага пахла чужим табаком",
                rule="в брифе запаха нет", kind="samovolka",
            )
        ],
    )
    st.transition("верифицировано-2", "verify2")

    review.build_review_pack(ws, chapter, 1)
    st.transition("на-приёмке", "review")
    assert (ws.chapter_dir(chapter) / "приёмка.md").exists()
    resolutions = review.load_resolutions(ws, chapter)
    assert [r.flag_id for r in resolutions] == ["F-001"]

    # автор решает: канонизировать самоволку; правок нет — черновик едет дальше
    (ws.chapter_dir(chapter) / "решения.json").write_text(
        json.dumps([Resolution(flag_id="F-001", decision="канонизировать", target_registry="эпистемика").model_dump()],
                   ensure_ascii=False),
        encoding="utf-8",
    )
    review.save_edits(ws, chapter, [])
    import shutil

    shutil.copyfile(draft, ws.draft_path(chapter, 2))
    st.set_draft(2)
    st.transition("правки", "apply-edits")
    from konveyer import verifier1

    report = verifier1.diff_check(ws, chapter, 1, 2, [])
    assert report.clean
    st.transition("дифф-контроль", "diff-check")
    st.transition("принято", "accept")

    # канонист: пакет (деградация без LLM) и применение
    batch = canonist.build_batch(ws, cfg, chapter, 2)
    assert batch.exists()
    assert "F-001" in batch.read_text(encoding="utf-8")
    head_before = gitops.head(library)
    commit = canonist.apply_batch(ws, cfg, library, chapter, 2)
    st.transition("зафиксировано", "canonize --apply")

    # атомарный коммит с шаблонным сообщением (FR-CN-2)
    assert commit != head_before
    log = subprocess.run(
        ["git", "-C", str(library), "log", "-1", "--format=%s"], capture_output=True, text=True, encoding="utf-8"
    ).stdout
    assert "[глава 1]" in log and "приёмка" in log
    assert not gitops.dirty(library)

    # текст главы в Проза/, корпус пересчитан, самоволка в реестре 3.1
    assert (library / "Проза" / "Том1_Глава01.md").exists()
    assert (ws.corpus / "Том1_Глава01.txt").exists()
    assert "табаком" in (library / "31_Матрица_знаний.md").read_text(encoding="utf-8")
    assert gitops.find_chapter_commit(library, 1) == commit

    # метрики для дашборда записаны
    metrics = (ws.logs / "метрики.jsonl").read_text(encoding="utf-8")
    assert '"chapter": 1' in metrics


def test_канонизация_требует_решений_по_самоволкам(ws, library):
    cfg = Config()
    d = ws.chapter_dir(2)
    d.mkdir(parents=True, exist_ok=True)
    ws.draft_path(2, 1).write_text("Текст.", encoding="utf-8")
    (d / "решения.json").write_text(
        json.dumps([{"flag_id": "F-009", "decision": None}]), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="F-009"):
        canonist.build_batch(ws, cfg, 2, 1)


def test_разбор_edits_md(ws):
    d = ws.chapter_dir(3)
    d.mkdir(parents=True, exist_ok=True)
    (d / "правки.md").write_text(
        "БЫЛО: Чай остыл.\nСТАЛО: Чай остыл давно.\n\nУКАЗАНИЕ: убрать вторую сентенцию про жизнь\n",
        encoding="utf-8",
    )
    edits = review.parse_edits_md(ws, 3)
    assert len(edits) == 2
    assert edits[0].before == "Чай остыл." and edits[0].after == "Чай остыл давно."
    assert edits[1].note == "свободное указание"
    assert (d / "правки.jsonl").exists()  # 5.3


def _accepted(ws, library, chapter: int = 1) -> None:
    """Глава текущего тома `ws.volume` доведена до «принято» с пакетом Канониста (без моделей)."""
    import shutil

    from konveyer import exporter, verifier1, verifier2

    exporter.run_export(library, ws.exports, ws.logs, ws.volume, ws.root)
    compiler.compile_window(ws, library, chapter)
    st = ChapterState(ws, chapter)
    st.transition("собрано", "compile")
    ws.draft_path(chapter, 1).write_text("Каширин нашёл записку утром возле хлебницы.", encoding="utf-8")
    st.set_draft(1)
    for state, cmd in (("сгенерировано", "write"), ("верифицировано-1", "verify1"), ("верифицировано-2", "verify2")):
        st.transition(state, cmd)
    verifier2.save_flags(ws, chapter, [])
    review.build_review_pack(ws, chapter, 1)
    st.transition("на-приёмке", "review")
    review.save_edits(ws, chapter, [])
    shutil.copyfile(ws.draft_path(chapter, 1), ws.draft_path(chapter, 2))
    st.set_draft(2)
    st.transition("правки", "apply-edits")
    verifier1.diff_check(ws, chapter, 1, 2, [])
    st.transition("дифф-контроль", "diff-check")
    st.transition("принято", "accept")
    canonist.build_batch(ws, Config(), chapter, 2)


def test_приёмка_одноимённой_главы_во_втором_томе(ws, library, monkeypatch):
    """Глава 1 тома 2 не «восстанавливается» по коммиту главы 1 тома 1: свой коммит, своя проза, свой тег;
    откат зафиксированной главы тома 2 ревертит именно её коммит."""
    from konveyer.config import set_volume
    from konveyer.steps import canon as canon_steps, tact

    monkeypatch.chdir(ws.root)
    _git(library, "init")
    _git(library, "config", "user.email", "автор@example.com")
    _git(library, "config", "user.name", "Автор")
    _git(library, "add", "-A")
    _git(library, "commit", "-m", "канон: начальное состояние")

    _accepted(ws, library, 1)
    c1 = tact.canonize(1, apply=True, yes=True)
    assert gitops.find_chapter_commit(library, 1) == c1 and gitops.find_chapter_commit(library, 1, 2) is None

    set_volume(ws, 2)
    ws2 = ws.for_volume(2)
    _accepted(ws2, library, 1)
    c2 = tact.canonize(1, apply=True, yes=True)
    assert c2 != c1 and gitops.head(library) == c2
    assert (library / "Проза" / "Том2_Глава01.md").exists()
    assert ChapterState(ws2, 1).state == "зафиксировано" and ChapterState(ws2, 1).data["коммит_приёмки"] == c2
    subject = subprocess.run(["git", "-C", str(library), "log", "-1", "--format=%s"], capture_output=True,
                             text=True, encoding="utf-8").stdout
    assert subject.startswith("[том 2 глава 1]")
    assert gitops.find_chapter_commit(library, 1, 2) == c2 and gitops.find_chapter_commit(library, 1, 1) == c1
    assert gitops.tags(library) == ["глава-1", "том2-глава-1"]

    # откат главы тома 2 ревертит её коммит, а не коммит тома 1
    st = ChapterState(ws2, 1)
    st.data.pop("коммит_приёмки")
    st._save()
    canon_steps.rollback(1, to="принято", yes=True)
    assert gitops.find_chapter_commit(library, 1, 2) is None and gitops.find_chapter_commit(library, 1, 1) == c1
    assert (library / "Проза" / "Том1_Глава01.md").exists() and not (library / "Проза" / "Том2_Глава01.md").exists()
