"""Канонист: пакет записей в канон на подпись автору (FR-K1…FR-K4).

Единственный компонент, которому разрешена запись в Библиотека/ —
и только после явного подтверждения автора (FR-K2, FR-K3).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import adapters, canonchange, exporter, gitops, guard, llmjson, review, verifier2
from .config import Config
from .paths import Workspace
from .schemas import Verdict

REGISTRY_GLOBS = {
    "3.1": "31_*.md",
    "3.2": "32_*.md",
    "3.3": "33_*.md",
    "1.2": "12_*.md",
}
# Ожидаемые колонки таблицы реестра (подстроки заголовков — как у экспортёра, Д-1).
# У реестра может быть несколько допустимых форматов: табличный демо и фактический канона.
REGISTRY_COLUMNS: dict[str, tuple[list[str], ...]] = {
    "3.1": (["fact_id", "факт", "субъект"], ["#", "Факт"]),  # демо | широкая матрица
    "3.2": (["plant_id", "что", "положена"],),
    "3.3": (["дата", "событие"],),
    "1.2": (["дата", "событие"],),
}
INBOX_DOC = "КОНВЕЙЕР_Входящие.md"
_NOTE_COLUMN_RE = re.compile(r"примеч|источник|коммент|заметк", re.IGNORECASE)
_EMPTY_CELLS = {"", "—", "-", "–"}

BATCH_ROW_RE = re.compile(r"^- РЕЕСТР\s+(?P<registry>[\d.]+)\s+→\s+(?P<row>.+)$")
TASTE_ROW_RE = re.compile(r"^- ПРАВИЛО\s+(?P<target>02 §[\d.]+)\s+→\s+(?P<rule>.+)$")


def _template(ws: Workspace) -> str:
    override = ws.templates / "канонист_система.md"
    if override.exists():
        return override.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/канонист_система.md").read_text(encoding="utf-8")


def _llm_proposals(ws: Workspace, cfg: Config, chapter: int, text: str) -> dict:
    """Предложения Канониста через API; при недоступности — пустой каркас (NFR-3)."""
    edits = review.load_edits(ws, chapter)
    resolutions = review.load_resolutions(ws, chapter)
    flags = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
    canonized = [
        {"flag_id": r.flag_id, "quote": flags[r.flag_id].quote if r.flag_id in flags else "", "target": r.target_registry}
        for r in resolutions
        if r.decision == "канонизировать"
    ]
    user = "\n".join(
        [
            f"# Глава {chapter}: материалы для пакета записей",
            "",
            "## Правки автора",
            *[f"{e.seq}. БЫЛО: {e.before} → СТАЛО: {e.after}" for e in edits],
            "",
            "## Канонизированные самоволки (решение автора уже принято)",
            *[f"- {c['flag_id']}: {c['quote']} (целевой реестр: {c['target'] or 'предложи'})" for c in canonized],
            "",
            "## ПРИНЯТЫЙ ТЕКСТ ГЛАВЫ",
            "",
            text,
        ]
    )
    system = _template(ws)
    guard.write_text(ws.chapter_dir(chapter) / "промпт_канониста.md", f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    try:
        raw = adapters.call_anthropic(
            system, user, cfg.canonist, cfg.api, ws.logs, role="канонист", chapter=chapter
        )
        return llmjson.extract_json(raw, dict)
    except (adapters.ManualModeNeeded, ValueError, json.JSONDecodeError) as e:
        # деградация (NFR-3): пакет собирается без LLM, самоволки — дословными строками
        reason = e.reason if isinstance(e, adapters.ManualModeNeeded) else f"ответ не распарсен: {e}"
        return {
            "facts": [],
            "samovolki": [
                {"flag_id": c["flag_id"], "registry": c["target"] or "3.1", "row": f"| — | {c['quote']} | (сформулировать) |"}
                for c in canonized
            ],
            "edit_classes": [],
            "taste_rules": [],
            "_manual_mode": reason,
        }


def build_batch(ws: Workspace, cfg: Config, chapter: int, draft: int) -> Path:
    """FR-K1: формирует пакет_канона.md на подпись автора."""
    unresolved = review.unresolved_samovolki(ws, chapter)
    if unresolved:
        raise RuntimeError(
            f"Не решены самоволки: {', '.join(unresolved)}. Заполните decision в главы/{chapter:03d}/решения.json (FR-V2.5)."
        )
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    proposals = _llm_proposals(ws, cfg, chapter, text)
    guard.write_text(
        ws.chapter_dir(chapter) / "пакет_канона.json",
        json.dumps(proposals, ensure_ascii=False, indent=2) + "\n",
    )

    edits = review.load_edits(ws, chapter)
    classes = {c.get("seq"): c for c in proposals.get("edit_classes", [])}
    for e in edits:
        cls = classes.get(e.seq, {}).get("class")
        if cls in {"вкус", "факт", "канон"}:
            e.class_ = cls  # класс проставляет Канонист, подтверждает автор (5.3)
    review.save_edits(ws, chapter, edits)

    lines = [
        f"# Пакет записей в канон · Глава {chapter}",
        "",
        "Удалите строки, которые НЕ принимаете (отклонённые будут залогированы, FR-K4).",
        "Применение: `konveyer canonize " + str(chapter) + " --apply` (подпись автора, FR-K2).",
        "",
        "## Новые факты",
    ]
    for f in proposals.get("facts", []):
        lines.append(f"- РЕЕСТР {f.get('registry', '3.1')} → {f.get('row', '')}  <!-- {f.get('reason', '')} -->")
    lines += ["", "## Канонизированные самоволки"]
    for s in proposals.get("samovolki", []):
        lines.append(f"- РЕЕСТР {s.get('registry', '3.1')} → {s.get('row', '')}  <!-- {s.get('flag_id', '')} -->")
    lines += ["", "## Кандидаты в правила вкуса (из повторяющихся правок)"]
    for t in proposals.get("taste_rules", []):
        lines.append(f"- ПРАВИЛО {t.get('target', '02 §6.1')} → {t.get('rule', '')}  <!-- {t.get('evidence', '')} -->")
    lines += [
        "",
        "## Классы правок (подтвердите/исправьте в главы/N/правки.jsonl)",
        *[f"- {e.seq}: {e.class_ or '—'} — {e.before[:60]} → {e.after[:60]}" for e in edits],
        "",
    ]
    if proposals.get("_manual_mode"):
        lines.insert(1, f"\n⚠ Канонист работал без LLM (ручной режим): {proposals['_manual_mode']}\n")
    path = ws.chapter_dir(chapter) / "пакет_канона.md"
    guard.write_text(path, "\n".join(lines) + "\n")
    return path


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _headers_match(headers: list[str], columns: list[str]) -> bool:
    if columns == ["#", "Факт"]:  # широкая матрица реального канона (realcanon.parse_wide_matrix)
        return "Факт" in headers and "#" in headers and len(headers) >= 5
    return all(any(col in h for h in headers) for col in columns)


def _find_registry_table(lines: list[str], registry: str) -> tuple[int, list[str], int] | None:
    """(строка заголовка, заголовки, строка последней записи) первой таблицы файла,
    чьи заголовки соответствуют реестру (2.7). Нет такой таблицы — None."""
    i = 0
    while i < len(lines) - 1:
        line = lines[i].strip()
        if line.startswith("|") and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            headers = _split_cells(line)
            last = i + 1
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                last = j
                j += 1
            if any(_headers_match(headers, cols) for cols in REGISTRY_COLUMNS.get(registry, ())):
                return i, headers, last
            i = j
        else:
            i += 1
    return None


def _build_row(headers: list[str], rows: list[list[str]], registry: str, row: str) -> list[str]:
    """Ячейки новой строки по фактическому заголовку таблицы (2.7)."""
    cells = _split_cells(row)
    if registry == "3.1" and _headers_match(headers, ["#", "Факт"]):
        # широкая матрица: № = max + 1, цитата → «Факт», пометка → колонка примечаний
        # (если такой колонки нет — в скобках при факте: колонки-субъекты трогать нельзя)
        numbers = [int(r[0]) for r in rows if r and r[0].strip().isdigit()]
        meaningful = [c for c in cells if c not in _EMPTY_CELLS]
        if meaningful and re.fullmatch(r"[MМ]-?\d+|\d+", meaningful[0]):
            meaningful = meaningful[1:]  # идентификатор/номер из пакета — номер назначает реестр
        fact = meaningful[0] if meaningful else row.strip()
        note = "; ".join(meaningful[1:])
        new = [str(max(numbers, default=0) + 1), fact] + ["—"] * (len(headers) - 2)
        if note:
            if _NOTE_COLUMN_RE.search(headers[-1]):
                new[-1] = note
            else:
                new[1] = f"{fact} ({note})"
        return new
    return (cells + ["—"] * len(headers))[: len(headers)]


def _append_registry_row(path: Path, registry: str, row: str) -> bool:
    """Дописывает строку в таблицу реестра, чьи заголовки соответствуют реестру.
    Возвращает False, если такой таблицы в файле нет — сиротская строка вне таблицы
    экспортёром молча игнорируется, а запись теряется (2.7); тогда — во «Входящие»."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found = _find_registry_table(lines, registry)
    if found is None:
        return False
    header_idx, headers, last = found
    rows = [_split_cells(ln) for ln in lines[header_idx + 2 : last + 1]]
    cells = _build_row(headers, rows, registry, row)
    lines.insert(last + 1, "| " + " | ".join(cells) + " |")
    guard.write_text(path, "\n".join(lines) + "\n")
    return True


def _chapter_prose_name(ws: Workspace, chapter: int) -> str:
    brief = exporter.load_brief(ws.exports, chapter)
    return f"Том{brief.volume}_Глава{chapter:02d}.md"


def apply_batch(ws: Workspace, cfg: Config, library: Path, chapter: int, draft: int) -> str:
    """FR-K2: применяет подписанный пакет = правки MD + export + атомарный git-коммит.

    Вызывается ТОЛЬКО после явного подтверждения автора (CLI, Д-8).
    Перед ЛЮБОЙ записью — проверки, что коммит завершится: повторное применение
    после сорвавшегося коммита продублировало бы строки реестров. Любой сбой после
    открытия сессии записи откатывает библиотеку к HEAD (2.6) — повтор возможен.
    """
    if not gitops.is_repo(library):
        raise RuntimeError(
            "библиотека не под git — без коммита приёмки откат невозможен (FR-K2): "
            "инициализируйте репозиторий (git init в библиотеке), затем повторите."
        )
    # незавершённый revert, грязная библиотека, отсутствие авторства git — отказ ДО чтения пакета
    # (canon_change повторит те же проверки перед записью)
    canonchange.check_git(library, commit=True, action="применение пакета")
    batch_path = ws.chapter_dir(chapter) / "пакет_канона.md"
    proposals = json.loads((ws.chapter_dir(chapter) / "пакет_канона.json").read_text(encoding="utf-8"))
    accepted_rows: list[tuple[str, str]] = []
    accepted_rules: list[tuple[str, str]] = []
    for line in batch_path.read_text(encoding="utf-8").splitlines():
        m = BATCH_ROW_RE.match(line.strip())
        if m:
            accepted_rows.append((m.group("registry"), re.sub(r"\s*<!--.*-->\s*$", "", m.group("row"))))
        m = TASTE_ROW_RE.match(line.strip())
        if m:
            accepted_rules.append((m.group("target"), re.sub(r"\s*<!--.*-->\s*$", "", m.group("rule"))))

    # FR-K4: отклонённые предложения — в лог (для настройки шаблонов Канониста)
    proposed_rows = [
        (p.get("registry", ""), p.get("row", ""))
        for p in proposals.get("facts", []) + proposals.get("samovolki", [])
    ]
    rejected = [p for p in proposed_rows if p not in accepted_rows]
    if rejected:
        guard.append_text(
            ws.logs / "отклонено_канонистом.jsonl",
            "".join(
                json.dumps({"chapter": chapter, "registry": r, "row": row}, ensure_ascii=False) + "\n"
                for r, row in rejected
            ),
        )

    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    n_facts = len(accepted_rows)
    n_edits = len(review.load_edits(ws, chapter))
    n_sam = sum(1 for r in review.load_resolutions(ws, chapter) if r.decision == "канонизировать")
    message = (
        f"[глава {chapter}] приёмка: записей в реестры {n_facts}, правок {n_edits}, "
        f"канонизировано самоволок {n_sam} (конвейер, Р-016)"
    )

    def write_batch() -> None:
        # 1) текст главы в Проза/
        guard.write_text(library / "Проза" / _chapter_prose_name(ws, chapter), text)
        # 2) строки реестров — только в таблицу с подходящими заголовками (2.7)
        inbox: list[str] = []
        for registry, row in accepted_rows:
            glob = REGISTRY_GLOBS.get(registry)
            target = exporter.volume_docs(library, glob, ws.volume) if glob else []
            if not (target and row.strip().startswith("|") and _append_registry_row(target[0], registry, row.strip())):
                inbox.append(f"- РЕЕСТР {registry}: {row}")
        # 3) статус закладок главы: положена (FR-K1); нет места для отметки — заметка во «Входящие»
        inbox += _update_plants_status(ws, library, chapter)
        if inbox:
            guard.append_text(
                library / INBOX_DOC,
                f"\n## Глава {chapter}\n" + "\n".join(inbox) + "\n",
            )
        # 4) кандидаты в правила вкуса → в конец 02 (разложит автор)
        if accepted_rules:
            p02 = exporter.volume_docs(library, "02_*.md", ws.volume)[0]
            guard.append_text(
                p02,
                f"\n### Кандидаты конвейера (глава {chapter}) — разложить по §6.1/§6.2\n"
                + "\n".join(f"- {t}: {r}" for t, r in accepted_rules)
                + "\n",
            )

    # 5–6) единый конвейер изменения канона: сессия записи → выгрузки и корпус (текст главы попадает
    # в корпус/) → линт → атомарный git-коммит (5.1, FR-K2). Сбой после открытия сессии откатывает
    # библиотеку к HEAD (2.6) — библиотека не остаётся грязной, повтор применения возможен.
    result = canonchange.canon_change(
        ws, cfg, library, write_batch, message, commit=True, author_confirmed=True, action="применение пакета",
    )
    commit = result.commit
    if commit is None:
        raise RuntimeError("после записи пакета в библиотеке нет изменений — коммит приёмки не создан (проверьте пакет).")
    # 7) запись метрик главы — только после состоявшегося коммита
    _write_metrics(ws, chapter)
    return commit


def _update_plants_status(ws: Workspace, library: Path, chapter: int) -> list[str]:
    """Помечает закладки главы «положена ✓» (FR-K1): в реестре 32 (демо-формат) либо в §7
    «Реестр дальних закладок» реестра информрежима (реальный канон, 2.7).
    Возвращает заметки для «Входящих» о закладках, которым места для отметки не нашлось."""
    brief = exporter.load_brief(ws.exports, chapter)
    plants = [
        p
        for p in exporter.load_plants(ws.exports)
        if p.plant_id in brief.plants
        or (p.placed.get("vol") == brief.volume and p.placed.get("ch") == chapter)
    ]
    if not plants:
        return []
    files = exporter.volume_docs(library, "32_*.md", ws.volume)
    if files:
        path = files[0]
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            for p in plants:
                if line.strip().startswith("|") and p.plant_id in line and "✓" not in line:
                    cells = line.split("|")
                    if len(cells) >= 3:
                        cells[-2] = " положена ✓ "
                        lines[i] = "|".join(cells)
        guard.write_text(path, "\n".join(lines) + "\n")
        return []
    notes = [f"- ЗАКЛАДКА {p.plant_id} «{p.what}» положена в главе {chapter} — отметьте в реестре закладок" for p in plants]
    registry = exporter.volume_docs(library, "*Реестр_информационного_режима*.md", ws.volume)
    if not registry:
        return notes
    path = registry[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    # §7: | Закладка | Где лежит | Где стреляет | — идентификаторы З-NN по порядку строк (realcanon)
    table = None
    for i in range(len(lines) - 1):
        if lines[i].strip().startswith("|") and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            headers = " ".join(_split_cells(lines[i])).lower()
            if "закладка" in headers and "стреляет" in headers:
                table = i
                break
    if table is None:
        return notes
    headers = _split_cells(lines[table])
    placed_col = next((k for k, h in enumerate(headers) if "лежит" in h.lower()), None)
    if placed_col is None:
        return notes
    remaining: list[str] = []
    for p, note in zip(plants, notes, strict=True):
        m = re.fullmatch(r"З-(\d+)", p.plant_id)
        idx = table + 1 + int(m.group(1)) if m else None
        if idx is None or idx >= len(lines) or not lines[idx].strip().startswith("|"):
            remaining.append(note)
            continue
        cells = _split_cells(lines[idx])
        if len(cells) != len(headers):
            remaining.append(note)
            continue
        if "положена ✓" not in cells[placed_col]:
            cells[placed_col] = f"{cells[placed_col]} · положена ✓"  # без цифр: главы читаются по «гл. N»
            lines[idx] = "| " + " | ".join(cells) + " |"
    guard.write_text(path, "\n".join(lines) + "\n")
    return remaining


def _write_metrics(ws: Workspace, chapter: int) -> None:
    """Метрики для дашборда (FR-D1): правки/1000 слов + метрики Э1."""
    verdict_path = ws.chapter_dir(chapter) / "вердикт.json"
    metrics: dict = {"chapter": chapter, "ts": datetime.now(timezone.utc).isoformat()}
    if verdict_path.exists():
        verdict = Verdict.model_validate(json.loads(verdict_path.read_text(encoding="utf-8")))
        for c in verdict.checks:
            try:
                metrics[c.check_id] = float(re.split(r"\s", c.actual)[0])
            except ValueError:
                pass
    edits = review.load_edits(ws, chapter)
    words = None
    stem_tokens = _corpus_tokens(ws, chapter)
    if stem_tokens:
        words = len(stem_tokens)
    if words:
        metrics["слов"] = words
        metrics["правок_на_1000"] = round(len(edits) / words * 1000, 2)
    guard.append_text(ws.logs / "метрики.jsonl", json.dumps(metrics, ensure_ascii=False) + "\n")


def _corpus_tokens(ws: Workspace, chapter: int) -> list[str]:
    try:
        volume = exporter.load_brief(ws.exports, chapter).volume
    except FileNotFoundError:
        volume = None
    f = exporter.find_corpus_file(ws.corpus, chapter, volume)
    return f.read_text(encoding="utf-8").split() if f else []
