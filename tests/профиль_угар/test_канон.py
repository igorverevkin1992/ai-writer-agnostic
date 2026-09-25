"""Запись в канон эталона через Канониста: строки реестров ложатся в существующие таблицы (широкая матрица) или во
«Входящие», сирот в документах прозаического формата не остаётся; экспорт после записи разбирается целиком."""

from __future__ import annotations

import json
import subprocess

from konveyer import canonist, exporter, gitops, mdparse
from konveyer.config import Config


def _init_repo(root) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_строки_реестров_без_сирот(ugar_copy):
    ws, lib, _ = ugar_copy
    _init_repo(lib)
    chapter = 6  # закладка З-04 «Недогоревший знак…» лежит в гл. 6 (реестр закладок информрежима)
    rows = {
        "эпистемика": "| — | Лемм заметил остаток знака в золе | (сформулировать) |",
        "закладки": "| P-101 | недогоревший знак в золе | т1 гл6 | т6 | 🔧 |",
        "континуити": "| 20.04.1926 | печь, знак догорает не до конца | 6 | — |",
    }
    ws.draft_path(chapter, 1).parent.mkdir(parents=True, exist_ok=True)
    ws.draft_path(chapter, 1).write_text("Лемм стоял у печи. Знак догорал не до конца.", encoding="utf-8")
    (ws.chapter_dir(chapter) / "пакет_канона.json").write_text(
        json.dumps({"facts": [{"registry": k, "row": v} for k, v in rows.items()]}, ensure_ascii=False), encoding="utf-8")
    (ws.chapter_dir(chapter) / "пакет_канона.md").write_text(
        "# Пакет\n\n## Новые факты\n" + "\n".join(f"- РЕЕСТР {k} → {v}" for k, v in rows.items()) + "\n", encoding="utf-8")
    matrix_doc = lib / "31_Эпистемическая_матрица_Том1.md"
    docs_before = {p.name: p.read_text(encoding="utf-8") for p in lib.glob("*.md")}
    facts_before = len(exporter.load_matrix(ws.exports))

    commit = canonist.apply_batch(ws, Config(), lib, chapter, 1).commit
    assert commit == gitops.head(lib) and not gitops.dirty(lib)

    # у эталона нет таблиц объявленного формата для этих реестров (широкая матрица, континуити буллетами) →
    # строки во «Входящие» дословно, ни один документ канона не тронут и не получил сиротской строки
    inbox = (lib / canonist.INBOX_DOC).read_text(encoding="utf-8")
    assert "## Глава 6" in inbox
    for k, v in rows.items():
        assert f"РЕЕСТР {k}: {v}" in inbox
    assert "З-04" in inbox and "отметьте в реестре закладок" in inbox  # закладка главы: напоминание, не правка за автора
    for name, text in docs_before.items():
        assert (lib / name).read_text(encoding="utf-8") == text, name
    assert "| — | Лемм заметил" not in matrix_doc.read_text(encoding="utf-8")
    wide = next(t for t in mdparse.parse_tables(matrix_doc) if "Факт" in t.headers)
    assert all(not r["Факт"].startswith("Лемм заметил") for r in wide.rows)
    assert (lib / "Проза" / "Том1_Глава06.md").exists()
    assert not any(ln.startswith("|") for ln in (lib / "33_Континуити_трекер.md").read_text(encoding="utf-8").splitlines())

    # экспорт после записи разбирается целиком, факты матрицы — прежние
    exporter.run_export(lib, ws.exports, ws.logs, 1, ws.root)
    assert len(exporter.load_matrix(ws.exports)) == facts_before
    plant = next(p for p in exporter.load_plants(ws.exports) if p.plant_id == "З-04")
    assert plant.chapters == [6] and "Недогоревший знак" in plant.what
