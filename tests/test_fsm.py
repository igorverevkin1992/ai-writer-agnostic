"""Тесты конечного автомата главы (FR-TK-2…4, FR-SC-4)."""

import pytest

from konveyer.fsm import ChapterState, TransitionError


def test_допустимая_цепочка(ws):
    st = ChapterState(ws, 1)
    for state, cmd in [
        ("собрано", "compile"), ("сгенерировано", "write"), ("верифицировано-1", "verify1"),
        ("верифицировано-2", "verify2"), ("на-приёмке", "review"), ("правки", "apply-edits"),
        ("дифф-контроль", "diff-check"), ("принято", "accept"), ("зафиксировано", "canonize"),
    ]:
        st.transition(state, cmd)
    assert st.state == "зафиксировано"
    assert len(st.data["история"]) == 9


def test_недопустимый_переход(ws):
    st = ChapterState(ws, 2)
    with pytest.raises(TransitionError):
        st.transition("принято")  # из «не-начато» сразу в «принято» нельзя


def test_авто_повтор_при_браке(ws):
    st = ChapterState(ws, 3)
    st.transition("собрано")
    st.transition("сгенерировано")
    st.transition("сгенерировано")  # брак → повторная генерация (FR-TK-3)
    assert st.bump_retries() == 1


def test_дифф_контроль_возврат_в_правки(ws):
    st = ChapterState(ws, 4)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки", "дифф-контроль"]:
        st.transition(s)
    st.transition("правки")  # самовольные изменения → цикл повторяется
    assert st.state == "правки"


def test_откат(ws):
    st = ChapterState(ws, 5)
    st.transition("собрано")
    st.transition("сгенерировано")
    st.rollback("собрано")
    assert st.state == "собрано"
    with pytest.raises(TransitionError):
        st.rollback("сгенерировано")  # вперёд — не откат


def test_зафиксировано_терминально(ws):
    st = ChapterState(ws, 6)
    for s in ["собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "на-приёмке", "правки", "дифф-контроль", "принято", "зафиксировано"]:
        st.transition(s)
    with pytest.raises(TransitionError):
        st.transition("собрано")
    with pytest.raises(TransitionError, match="git-revert"):
        st.rollback("принято")


def test_состояние_переживает_перезапуск(ws):
    st = ChapterState(ws, 7)
    st.transition("собрано")
    st2 = ChapterState(ws, 7)  # Д-3: YAML-файл, без БД
    assert st2.state == "собрано"


def test_фсм_имена_состояний_тз_как_синонимы(ws):
    """FR-TK-2: цепочка ТЗ принимается синонимами имён кода; `STATES` покрывает всю цепочку."""
    from konveyer.fsm import STATES, canonical_state

    chain = ["запланирована", "собрано", "сгенерировано", "проверено-машинно", "проверено-моделью",
             "на-приёмке", "правки", "дифф-контроль", "принято", "зафиксировано"]
    assert [canonical_state(s) for s in chain] == STATES
    st = ChapterState(ws, 8)
    for s in chain[1:]:
        st.transition(s)
    assert st.state == "зафиксировано"
    st = ChapterState(ws, 9)
    st.transition("собрано")
    st.transition("сгенерировано")
    st.transition("проверено-машинно")
    st.rollback("запланирована")
    assert st.state == "не-начато"
    st.require("запланирована")


def test_состояние_несёт_ссылки_на_артефакты(ws):
    """FR-TK-2: состояние.yaml — состояние, счётчики и пути текущих артефактов (только существующих)."""
    import yaml

    st = ChapterState(ws, 10)
    ws.chapter_dir(10).mkdir(parents=True, exist_ok=True)
    ws.window_path(10).write_text("окно", encoding="utf-8")
    st.transition("собрано")
    data = yaml.safe_load(ws.status_path(10).read_text(encoding="utf-8"))
    assert data["артефакты"] == {"окно": "главы/010/окно.md"}
    ws.draft_path(10, 2).write_text("текст", encoding="utf-8")
    (ws.chapter_dir(10) / "вердикт.json").write_text("{}", encoding="utf-8")
    st.set_draft(2)
    data = yaml.safe_load(ws.status_path(10).read_text(encoding="utf-8"))
    assert data["артефакты"] == {"окно": "главы/010/окно.md", "черновик": "главы/010/черновик_2.md", "вердикт": "главы/010/вердикт.json"}
    assert list(data["артефакты"]) == ["окно", "черновик", "вердикт"]  # порядок шагов такта, детерминированно
