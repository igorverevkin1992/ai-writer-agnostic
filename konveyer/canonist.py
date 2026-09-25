"""Канонист: пакет записей в канон на подпись автору (FR-CN-1…FR-CN-4).

Единственный путь записи в библиотеку — `canonchange.canon_change` после явного подтверждения автора. Реестры,
в которые дописываются строки, — типы документов каталога с табличным форматом (эпистемика, закладки, континуити,
хронология, мир, предметы…): строка дописывается в таблицу, чьи заголовки соответствуют типу; иначе — во «Входящие».
Ключ строки (колонка с ролью «ключ») присваивается по реестру: следующий свободный номер, если модель или автор
оставили его пустым; занятый чужой строкой ключ заменяется свежим с заметкой во «Входящие».
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from jinja2 import Environment

from . import adapters, canonchange, catalog, declparse, exporter, gitops, guard, llmjson, manifest as manifest_mod
from . import review, verifier2
from .config import Config, library_dir
from .paths import Workspace
from .schemas import Verdict

INBOX_DOC = manifest_mod.ENGINE_DOC_PREFIX + "Входящие.md"  # собственный документ движка — вне карты библиотеки
ANSWER_FILE = "ответ_канониста.json"   # ручной режим: ответ модели, вставленный автором (FR-RL-3)
PROMPT_FILE = "промпт_канониста.md"
REJECTED_LOG = "отклонено_канонистом.jsonl"
PLACED_MARK = "положена ✓"
_NOTE_COLUMN_RE = re.compile(r"примеч|источник|коммент|заметк", re.IGNORECASE)
_EMPTY_CELLS = {"", "—", "-", "–"}
BATCH_ROW_RE = re.compile(r"^- РЕЕСТР\s+(?P<registry>[\w.\-]+)\s+→\s+(?P<row>.+)$")
TASTE_ROW_RE = re.compile(r"^- ПРАВИЛО\s+(?P<target>[^→]+?)\s+→\s+(?P<rule>.+)$")
_COMMENT_RE = re.compile(r"\s*<!--.*?-->\s*$")
_INDEX_RE = re.compile(r"<!--\s*№(?P<n>\d+)")
_ID_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


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
    """Шаблон роли: `промпты/канонист_система.md` проекта (имя движкового файла, как у остальных ролей),
    старое имя `промпты/канонист.md` — синоним; затем `шаблоны/` проекта; затем движок (FR-RL-2)."""
    for cand in (ws.root / "промпты" / "канонист_система.md", ws.root / "промпты" / "канонист.md",
                 ws.templates / "канонист_система.md"):
        if cand.exists():
            return cand.read_text(encoding="utf-8")
    return resources.files("konveyer").joinpath("шаблоны/канонист_система.md").read_text(encoding="utf-8")


# ограждение цитат самоволок (фрагменты прозы из флагов Э2) — данные, не инструкции (FR-SC-8)
QUOTE_OPEN = "<цитата>"
QUOTE_CLOSE = "</цитата>"


def _system(ws: Workspace, next_ids: dict[str, str] | None = None) -> str:
    man = verifier2._manifest(ws)
    regs = [{"name": n, "purpose": r["purpose"], "columns": r["columns"], "next_id": (next_ids or {}).get(n, "")}
            for n, r in registries(ws.root).items()]
    return Environment().from_string(_template(ws)).render(series=man.проект.имя, registries=regs)


def _norm_row(row: str) -> str:
    """Строка пакета в одну строку без лишних пробелов: перенос в ответе модели или хвостовой пробел не делают
    принятую строку «отклонённой» (FR-CN-4)."""
    return " ".join(str(row).split())


def build_user_prompt(ws: Workspace, chapter: int, text: str) -> str:
    edits = review.load_edits(ws, chapter)
    canonized = _canonized(ws, chapter)
    return "\n".join([
        f"# Глава {chapter}: материалы для пакета записей", "",
        "## Правки автора", *[f"{e.seq}. БЫЛО: {e.before} → СТАЛО: {e.after}" for e in edits], "",
        "## Канонизированные самоволки (решение автора уже принято)",
        *[f"- {c['flag_id']}: {QUOTE_OPEN}{c['quote']}{QUOTE_CLOSE} (целевой реестр: {c['target'] or 'предложи'})"
          for c in canonized], "",
        "## ПРИНЯТЫЙ ТЕКСТ ГЛАВЫ", "", verifier2.FENCE_OPEN, text, verifier2.FENCE_CLOSE,
    ])


def _canonized(ws: Workspace, chapter: int) -> list[dict]:
    flags = {f.flag_id: f for f in verifier2.load_flags(ws, chapter)}
    return [
        {"flag_id": r.flag_id, "quote": flags[r.flag_id].quote if r.flag_id in flags else "", "target": r.target_registry}
        for r in review.load_resolutions(ws, chapter) if r.decision == "канонизировать"
    ]


def _fallback_proposals(ws: Workspace, chapter: int, reason: str, next_ids: dict[str, str]) -> dict:
    """Пакет без модели: только канонизированные самоволки, ключи — следующие свободные по реестру."""
    regs = registries(ws.root)
    default_reg = next(iter(regs), "эпистемика")
    counters = dict(next_ids)
    samovolki = []
    for c in _canonized(ws, chapter):
        reg = c["target"] or default_reg
        key = counters.get(reg, "")
        if key:
            counters[reg] = _next_key([key])
        samovolki.append({"flag_id": c["flag_id"], "registry": reg,
                          "row": f"| {key or '—'} | {c['quote']} | (сформулировать) |"})
    return {"facts": [], "samovolki": samovolki, "edit_classes": [], "taste_rules": [], "_manual_mode": reason}


def _llm_proposals(ws: Workspace, cfg: Config, chapter: int, text: str, answer: str | None = None) -> dict:
    """Предложения Канониста: ответ модели, либо `answer` — ответ модели, вставленный автором (ручной режим,
    FR-RL-3), либо, без того и другого, пакет из одних канонизированных самоволок."""
    next_ids = next_key_ids(ws, cfg)
    system = _system(ws, next_ids)
    user = build_user_prompt(ws, chapter, text)
    guard.write_text(ws.chapter_dir(chapter) / PROMPT_FILE, f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
    if answer is not None:
        try:
            return llmjson.extract_json(answer, dict)
        except ValueError as e:
            raise ValueError(f"ответ Канониста ({ANSWER_FILE}) не разобран: {e}") from None
    try:
        raw = adapters.call_role(cfg, "канонист", system, user, ws.logs, role="канонист", chapter=chapter)
        try:
            return llmjson.extract_json(raw, dict)
        except ValueError as e:
            guard.write_text(ws.chapter_dir(chapter) / "ответ_канониста_сырой.md", raw)
            raise ValueError(f"ответ не разобран: {e}; сохранён в ответ_канониста_сырой.md") from None
    except (adapters.ManualModeNeeded, ValueError, json.JSONDecodeError) as e:
        reason = e.reason if isinstance(e, adapters.ManualModeNeeded) else str(e)
        return _fallback_proposals(ws, chapter, reason, next_ids)


def build_batch(ws: Workspace, cfg: Config, chapter: int, draft: int, answer: str | None = None) -> Path:
    """FR-CN-1: пакет на подпись автора. `answer` — JSON-ответ модели, вставленный автором (ручной режим)."""
    unresolved = review.unresolved_samovolki(ws, chapter)
    if unresolved:
        raise RuntimeError(f"Не решены самоволки: {', '.join(unresolved)}. Заполните decision в {ws.chapter_rel(chapter)}/решения.json.")
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    proposals = _llm_proposals(ws, cfg, chapter, text, answer)
    for key in ("facts", "samovolki", "edit_classes", "taste_rules"):
        if not isinstance(proposals.get(key), list):
            proposals[key] = []
    guard.write_text(ws.chapter_dir(chapter) / "пакет_канона.json", json.dumps(proposals, ensure_ascii=False, indent=2) + "\n")
    edits = review.load_edits(ws, chapter)
    classes = {c.get("seq"): c for c in proposals["edit_classes"] if isinstance(c, dict)}
    for e in edits:
        cls = classes.get(e.seq, {}).get("class")
        if cls in {"вкус", "факт", "канон"}:
            e.class_ = cls
    review.save_edits(ws, chapter, edits)
    lines = [
        f"# Пакет записей в канон · Глава {chapter}", "",
        "Удалите строки, которые НЕ принимаете (отклонённые будут залогированы, FR-CN-4); формулировки можно править.",
        f"Применение: `konveyer канон {chapter} --применить` (подпись автора).", "",
        "## Новые факты",
    ]
    n = 0
    for f in proposals["facts"]:
        n += 1
        lines.append(f"- РЕЕСТР {f.get('registry', 'эпистемика')} → {_norm_row(f.get('row', ''))}  <!-- №{n} · {_norm_row(f.get('reason', ''))} -->")
    lines += ["", "## Канонизированные самоволки"]
    for s in proposals["samovolki"]:
        n += 1
        lines.append(f"- РЕЕСТР {s.get('registry', 'эпистемика')} → {_norm_row(s.get('row', ''))}  <!-- №{n} · {s.get('flag_id', '')} -->")
    lines += ["", "## Кандидаты в правила вкуса (из повторяющихся правок)"]
    for t in proposals["taste_rules"]:
        n += 1
        lines.append(f"- ПРАВИЛО {_norm_row(t.get('target', 'правила вкуса'))} → {_norm_row(t.get('rule', ''))}  <!-- №{n} · {_norm_row(t.get('evidence', ''))} -->")
    lines += ["", "## Классы правок (подтвердите/исправьте в правки.jsonl)",
              *[f"- {e.seq}: {e.class_ or '—'} — {e.before[:60]} → {e.after[:60]}" for e in edits], ""]
    if proposals.get("_manual_mode"):
        lines.insert(1, f"\n⚠ Канонист работал без модели (ручной режим): {proposals['_manual_mode']}\n"
                        f"Промпт сохранён в {PROMPT_FILE}; ответ модели можно положить в {ANSWER_FILE} и пересобрать пакет: "
                        f"`konveyer канон {chapter} --manual`.\n")
    path = ws.chapter_dir(chapter) / "пакет_канона.md"
    guard.write_text(path, "\n".join(lines) + "\n")
    return path


# ------------------------------------------------------------ таблицы реестров


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _join_cells(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _read_doc(path: Path) -> tuple[str, list[str], str, bool]:
    """(исходный текст, строки, перевод строки документа, есть ли перевод строки в конце) — чтобы запись
    не меняла ничего сверх нужной строки (CRLF и отсутствие завершающего перевода строки сохраняются)."""
    with path.open(encoding="utf-8", newline="") as f:
        raw = f.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw, raw.splitlines(), newline, raw.endswith(("\n", "\r"))


def _write_doc(path: Path, raw: str, lines: list[str], newline: str, trailing: bool) -> bool:
    """Записывает документ только если он действительно изменился (FR-CN-2: ни одной строки сверх)."""
    text = newline.join(lines) + (newline if trailing or not raw else "")
    if text == raw:
        return False
    guard.write_text(path, text)
    return True


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


def _column_index(headers: list[str], mapping: dict[str, str], field: str) -> int | None:
    header = mapping.get(field)
    return headers.index(header) if header in headers else None


def _key_field(columns: dict) -> str | None:
    return next((f for f, s in columns.items() if isinstance(s, dict) and s.get("роль") == "ключ"), None)


def _entity_field(columns: dict, key_field: str | None) -> str | None:
    """Первая обязательная колонка помимо ключа («факт», «что»…): по ней видно, та же это сущность под занятым
    ключом (ещё один субъект того же факта) или чужая строка с выдуманным ключом."""
    return next((f for f, s in columns.items()
                 if f != key_field and isinstance(s, dict) and bool(s.get("обязательна", False))), None)


def _next_key(existing: list[str]) -> str:
    """Следующий свободный ключ: максимальный номер + 1 с тем же префиксом и шириной («M-009» → «M-010»,
    «P-1» → «P-2»); в пустом реестре — «1»."""
    best: tuple[int, str, int] | None = None
    for k in existing:
        m = _ID_RE.match(k.strip())
        if m:
            n = int(m.group("num"))
            if best is None or n > best[0]:
                best = (n, m.group("prefix"), len(m.group("num")))
    if best is None:
        return "1"
    n, prefix, width = best
    return f"{prefix}{n + 1:0{width}d}"


def _build_row(headers: list[str], row: str) -> list[str] | None:
    """Ячейки строки по числу колонок; недостающие — «—». None — ячеек больше, чем колонок (усекать молча
    нельзя: строка уйдёт во «Входящие»)."""
    cells = _split_cells(row)
    while len(cells) > len(headers) and cells[-1] in _EMPTY_CELLS:
        cells.pop()
    if len(cells) > len(headers):
        return None
    return cells + ["—"] * (len(headers) - len(cells))


@dataclass
class RowOutcome:
    """Судьба строки реестра: `ok` — строка в таблице (дописана или уже была); `reason` — почему не дописана
    либо заметка о присвоенном ключе; `key` — ключ строки."""

    ok: bool
    reason: str = ""
    key: str = ""


def next_key_of(path: Path, fmt: dict, overrides: dict | None = None) -> str:
    """Следующий свободный ключ таблицы реестра в документе (пусто — таблицы или колонки-ключа нет)."""
    _, lines, _, _ = _read_doc(path)
    found = _find_registry_table(lines, fmt, overrides or {})
    if found is None:
        return ""
    header_idx, headers, last = found
    columns = fmt.get("колонки") or {}
    mapping = declparse.match_columns(headers, columns, overrides or {}) or {}
    key_idx = _column_index(headers, mapping, _key_field(columns) or "")
    if key_idx is None:
        return ""
    rows = [_split_cells(ln) for ln in lines[header_idx + 2: last + 1]]
    return _next_key([r[key_idx] for r in rows if len(r) > key_idx])


def next_key_ids(ws: Workspace, cfg: Config) -> dict[str, str]:
    """{реестр: следующий свободный ключ} по документам текущего тома — модель и ручной режим не выдумывают
    занятые идентификаторы (FR-CN-1)."""
    library = library_dir(ws, cfg)
    out: dict[str, str] = {}
    try:
        man = manifest_mod.effective(ws.root, library, catalog.load_types(ws.root))
    except Exception:  # noqa: BLE001 — без манифеста ключи просто не подсказываются (П-5)
        return out
    for name, reg in registries(ws.root).items():
        try:
            targets = exporter.docs_of_type(library, name, ws.volume, ws.root)
        except Exception:  # noqa: BLE001
            targets = []
        if not targets:
            continue
        entry = man.entry_for(targets[0].relative_to(library).as_posix())
        key = next_key_of(targets[0], reg["format"], entry.колонки if entry else {})
        if key:
            out[name] = key
    return out


def append_registry_row(path: Path, fmt: dict, row: str, overrides: dict | None = None, *,
                        dry_run: bool = False) -> RowOutcome:
    """Дописывает строку в таблицу реестра (FR-CN-1): ключ пустой — присваивается следующий свободный; ключ занят
    другой сущностью — заменяется свежим с заметкой; такая же строка уже есть — ничего не пишется (FR-CN-3);
    таблицы нет или ячеек больше колонок — `ok=False` (строка уйдёт во «Входящие», не сиротой)."""
    raw, lines, newline, trailing = _read_doc(path)
    overrides = overrides or {}
    found = _find_registry_table(lines, fmt, overrides)
    if found is None:
        return RowOutcome(False, f"в {path.name} нет таблицы реестра с нужными колонками")
    header_idx, headers, last = found
    cells = _build_row(headers, row)
    if cells is None:
        return RowOutcome(False, f"ячеек больше, чем колонок в {path.name} ({len(headers)})")
    existing_lines = lines[header_idx + 2: last + 1]
    rows = [_split_cells(ln) for ln in existing_lines]
    columns = fmt.get("колонки") or {}
    mapping = declparse.match_columns(headers, columns, overrides) or {}
    key_field = _key_field(columns)
    key_idx = _column_index(headers, mapping, key_field or "")
    note = ""
    if key_idx is not None:
        keys = [r[key_idx] for r in rows if len(r) > key_idx]
        key = cells[key_idx]
        if key in _EMPTY_CELLS or declparse.is_placeholder(key):
            cells[key_idx] = _next_key(keys)
        elif key in keys:
            if _join_cells(cells) in (ln.strip() for ln in existing_lines):
                return RowOutcome(True, "", key)  # идемпотентность: такая строка уже есть (FR-CN-3)
            ent_idx = _column_index(headers, mapping, _entity_field(columns, key_field) or "")
            same_entity = ent_idx is not None and any(
                len(r) > max(key_idx, ent_idx) and r[key_idx] == key and r[ent_idx] == cells[ent_idx] for r in rows)
            if not same_entity:
                cells[key_idx] = _next_key(keys)
                note = f"ключ {key} в {path.name} уже занят другой строкой — записано под {cells[key_idx]}"
    new_line = _join_cells(cells)
    if new_line in (ln.strip() for ln in existing_lines):
        return RowOutcome(True, "", cells[key_idx] if key_idx is not None else "")
    if not dry_run:
        lines.insert(last + 1, new_line)
        _write_doc(path, raw, lines, newline, trailing)
    return RowOutcome(True, note, cells[key_idx] if key_idx is not None else "")


# ------------------------------------------------------------ применение пакета


def _parse_signed_batch(batch_path: Path) -> tuple[list[tuple[str, str, int | None]], list[tuple[str, str, int | None]]]:
    """Принятые автором строки пакета: (реестр, строка, № предложения) и (цель, правило, № предложения)."""
    rows: list[tuple[str, str, int | None]] = []
    rules: list[tuple[str, str, int | None]] = []
    for line in batch_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m_idx = _INDEX_RE.search(line)
        idx = int(m_idx.group("n")) if m_idx else None
        m = BATCH_ROW_RE.match(line)
        if m:
            rows.append((m.group("registry"), _norm_row(_COMMENT_RE.sub("", m.group("row"))), idx))
            continue
        m = TASTE_ROW_RE.match(line)
        if m:
            rules.append((m.group("target").strip(), _norm_row(_COMMENT_RE.sub("", m.group("rule"))), idx))
    return rows, rules


def _log_rejected(ws: Workspace, chapter: int, proposals: dict, rows: list, rules: list) -> None:
    """FR-CN-4: предложения, которых нет в подписанном пакете, — в журнал. Сопоставление по № предложения
    (автор мог поправить формулировку), без № — по нормализованному тексту."""
    accepted_idx = {i for *_, i in rows + rules if i is not None}
    accepted_rows = {(r, row) for r, row, _ in rows}
    accepted_rules = {(t, rule) for t, rule, _ in rules}
    n = 0
    entries: list[dict] = []
    for kind, key in (("факт", "facts"), ("самоволка", "samovolki")):
        for p in proposals.get(key, []):
            n += 1
            if not isinstance(p, dict) or n in accepted_idx:
                continue
            reg, row = str(p.get("registry", "")), _norm_row(p.get("row", ""))
            if (reg, row) not in accepted_rows:
                entries.append({"chapter": chapter, "kind": kind, "registry": reg, "row": row, "flag_id": p.get("flag_id")})
    for p in proposals.get("taste_rules", []):
        n += 1
        if not isinstance(p, dict) or n in accepted_idx:
            continue
        target, rule = _norm_row(p.get("target", "правила вкуса")), _norm_row(p.get("rule", ""))
        if (target, rule) not in accepted_rules:
            entries.append({"chapter": chapter, "kind": "правило", "target": target, "rule": rule})
    if entries:
        guard.append_text(ws.logs / REJECTED_LOG, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def _row_target(ws: Workspace, library: Path, regs: dict, man, registry: str, row: str):
    """(документ, формат, переопределения колонок) для строки реестра; None — реестра/документа нет."""
    reg = regs.get(registry)
    if not reg or not row.strip().startswith("|"):
        return None
    targets = exporter.docs_of_type(library, registry, ws.volume, ws.root)
    if not targets:
        return None
    entry = man.entry_for(targets[0].relative_to(library).as_posix())
    return targets[0], reg["format"], (entry.колонки if entry else {})


def apply_batch(ws: Workspace, cfg: Config, library: Path, chapter: int, draft: int) -> canonchange.ChangeResult:
    """FR-CN-2: применяет подписанный пакет = правки документов + экспорт + линт + атомарный коммит.
    Возвращает итог изменения канона (`.commit` — SHA коммита приёмки, `.lint` — отчёт линтера)."""
    if not gitops.is_repo(library):
        raise RuntimeError("библиотека не под git — без коммита приёмки откат невозможен: инициализируйте репозиторий (git init в библиотеке).")
    canonchange.check_git(library, commit=True, action="применение пакета")
    batch_path = ws.chapter_dir(chapter) / "пакет_канона.md"
    proposals_path = ws.chapter_dir(chapter) / "пакет_канона.json"
    proposals = json.loads(proposals_path.read_text(encoding="utf-8")) if proposals_path.exists() else {}
    accepted_rows, accepted_rules = _parse_signed_batch(batch_path)
    _log_rejected(ws, chapter, proposals, accepted_rows, accepted_rules)
    text = ws.draft_path(chapter, draft).read_text(encoding="utf-8")
    regs = registries(ws.root)
    man = manifest_mod.effective(ws.root, library, catalog.load_types(ws.root))
    # сухой прогон: сколько строк ляжет в реестры, а сколько — во «Входящие» (для честного сообщения коммита)
    n_registry = 0
    for registry, row, _ in accepted_rows:
        target = _row_target(ws, library, regs, man, registry, row)
        if target is not None and append_registry_row(target[0], target[1], row, target[2], dry_run=True).ok:
            n_registry += 1
    n_inbox = len(accepted_rows) - n_registry
    n_edits = len(review.load_edits(ws, chapter))
    n_sam = sum(1 for r in review.load_resolutions(ws, chapter) if r.decision == "канонизировать")
    message = (f"{gitops.chapter_subject(chapter, ws.volume)} приёмка: записей в реестры {n_registry}"
               + (f", во «Входящие» {n_inbox}" if n_inbox else "")
               + f", правок {n_edits}, канонизировано самоволок {n_sam} "
               f"(конвейер; решения — {ws.chapter_rel(chapter)}/решения.json)"
               + gitops.acceptance_trailer(chapter, ws.draft_path(chapter, draft).name, ws.volume))

    def write_batch() -> None:
        brief = exporter.load_brief(ws.exports, chapter)
        prose_dir = exporter.prose_folder(library, ws.root)
        guard.write_text(prose_dir / exporter.prose_name(library, brief.volume, chapter, ws.root), text)
        inbox: list[str] = []
        for registry, row, _ in accepted_rows:
            target = _row_target(ws, library, regs, man, registry, row)
            if target is None:
                inbox.append(f"- РЕЕСТР {registry}: {row}")
                continue
            outcome = append_registry_row(target[0], target[1], row, target[2])
            if not outcome.ok:
                inbox.append(f"- РЕЕСТР {registry}: {row}  ← {outcome.reason}")
            elif outcome.reason:
                inbox.append(f"- ЗАМЕТКА {registry}: {outcome.reason}")
        inbox += _update_plants_status(ws, library, chapter, regs, man)
        if inbox:
            guard.append_text(library / INBOX_DOC, f"\n## Глава {chapter}\n" + "\n".join(inbox) + "\n")
        if accepted_rules:
            style = exporter.docs_of_type(library, "стиль", None, ws.root)
            if style:
                guard.append_text(style[0], f"\n### Кандидаты конвейера (глава {chapter}) — разложить по правилам вкуса\n"
                                  + "\n".join(f"- {t}: {r}" for t, r, _ in accepted_rules) + "\n")

    result = canonchange.canon_change(ws, cfg, library, write_batch, message, commit=True, author_confirmed=True,
                                      action="применение пакета")
    if result.commit is None:
        raise RuntimeError("после записи пакета в библиотеке нет изменений — коммит приёмки не создан (проверьте пакет).")
    _write_metrics(ws, chapter)
    return result


def _update_plants_status(ws: Workspace, library: Path, chapter: int, regs: dict, man) -> list[str]:
    """Помечает закладки главы «положена ✓» в колонке «статус» реестра закладок (колонка — по заголовку,
    закладка — по точному совпадению ключа); без колонки или строки — заметка во «Входящие»."""
    brief = exporter.load_brief(ws.exports, chapter)
    plants = [p for p in exporter.load_plants(ws.exports)
              if p.plant_id in brief.plants or (p.placed.get("vol") == brief.volume and p.placed.get("ch") == chapter)]
    if not plants:
        return []

    def note(p) -> str:
        return f"- ЗАКЛАДКА {p.plant_id} «{p.what}» положена в главе {chapter} — отметьте в реестре закладок"

    reg = regs.get("закладки")
    files = exporter.docs_of_type(library, "закладки", ws.volume, ws.root) if reg else []
    if not files:
        return [note(p) for p in plants]
    path = files[0]
    entry = man.entry_for(path.relative_to(library).as_posix())
    overrides = entry.колонки if entry else {}
    raw, lines, newline, trailing = _read_doc(path)
    found = _find_registry_table(lines, reg["format"], overrides)
    if found is None:
        return [note(p) for p in plants]
    header_idx, headers, last = found
    columns = reg["format"].get("колонки") or {}
    mapping = declparse.match_columns(headers, columns, overrides) or {}
    key_idx = _column_index(headers, mapping, _key_field(columns) or "")
    status_idx = _column_index(headers, mapping, "статус")
    notes: list[str] = []
    for p in plants:
        done = False
        for i in range(header_idx + 2, last + 1):
            cells = _split_cells(lines[i])
            if key_idx is None or len(cells) <= key_idx or cells[key_idx] != p.plant_id:
                continue
            if status_idx is None or len(cells) <= status_idx:
                break  # некуда ставить отметку — заметка автору, чужие колонки не трогаем
            if "✓" in cells[status_idx]:
                done = True
                break
            cells[status_idx] = PLACED_MARK
            lines[i] = _join_cells(cells)
            done = True
            break
        if not done:
            notes.append(note(p))
    _write_doc(path, raw, lines, newline, trailing)
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
