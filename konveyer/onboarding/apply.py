"""Применение онбординга (FR-ON-15…FR-ON-18, FR-ON-21, FR-ON-22): `konveyer онбординг --применить`.

Одна транзакция: нормализованные документы → библиотека, манифест (карта + сопоставление колонок), индекс
библиотеки, индекс сырья, выгрузки, один git-коммит «онбординг: N документов». Сбой на любом шаге откатывает всё:
библиотеку (снимок папки), манифест и индекс сырья (прежний текст). Повторный импорт изменившегося источника —
трёхстороннее сравнение (прежнее извлечение, новое извлечение, текущий документ канона): совпадающие изменения
применяются, расхождения — конфликт в `онбординг/конфликты/`, канон не трогается.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import canonchange, catalog, guard, manifest as manifest_mod
from ..config import Config
from ..manifest import LibraryEntry, Manifest
from ..paths import Workspace
from . import importer, normalize, propose

CONFLICTS_DIR = "конфликты"
INDEX_DOC_INTRO = "Генерируется системой из манифеста `проект.yaml`; правится только через манифест."


@dataclass
class ApplyResult:
    written: list[str] = field(default_factory=list)       # документы библиотеки (относительные пути)
    raw_kept: list[str] = field(default_factory=list)      # оставлены сырьём
    rejected: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)     # файлы конфликтов трёхстороннего сравнения
    updated: list[str] = field(default_factory=list)       # документы, обновлённые повторным импортом
    commit: str | None = None
    message: str = ""
    lint_errors: int = 0


class OnboardingError(RuntimeError):
    pass


# ------------------------------------------------------------------ индекс библиотеки


def render_index(man: Manifest, types: dict[str, catalog.TypeSpec]) -> str:
    """Документ «индекс библиотеки» из манифеста (тип `индекс_библиотеки`): файл → тип → назначение."""
    lines = [f"# Индекс библиотеки — серия «{man.проект.имя}»", "", INDEX_DOC_INTRO, "",
             "| Файл | Тип | Назначение |", "|---|---|---|"]
    for e in man.библиотека:
        if e.выключен or e.тип == "индекс_библиотеки":
            continue
        spec = types.get(e.тип)
        lines.append(f"| {e.файл} | {e.тип} | {spec.purpose if spec else '—'} |")
    return "\n".join(lines) + "\n"


def index_doc_path(man: Manifest, library: Path, types: dict[str, catalog.TypeSpec]) -> Path:
    for e in man.библиотека:
        if e.тип == "индекс_библиотеки" and not e.is_folder:
            return library / e.файл
    spec = types.get("индекс_библиотеки")
    return library / (spec.default_name if spec and spec.default_name else "00_Индекс_библиотеки.md")


# ------------------------------------------------------------------ снимки для отката


class _Snapshot:
    """Снимок библиотеки, манифеста и индекса сырья — для отката транзакции без git (и поверх git)."""

    def __init__(self, ws: Workspace, library: Path):
        self.ws, self.library = ws, library
        self.tmp = Path(tempfile.mkdtemp(prefix="konveyer_onb_"))
        self.lib_copy = self.tmp / "lib"
        shutil.copytree(library, self.lib_copy, ignore=shutil.ignore_patterns(".git"))
        mp = manifest_mod.path_of(ws.root)
        self.manifest_text = mp.read_text(encoding="utf-8") if mp.exists() else None
        ip = importer.index_path(ws)
        self.index_text = ip.read_text(encoding="utf-8") if ip.exists() else None

    def restore(self) -> None:
        for p in sorted(self.library.rglob("*")):
            if ".git" in p.parts:
                continue
            rel = p.relative_to(self.library)
            if p.is_file() and not (self.lib_copy / rel).exists():
                p.unlink()
        for p in sorted(self.lib_copy.rglob("*")):
            rel = p.relative_to(self.lib_copy)
            if p.is_file():
                target = self.library / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or target.read_bytes() != p.read_bytes():
                    shutil.copyfile(p, target)
        for p in sorted((d for d in self.library.rglob("*") if d.is_dir() and ".git" not in d.parts), reverse=True):
            if not any(p.iterdir()):
                p.rmdir()
        mp = manifest_mod.path_of(self.ws.root)
        if self.manifest_text is None:
            mp.unlink(missing_ok=True)
        else:
            mp.write_text(self.manifest_text, encoding="utf-8")
        ip = importer.index_path(self.ws)
        if self.index_text is None:
            ip.unlink(missing_ok=True)
        else:
            ip.write_text(self.index_text, encoding="utf-8")

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ------------------------------------------------------------------ трёхстороннее сравнение


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def three_way(prev_norm: str, new_norm: str, canon: str) -> tuple[str, str | None]:
    """(исход, текст): «без_изменений» — источник не менялся; «обновить» — канон совпадал с прежним извлечением,
    берём новое; «конфликт» — правили и источник, и канон (FR-ON-21)."""
    if _sha(prev_norm) == _sha(new_norm):
        return "без_изменений", None
    if _sha(canon) == _sha(prev_norm):
        return "обновить", new_norm
    # построчное слияние: строки, изменённые только с одной стороны, применяются; двусторонние — конфликт
    return "конфликт", None


def _write_conflict(ws: Workspace, doc: str, prev_norm: str, new_norm: str, canon: str) -> Path:
    d = propose.onboarding_dir(ws) / CONFLICTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / (re.sub(r"[^\w.\-]+", "_", re.sub(r"\.md$", "", doc)) + ".md")
    path.write_text("\n".join([
        f"# Конфликт повторного импорта: {doc}", "",
        "И источник, и документ канона изменились с прошлого импорта. Канон не тронут. Выберите:",
        "«взять из источника» — скопируйте раздел «Новое извлечение» в документ; «оставить канон» — ничего не делать;",
        "«объединить вручную» — правьте документ канона. Затем `konveyer канон-коммит`.", "",
        "## Прежнее извлечение", "", "```markdown", prev_norm.rstrip("\n"), "```", "",
        "## Новое извлечение", "", "```markdown", new_norm.rstrip("\n"), "```", "",
        "## Текущий документ канона", "", "```markdown", canon.rstrip("\n"), "```", "",
    ]), encoding="utf-8")
    return path


# ------------------------------------------------------------------ применение


def _volume_for(spec: catalog.TypeSpec, pr: propose.Proposal, man: Manifest) -> int | None:
    return (pr.том or man.проект.текущий_том) if spec.per_volume else None


def apply(ws: Workspace, cfg: Config, library: Path, *, author_confirmed: bool, commit: bool = True) -> ApplyResult:
    """Применить решения автора из `онбординг/предложение.json`. Без решения по файлу — предложенный тип принимается,
    если уверенность ≥ порога; иначе файл остаётся сырьём."""
    if not author_confirmed:
        raise PermissionError("онбординг применяется только по подтверждению автора (FR-ON-12, Д-8).")
    root = ws.root
    types = catalog.load_types(root)
    proposals = propose.load(ws)
    if not proposals:
        raise OnboardingError("предложения нет — сначала `konveyer импорт <путь>` и `konveyer онбординг`.")
    entries = importer.load_index(ws)
    by_name = {e.файл: e for e in entries}
    man = manifest_mod.load(root) or manifest_mod.Manifest()
    taken = {e.файл for e in man.библиотека}
    result = ApplyResult()
    plan: list[tuple[propose.Proposal, importer.RawEntry, catalog.TypeSpec, str, str]] = []  # (pr, raw, spec, doc, text)

    for pr in proposals:
        raw = by_name.get(pr.файл)
        if raw is None or raw.статус != "сырьё" or not pr.извлечено_в:
            continue
        decision = pr.решение or ("принять" if pr.тип != "сырьё" else "сырьё")
        if decision == "отклонить":
            raw.статус = "отклонено"
            result.rejected.append(pr.файл)
            continue
        if decision == "сырьё" or (decision == "принять" and pr.тип == "сырьё"):
            result.raw_kept.append(pr.файл)
            continue
        type_name = decision[4:].strip() if decision.startswith("тип:") else pr.тип
        spec = types.get(type_name)
        if spec is None:
            raise OnboardingError(f"{pr.файл}: неизвестный тип «{type_name}»; доступные: {', '.join(sorted(types))}")
        src_path = root / pr.извлечено_в
        source_mark = f"сырьё/оригиналы/{pr.файл}"
        mapping = (pr.колонки or {}).get("mapping") if pr.колонки else None
        if decision == "разбить" and pr.разбить:
            for heading, part_type, part_text in normalize.split_by_sections(src_path, pr.разбить):
                pspec = types.get(part_type)
                if pspec is None:
                    continue
                doc = propose._proposed_name(pspec, heading + ".md", _volume_for(pspec, pr, man), taken)
                taken.add(doc)
                norm = normalize.normalize(part_text, pspec, source=source_mark, mapping=None, title=heading)
                plan.append((pr, raw, pspec, doc, norm.text))
            continue
        doc = pr.имя_документа if (pr.имя_документа and pr.тип == type_name and pr.имя_документа not in taken) \
            else propose._proposed_name(spec, pr.файл, _volume_for(spec, pr, man), taken)
        taken.add(doc)
        norm = normalize.normalize(src_path.read_text(encoding="utf-8", errors="replace"), spec,
                                   source=source_mark, mapping=mapping, title=Path(pr.файл).stem)
        plan.append((pr, raw, spec, doc, norm.text))

    # повторный импорт: тот же источник уже в каноне (FR-ON-21)
    previous_by_source = {e.исходный_путь: e for e in entries if e.статус == "в_каноне" and e.документ_канона}
    final_plan = []
    for pr, raw, spec, doc, text in plan:
        prev = previous_by_source.get(raw.исходный_путь)
        if prev is None or prev.файл == raw.файл:
            final_plan.append((pr, raw, spec, doc, text, None))
            continue
        canon_path = library / prev.документ_канона
        prev_extract = root / prev.извлечено_в if prev.извлечено_в else None
        if not canon_path.exists() or prev_extract is None or not prev_extract.exists():
            final_plan.append((pr, raw, spec, doc, text, None))
            continue
        prev_mapping = None
        entry = man.entry_for(prev.документ_канона)
        if entry is not None and entry.колонки:
            prev_mapping = dict(entry.колонки)
        prev_norm = normalize.normalize(prev_extract.read_text(encoding="utf-8", errors="replace"), spec,
                                        source=f"сырьё/оригиналы/{prev.файл}", mapping=prev_mapping, title=Path(prev.файл).stem).text
        canon_text = canon_path.read_text(encoding="utf-8")
        outcome, merged = three_way(normalize.strip_source(prev_norm), normalize.strip_source(text),
                                    normalize.strip_source(canon_text))
        if outcome == "без_изменений":
            raw.статус, raw.тип, raw.документ_канона = "в_каноне", spec.name, prev.документ_канона
            continue
        if outcome == "конфликт":
            result.conflicts.append(str(_write_conflict(ws, prev.документ_канона, prev_norm, text, canon_text).relative_to(root)))
            continue
        final_plan.append((pr, raw, spec, prev.документ_канона, text, prev))

    if not final_plan:
        if result.conflicts:
            importer.save_index(ws, entries)
            result.message = "документы не изменены: все повторные импорты в конфликте — см. онбординг/конфликты/"
            return result
        raise OnboardingError("применять нечего: ни одного подтверждённого документа.")

    snapshot = _Snapshot(ws, library)
    try:
        def writer() -> None:
            for pr, raw, spec, doc, text, prev in final_plan:
                target = library / doc
                guard.write_text(target, text)
                entry = man.entry_for(doc)
                mapping = (pr.колонки or {}).get("mapping") if pr.колонки else None
                if entry is None:
                    man.библиотека.append(LibraryEntry(
                        файл=doc, тип=spec.name, том=_volume_for(spec, pr, man),
                        колонки={k: v for k, v in (mapping or {}).items() if k != v} if mapping else {},
                        источник=f"сырьё/оригиналы/{raw.файл}", уверенность=pr.уверенность or None))
                else:
                    entry.источник = f"сырьё/оригиналы/{raw.файл}"
                    entry.авто = False
                raw.статус, raw.тип, raw.документ_канона = "в_каноне", spec.name, doc
                if prev is not None:
                    prev.статус = "заменён"
                    prev.причина = f"заменён новой версией {raw.файл}"
                    result.updated.append(doc)
                else:
                    result.written.append(doc)
            # индекс библиотеки — из манифеста
            idx = index_doc_path(man, library, types)
            guard.write_text(idx, render_index(man, types))
            rel_idx = idx.relative_to(library).as_posix()
            if man.entry_for(rel_idx) is None:
                man.библиотека.insert(0, LibraryEntry(файл=rel_idx, тип="индекс_библиотеки"))
            man.выведен = False
            manifest_mod.save(root, man)
            importer.save_index(ws, entries)

        n = len(final_plan)
        change = canonchange.canon_change(
            ws, cfg, library, writer, f"онбординг: {n} документов", commit=commit, author_confirmed=True,
            require_clean=False, action="онбординг", require_docs=False)
        result.commit = change.commit
        result.message = change.message
        result.lint_errors = change.lint.errors if change.lint else 0
    except BaseException:
        snapshot.restore()
        raise
    finally:
        snapshot.close()
    return result
