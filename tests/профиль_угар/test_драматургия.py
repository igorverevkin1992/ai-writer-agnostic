"""Драматургия на библиотеке эталона: акты и охваты кругов, материалы аналитика без лишнего, ручной режим,
арки тома (скелет, фильтр тайн в строке арки, находки АРКА-1/2), панель кругов."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from konveyer import adapters, circles, compiler, exporter, lint, server
from konveyer.config import Config


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ------------------------------------------------------------------ круги истории


def test_акты_тома_и_охваты(ugar):
    ws, lib, _ = ugar
    acts = exporter.load_acts(ws.exports)
    parts = exporter.load_parts(ws.exports)  # части тома — те же границы, что у актов (документ актов эталона)
    assert [(p["part"], p["from_chapter"], p["to_chapter"]) for p in parts] == [(a.act, a.from_chapter, a.to_chapter) for a in acts]
    assert [(a.act, a.from_chapter, a.to_chapter) for a in acts] == [(1, 1, 9), (2, 10, 18), (3, 19, 36), (4, 37, 46)]
    assert acts[2].parts == "III–IV" and "Обретение" in acts[2].steps
    assert len(circles.targets(ws, "всё")) == 1 + 4 + 46
    assert circles.targets(ws, "части") == circles.targets(ws, "акты")  # старое имя охвата — синоним
    assert circles.targets(ws, "главы", chapter=5) == [("глава", 5)]
    frame = circles.frame_for_chapter([], acts, 20)
    assert frame["act"].act == 3 and frame["act"].title == "ТРАУР · КОММЕРСАНТ"


def test_материалы_не_раскрывают_лишнего(ugar):
    ws, lib, _ = ugar
    title, book = circles.build_material(ws, "книга")
    assert "МОКРОЕ ДЕЛО" in book and "гл. 46" in book and "Реестр тайн" in book
    assert "## Акты тома" in book and "Акт 3 «ТРАУР · КОММЕРСАНТ» — гл. 19–36" in book
    title, act = circles.build_material(ws, "акт", 1)
    assert "Акт 1" in title and "— гл. 1–9" in act and "Шаги каркаса тома" in act
    assert "гл. 9 · " in act and "гл. 10 · " not in act  # события сетки только своих глав
    title, act3 = circles.build_material(ws, "акт", 3)
    assert "— гл. 19–36" in act3 and "гл. 19 · " in act3 and "гл. 36 · " in act3 and "гл. 37 · " not in act3
    title, ch = circles.build_material(ws, "глава", 5)
    assert "обыск стола" in ch and "М-04" in ch and "М-06" not in ch  # знание фокала, не тайны


def test_материал_аналитика_тома(ugar):
    """Тема цикла из концепции цикла, арки целиком, события сетки с участниками; промпт Писателю не идёт."""
    ws, lib, _ = ugar
    title, material = circles.build_material(ws, "книга")
    assert title == "Книга (том целиком)"
    assert "## Тема серии" in material and material.split("## Тема серии")[1].split("\n## ")[0].strip()
    assert "## Арки тома (" in material and "| Штерн | 1 |" in material and "| Ася | 4 |" in material
    assert "гл. 13 · " in material and "фокал Штерн · участники: Степан" in material
    _, act_material = circles.build_material(ws, "акт", 2)
    assert "## Арки акта 2" in act_material and "| Лемм | 2 |" in act_material and "| Лемм | 1 |" not in act_material
    assert "участники:" in act_material
    assert circles.cycle_theme(ws) in material  # тема — из документа замысла (тип «методика»), без него пусто
    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "Тема серии" not in w and "| Ложь |" not in w and "Ядро цикла" not in w


def test_без_api_сохраняются_промпты(ugar_copy, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ws, lib, _ = ugar_copy
    result = circles.run(ws, Config(), "акты")
    assert result["ручной_режим"] and len(result["промпты"]) == 4 and not result["готово"]
    assert (ws.root / "драматургия" / "промпты" / "акт_1.md").exists()


def test_генерация_через_подменённый_адаптер(ugar_copy, monkeypatch):
    def fake(system, user, mc, api, logs_dir, *, role, chapter=None):
        assert "восемь шагов" in system.lower() or "кругу истории" in system
        return json.dumps({
            "title": "т", "summary": "суть",
            "steps": [{"n": i, "name": f"шаг {i}", "text": "…", "chapters": "гл. 1"} for i in range(1, 9)],
            "weak_spot": "нет",
        }, ensure_ascii=False)

    ws, lib, _ = ugar_copy
    monkeypatch.setattr(adapters, "call_anthropic", fake)
    result = circles.run(ws, Config(), "книга")
    assert len(result["готово"]) == 1 and not result["ручной_режим"]
    md = (ws.root / "драматургия" / "книга.md").read_text(encoding="utf-8")
    assert md.startswith("# Каркас") and "## 8. шаг 8" in md
    assert circles.run(ws, Config(), "книга")["готово"] == []  # повторный запуск без --заново ничего не делает
    assert circles.list_circles(ws)[0]["scope"] == "книга"


def test_ручной_приём_и_панель(ugar_copy, monkeypatch):
    ws, lib, _ = ugar_copy
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    raw = 'Вот круг:\n{"steps": [{"n": 1, "name": "Ты", "text": "Степан у стола"}], "summary": "обыск"}'
    path = circles.accept_manual(ws, "глава", 5, raw)
    assert path.name == "глава_05.md" and "Глава 5" in path.read_text(encoding="utf-8")

    srv = server.serve(ws, Config(), lib, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/circles", timeout=5) as r:
            data = json.loads(r.read().decode())
        assert data["parts"][0]["title"] == "МОКРОЕ ДЕЛО" and len(data["acts"]) == 4
        assert data["circles"][0]["key"] == 5
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/command", method="POST",
            data=json.dumps({"cmd": "story-circles", "params": {"scope": "книга"}}).encode(),
            headers={"Content-Type": "application/json", "X-Konveyer-Panel": "1"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read().decode())["job"]["name"] == "story-circles"
    finally:
        srv.shutdown(); srv.server_close()


# ------------------------------------------------------------------ арки тома


def test_скелет_арок_эталона(ugar):
    ws, lib, _ = ugar
    arcs = exporter.load_arcs(ws.exports)
    assert {a.character for a in arcs} == {"Лемм", "Степан", "Штерн", "Заварзин", "Ася"}
    assert sorted({a.act for a in arcs}) == [1, 2, 3, 4] and len(arcs) == 20
    assert not any(a.filled for a in arcs)  # скелет: ничего не сочинено за автора
    assert {a.act for a in exporter.load_acts(ws.exports)} == {1, 2, 3, 4}
    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "арки тома" not in w and "⚠ заполнить" not in w
    report = lint.run_lint(lib, ws.exports, ws.logs, export=False, root=ws.root, use_cache=False)
    assert not [f for f in report.findings if f.code.startswith("АРКА")]


def test_фильтр_тайн_в_строке_арки(ugar_copy):
    """Строка Штерна с маркером тайны Т-05 («сын») фокалу Степану не показывается; строка Лемма без маркеров — да;
    ложь/желание/потребность не выводятся никогда."""
    ws, lib, _ = ugar_copy
    doc = lib / "22_Арки_Том1.md"
    _edit(doc, "| Штерн | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Штерн | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | Держится чужим; отца не поминает, сын молчит. |")
    _edit(doc, "| Лемм | 1 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Лемм | 1 | ЛОЖЬ-ЛЕММА | ЖЕЛАНИЕ-ЛЕММА | ПОТРЕБНОСТЬ-ЛЕММА | ГДЕ-НА-АРКЕ | Сухой, точный; смотрит поверх головы собеседника. |")
    exporter.run_export(lib, ws.exports, ws.logs, 1, ws.root)
    arcs, acts, infobans = exporter.load_arcs(ws.exports), exporter.load_acts(ws.exports), exporter.load_infobans(ws.exports)
    brief = exporter.load_brief(ws.exports, 5)  # фокал Степан, акт 1
    assert brief.focal == "Степан"
    t05 = next(b for b in infobans if b.ban_id == "Т-05")
    assert "сын" in t05.markers and not t05.known_to("Степан", 5)
    brief = brief.model_copy(update={"participants": ["Лемм", "Штерн"]})
    lines = compiler.arc_lines(arcs, acts, brief, infobans, ["Лемм", "Степан", "Штерн"])
    assert lines == ["Лемм: Сухой, точный; смотрит поверх головы собеседника."]

    w = compiler.compile_window(ws, lib, 5)[0].read_text(encoding="utf-8")
    assert "- Лемм: Сухой, точный; смотрит поверх головы собеседника." in w
    assert "сын молчит" not in w
    for hidden in ("ЛОЖЬ-ЛЕММА", "ЖЕЛАНИЕ-ЛЕММА", "ПОТРЕБНОСТЬ-ЛЕММА", "ГДЕ-НА-АРКЕ"):
        assert hidden not in w
    _, material = circles.build_material(ws, "книга")  # аналитику кругов — всё, включая ложь и строку с маркером тайны
    assert "ЛОЖЬ-ЛЕММА" in material and "сын молчит" in material


def test_линтер_арка_1_и_2(ugar_copy):
    ws, lib, _ = ugar_copy
    doc = lib / "22_Арки_Том1.md"
    _edit(doc, "| Ася | 4 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |",
          "| Ася | 4 | ⚠ заполнить | ⚠ заполнить | ⚠ заполнить | — | — |\n| Никто | 9 | — | — | — | — | — |")
    report = lint.run_lint(lib, ws.exports, ws.logs, root=ws.root, use_cache=False)
    found = {f.code: f for f in report.findings if f.code.startswith("АРКА")}
    assert set(found) == {"АРКА-1", "АРКА-2"} and all(f.severity == "заметка" for f in found.values())
    assert "Никто" in found["АРКА-1"].message and "22_Арки_Том1.md" in found["АРКА-1"].file
    assert "акт 9" in found["АРКА-2"].message and found["АРКА-2"].line
