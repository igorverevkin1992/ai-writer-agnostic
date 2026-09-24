"""Применение онбординга (FR-ON-15…FR-ON-18, FR-ON-21, FR-ON-22): `konveyer онбординг --применить`.

Одна транзакция: нормализованные документы → библиотека, манифест (карта + сопоставление колонок), индекс
библиотеки, индекс сырья, выгрузки, один git-коммит «онбординг: N документов». Сбой на любом шаге откатывает всё:
библиотеку (снимок папки), манифест и индекс сырья (прежний текст). До транзакции каждый документ проверяется
разбором типа: файл без явного решения автора, который машина не читает, остаётся сырьём с вопросом, а не роняет
всё применение (§14.3 п. 1). Повторный импорт изменившегося источника — трёхстороннее построчное слияние
(прежнее извлечение, новое извлечение, текущий документ канона): изменения с одной стороны и совпадающие с обеих
применяются, пересечения — конфликт в `онбординг/конфликты/` с маркерами, канон не трогается; решения автора
`источник` (взять новое) и `канон` (оставить канон) снимают конфликт.
"""

from __future__ import annotations

import difflib
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
PART_MARK = "<!-- источник: {source} -->"   # склеенная часть внутри документа (FR-ON-9, FR-ON-18)
CONFLICT_A, CONFLICT_SEP, CONFLICT_B = "<<<<<<< источник", "=======", ">>>>>>> канон"


@dataclass
class ApplyResult:
    written: list[str] = field(default_factory=list)       # документы библиотеки (относительные пути)
    raw_kept: list[str] = field(default_factory=list)      # оставлены сырьём
    rejected: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)     # файлы конфликтов трёхстороннего сравнения
    updated: list[str] = field(default_factory=list)       # документы, обновлённые повторным импортом
    questions: list[str] = field(default_factory=list)     # почему файл оставлен сырьём без решения автора
    commit: str | None = None
    message: str = ""
    lint_errors: int = 0


class OnboardingError(RuntimeError):
    pass


@dataclass
class _Item:
    """Документ к записи: предложения-источники (при склейке — несколько), записи сырья, тип, имя, текст."""
    prs: list[propose.Proposal]
    raws: list[importer.RawEntry]
    spec: catalog.TypeSpec
    doc: str
    text: str
    explicit: bool                       # решение автора задано явно (иначе — принятие по порогу)
    prev: importer.RawEntry | None = None   # прежняя запись источника, чей документ обновляется
    merged: bool = False


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


def _sync_regions(base: list[str], a: list[str], b: list[str]) -> list[tuple[int, int, int, int, int, int]]:
    """Участки, совпадающие во всех трёх текстах: (base_от, base_до, a_от, a_до, b_от, b_до)."""
    ma = [m for m in difflib.SequenceMatcher(None, base, a, autojunk=False).get_matching_blocks() if m.size]
    mb = [m for m in difflib.SequenceMatcher(None, base, b, autojunk=False).get_matching_blocks() if m.size]
    out = []
    for x in ma:
        for y in mb:
            start, end = max(x.a, y.a), min(x.a + x.size, y.a + y.size)
            if start < end:
                out.append((start, end, x.b + (start - x.a), x.b + (end - x.a), y.b + (start - y.a), y.b + (end - y.a)))
    return sorted(out)


def merge3(base: str, a: str, b: str) -> tuple[str, bool]:
    """Построчное трёхстороннее слияние: (текст, есть_конфликт). Участок, изменённый только в `a` или только в `b`,
    берётся из изменившей стороны; одинаковое изменение с обеих — один раз; разные изменения одного участка —
    маркеры конфликта «<<<<<<< источник / ======= / >>>>>>> канон»."""
    lb, la, lc = base.splitlines(), a.splitlines(), b.splitlines()
    out: list[str] = []
    conflict = False
    pb = pa = pc = 0
    regions = _sync_regions(lb, la, lc) + [(len(lb), len(lb), len(la), len(la), len(lc), len(lc))]
    for bs, be, as_, ae, cs, ce in regions:
        if bs < pb or as_ < pa or cs < pc:
            continue  # перекрывающийся участок — уже покрыт предыдущим
        chunk_b, chunk_a, chunk_c = lb[pb:bs], la[pa:as_], lc[pc:cs]
        if chunk_a == chunk_b:
            out += chunk_c
        elif chunk_c == chunk_b or chunk_a == chunk_c:
            out += chunk_a
        else:
            conflict = True
            out += [CONFLICT_A, *chunk_a, CONFLICT_SEP, *chunk_c, CONFLICT_B]
        out += la[as_:ae]
        pb, pa, pc = be, ae, ce
    return "\n".join(out) + ("\n" if out else ""), conflict


def three_way(prev_norm: str, new_norm: str, canon: str) -> tuple[str, str | None]:
    """(исход, текст): «без_изменений» — источник не менялся; «обновить» — канон совпадал с прежним извлечением,
    берём новое; «слито» — правили обе стороны в разных местах, слияние без конфликтов; «конфликт» — одни и те же
    строки правили и в источнике, и в каноне (FR-ON-21): текст — слияние с маркерами."""
    if _sha(prev_norm) == _sha(new_norm):
        return "без_изменений", None
    if _sha(canon) == _sha(prev_norm):
        return "обновить", new_norm
    if _sha(canon) == _sha(new_norm):
        return "без_изменений", None
    merged, conflict = merge3(prev_norm, new_norm, canon)
    return ("конфликт", merged) if conflict else ("слито", merged)


def _write_conflict(ws: Workspace, doc: str, raw_name: str, prev_norm: str, new_norm: str, canon: str, merged: str) -> Path:
    d = propose.onboarding_dir(ws) / CONFLICTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / (re.sub(r"[^\w.\-]+", "_", re.sub(r"\.md$", "", doc)) + ".md")
    path.write_text("\n".join([
        f"# Конфликт повторного импорта: {doc}", "",
        "И источник, и документ канона изменились в одних и тех же строках с прошлого импорта. Канон не тронут. Выберите:", "",
        f"- «взять из источника» — `konveyer онбординг --решение {raw_name}=источник --применить`;",
        f"- «оставить канон» — `konveyer онбординг --решение {raw_name}=канон --применить`;",
        "- «объединить вручную» — перенесите нужное из раздела «Слияние с маркерами» в документ канона, "
        f"затем `konveyer канон-коммит` и `konveyer онбординг --решение {raw_name}=канон --применить`.", "",
        "## Слияние с маркерами", "", "```markdown", merged.rstrip("\n"), "```", "",
        "## Прежнее извлечение", "", "```markdown", prev_norm.rstrip("\n"), "```", "",
        "## Новое извлечение", "", "```markdown", new_norm.rstrip("\n"), "```", "",
        "## Текущий документ канона", "", "```markdown", canon.rstrip("\n"), "```", "",
    ]), encoding="utf-8")
    return path


# ------------------------------------------------------------------ применение


def _volume_for(spec: catalog.TypeSpec, pr: propose.Proposal, man: Manifest) -> int | None:
    return (pr.том or man.проект.текущий_том) if spec.per_volume else None


def _decision_of(pr: propose.Proposal) -> tuple[str, bool]:
    """(решение, явное ли): без решения автора принимается предложенный тип выше порога, иначе — сырьё."""
    if pr.решение:
        return pr.решение, True
    return ("принять" if pr.тип != "сырьё" else "сырьё"), False


def _type_of(pr: propose.Proposal, decision: str, types: dict[str, catalog.TypeSpec]) -> catalog.TypeSpec:
    type_name = decision[4:].strip() if decision.startswith("тип:") else pr.тип
    spec = types.get(type_name)
    if spec is None:
        raise OnboardingError(f"{pr.файл}: неизвестный тип «{type_name}»; доступные: {', '.join(sorted(types))}")
    return spec


def _glue_text(root: Path, head: propose.Proposal, parts: list[propose.Proposal]) -> str:
    """Склейка частей (FR-ON-9): текст головного файла, затем каждой части без её заголовка 1-го уровня,
    с пометкой источника части — содержимое дословно, только раскладка."""
    out = (root / head.извлечено_в).read_text(encoding="utf-8", errors="replace").rstrip("\n")
    for pr in parts:
        text = (root / pr.извлечено_в).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        text = re.sub(r"^#\s+.*\n+", "", text, count=1)
        out += "\n\n" + PART_MARK.format(source=f"сырьё/оригиналы/{pr.файл}") + "\n\n" + text.rstrip("\n")
    return out + "\n"


def check_readable(ws: Workspace, spec: catalog.TypeSpec, text: str, name: str, volume: int) -> propose.Preview:
    """Разбор нормализованного текста форматами типа до транзакции — как его прочитает экспорт."""
    return propose.preview(propose.write_preview(ws, name, text), spec, ws.root, {}, volume)


def apply(ws: Workspace, cfg: Config, library: Path, *, author_confirmed: bool, commit: bool = True) -> ApplyResult:
    """Применить решения автора из `онбординг/предложение.json`. Без решения по файлу — предложенный тип принимается,
    если уверенность ≥ порога и машина читает документ; иначе файл остаётся сырьём с вопросом."""
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
    taken = {e.файл for e in man.библиотека} | propose.library_files(library)
    reusable = propose.untouched_skeletons(library, man, types)
    result = ApplyResult()
    index_changed = False
    plan: list[_Item] = []
    props_by_file = {p.файл: p for p in proposals}
    glued: dict[str, list[propose.Proposal]] = {}   # головной файл → части
    remainders: list[tuple[importer.RawEntry, str]] = []

    def active(pr: propose.Proposal) -> bool:
        raw = by_name.get(pr.файл)
        return raw is not None and raw.статус == "сырьё" and bool(pr.извлечено_в)

    # 1. склейка: части собираются к головному файлу (FR-ON-9)
    for pr in proposals:
        decision, _ = _decision_of(pr)
        if active(pr) and decision.startswith("склеить:"):
            head = decision[len("склеить:"):].strip()
            head_pr = props_by_file.get(head)
            if head_pr is None or not active(head_pr):
                raise OnboardingError(f"{pr.файл}: склеить с «{head}» нельзя — файла нет в предложении или он уже не сырьё")
            head_dec, _ = _decision_of(head_pr)
            if head_dec in ("отклонить", "сырьё", "разбить") or head_dec.startswith("склеить:"):
                raise OnboardingError(f"{pr.файл}: склеить с «{head}» нельзя — у головного файла решение «{head_dec}»")
            glued.setdefault(head, []).append(pr)

    # 2. план документов
    for pr in proposals:
        raw = by_name.get(pr.файл)
        if not active(pr):
            continue
        decision, explicit = _decision_of(pr)
        if decision == "отклонить":
            raw.статус = "отклонено"
            index_changed = True
            result.rejected.append(pr.файл)
            continue
        if decision == "сырьё" or (decision == "принять" and pr.тип == "сырьё"):
            result.raw_kept.append(pr.файл)
            continue
        if decision.startswith("склеить:"):
            continue  # войдёт в документ головного файла
        spec = _type_of(pr, decision, types)
        src_path = root / pr.извлечено_в
        source_mark = f"сырьё/оригиналы/{pr.файл}"
        mapping = (pr.колонки or {}).get("mapping") if pr.колонки else None
        volume = _volume_for(spec, pr, man)
        if decision == "разбить":
            if not pr.разбить:
                raise OnboardingError(f"{pr.файл}: решение «разбить», но частей для разбиения нет — выполните "
                                      f"`konveyer онбординг` заново или выберите тип (`--решение {pr.файл}=тип:<имя>`)")
            parts, remainder = normalize.split_by_sections(src_path, pr.разбить)
            docs: list[str] = []
            for heading, part_type, part_text in parts:
                pspec = types.get(part_type)
                if pspec is None:
                    raise OnboardingError(f"{pr.файл}: часть «{heading}» — неизвестный тип «{part_type}»")
                doc = propose._proposed_name(pspec, heading + ".md", _volume_for(pspec, pr, man), taken)
                taken.add(doc)
                norm = normalize.normalize(part_text, pspec, source=source_mark, mapping=None, title=heading)
                plan.append(_Item([pr], [raw], pspec, doc, norm.text, True))
                docs.append(doc)
            if remainder.strip():
                rspec = types.get(pr.тип)
                if rspec is not None:
                    doc = propose._proposed_name(rspec, pr.файл, _volume_for(rspec, pr, man), taken, reusable)
                    taken.add(doc)
                    norm = normalize.normalize(remainder, rspec, source=source_mark, mapping=mapping,
                                               title=propose.display_stem(pr.файл), questions=pr.вопросы)
                    plan.append(_Item([pr], [raw], rspec, doc, norm.text, True))
                else:
                    remainders.append((raw, remainder))  # без типа — отдельным сырьём, ничего не теряется (П-7)
            continue
        parts = sorted(glued.get(pr.файл, []), key=lambda p: propose._sort_key(p.файл))
        text = _glue_text(root, pr, parts) if parts else src_path.read_text(encoding="utf-8", errors="replace")
        norm = normalize.normalize(text, spec, source=source_mark, mapping=mapping, title=propose.display_stem(pr.файл),
                                   questions=pr.вопросы)
        pv = check_readable(ws, spec, norm.text, pr.файл, volume or man.проект.текущий_том)
        if spec.extractions and (pv.error or (pv.records == 0 and not explicit)):
            problem = pv.error or "машина прочитала 0 записей"
            if not explicit:
                # без решения автора файл, который машина не читает, не применяется и не роняет остальные (§14.3 п. 1)
                result.raw_kept.append(pr.файл)
                result.questions.append(f"{pr.файл}: {problem} — оставлен сырьём; решение: "
                                        f"`--решение {pr.файл}=принять` (внести как есть), `=сырьё`, `=тип:<имя>` "
                                        f"или `=колонка:<поле>=<заголовок>`")
                continue
            raise OnboardingError(f"{pr.файл}: {problem}. Отклоните файл (`konveyer онбординг --решение {pr.файл}=сырьё`), "
                                  f"поправьте сопоставление колонок (`--решение {pr.файл}=колонка:<поле>=<заголовок>`) "
                                  "или исправьте документ и импортируйте заново")
        if pr.имя_документа and pr.тип == spec.name and (pr.имя_документа not in taken or pr.имя_документа in reusable
                                                            or (pr.обновление and man.entry_for(pr.имя_документа) is not None)):
            doc = pr.имя_документа
            reusable.discard(doc)
        else:
            doc = propose._proposed_name(spec, pr.файл, volume, taken, reusable)
        taken.add(doc)
        plan.append(_Item([pr, *parts], [raw, *[by_name[p.файл] for p in parts]], spec, doc, norm.text, explicit))

    # 3. повторный импорт: тот же источник уже в каноне (FR-ON-21)
    previous_by_source = {e.исходный_путь: e for e in entries if e.статус == "в_каноне" and e.документ_канона}
    final_plan: list[_Item] = []
    for item in plan:
        pr, raw = item.prs[0], item.raws[0]
        prev = previous_by_source.get(raw.исходный_путь)
        if prev is None or prev.файл == raw.файл:
            final_plan.append(item)
            continue
        decision, _ = _decision_of(pr)
        canon_path = library / prev.документ_канона
        prev_extract = root / prev.извлечено_в if prev.извлечено_в else None
        if not canon_path.exists() or prev_extract is None or not prev_extract.exists():
            final_plan.append(item)
            continue
        if decision == "источник":
            item.prev, item.doc = prev, prev.документ_канона
            final_plan.append(item)
            continue
        canon_text = canon_path.read_text(encoding="utf-8")
        if decision == "канон":
            # автор оставил канон (или слил вручную): новая версия считается внесённой, конфликт не повторяется
            _link(raw, item.spec, [prev.документ_канона])
            prev.статус, prev.причина = "заменён", f"заменён версией {raw.файл} (оставлен канон)"
            raw.хэш_извлечения = _sha(normalize.strip_source(canon_text))
            index_changed = True
            result.updated.append(prev.документ_канона)
            continue
        prev_mapping = None
        entry = man.entry_for(prev.документ_канона)
        if entry is not None and entry.колонки:
            prev_mapping = {k: v for k, v in entry.колонки.items() if isinstance(v, str)}
        prev_norm = normalize.normalize(prev_extract.read_text(encoding="utf-8", errors="replace"), item.spec,
                                        source=f"сырьё/оригиналы/{prev.файл}", mapping=prev_mapping, title=Path(prev.файл).stem).text
        outcome, merged = three_way(normalize.strip_source(prev_norm), normalize.strip_source(item.text),
                                    normalize.strip_source(canon_text))
        if outcome == "без_изменений":
            _link(raw, item.spec, [prev.документ_канона])
            prev.статус, prev.причина = "заменён", f"заменён версией {raw.файл} (без изменений)"
            index_changed = True
            continue
        if outcome == "конфликт":
            result.conflicts.append(str(_write_conflict(ws, prev.документ_канона, raw.файл, prev_norm, item.text,
                                                        canon_text, merged or "").relative_to(root)))
            continue
        if outcome == "слито":
            item.text = normalize.with_source(merged or "", f"сырьё/оригиналы/{raw.файл}")
            item.merged = True
        item.prev, item.doc = prev, prev.документ_канона
        final_plan.append(item)

    if not final_plan:
        if index_changed or result.conflicts:
            importer.save_index(ws, entries)
        if result.conflicts:
            result.message = "документы не изменены: все повторные импорты в конфликте — см. онбординг/конфликты/"
            return result
        if index_changed:
            result.message = (f"документы не изменены: отклонено {len(result.rejected)}, оставлено сырьём {len(result.raw_kept)}, "
                              f"обновлено без записи {len(result.updated)}")
            return result
        if result.questions:
            raise OnboardingError("применять нечего: ни одного подтверждённого документа.\n" + "\n".join(result.questions))
        raise OnboardingError("применять нечего: ни одного подтверждённого документа.")

    docs = [it.doc for it in final_plan]
    dup = sorted({d for d in docs if docs.count(d) > 1})
    if dup:
        raise OnboardingError(f"два документа претендуют на одно имя: {', '.join(dup)} — переименуйте сырьё или выберите тип")

    snapshot = _Snapshot(ws, library)
    try:
        def writer() -> None:
            for item in final_plan:
                pr, raw = item.prs[0], item.raws[0]
                target = library / item.doc
                guard.write_text(target, item.text)
                entry = man.entry_for(item.doc)
                mapping = (pr.колонки or {}).get("mapping") if pr.колонки else None
                if entry is None:
                    man.библиотека.append(LibraryEntry(
                        файл=item.doc, тип=item.spec.name, том=_volume_for(item.spec, pr, man),
                        колонки={k: v for k, v in (mapping or {}).items() if k.lower() != v.lower()} if mapping else {},
                        источник=f"сырьё/оригиналы/{raw.файл}", уверенность=pr.уверенность or None))
                else:
                    entry.источник = f"сырьё/оригиналы/{raw.файл}"
                    entry.авто = False
                for r in item.raws:
                    _link(r, item.spec, [item.doc])
                if item.prev is not None:
                    item.prev.статус = "заменён"
                    item.prev.причина = f"заменён новой версией {raw.файл}" + (" (слияние)" if item.merged else "")
                    result.updated.append(item.doc)
                else:
                    result.written.append(item.doc)
            for raw, text in remainders:
                importer.add_extraction(ws, entries, raw, text, "остаток")
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


def _link(raw: importer.RawEntry, spec: catalog.TypeSpec, docs: list[str]) -> None:
    """Обратная связь сырья с каноном (FR-ON-18): все документы, в которые ушёл файл; первый — в документ_канона."""
    raw.статус, raw.тип = "в_каноне", spec.name
    for d in docs:
        if d not in raw.документы_канона:
            raw.документы_канона.append(d)
    raw.документ_канона = raw.документы_канона[0] if raw.документы_канона else None
