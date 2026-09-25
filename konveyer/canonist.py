"""Канонист: пакет записей в канон на подпись автору (FR-CN-1…FR-CN-4).

Единственный путь записи в библиотеку — `canonchange.canon_change` после явного подтверждения автора. Реестры,
в которые дописываются строки, — типы документов каталога с табличным форматом (эпистемика, закладки, континуити,
хронология, мир, предметы…): строка дописывается в таблицу, чьи заголовки соответствуют типу; иначе — во «Входящие».
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from jinja2 import Environment

from . import adapters, canonchange, catalog, declparse, exporter, gitops, guard, llmjson, manifest as manifest_mod
from . import review, verifier2
from .config import Config
from .paths import Workspace
from .schemas import Verdict

INBOX_DOC = manifest_mod.ENGINE_DOC_PREFIX + "Входящие.md"  # собственный документ движка — вне карты библиотеки
_NOTE_COLUMN_RE = re.compile(r"примеч|источник|коммент|заметк", re.IGNORECASE)
_EMPTY_CELLS = {"", "—", "-", "–"}
BATCH_ROW_RE = re.compile(r"^- РЕЕСТР\s+(?P<registry>[\w.\-]+)\s+→\s+(?P<row>.+)$")
TASTE_ROW_RE = re.compile(r"^- ПРАВИЛО\s+(?P<target>[^→]+?)\s+→\s+(?P<rule>.+)$")


def registries(root: Path | None = None) -> dict[str, dict]:
    """Реестры, принимающие строки Канониста: типы с табличным извлечением. {тип: {purpose, columns, extraction}}."""
    out: dict[str, dict] = {}
    for spec in catalog.load_types(root).values():
        for ext in spec.extractions:
            fmt = next((f for f in ext.get("форматы", []) if f.get("вид") == "таблица"), None)
            if fmt is None or spec.name in out:
                continue
            cols = list((fmt.get("колонки") or {}).keys())
            out[spec.name] = {"purpose": spec.purpose, "columns": cols, "format": fmt, "extraction": ext["имя"]}
            break
    return out


# совместимость: имена реестров для панели и CLI
REGISTRY_GLOBS = {name: name for name in registries()}


def _template(ws: Workspace) -> str:
    for cand in (ws.root / "промпты" / "канонист.md", ws.templates / "канонист_система.md"):
        if cand.exists():
            return cand.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/канонист_система.md").read_text(encoding="utf-8")


# ограждение цитат самоволок (фрагменты прозы из флагов Э2) — данные, не инструкции (FR-SC-8)
QUOTE_OPEN = "<цитата>"
QUOTE_CLOSE = "</цитата>"


def _system(ws: Workspace) -> str:
    man = verifier2._manifest(ws)
    regs = [{"name": n, "purpose": r["purpose"], "columns": r["columns"]} for n, r in registries(ws.root).items()]
    return Environment().from_string(_template(ws)).render(series=man.проект.имя, registries=regs)


def _llm_proposals(ws: Workspace, cfg: Config, chapter: int, text: str) -> dict:
    edits = review.load_edits(ws, chapter)
    resolutions = review.load_resolutions(ws, chapter)
    flags = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
    canonized = [
        {"flag_id": r.flag_id, "quote": flags[r.flag_id].quote if r.flag_id in flags else "", "target": r.target_registry}
        for r in resolutions if r.decision == "канонизировать"
    ]
    user = "\n".join([
        f"# Глава {chapter}: материалы для пакета записей", "",
        "## Правки автора", *[f"{e.seq}. БЫЛО: {e.before} → СТАЛО: {e.after}" for e in edits], "",
        "## Канонизированные самоволки (решение автора уже принято)",
        *[f"- {c['flag_id']}: {QUOTE_OPEN}{c['quote']}{QUOTE_CLOSE} (целевой реестр: {c['target'] or 'предложи'})"
          for c in canonized], "",
        "## ПРИНЯТЫЙ ТЕКСТ ГЛАВЫ", "", verifier2.FENCE_OPEN, text, verifier2.FENCE_CLOSE,
    ])
    system = _system(ws)
    guard.write_text(ws.chapter_dir(chapter) / "промпт_канониста.md", f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    try:
        raw = adapters.call_role(cfg, "канонист", system, user, ws.logs, role="канонист", chapter=chapter)
        try:
            return llmjson.extract_json(raw, dict)
        except ValueError as e:
            guard.write_text(ws.chapter_dir(chapter) / "ответ_канониста_сырой.md", raw)
            raise ValueError(f"ответ не разобран: {e}; сохранён в ответ_канониста_сырой.md") from None
    except (adapters.ManualModeNeeded, ValueError, json.JSONDecodeError) as e:
        reason = e.reason if isinstance(e, adapters.ManualModeNeeded) else str(e)
        default_reg = next(iter(registries(ws.root)), "эпистемика")
        return {
            "facts": [],
            "samovolki": [{"flag_id": c["flag_id"], "registry": c["target"] or default_reg,
                           "row": f"| — | {c['quote']} | (сформулировать) |"} for c in canonized],
            "edit_classes": [], "taste_rules": [], "_manual_mode": reason,
        }


def build_batch(ws: Workspace, cfg: Config, chapter: int, draft: int) -> Path:
    """FR-CN-1: пакет на подпись автора."""
    unresolved = review.unresolved_samovolki(ws, chapter)
    if unresolved:
        raise RuntimeError(f"Не решены самоволки: {', '.join(unresolved)}. Заполните decision в {ws.chapter_rel(chapter)}/решения.json.")
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    proposals = _llm_proposals(ws, cfg, chapter, text)
    guard.write_text(ws.chapter_dir(chapter) / "пакет_канона.json", json.dumps(proposals, ensure_ascii=False, indent=2) + "\n")
    edits = review.load_edits(ws, chapter)
    classes = {c.get("seq"): c for c in proposals.get("edit_classes", [])}
    for e in edits:
        cls = classes.get(e.seq, {}).get("class")
        if cls in {"вкус", "факт", "канон"}:
            e.class_ = cls
    review.save_edits(ws, chapter, edits)
    lines = [
        f"# Пакет записей в канон · Глава {chapter}", "",
        "Удалите строки, которые НЕ принимаете (отклонённые будут залогированы, FR-CN-4).",
        f"Применение: `konveyer канон {chapter} --применить` (подпись автора).", "",
        "## Новые факты",
    ]
    for f in proposals.get("facts", []):
        lines.append(f"- РЕЕСТР {f.get('registry', 'эпистемика')} → {f.get('row', '')}  <!-- {f.get('reason', '')} -->")
    lines += ["", "## Канонизированные самоволки"]
    for s in proposals.get("samovolki", []):
        lines.append(f"- РЕЕСТР {s.get('registry', 'эпистемика')} → {s.get('row', '')}  <!-- {s.get('flag_id', '')} -->")
    lines += ["", "## Кандидаты в правила вкуса (из повторяющихся правок)"]
    for t in proposals.get("taste_rules", []):
        lines.append(f"- ПРАВИЛО {t.get('target', 'правила вкуса')} → {t.get('rule', '')}  <!-- {t.get('evidence', '')} -->")
    lines += ["", "## Классы правок (подтвердите/исправьте в правки.jsonl)",
              *[f"- {e.seq}: {e.class_ or '—'} — {e.before[:60]} → {e.after[:60]}" for e in edits], ""]
    if proposals.get("_manual_mode"):
        lines.insert(1, f"\n⚠ Канонист работал без модели (ручной режим): {proposals['_manual_mode']}\n")
    path = ws.chapter_dir(chapter) / "пакет_канона.md"
    guard.write_text(path, "\n".join(lines) + "\n")
    return path


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_registry_table(lines: list[str], fmt: dict, overrides: dict) -> tuple[int, list[str], int] | None:
    """(строка заголовка, заголовки, строка последней записи) первой таблицы, чьи заголовки соответствуют типу."""
    columns = fmt.get("колонки") or {}
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
            if declparse.match_columns(headers, columns, overrides) is not None:
                return i, headers, last
            i = j
        else:
            i += 1
    return None


def _build_row(headers: list[str], rows: list[list[str]], row: str) -> list[str]:
    cells = _split_cells(row)
    return (cells + ["—"] * len(headers))[: len(headers)]


def append_registry_row(path: Path, fmt: dict, row: str, overrides: dict | None = None) -> bool:
    """Дописывает строку в таблицу реестра; False — таблицы нет (строка ушла бы сиротой, FR-CN-1)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found = _find_registry_table(lines, fmt, overrides or {})
    if found is None:
        return False
    header_idx, headers, last = found
    rows = [_split_cells(ln) for ln in lines[header_idx + 2: last + 1]]
    cells = _build_row(headers, rows, row)
    new_line = "| " + " | ".join(cells) + " |"
    if new_line in lines[header_idx + 2: last + 1]:
        return True  # идемпотентность: такая строка уже есть (FR-CN-3)
    lines.insert(last + 1, new_line)
    guard.write_text(path, "\n".join(lines) + "\n")
    return True


def apply_batch(ws: Workspace, cfg: Config, library: Path, chapter: int, draft: int) -> str:
    """FR-CN-2: применяет подписанный пакет = правки документов + экспорт + линт + атомарный коммит."""
    if not gitops.is_repo(library):
        raise RuntimeError("библиотека не под git — без коммита приёмки откат невозможен: инициализируйте репозиторий (git init в библиотеке).")
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
            accepted_rules.append((m.group("target").strip(), re.sub(r"\s*<!--.*-->\s*$", "", m.group("rule"))))
    proposed_rows = [(p.get("registry", ""), p.get("row", "")) for p in proposals.get("facts", []) + proposals.get("samovolki", [])]
    rejected = [p for p in proposed_rows if p not in accepted_rows]
    if rejected:
        guard.append_text(ws.logs / "отклонено_канонистом.jsonl",
                          "".join(json.dumps({"chapter": chapter, "registry": r, "row": row}, ensure_ascii=False) + "\n" for r, row in rejected))
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    n_facts = len(accepted_rows)
    n_edits = len(review.load_edits(ws, chapter))
    resolutions = review.load_resolutions(ws, chapter)
    n_sam = sum(1 for r in resolutions if r.decision == "канонизировать")
    message = (f"{gitops.chapter_subject(chapter, ws.volume)} приёмка: записей в реестры {n_facts}, правок {n_edits}, канонизировано самоволок {n_sam} "
               f"(конвейер; решения — {ws.chapter_rel(chapter)}/решения.json)")
    regs = registries(ws.root)
    man = manifest_mod.effective(ws.root, library, catalog.load_types(ws.root))

    def write_batch() -> None:
        brief = exporter.load_brief(ws.exports, chapter)
        prose_dir = exporter.prose_folder(library, ws.root)
        guard.write_text(prose_dir / exporter.prose_name(library, brief.volume, chapter, ws.root), text)
        inbox: list[str] = []
        for registry, row in accepted_rows:
            reg = regs.get(registry)
            targets = exporter.docs_of_type(library, registry, ws.volume, ws.root) if reg else []
            ok = False
            if targets and row.strip().startswith("|") and reg:
                entry = man.entry_for(targets[0].relative_to(library).as_posix())
                ok = append_registry_row(targets[0], reg["format"], row.strip(), (entry.колонки if entry else {}))
            if not ok:
                inbox.append(f"- РЕЕСТР {registry}: {row}")
        inbox += _update_plants_status(ws, library, chapter, regs, man)
        if inbox:
            guard.append_text(library / INBOX_DOC, f"\n## Глава {chapter}\n" + "\n".join(inbox) + "\n")
        if accepted_rules:
            style = exporter.docs_of_type(library, "стиль", None, ws.root)
            if style:
                guard.append_text(style[0], f"\n### Кандидаты конвейера (глава {chapter}) — разложить по правилам вкуса\n"
                                  + "\n".join(f"- {t}: {r}" for t, r in accepted_rules) + "\n")

    result = canonchange.canon_change(ws, cfg, library, write_batch, message, commit=True, author_confirmed=True,
                                      action="применение пакета")
    commit = result.commit
    if commit is None:
        raise RuntimeError("после записи пакета в библиотеке нет изменений — коммит приёмки не создан (проверьте пакет).")
    _write_metrics(ws, chapter)
    return commit


def _update_plants_status(ws: Workspace, library: Path, chapter: int, regs: dict, man) -> list[str]:
    """Помечает закладки главы «положена ✓» в реестре закладок; без места для отметки — заметка во «Входящие»."""
    brief = exporter.load_brief(ws.exports, chapter)
    plants = [p for p in exporter.load_plants(ws.exports)
              if p.plant_id in brief.plants or (p.placed.get("vol") == brief.volume and p.placed.get("ch") == chapter)]
    if not plants:
        return []
    files = exporter.docs_of_type(library, "закладки", ws.volume, ws.root)
    if not files:
        return [f"- ЗАКЛАДКА {p.plant_id} «{p.what}» положена в главе {chapter} — отметьте в реестре закладок" for p in plants]
    path = files[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    notes: list[str] = []
    for p in plants:
        done = False
        for i, line in enumerate(lines):
            if line.strip().startswith("|") and p.plant_id in line:
                if "✓" in line:
                    done = True
                    break
                cells = line.split("|")
                if len(cells) >= 3:
                    cells[-2] = " положена ✓ "
                    lines[i] = "|".join(cells)
                    done = True
                break
        if not done:
            notes.append(f"- ЗАКЛАДКА {p.plant_id} «{p.what}» положена в главе {chapter} — отметьте в реестре закладок")
    guard.write_text(path, "\n".join(lines) + "\n")
    return notes


def _write_metrics(ws: Workspace, chapter: int) -> None:
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
    tokens = _corpus_tokens(ws, chapter)
    if tokens:
        metrics["слов"] = len(tokens)
        metrics["правок_на_1000"] = round(len(edits) / len(tokens) * 1000, 2)
    guard.append_text(ws.logs / "метрики.jsonl", json.dumps(metrics, ensure_ascii=False) + "\n")


def _corpus_tokens(ws: Workspace, chapter: int) -> list[str]:
    try:
        volume = exporter.load_brief(ws.exports, chapter).volume
    except FileNotFoundError:
        volume = None
    f = exporter.find_corpus_file(ws.corpus, chapter, volume)
    return f.read_text(encoding="utf-8").split() if f else []
