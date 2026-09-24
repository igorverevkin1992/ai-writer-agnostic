"""Writer-adapter: вызовы Писателя (FR-WR-1…FR-WR-5) и дословные правки кодом (FR-ED-2)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources

from jinja2 import Environment, StrictUndefined

from . import adapters, cancel, catalog, guard, manifest as manifest_mod
from .verifier2 import FENCE_CLOSE, FENCE_OPEN
from .config import Config
from .paths import Workspace
from .schemas import Edit

MODE_LOCAL = "правки (код)"


def require_text(text: str, role: str) -> str:
    """Пустой или пробельный ответ модели — не черновик (FR-WR-1, П-5): артефакт не пишется, состояние главы
    не меняется, автору — ручной режим с причиной."""
    if not (text or "").strip():
        raise adapters.ManualModeNeeded(
            f"{role}: модель вернула пустой ответ (отказ, блокировка или обрыв) — черновик не сохранён.",
            "повторите шаг позже или прогоните окно/промпт вручную и сохраните ответ в ожидаемый файл артефакта.",
        )
    return text


def _save_draft(ws: Workspace, chapter: int, k: int, text: str, cfg: Config, mode: str, extra: dict | None = None,
                suffix: str = "") -> None:
    """черновик_k.md + черновик_k.meta.json; `suffix` («.alt1») — вариант A/B рядом с основным черновиком."""
    chdir = ws.chapter_dir(chapter)
    guard.write_text(chdir / f"черновик_{k}{suffix}.md", text)
    meta = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": cfg.writer.model,
        "params": cfg.writer.params,  # Д-6: параметры пинуются конфигом
        "mode": mode,
    }
    if extra:
        meta.update(extra)
    guard.write_text(
        chdir / f"черновик_{k}{suffix}.meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )


def write_chapter(ws: Workspace, cfg: Config, chapter: int, k: int) -> None:
    """FR-W1: отправляет окно, сохраняет ответ как черновик_k.md. Контекст — только окно."""
    window = ws.window_path(chapter).read_text(encoding="utf-8")
    text = require_text(adapters.call_model(cfg.writer, cfg.api, "", window, ws.logs, role="писатель", chapter=chapter), "Писатель")
    _save_draft(ws, chapter, k, text, cfg, mode="генерация")


# ------------------------------------------------------------ варианты A/B (аудит 2, п. 24б)


def variant_suffix(label: str) -> str:
    """«основной» → "", «alt1» → ".alt1"."""
    return "" if label in ("", "основной", "main") else f".{label}"


def variant_labels(n: int) -> list[str]:
    return ["основной"] + [f"alt{i}" for i in range(1, n)]


def write_variants(ws: Workspace, cfg: Config, chapter: int, k: int, n: int) -> list[str]:
    """A/B: n вызовов Писателя по одному окну → черновик_k.md, черновик_k.alt1.md … (+ meta).
    Между вызовами — точка отмены. Без ключа ManualModeNeeded поднимается на первом вызове;
    сохранённые до отказа варианты остаются на диске."""
    window = ws.window_path(chapter).read_text(encoding="utf-8")
    labels = variant_labels(n)
    saved: list[str] = []
    for i, label in enumerate(labels):
        if i:
            cancel.check(f"вариант {label}")
        text = require_text(adapters.call_model(cfg.writer, cfg.api, "", window, ws.logs, role="писатель", chapter=chapter),
                            f"Писатель (вариант {label})")
        _save_draft(ws, chapter, k, text, cfg, mode=f"генерация (вариант {label})",
                    extra={"вариант": label, "вариантов": n}, suffix=variant_suffix(label))
        saved.append(label)
    return saved


def variant_path(ws: Workspace, chapter: int, k: int, label: str):
    return ws.chapter_dir(chapter) / f"черновик_{k}{variant_suffix(label)}.md"


def existing_variants(ws: Workspace, chapter: int, k: int) -> list[str]:
    """Метки вариантов черновика k, лежащих на диске: «основной», alt0 (вытесненный), alt1…"""
    chdir = ws.chapter_dir(chapter)
    labels = ["основной"] if ws.draft_path(chapter, k).exists() else []
    alts = sorted(
        (p.name[len(f"черновик_{k}."):-3] for p in chdir.glob(f"черновик_{k}.alt*.md")),
        key=lambda s: int(re.sub(r"\D", "", s) or 0),
    )
    return labels + alts


def choose_variant(ws: Workspace, chapter: int, k: int, label: str) -> None:
    """Выбор автора: вариант label становится черновик_k.md; прежний основной сохраняется как alt0
    (выбор обратим: `--выбрать alt0`). Состояние главы не меняется."""
    if label in ("основной", "main", ""):
        return
    src = variant_path(ws, chapter, k, label)
    if not src.exists():
        raise FileNotFoundError(
            f"варианта «{label}» нет: {src.name} — есть {', '.join(existing_variants(ws, chapter, k)) or 'ничего'}."
        )
    main = ws.draft_path(chapter, k)
    alt0 = variant_path(ws, chapter, k, "alt0")
    if main.exists() and label != "alt0" and not alt0.exists():
        guard.write_text(alt0, main.read_text(encoding="utf-8"))
        main_meta = ws.chapter_dir(chapter) / f"черновик_{k}.meta.json"
        if main_meta.exists():
            guard.write_text(ws.chapter_dir(chapter) / f"черновик_{k}.alt0.meta.json", main_meta.read_text(encoding="utf-8"))
    guard.write_text(main, src.read_text(encoding="utf-8"))
    src_meta = ws.chapter_dir(chapter) / f"черновик_{k}{variant_suffix(label)}.meta.json"
    meta = json.loads(src_meta.read_text(encoding="utf-8")) if src_meta.exists() else {}
    meta["выбран"] = label
    meta["выбран_ts"] = datetime.now(timezone.utc).isoformat()
    guard.write_text(ws.chapter_dir(chapter) / f"черновик_{k}.meta.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


# ------------------------------------------------------------ правки кодом (аудит 2, п. 19; Р-023)


@dataclass
class LocalEdits:
    """Итог дословного применения правок: текст, что применено кодом, что остаётся Писателю."""

    text: str
    applied: list[Edit] = field(default_factory=list)
    remaining: list[Edit] = field(default_factory=list)
    reasons: dict[int, str] = field(default_factory=dict)  # seq → почему правка ушла Писателю

    @property
    def needs_model(self) -> bool:
        return bool(self.remaining)


def _quote_pattern(quote: str) -> re.Pattern:
    """Цитата «БЫЛО» с терпимостью к переносам/пробелам внутри (автор копирует из приёмки с разной вёрсткой),
    но по границам слова: «Он » не находится внутри «Оно», намеренный крайний пробел цитаты обязателен."""
    core = r"\s+".join(re.escape(w) for w in quote.split())
    lead = r"\s" if quote[:1].isspace() else (r"(?<!\w)" if quote[:1].isalnum() else "")
    trail = r"\s" if quote[-1:].isspace() else (r"(?!\w)" if quote[-1:].isalnum() else "")
    return re.compile(lead + core + trail)


def find_quote(text: str, quote: str) -> list[tuple[int, int]]:
    """Все вхождения цитаты (дословно, с точностью до внутренних пробелов, по границам слова): список (start, end)."""
    quote = quote.strip("\n\r")
    if not quote.strip():
        return []
    return [(m.start(), m.end()) for m in _quote_pattern(quote).finditer(text)]


def apply_edits_text(text: str, edits: list[Edit]) -> LocalEdits:
    """FR-ED-1: пары БЫЛО/СТАЛО, чьё «БЫЛО» найдено в тексте ровно один раз (по границам слова), применяются
    кодом (пустое «СТАЛО» — удаление). Крайние пробелы цитаты — часть цитаты («Он » → «Она »), переносы строк
    вокруг неё — нет. Свободные указания и не найденные / неоднозначные цитаты остаются Писателю.
    Правки применяются по порядку к уже изменённому тексту."""
    result = LocalEdits(text=text)
    for e in edits:
        before, after = e.before.strip("\n\r"), e.after.strip("\n\r")
        if not after.strip():
            after = ""
        if not before.strip():
            result.remaining.append(e)
            result.reasons[e.seq] = "свободное указание"
            continue
        hits = find_quote(result.text, before)
        if len(hits) != 1:
            result.remaining.append(e)
            result.reasons[e.seq] = "«БЫЛО» не найдено дословно" if not hits else f"«БЫЛО» встречается {len(hits)} раза(-)"
            continue
        start, end = hits[0]
        t = result.text
        if not after:
            # удаление: убираем и один разделитель — пробел после цитаты (или перед, если она в конце)
            if end < len(t) and t[end] == " ":
                end += 1
            elif start > 0 and t[start - 1] == " ":
                start -= 1
        result.text = t[:start] + after + t[end:]
        result.applied.append(e)
    return result


def apply_edits_locally(ws: Workspace, cfg: Config, chapter: int, черновик_k: int, edits: list[Edit],
                        new_k: int | None = None) -> tuple[int, LocalEdits]:
    """Правки кодом от черновика черновик_k (база приёмки, FR-E3): новый черновик_{new_k} с mode «правки (код)».
    Возвращает (номер нового черновика, итог). Если что-то осталось Писателю — черновик НЕ пишется:
    промежуточный текст отдаётся в `apply_edits(..., base_text=…)`."""
    base_text = ws.draft_path(chapter, черновик_k).read_text(encoding="utf-8")
    result = apply_edits_text(base_text, edits)
    new_k = new_k or черновик_k + 1
    if not result.needs_model:
        _save_draft(ws, chapter, new_k, result.text, cfg, mode=MODE_LOCAL,
                    extra={"применено_кодом": [e.seq for e in result.applied], "база": черновик_k})
    return new_k, result


def edit_prompt(ws: Workspace, chapter: int, черновик_k: int, edits: list[Edit], draft_text: str | None = None) -> str:
    """FR-W2: принятый черновик + правки + инструкция «внести точно» (шаблон в шаблоны/).
    `draft_text` — промежуточный текст после правок кодом (Р-023); без него — сам черновик_k."""
    tpl = None
    for cand in (ws.root / "промпты" / "правки.md.j2", ws.templates / "правки.md.j2"):
        if cand.exists():
            tpl = cand.read_text(encoding="utf-8")
            break
    if tpl is None:
        tpl = resources.files("konveyer").joinpath("шаблоны/правки.md.j2").read_text(encoding="utf-8")
    draft = draft_text if draft_text is not None else ws.draft_path(chapter, черновик_k).read_text(encoding="utf-8")
    lib = guard._library() or ws.root / "Библиотека"
    series = manifest_mod.effective(ws.root, lib, catalog.load_types(ws.root)).проект.имя
    env = Environment(undefined=StrictUndefined)
    return env.from_string(tpl).render(edits=edits, draft=draft, series=series, fence_open=FENCE_OPEN, fence_close=FENCE_CLOSE)


def apply_edits(ws: Workspace, cfg: Config, chapter: int, черновик_k: int, edits: list[Edit], new_k: int | None = None,
                base_text: str | None = None, applied_locally: list[int] | None = None) -> int:
    """Вызов Писателя в режиме правок от черновика черновик_k (база приёмки, FR-E3); возвращает номер нового черновика.
    `base_text` — текст с уже применёнными кодом правками (Писателю уходят только `edits`)."""
    prompt = edit_prompt(ws, chapter, черновик_k, edits, draft_text=base_text)
    guard.write_text(ws.chapter_dir(chapter) / "промпт_правок.md", prompt)
    text = require_text(adapters.call_model(cfg.writer, cfg.api, "", prompt, ws.logs, role="писатель (правки)", chapter=chapter),
                        "Писатель (правки)")
    new_k = new_k or черновик_k + 1
    extra = {"база": черновик_k}
    if applied_locally:
        extra["применено_кодом"] = applied_locally
    _save_draft(ws, chapter, new_k, text, cfg, mode="правки", extra=extra)
    return new_k
