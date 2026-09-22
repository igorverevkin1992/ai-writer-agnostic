"""Линтер: эпистемика матрицы и тайн, закладки, поглавник против сетки (аудит 2, 3.3–3.5, 3.7, 3.8).

Каждый класс проверок подтверждён двумя способами: позитивная мутация временной копии реальной
библиотеки (находка появляется) и нетронутая библиотека (ложных ошибок нет)."""

import shutil
from pathlib import Path

import pytest

from konveyer import guard, lint, lint_epist
from konveyer.paths import Workspace
from konveyer.schemas import LintReport, Plant

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "Библиотека"
real_only = pytest.mark.skipif(not LIBRARY.exists(), reason="реальная библиотека не подключена")


@pytest.fixture
def real(tmp_path: Path) -> Path:
    """Временная копия реальной библиотеки: мутации не трогают канон автора."""
    lib = tmp_path / "Библиотека"
    shutil.copytree(LIBRARY, lib)
    (tmp_path / "конфиг.yaml").write_text("library_dir: Библиотека\n", encoding="utf-8")
    guard.set_library_dir(lib)
    return lib


def _lint(lib: Path) -> LintReport:
    ws = Workspace(lib.parent)
    return lint.run_lint(lib, ws.exports, ws.logs)


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _codes(report: LintReport, code: str, severity: str | None = None) -> list[str]:
    return [f.message for f in report.findings
            if f.code == code and (severity is None or f.severity == severity)]


MATRIX = "31_Эпистемическая_матрица_Том1.md"
REGISTRY = "УГАР_Том1_Реестр_информационного_режима.md"
POGLAVNIK = "23_Поглавник_Часть_I.md"
ACTS = "21_Круги_истории_Том1.md"


# ------------------------------------------------------------------ матрица 3.1


@real_only
def test_матр2_субъект_узнаёт_в_чужой_главе(real):
    """М-04: Лемм узнаёт о рапортах в гл. 17 (его глава) — норма; гл. 16 — глава Штерна."""
    assert not _codes(_lint(real), "МАТР-2", "ошибка")
    _edit(real / MATRIX, "| гл.17 / фраза оперативника |", "| гл.16 / фраза оперативника |")
    msgs = _codes(_lint(real), "МАТР-2", "ошибка")
    assert any("М-04" in m and "гл. 16" in m and "Штерн" in m for m in msgs), msgs


@real_only
def test_матр2_знание_за_кадром_не_проверяется(real):
    """М-12 (куратор ОГПУ, гл. 34) и М-17 (Заварзин, гл. 29) помечены «/ по своим каналам (за кадром)» —
    присутствие в главе не требуется (Р-033); без пометки — предупреждение."""
    assert not _codes(_lint(real), "МАТР-2")
    _edit(real / MATRIX, "| гл.29≈ / по своим каналам (за кадром) |", "| гл.29≈ |")
    msgs = _codes(_lint(real), "МАТР-2", "предупреждение")
    assert any("М-17" in m and "Заварзин" in m and "гл. 29" in m for m in msgs), msgs


@real_only
def test_матр3_знание_раньше_события(real):
    """М-11: фальсификация протокола происходит в гл. 32 — Штерн не может знать о ней с гл. 30."""
    _edit(real / MATRIX, "| гл.36≈ / каналы |", "| гл.30 / каналы |")
    msgs = _codes(_lint(real), "МАТР-3", "ошибка")
    assert any("М-11" in m and "Штерн" in m and "раньше события" in m for m in msgs), msgs


@real_only
def test_матр3_автор_действия_позже_читателя(real):
    """М-14: подлог совершает Степан («/ автор») — он не может узнать о нём позже читателя."""
    _edit(real / MATRIX, "| гл.43–44 / автор |", "| гл.45 / автор |")
    msgs = _codes(_lint(real), "МАТР-3", "ошибка")
    assert any("М-14" in m and "автор действия" in m for m in msgs), msgs


@real_only
def test_матр3_читатель_впереди_фокала(real):
    """М-12 реального канона: читатель узнаёт в гл. 34, фокал Лемм — только в гл. 40."""
    msgs = _codes(_lint(real), "МАТР-3", "предупреждение")
    assert any("М-12" in m and "через голову фокала" in m for m in msgs), msgs


# ------------------------------------------------------------------ реестр тайн


@real_only
def test_тайна4_раскрытие_в_главе_фокала_который_не_знает(real):
    """Т-05 раскрывается в гл. 46 (фокал Штерн знает) — норма; Т-09 в гл. 43 — фокал Степан не знает."""
    assert not _codes(_lint(real), "ТАЙНА-4")
    _edit(real / REGISTRY, "| Штерн укрыл подлог Степана (решение Хранителя) | Гл. 45 |",
          "| Штерн укрыл подлог Степана (решение Хранителя) | Гл. 43 |")
    msgs = _codes(_lint(real), "ТАЙНА-4", "ошибка")
    assert any("Т-09" in m and "гл. 43" in m and "Степан" in m for m in msgs), msgs


@real_only
def test_тайна5_персонажи_знают_против_матрицы(real):
    """Расхождения аудита 7.2 (сняты в каноне Р-033, здесь возвращаются в копию): Т-04, Т-03, Т-06 —
    имя есть в матрице и нет в реестре; Т-08 — имя в реестре без главы рядом с именами с главами."""
    assert not _codes(_lint(real), "ТАЙНА-5")
    reg = real / REGISTRY
    _edit(reg, "| Степан, куратор ОГПУ, Заварзин; Штерн — с гл. 7; Лемм — с гл. 17 |",
          "| Степан, куратор ОГПУ; Штерн — с гл. 7; Лемм — с гл. 17 |")
    _edit(reg, "| Штерн; больше никто из героев до конца тома |", "| Никто из героев |")
    _edit(reg, "| Лемм, Штерн; Заварзин узнает в томе 3 |", "| Лемм; Заварзин узнает в томе 3 |")
    _edit(reg, "| Степан — с гл. 43; Штерн — гл. 45 |", "| Степан; Штерн — гл. 45 |")
    msgs = _codes(_lint(real), "ТАЙНА-5", "предупреждение")
    assert any("Т-04" in m and "Штерн" in m for m in msgs), msgs
    assert any("Т-03" in m and "Заварзин" in m for m in msgs), msgs
    assert any("Т-06" in m and "Штерн" in m for m in msgs), msgs
    assert any("Т-08" in m and "Степан" in m for m in msgs), msgs


# ------------------------------------------------------------------- закладки §7


@real_only
def test_закл3_носитель_закладки_не_в_главе(real):
    """З-03 «Картотека Лемма» лежит в главах Лемма (9, 27); гл. 7 — глава Штерна."""
    assert not _codes(_lint(real), "ЗАКЛ-3")
    _edit(real / REGISTRY, "| Картотека Лемма (метод + физический объект) | Гл. 9, 27 |",
          "| Картотека Лемма (метод + физический объект) | Гл. 7, 27 |")
    msgs = _codes(_lint(real), "ЗАКЛ-3", "ошибка")
    assert any("З-03" in m and "гл. 7" in m and "Штерн" in m and "Лемм" in m for m in msgs), msgs


@real_only
def test_закл5_реестр_против_сквозного_контроля(real):
    """Зола (§7 «Гл. 6») и сквозной контроль поглавника («знак/зола (6.2)») обязаны сходиться."""
    assert not _codes(_lint(real), "ЗАКЛ-5", "ошибка")
    _edit(real / REGISTRY, "| Недогоревший знак «Континенталя» в печной золе | Гл. 6 |",
          "| Недогоревший знак «Континенталя» в печной золе | Гл. 7 |")
    msgs = _codes(_lint(real), "ЗАКЛ-5", "ошибка")
    assert any("З-04" in m and "гл. 7" in m and "Штерн" in m and "гл. 6" in m for m in msgs), msgs


@real_only
def test_закл5_закладка_поглавника_без_строки_в_реестре(real):
    """«часы (гл. 4)» и «почтовый канал (гл. 4)» есть в сквозном контроле и нет в §7 — заметки
    (в каноне строки §7 добавлены Р-033; здесь убираются из копии)."""
    assert not _codes(_lint(real), "ЗАКЛ-5", "заметка")
    reg = real / REGISTRY
    text = reg.read_text(encoding="utf-8")
    kept = [ln for ln in text.splitlines() if not ln.startswith(("| Часы Штерна", "| Почтовый канал Штерна"))]
    assert len(kept) == len(text.splitlines()) - 2
    reg.write_text("\n".join(kept) + "\n", encoding="utf-8")
    msgs = _codes(_lint(real), "ЗАКЛ-5", "заметка")
    assert any("часы" in m for m in msgs) and any("почтовый канал" in m for m in msgs), msgs


@real_only
def test_закл6_печь_в_мае_вне_разрешённых_глав(real):
    """Правило доз §5: «печь в мае» — только доза №1 (гл. 12) и гл. 46."""
    assert not _codes(_lint(real), "ЗАКЛ-6")
    _edit(real / REGISTRY, "| «Печь в мае» | Доза №1 (гл. 12), гл. 46 |",
          "| «Печь в мае» | Доза №1 (гл. 12), гл. 45 |")
    msgs = _codes(_lint(real), "ЗАКЛ-6", "ошибка")
    assert any("Печь в мае" in m and "45" in m for m in msgs), msgs


@real_only
def test_закл7_две_главы_через_или(real):
    """З-02 «Гл. 29 или 40» — открытое решение автора, а не две главы (в каноне решено: гл. 29, Р-028)."""
    assert not _codes(_lint(real), "ЗАКЛ-7")
    _edit(real / REGISTRY, "| Гл. 29, деталью без акцента (Р-028) |", "| Гл. 29 или 40, деталью без акцента |")
    msgs = _codes(_lint(real), "ЗАКЛ-7", "предупреждение")
    assert any("З-02" in m and "или" in m for m in msgs), msgs


def test_закл4_выстрел_раньше_тома_закладки(tmp_path):
    """ЗАКЛ-2 сравнивает главы и на реальном каноне молчит (выстрелы §7 заданы томами) —
    ту же гарантию на уровне томов даёт ЗАКЛ-4."""
    doc = lint_epist._Doc(tmp_path, None)
    plant = Plant(plant_id="З-99", what="проверка", placed={"vol": 3, "ch": 1}, chapters=[],
                  fires=[{"vol": 2}, {"vol": 5}])
    out = lint_epist.check_plants([plant], [], [], set(), doc, doc, {})
    assert [f.code for f in out] == ["ЗАКЛ-4"] and "томе 2" in out[0].message


# --------------------------------------------------- поглавник, акты, пропорция


@real_only
def test_погл1_заголовок_главы_против_сетки(real):
    """«## Гл. 3 · 15 апреля · фокал ЛЕММ» — сетка «3 | Лемм | 15.04»; расхождения ловятся оба."""
    assert not _codes(_lint(real), "ПОГЛ-1")
    _edit(real / POGLAVNIK, "## Гл. 3 · 15 апреля · фокал ЛЕММ", "## Гл. 3 · 17 апреля · фокал ШТЕРН")
    msgs = _codes(_lint(real), "ПОГЛ-1", "ошибка")
    assert any("фокал ШТЕРН" in m and "Лемм" in m for m in msgs), msgs
    assert any("17 апреля" in m and "15.04" in m for m in msgs), msgs


@real_only
def test_акт2_колонка_части_против_реестра(real):
    """Акт 4 занимает гл. 37–46 = часть V; «IV–V» — это гл. 27–46."""
    assert not _codes(_lint(real), "АКТ-2")
    _edit(real / ACTS, "| 4 | «КАБАРЕ» | 37–46 | V |", "| 4 | «КАБАРЕ» | 37–46 | IV–V |")
    msgs = _codes(_lint(real), "АКТ-2", "ошибка")
    assert any("акт 4" in m and "27–46" in m for m in msgs), msgs


@real_only
def test_фокал3_пропорция_фокалов(real):
    """§1.6 (45/35/20) и «Пропорция фокалов» сквозного контроля сходятся с сеткой; допуск ±1 глава."""
    assert not _codes(_lint(real), "ФОКАЛ-3")
    _edit(real / REGISTRY, "**Лемм 45% / Степан 35% / Штерн 20%**", "**Лемм 20% / Степан 35% / Штерн 45%**")
    _edit(real / POGLAVNIK, "Пропорция фокалов: Лемм 4 гл. / Степан 3 / Штерн 2",
          "Пропорция фокалов: Лемм 5 гл. / Степан 3 / Штерн 1")
    msgs = _codes(_lint(real), "ФОКАЛ-3", "предупреждение")
    assert any("том" in m and "Лемм" in m and "21" in m for m in msgs), msgs
    assert any("сквозной контроль" in m and "Штерн" in m for m in msgs), msgs


# ---------------------------------------------------------------------- проза


@real_only
def test_проза4_новое_имя_прозы(real):
    """Отчество «Ильич» (гл. 4) нет ни в досье, ни в континуити 3.3 — новый факт прозы (П6).
    В каноне отчество внесено в досье (Р-031); здесь убирается из копии."""
    dossier = real / "Досье" / "Степан_Кожух.md"
    assert not _codes(_lint(real), "ПРОЗА-4")
    _edit(dossier, "Степан Ильич Кожух (отчество — из принятой прозы гл. 4, Р-031). Рожд.", "Рожд.")
    msgs = _codes(_lint(real), "ПРОЗА-4", "заметка")
    assert any("Степан Ильич" in m for m in msgs), msgs
    dossier.write_text(dossier.read_text(encoding="utf-8") + "\n- Отчество: Ильич\n", encoding="utf-8")
    assert not any("Степан Ильич" in m for m in _codes(_lint(real), "ПРОЗА-4"))


@real_only
def test_реальная_библиотека_новых_ошибок_нет(real):
    """Нетронутый канон: ни одна новая проверка не даёт «ошибки» — только подсветка для автора."""
    report = _lint(real)
    assert report.errors == 0, [f.message for f in report.findings if f.severity == "ошибка"]
    codes = {f.code for f in report.findings}
    # расхождения аудита 2 сняты автором (Р-028, Р-031, Р-033): эти проверки на чистом каноне молчат
    assert not codes & {"МАТР-2", "ТАЙНА-1", "ТАЙНА-5", "ЗАКЛ-5", "ЗАКЛ-7", "ПРОЗА-4"}, codes
