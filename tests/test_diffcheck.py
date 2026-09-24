"""Тесты дифф-контроля (FR-V1-6): внесено / не внесено / самовольные изменения."""

from konveyer import verifier1
from konveyer.schemas import Edit


def _drafts(ws, old: str, new: str):
    d = ws.chapter_dir(1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "черновик_1.md").write_text(old, encoding="utf-8")
    (d / "черновик_2.md").write_text(new, encoding="utf-8")


def test_правки_внесены_чисто(ws):
    _drafts(
        ws,
        "Чай остыл. Зоя не звонила. День обещал пустоту.",
        "Чай остыл давно. Зоя не звонила. День обещал пустоту.",
    )
    edits = [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл давно.")]
    report = verifier1.diff_check(ws, 1, 1, 2, edits)
    assert report.applied_share == 1.0 and report.clean


def test_правка_не_внесена(ws):
    _drafts(ws, "Чай остыл. Зоя не звонила.", "Чай остыл. Зоя не звонила.")
    edits = [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай был горячий.")]
    report = verifier1.diff_check(ws, 1, 1, 2, edits)
    assert report.not_applied == [1] and not report.clean


def test_диффконтроль_самовольные_изменения(ws):
    _drafts(
        ws,
        "Чай остыл. Зоя не звонила. День обещал пустоту.",
        "Чай остыл давно. Зоя не звонила. Вечером пошёл липкий снег.",
    )
    edits = [Edit(chapter=1, seq=1, before="Чай остыл.", after="Чай остыл давно.")]
    report = verifier1.diff_check(ws, 1, 1, 2, edits)
    assert any("снег" in u for u in report.unauthorized)  # самоволие поймано
    assert not report.clean
