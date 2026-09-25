"""Такт главы (§7.2, FR-TK-1): экспорт → сборка окна → генерация → Э1 → Э2 → пакет приёмки → правки →
дифф-контроль → принятие → пакет канона → фиксация; `run` — такт целиком с паузами на шагах автора.

Ядро без typer: аргументы — обычные значения, вывод — stdout, ошибки — исключения konveyer/errors.py.
Подсказки автору называют команды русскими именами (`cmd()`), как в справке.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import (
    adapters,
    backup as backup_mod,
    cancel,
    canonist,
    compiler,
    exporter,
    gitops,
    guard,
    regression as regression_mod,
    review as review_mod,
    verifier1,
    verifier2,
    writer,
)
from ..config import Config
from ..errors import StepError, StepExit
from ..fsm import ChapterState
from ..mdparse import MarkupError
from ..paths import Workspace
from .common import (
    Confirm,
    _ctx,
    _print_variants,
    _print_verdict,
    _sha256,
    cmd,
    colors,
    confirm_or_reject,
    current_chapter,
    echo,
    secho,
)

# журнал решений автора по такту (брак Э1 принят решением автора и т. п.) — вне git, как и api.jsonl
AUTHOR_DECISIONS_LOG = "решения_автора.jsonl"


def export() -> dict[str, str]:
    """Перегенерировать все выгрузки из MD-библиотеки (FR-EX-1…3). Возвращает хэши файлов выгрузок."""
    ws, cfg, lib = _ctx()
    try:
        hashes = exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    except MarkupError as e:
        raise StepError(f"документ канона не разобран (формат типа — `konveyer типы <тип>`) → {e}") from e
    secho(f"Выгрузки обновлены (том {ws.volume}): {len(hashes)} файлов в {ws.exports}/", fg=colors.GREEN)
    return hashes


def compile(chapter: int) -> Path:  # noqa: A001 — имя команды `konveyer собрать`
    """Собрать окно контекста главы N (FR-WN-1…7). Экспорт выполняется автоматически (риск R-8).
    Зафиксированная глава — отказ: её окно — артефакт принятого текста (FR-TK-4, П-7); в прочих состояниях
    после генерации прежнее окно сохраняется копией `окно_k.md`."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    if st.state == "зафиксировано":
        raise StepError(
            f"глава {chapter} зафиксирована — её окно не пересобирается (артефакт принятого текста). "
            f"Нужна новая редакция — сначала откат: `{cmd('rollback', chapter, '--to принято')}`."
        )
    window_path = ws.window_path(chapter)
    if st.state not in ("не-начато", "собрано") and window_path.exists():
        # П-7: окно, по которому шла генерация, не теряется
        keep = window_path.with_name(f"окно_{st.draft}.md")
        guard.write_text(keep, window_path.read_text(encoding="utf-8"))
    try:
        exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
        path, breakdown = compiler.compile_window(ws, lib, chapter, limits=compiler.WindowLimits.from_config(cfg))
    except MarkupError as e:
        raise StepError(str(e)) from e
    except FileNotFoundError as e:
        raise StepError(str(e)) from e
    size = sum(breakdown.values())
    if st.state == "не-начато":
        st.transition("собрано", "compile")
    elif st.state == "собрано":
        st.transition("собрано", "compile (пересборка)")
    else:
        secho(
            f"⚠ Глава в состоянии «{st.state}»: окно пересобрано (прежнее — окно_{st.draft}.md), но текущий черновик "
            f"генерировался по старому окну — при необходимости `{cmd('rollback', chapter, '--to собрано')}`.",
            fg=colors.YELLOW,
        )
    secho(f"Окно собрано: {path} (~{size} символов)", fg=colors.GREEN)
    if (ws.chapter_dir(chapter) / "window_size_флаг.md").exists() and size > cfg.window_soft_limit_chars:
        secho(
            f"⚠ Превышен мягкий лимит окна {cfg.window_soft_limit_chars} символов (FR-WN-6) — "
            f"раскладка в {ws.chapter_rel(chapter)}/window_size_флаг.md",
            fg=colors.YELLOW,
        )
    return path


def _manual_draft_hint(ws: Workspace, chapter: int, k: int, then: str = "") -> str:
    """Подсказка ручного режима Писателя: какой файл сохранить и какой командой продолжить (FR-WR-4)."""
    return (f"скопируйте {ws.chapter_rel(chapter)}/окно.md в чат модели, ответ сохраните как "
            f"{ws.chapter_rel(chapter)}/черновик_{k}.md и выполните `{cmd('write', chapter, '--manual')}`{then}.")


def write(chapter: int, manual: bool = False, variants: int = 1, choose: str | None = None) -> int | None:
    """Отправить окно Писателю, сохранить черновик_k.md (FR-WR-1). `variants` ≥ 2 — A/B (черновик_k.md, черновик_k.alt1.md …),
    `choose` — сделать вариант текущим черновик_k.md (состояние не меняется). Возвращает номер черновика."""
    ws, cfg, lib = _ctx()
    current_chapter(chapter)  # порог «стоимость главы» в хуке перед вызовом модели
    variants = int(variants)
    st = ChapterState(ws, chapter)
    if choose:
        st.require("сгенерировано")
        writer.choose_variant(ws, chapter, st.draft, choose)
        secho(f"Вариант «{choose}» → черновик_{st.draft}.md (прежний основной — alt0). Далее: `{cmd('verify1', chapter)}`.",
              fg=colors.GREEN)
        return st.draft
    st.require("собрано", "сгенерировано")
    k = st.draft + 1
    labels = writer.variant_labels(variants)
    window_path = ws.window_path(chapter)
    if not window_path.exists():
        raise StepError(f"нет файла {ws.chapter_rel(chapter)}/окно.md — соберите окно: `{cmd('compile', chapter)}`.")
    if manual:
        missing = [writer.variant_path(ws, chapter, k, lb).name for lb in labels if not writer.variant_path(ws, chapter, k, lb).exists()]
        if missing:
            raise StepError(
                f"нет файла {ws.chapter_rel(chapter)}/{missing[0]} — скопируйте окно в чат модели, "
                f"сохраните ответ этим файлом и повторите `{cmd('write', chapter, '--manual')}` (ручной режим)."
            )
    elif variants > 1:
        try:
            writer.write_variants(ws, cfg, chapter, k, variants)
        except adapters.ManualModeNeeded as e:
            raise adapters.ManualModeNeeded(
                e.reason,
                f"окно для всех вариантов одно: {ws.chapter_rel(chapter)}/окно.md — прогоните его {variants} раз(а), "
                f"сохраните ответы как {', '.join(writer.variant_path(ws, chapter, k, lb).name for lb in labels)} "
                f"и выполните `{cmd('write', chapter, '--manual', '--варианты', variants)}`.",
            ) from None
    else:
        try:
            writer.write_chapter(ws, cfg, chapter, k)
        except adapters.ManualModeNeeded as e:
            raise adapters.ManualModeNeeded(e.reason, _manual_draft_hint(ws, chapter, k)) from None
    st.set_draft(k)
    st.reset_retries()  # свежая генерация — бюджет авто-повторов Э1 заново
    st.transition("сгенерировано", "write" + (" (manual)" if manual else "") + (f" (варианты: {variants})" if variants > 1 else ""))
    secho(f"Черновик {'принят' if manual else 'получен'}: {ws.draft_path(chapter, k)}", fg=colors.GREEN)
    if variants > 1:
        summary = verifier1.variants_summary(ws, chapter, k, labels)
        _print_variants(summary)
        echo(f"Основной — черновик_{k}.md; выбрать другой: `{cmd('write', chapter, '--выбрать alt1')}`.")
    echo(f"Далее: `{cmd('verify1', chapter)}`.")
    return k


def _log_author_decision(ws: Workspace, entry: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "том": ws.volume, **entry}
    guard.append_text(ws.logs / AUTHOR_DECISIONS_LOG, json.dumps(entry, ensure_ascii=False) + "\n")


def verify1(chapter: int, accept_brak: bool = False, reason: str = ""):
    """Машинные проверки Э1 (FR-V1-5). БРАК → авто-повтор генерации Писателем (лимит `auto_retries_verify1`
    из конфига), затем остановка с вердиктом автору (`StepExit(1)`). Счётчик авто-повторов расходуется только
    состоявшимися генерациями: при ручном Писателе (нет ключа/SDK, провайдер «ручной») шаг сразу говорит,
    какой файл сохранить и какой командой продолжить. `accept_brak` с `reason` — решение автора принять текст
    вопреки браку (§1.3: спорное решает автор): запись в историю главы и журнал решений автора."""
    ws, cfg, lib = _ctx()
    current_chapter(chapter)  # порог «стоимость главы» в хуке перед вызовом модели
    st = ChapterState(ws, chapter)
    st.require("сгенерировано")
    if accept_brak and not reason.strip():
        raise StepError(f"решение принять брак Э1 требует причины: `{cmd('verify1', chapter, '--принять-брак --причина «…»')}`.")
    while True:
        verdict = verifier1.run_verify1(ws, chapter, st.draft)
        echo(f"Вердикт Э1 (глава {chapter}, черновик {st.draft}):")
        _print_verdict(verdict)
        if not verdict.has_brak:
            st.transition("верифицировано-1", "verify1")
            secho("Э1 пройден.", fg=colors.GREEN)
            echo(f"Далее: `{cmd('verify2', chapter)}`.")
            return verdict
        if accept_brak:
            brak_ids = [c.check_id for c in verdict.checks if c.status == "BRAK"]
            st.data["брак_принят"] = {"черновик": st.draft, "метрики": brak_ids, "причина": reason.strip()}
            st.transition("верифицировано-1", f"verify1 (брак принят автором: {reason.strip()})")
            _log_author_decision(ws, {"глава": chapter, "черновик": st.draft, "решение": "принять брак Э1",
                                      "метрики": brak_ids, "причина": reason.strip()})
            secho(f"БРАК Э1 по {', '.join(brak_ids)} принят решением автора: {reason.strip()} "
                  f"(журналы/{AUTHOR_DECISIONS_LOG}).", fg=colors.YELLOW)
            echo(f"Далее: `{cmd('verify2', chapter)}`.")
            return verdict
        retries = int(st.data.get("авто_повторов", 0) or 0)
        k = st.draft + 1
        if retries >= cfg.auto_retries_verify1:
            secho(
                f"БРАК метрик после {cfg.auto_retries_verify1} авто-повторов — стоп, вердикт автору "
                f"({ws.chapter_rel(chapter)}/вердикт.json).",
                fg=colors.RED,
            )
            echo(f"Решение за автором: новый текст — {_manual_draft_hint(ws, chapter, k)} "
                 f"Либо принять текст вопреки браку: `{cmd('verify1', chapter, '--принять-брак --причина «…»')}`.")
            raise StepExit(1)
        unavailable = adapters.unavailable_reason(cfg.writer, "писатель")
        if unavailable:
            # авто-повтор невозможен без модели: счётчик не расходуется, глава остаётся «сгенерировано»
            secho("БРАК метрик — авто-повтор генерации без модели невозможен.", fg=colors.YELLOW)
            raise adapters.ManualModeNeeded(
                f"{unavailable} Черновик {st.draft} забракован Э1 (вердикт: {ws.chapter_rel(chapter)}/вердикт.json).",
                _manual_draft_hint(ws, chapter, k, f", затем `{cmd('verify1', chapter)}`"),
            )
        secho(f"БРАК метрик — авто-повтор генерации №{retries + 1} из {cfg.auto_retries_verify1}…", fg=colors.YELLOW)
        cancel.check(f"авто-повтор Э1 №{retries + 1}")
        try:
            writer.write_chapter(ws, cfg, chapter, k)
        except adapters.ManualModeNeeded as e:
            # вызов не состоялся (сеть, биллинг, обрыв) — повтор не засчитан
            raise adapters.ManualModeNeeded(e.reason, _manual_draft_hint(ws, chapter, k, f", затем `{cmd('verify1', chapter)}`")) from None
        st.bump_retries()  # генерация состоялась: счётчик, история, вердикт_k.json бракованного черновика
        st.set_draft(k)


def _manual_flags_file(ws: Workspace, chapter: int, name: str, cfg: Config, hint_cmd: str) -> list:
    """Ручной ввод флагов Э2 файлом (FR-TK-6, FR-AD-3): ответ модели «как есть» — с прозой, ```-ограждением,
    хвостовым текстом — разбирается так же устойчиво, как в панели, и пересохраняется чистым JSON."""
    path = ws.chapter_dir(chapter) / name
    if not path.exists():
        raise StepError(
            f"нет файла {ws.chapter_rel(chapter)}/{name} — сохраните в него ответ модели "
            f"(промпт: {hint_cmd}), затем повторите."
        )
    try:
        return verifier2.parse_flags(path.read_text(encoding="utf-8"), cfg.e2_quote_words)
    except ValueError as e:
        raise StepError(f"{ws.chapter_rel(chapter)}/{name}: {e}") from e


def verify2(chapter: int, manual: bool = False, taste: bool = False, again: bool = False) -> list:
    """Смысловые проверки Э2 (FR-V2-*). `again` — второй прогон после правок (совещательно). Ручной ввод
    (`manual`) помечается в истории состояния (FR-TK-6). Возвращает флаги."""
    ws, cfg, lib = _ctx()
    current_chapter(chapter)  # порог «стоимость главы» в хуке перед вызовом модели
    st = ChapterState(ws, chapter)
    if again:
        return _verify2_again(ws, cfg, st, manual)
    if taste and manual and st.state == "верифицировано-2":
        # Э2 уже пройден, автор вносит файлом только вкус (промпт вкуса остался без ответа модели)
        _taste(ws, cfg, st, manual=True)
        return verifier2.load_flags(ws, chapter)
    st.require("верифицировано-1")
    if manual:
        flags = _manual_flags_file(ws, chapter, "флаги.json", cfg, "промпт_э2.md")
        verifier2.save_flags(ws, chapter, flags)
        echo(f"Принят ручной флаги.json: {len(flags)} флагов.")
    else:
        try:
            flags = verifier2.run_verify2(ws, cfg, chapter, st.draft)
        except adapters.ManualModeNeeded as e:
            raise adapters.ManualModeNeeded(
                e.reason,
                f"промпт сохранён: {ws.chapter_rel(chapter)}/промпт_э2.md — прогоните вручную, "
                f"сохраните ответ в {ws.chapter_rel(chapter)}/флаги.json и выполните `{cmd('verify2', chapter, '--manual')}`.",
            ) from None
        except ValueError as e:
            raise StepError(str(e)) from e
    st.transition("верифицировано-2", "verify2" + (" (manual)" if manual else ""))
    sam = sum(1 for f in flags if f.kind == "samovolka")
    secho(f"Э2 завершён: {len(flags)} флагов, из них самоволок: {sam}.", fg=colors.GREEN)
    if taste:
        _taste(ws, cfg, st, manual)
    echo(f"Далее: `{cmd('review', chapter)}`.")
    return flags


def _taste(ws: Workspace, cfg: Config, st: ChapterState, manual: bool) -> list:
    """Проход «вкус» (FR-V2-7): совещательно, в отдельный файл вкус.json; ручной ввод — файлом, как флаги."""
    chapter = st.chapter
    if manual:
        advice = [f.model_copy(update={"severity": "мелочь", "type": "вкус", "kind": "violation"})
                  for f in _manual_flags_file(ws, chapter, "вкус.json", cfg, "промпт_вкуса.md")]
        guard.write_text(ws.chapter_dir(chapter) / "вкус.json",
                         json.dumps([f.model_dump() for f in advice], ensure_ascii=False, indent=2) + "\n")
        st._record(st.state, st.state, "verify2 --вкус (manual)")
        st._save()
        echo(f"Принят ручной вкус.json: замечаний {len(advice)}.")
        return advice
    try:
        advice = verifier2.run_taste(ws, cfg, chapter, st.draft)
        echo(f"Вкус (совещательно): замечаний {len(advice)} → {ws.chapter_rel(chapter)}/вкус.json")
        return advice
    except adapters.ManualModeNeeded:
        echo(f"Промпт вкуса сохранён: {ws.chapter_rel(chapter)}/промпт_вкуса.md — ответ сохраните в вкус.json и выполните "
             f"`{cmd('verify2', chapter, '--вкус --manual')}` — шаг примет только вкус, Э2 не повторится.")
    except ValueError as e:
        secho(f"⚠ Вкус: {e}", fg=colors.YELLOW)
    return []


def _verify2_again(ws: Workspace, cfg: Config, st: ChapterState, manual: bool) -> list:
    """Повторный Э2 после правок (FR-V2-7): по текущему черновику, без смены состояния."""
    chapter = st.chapter
    st.require("правки", "дифф-контроль")
    if manual:
        path = ws.chapter_dir(chapter) / verifier2.AGAIN_FLAGS
        if not path.exists():
            raise StepError(
                f"нет файла {ws.chapter_rel(chapter)}/{verifier2.AGAIN_FLAGS} — сохраните в него ответ модели "
                f"(промпт: {verifier2.AGAIN_PROMPT}), затем повторите `{cmd('verify2', chapter, '--повторно --manual')}`."
            )
        черновик_k, flags = verifier2.load_flags_again(ws, chapter)
        if черновик_k is None:
            verifier2.save_flags_again(ws, chapter, flags, st.draft)
        st._record(st.state, st.state, "verify2 --повторно (manual)")
        st._save()
        echo(f"Принят ручной {verifier2.AGAIN_FLAGS}: {len(flags)} флагов.")
    else:
        try:
            flags = verifier2.run_verify2_again(ws, cfg, chapter, st.draft)
        except adapters.ManualModeNeeded as e:
            raise adapters.ManualModeNeeded(
                e.reason,
                f"промпт сохранён: {ws.chapter_rel(chapter)}/{verifier2.AGAIN_PROMPT} — прогоните вручную, "
                f"сохраните ответ в {ws.chapter_rel(chapter)}/{verifier2.AGAIN_FLAGS} и выполните "
                f"`{cmd('verify2', chapter, '--повторно --manual')}`.",
            ) from None
        except ValueError as e:
            raise StepError(str(e)) from e
    review_mod.append_second_pass(ws, chapter, st.draft, flags)
    sam = sum(1 for f in flags if f.kind == "samovolka")
    secho(
        f"Повторный Э2 (черновик {st.draft}, совещательно): {len(flags)} флагов, из них самоволок: {sam} → "
        f"{ws.chapter_rel(chapter)}/{verifier2.AGAIN_FLAGS}; состояние «{st.state}» не изменено.",
        fg=colors.GREEN,
    )
    for f in flags[:12]:
        echo(f"  - [{f.kind}/{f.severity}] {f.flag_id} · {f.type}: {f.rule}")
    return flags


def review(chapter: int) -> Path:
    """Пакет приёмки автора: приёмка.md + правки.md + решения.json (FR-RV-1). Возвращает путь приёмка.md."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("верифицировано-2")
    path = review_mod.build_review_pack(ws, chapter, st.draft)
    from .. import htmlreview

    html_path = htmlreview.build_review_html(ws, chapter, st.draft)
    st.data["база_приёмки"] = st.draft  # FR-ED-2: каждый цикл правок стартует от текста, принятого на приёмке
    st.transition("на-приёмке", "review")
    secho(f"Пакет приёмки: {path}", fg=colors.GREEN)
    secho(f"Чтение с флагами (браузер): {html_path}", fg=colors.GREEN)
    echo(
        f"Дальше: правки — в правки.md (`{cmd('edits', chapter)}` — предпросмотр); "
        f"решения по самоволкам — `{cmd('resolve', chapter)}`; затем `{cmd('apply-edits', chapter)}`."
    )
    return path


def apply_edits(chapter: int, manual: bool = False) -> int:
    """Внесение правок: дословные БЫЛО/СТАЛО — кодом (FR-ED-1), свободные указания — Писателем (FR-ED-2).
    Из «правки» (после грязного дифф-контроля, FR-TK-3) — новая итерация цикла. Возвращает номер нового черновика."""
    ws, cfg, lib = _ctx()
    current_chapter(chapter)  # порог «стоимость главы» в хуке перед вызовом модели
    st = ChapterState(ws, chapter)
    st.require("на-приёмке", "правки", "дифф-контроль")
    edits = review_mod.parse_edits_md(ws, chapter)
    # база правок — черновик приёмки (FR-ED-2): повторный цикл не наследует самоволия прошлой итерации
    base = int(st.data.get("база_приёмки", st.draft))
    new_k = st.draft + 1
    base_path = ws.draft_path(chapter, base)
    local = None
    if not manual and base_path.exists():
        local = writer.apply_edits_text(base_path.read_text(encoding="utf-8"), edits)
    over = st.data.get("итераций_правок", 0) >= cfg.edit_cycle_max_iterations
    if not manual and over and (local is None or local.needs_model):
        raise StepError(
            f"итераций правок уже {st.data['итераций_правок']} (лимит FR-ED-3) — внесите правки вручную: "
            f"сохраните исправленный текст как черновик_{st.draft + 1}.md, выполните "
            f"`{cmd('apply-edits', chapter, '--manual')}`, затем `{cmd('diff-check', chapter, '--авторская-правка')}`."
        )
    st.data["база_правок"] = base
    n_local = n_model = 0
    if manual:
        # завершение сорвавшейся автоматической итерации либо ручная правка автора —
        # бюджет итераций FR-ED-3 (для циклов Писателя) не расходуется
        if not ws.draft_path(chapter, new_k).exists():
            raise StepError(f"нет файла {ws.chapter_rel(chapter)}/черновик_{new_k}.md (ручной режим).")
    elif not edits:
        # правок нет — черновик приёмки переходит дальше без вызова Писателя
        shutil.copyfile(base_path, ws.draft_path(chapter, new_k))
    elif local is None:
        raise StepError(f"нет базового черновика {ws.chapter_rel(chapter)}/черновик_{base}.md (правки идут от черновика приёмки).")
    elif not local.needs_model:
        # FR-ED-1: все пары найдены дословно ровно один раз — модель не нужна, бюджет итераций не расходуется
        new_k, local = writer.apply_edits_locally(ws, cfg, chapter, base, edits, new_k=new_k)
        n_local = len(local.applied)
    else:
        n_local, n_model = len(local.applied), len(local.remaining)
        for e in local.remaining:
            echo(f"  Писателю: правка {e.seq} — {local.reasons.get(e.seq, '')}")
        try:
            new_k = writer.apply_edits(
                ws, cfg, chapter, base, local.remaining, new_k=new_k,
                base_text=local.text, applied_locally=[e.seq for e in local.applied],
            )
        except adapters.ManualModeNeeded as e:
            raise adapters.ManualModeNeeded(
                e.reason,
                f"Правок кодом: {n_local} (уже в тексте промпта), Писателю: {n_model}. "
                f"Промпт правок сохранён: {ws.chapter_rel(chapter)}/промпт_правок.md — прогоните вручную, "
                f"сохраните ответ как черновик_{st.draft + 1}.md и выполните `{cmd('apply-edits', chapter, '--manual')}`.",
            ) from None
        st.bump_edit_iterations()  # итерация Писателя состоялась
    st.set_draft(new_k)
    st.transition("правки", "apply-edits" + (" (manual)" if manual else "") + (" (код)" if n_local and not n_model else ""))
    how = f"применено кодом {n_local}, Писателю {n_model}" if not manual else "ручной режим"
    secho(f"Правки внесены ({len(edits)} шт.: {how}) → черновик_{new_k}.md. Далее: `{cmd('diff-check', chapter)}`.",
          fg=colors.GREEN)
    return new_k


def diff_check(chapter: int, author_fix: bool = False, fragments: list[str] | None = None):
    """Дифф-контроль до/после правок (FR-V1-6, FR-ED-3). Чистый отчёт — «дифф-контроль»; самовольные изменения
    или невнесённые правки возвращают главу в «правки» (FR-TK-3) — до лимита итераций. Возвращает отчёт."""
    ws, cfg, lib = _ctx()
    if not isinstance(fragments, list):
        fragments = []
    st = ChapterState(ws, chapter)
    st.require("правки", "дифф-контроль")  # повторный прогон/подтверждение разрешён
    edits = review_mod.load_edits(ws, chapter)
    base = int(st.data.get("база_правок", st.draft - 1))
    report = verifier1.diff_check(ws, chapter, base, st.draft, edits)
    if author_fix and report.unauthorized:
        # снятые самоволия фиксируются в дифф.json; с --фрагмент — только перечисленные
        waived, missing = verifier1.waive_unauthorized(ws, chapter, report, fragments)
        secho(f"Авторская правка: снято самоволий {len(waived)}.", fg=colors.YELLOW)
        if missing:
            secho(f"⚠ Не найдены среди самоволий: {missing}", fg=colors.YELLOW)
    elif fragments and not author_fix:
        secho("⚠ --фрагмент действует только вместе с --авторская-правка.", fg=colors.YELLOW)
    st.transition("дифф-контроль", "diff-check")
    echo(f"Внесено правок: {report.applied_share:.0%}; не внесено: {report.not_applied or '—'}")
    if report.unverifiable:
        secho(
            f"Свободные указания {report.unverifiable}: механически не проверяются — "
            "оцените их результат глазами (приёмку не блокируют).",
            fg=colors.YELLOW,
        )
    if report.unauthorized:
        secho(f"Самовольные изменения ({len(report.unauthorized)}):", fg=colors.RED)
        for u in report.unauthorized[:10]:
            echo(f"  > {u[:200]}")
        if len(report.unauthorized) > 10:
            echo(f"  … и ещё {len(report.unauthorized) - 10} (полный список — в дифф.json главы)")
        if report.unverifiable:
            echo(
                "Часть изменений может быть следствием свободных указаний — если это так, "
                f"подтвердите `{cmd('diff-check', chapter, '--авторская-правка')}`."
            )
    elif report.not_applied:
        secho("Часть правок не внесена.", fg=colors.YELLOW)
    if not report.clean:
        # FR-TK-3: автоматический возврат в «правки»; отчёт дифф.json остаётся для панели и подсказки
        st.transition("правки", "diff-check (не чист → правки)")
        left = max(0, cfg.edit_cycle_max_iterations - int(st.data.get("итераций_правок", 0) or 0))
        echo(f"Глава возвращена в «правки»: поправьте правки.md и выполните `{cmd('apply-edits', chapter)}` "
             f"(итераций Писателя осталось {left}; либо `{cmd('diff-check', chapter, '--авторская-правка')}`, если правили сами).")
    else:
        secho(f"Дифф-контроль чист. Далее: `{cmd('accept', chapter)}`.", fg=colors.GREEN)
    return report


def accept(chapter: int, yes: bool = False, confirm: Confirm | None = None) -> None:
    """Приёмка главы автором (FR-RV-4): только из «дифф-контроль» с чистым и актуальным отчётом дифф.json
    (по текущему черновику), с явным подтверждением."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("дифф-контроль")
    report_path = ws.chapter_dir(chapter) / "дифф.json"
    if not report_path.exists():
        raise StepError(f"нет отчёта дифф-контроля ({ws.chapter_rel(chapter)}/дифф.json) — выполните `{cmd('diff-check', chapter)}`.")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise StepError(f"отчёт дифф-контроля повреждён ({e}) — выполните `{cmd('diff-check', chapter)}` заново.") from e
    if data.get("draft_after") != st.draft:
        raise StepError(
            f"отчёт дифф-контроля относится к черновику {data.get('draft_after')}, текущий — {st.draft}: "
            f"выполните `{cmd('diff-check', chapter)}` заново."
        )
    if data.get("not_applied") or data.get("unauthorized"):
        raise StepError("дифф-контроль не чист — приёмка недоступна (FR-RV-4).")
    unresolved = review_mod.unresolved_samovolki(ws, chapter)
    if unresolved:
        raise StepError(f"не решены самоволки: {', '.join(unresolved)} (решения.json).")
    green = regression_mod.is_green(ws)
    if green is False:
        secho("⚠ Регрессия КРАСНАЯ (FR-RG-3) — смена конфигурации запрещена, приёмка под вашу ответственность.", fg=colors.YELLOW)
    confirm_or_reject(yes, confirm, f"Принять главу {chapter}? (y)")
    st.transition("принято", "accept")
    secho(f"Глава {chapter} принята. Далее: `{cmd('canonize', chapter)}`.", fg=colors.GREEN)


def canonize(
    chapter: int, apply: bool = False, yes: bool = False, redo: bool = False, confirm: Confirm | None = None,
) -> str | Path | None:
    """Канонист: пакет записей в канон (FR-CN-1); применение — только после подписи (FR-CN-2).
    Без `apply` возвращает путь пакета, с `apply` — SHA коммита приёмки."""
    ws, cfg, lib = _ctx()
    current_chapter(chapter)  # порог «стоимость главы» в хуке перед вызовом модели
    if not isinstance(redo, bool):
        redo = False
    st = ChapterState(ws, chapter)
    st.require("принято")
    batch_path = ws.chapter_dir(chapter) / "пакет_канона.md"
    if not apply:
        if batch_path.exists():
            current = _sha256(batch_path)
            edited = st.data.get("пакет_хэш") != current
            if edited and not redo:
                # отредактированный автором пакет не перезаписывается (и вызов модели не тратится)
                raise StepError(
                    f"пакет {ws.chapter_rel(chapter)}/пакет_канона.md уже правился автором — "
                    f"примените его (`{cmd('canonize', chapter, '--apply')}`) или пересоберите явно "
                    f"(`{cmd('canonize', chapter, '--заново')}`; прежний пакет сохранится копией)."
                )
            if redo:
                # П-7: правки автора в пакете не теряются — копия под хэшем содержимого
                keep = batch_path.with_name(f"пакет_канона.{current[:8]}.md")
                guard.write_text(keep, batch_path.read_text(encoding="utf-8"))
                secho(f"Прежний пакет сохранён: {ws.chapter_rel(chapter)}/{keep.name}", fg=colors.YELLOW)
        try:
            path = canonist.build_batch(ws, cfg, chapter, st.draft)
        except RuntimeError as e:
            raise StepError(str(e)) from e
        st.data["пакет_хэш"] = _sha256(path)
        st._save()
        secho(f"Пакет на подпись: {path}", fg=colors.GREEN)
        echo(f"Проверьте/поправьте пакет и примените: `{cmd('canonize', chapter, '--apply')}`.")
        return path
    if not batch_path.exists():
        raise StepError(f"нет пакета пакет_канона.md — сначала `{cmd('canonize', chapter)}`.")
    if not gitops.is_repo(lib):
        # без git нет коммита приёмки и отката — отказ ДО записи и без перевода FSM
        raise StepError(
            "библиотека не под git — применение пакета невозможно (FR-CN-2: откат только git-revert'ом). "
            "Инициализируйте репозиторий в библиотеке (git init; git add -A; git commit), затем повторите."
        )
    # Идемпотентность (FR-SC-3): если приёмка уже закоммичена, а состояние не успело смениться
    # (сбой между коммитом и записью состояние.yaml), повтор НЕ применяет пакет второй раз —
    # он восстанавливает состояние по действующему коммиту приёмки этой главы ЭТОГО тома.
    existing = gitops.find_chapter_commit(lib, chapter, ws.volume)
    if existing:
        st.data["коммит_приёмки"] = existing
        st.transition("зафиксировано", "canonize --apply (восстановление по коммиту)")
        _after_canonize(ws, cfg, lib, chapter, existing)
        secho(
            f"Пакет главы {chapter} уже применён коммитом {existing[:10]} — повторное применение "
            f"продублировало бы записи. Состояние восстановлено: «зафиксировано».",
            fg=colors.YELLOW,
        )
        return existing
    confirm_or_reject(yes, confirm, f"Применить пакет главы {chapter} к библиотеке и закоммитить? (y)")
    commit = canonist.apply_batch(ws, cfg, lib, chapter, st.draft)
    st.data["коммит_приёмки"] = commit  # откат зафиксированной главы — строго по этому SHA
    st.transition("зафиксировано", "canonize --apply")
    secho(f"Глава {chapter} зафиксирована. Коммит: {commit}", fg=colors.GREEN)
    _after_canonize(ws, cfg, lib, chapter, commit)
    return commit


def _after_canonize(ws: Workspace, cfg: Config, lib: Path, chapter: int, commit: str) -> None:
    """После приёмки: тег версии канона (`глава-N`, том ≥ 2 — `томV-глава-N`; повторная приёмка после
    отката — `…-2`) и архив рабочей области, если в конфиг.yaml задан backup_dir. Ни то, ни другое не может
    сорвать приёмку: она уже закоммичена и состояние сменено; сбой — предупреждение."""
    name = gitops.tag_chapter(lib, chapter, commit, ws.volume)
    if name:
        echo(f"Тег канона: {name}")
    else:
        secho("⚠ Тег главы не поставлен (git tag не удался) — приёмка при этом закоммичена.", fg=colors.YELLOW)
    if cfg.backup_dir:
        try:
            path, removed = backup_mod.make_archive(ws, cfg)
            echo(f"Архив рабочей области: {path}" + (f" (удалено старых: {len(removed)})" if removed else ""))
        except OSError as e:
            secho(f"⚠ Архив рабочей области не создан: {e}", fg=colors.YELLOW)
    if cfg.push_after_canonize:
        # второе место хранения актуально сразу после приёмки (FR-BK-1); сбой сети — предупреждение
        remotes = gitops.remotes(lib)
        if not remotes:
            secho(f"⚠ push_после_приёмки включён, но у библиотеки нет remotes — `{cmd('backup', '--добавить-remote …')}`.", fg=colors.YELLOW)
        for remote in remotes:
            try:
                gitops.push(lib, remote)
                echo(f"Отправлено в {remote}.")
            except (RuntimeError, FileNotFoundError) as e:
                secho(f"⚠ Не отправлено в {remote}: {e} — повторите `{cmd('backup', '--push')}`.", fg=colors.YELLOW)


def run(chapter: int) -> None:
    """Такт целиком с паузами на шагах автора: приёмка, принятие, подпись пакета."""
    ws, cfg, lib = _ctx()
    while True:
        st = ChapterState(ws, chapter)
        state = st.state
        cancel.check(f"такт, состояние «{state}»")  # между шагами: глава остаётся на завершённом шаге
        if state == "не-начато":
            compile(chapter)
        elif state == "собрано":
            write(chapter, manual=False)
        elif state == "сгенерировано":
            verify1(chapter)
        elif state == "верифицировано-1":
            verify2(chapter, manual=False)
        elif state == "верифицировано-2":
            review(chapter)
            echo(f"⏸ Пауза такта: заполните правки.md и решения.json, затем снова `{cmd('run', chapter)}`.")
            return
        elif state == "на-приёмке":
            apply_edits(chapter, manual=False)
        elif state == "правки":
            diff_check(chapter, author_fix=False)
            if ChapterState(ws, chapter).state == "правки":
                echo(f"⏸ Пауза такта: дифф-контроль не чист — решите и продолжите `{cmd('run', chapter)}`.")
                return
        elif state == "дифф-контроль":
            echo(f"⏸ Пауза такта: приёмка автора — `{cmd('accept', chapter)}`, затем `{cmd('run', chapter)}`.")
            return
        elif state == "принято":
            if not (ws.chapter_dir(chapter) / "пакет_канона.md").exists():
                canonize(chapter, apply=False, yes=False)
            echo(f"⏸ Пауза такта: подпишите пакет — `{cmd('canonize', chapter, '--apply')}`.")
            return
        elif state == "зафиксировано":
            secho(f"Глава {chapter} зафиксирована — такт завершён.", fg=colors.GREEN)
            return
