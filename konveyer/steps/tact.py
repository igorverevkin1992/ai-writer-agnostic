"""Такт главы (§5.4, FR-O2): export → compile → write → verify1 → verify2 → review → apply-edits →
diff-check → accept → canonize; `run` — такт целиком с паузами на шагах автора (FR-O1).

Ядро без typer: аргументы — обычные значения, вывод — stdout, ошибки — исключения konveyer/errors.py.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import (
    adapters,
    backup as backup_mod,
    cancel,
    canonist,
    compiler,
    exporter,
    gitops,
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
from .common import Confirm, _ctx, _print_variants, _print_verdict, _sha256, colors, confirm_or_reject, echo, secho


def export() -> dict[str, str]:
    """Перегенерировать все выгрузки из MD-библиотеки (FR-X1…FR-X3). Возвращает хэши файлов выгрузок."""
    ws, cfg, lib = _ctx()
    try:
        hashes = exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    except MarkupError as e:
        raise StepError(f"структура MD расходится с соглашениями Д-1 → {e}") from e
    secho(f"Выгрузки обновлены (том {ws.volume}): {len(hashes)} файлов в {ws.exports}/", fg=colors.GREEN)
    return hashes


def compile(chapter: int) -> Path:  # noqa: A001 — имя команды `konveyer compile`
    """Собрать окно контекста главы N (FR-C1…FR-C6). Экспорт выполняется автоматически (риск R-5)."""
    ws, cfg, lib = _ctx()
    try:
        exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
        path, breakdown = compiler.compile_window(ws, lib, chapter, cfg.window_soft_limit_chars)
    except MarkupError as e:
        raise StepError(str(e)) from e
    except FileNotFoundError as e:
        raise StepError(str(e)) from e
    size = sum(breakdown.values())
    st = ChapterState(ws, chapter)
    if st.state == "не-начато":
        st.transition("собрано", "compile")
    elif st.state == "собрано":
        st.transition("собрано", "compile (пересборка)")
    else:
        secho(
            f"⚠ Глава в состоянии «{st.state}»: окно пересобрано, но текущий черновик "
            f"генерировался по старому окну — при необходимости `konveyer rollback {chapter} --to собрано`.",
            fg=colors.YELLOW,
        )
    secho(f"Окно собрано: {path} (~{size} символов)", fg=colors.GREEN)
    if breakdown.get("драматургия", 0) and "в канон ещё не внесён" in path.read_text(encoding="utf-8"):
        secho(
            f"⚠ Каркас драматургии главы {chapter} в канон не внесён (Р-020): `konveyer circles` → "
            "`konveyer circles --в-канон`, затем пересоберите окно.",
            fg=colors.YELLOW,
        )
    if (ws.chapter_dir(chapter) / "window_size_флаг.md").exists() and size > cfg.window_soft_limit_chars:
        secho(
            f"⚠ Превышен мягкий лимит окна {cfg.window_soft_limit_chars} символов (Д-12) — "
            f"раскладка в {ws.chapter_rel(chapter)}/window_size_флаг.md",
            fg=colors.YELLOW,
        )
    return path


def write(chapter: int, manual: bool = False, variants: int = 1, choose: str | None = None) -> int | None:
    """Отправить окно Писателю, сохранить черновик_k.md (FR-W1). `variants` ≥ 2 — A/B (черновик_k.md, черновик_k.alt1.md …),
    `choose` — сделать вариант текущим черновик_k.md (состояние не меняется). Возвращает номер черновика."""
    ws, cfg, lib = _ctx()
    variants = int(variants)
    st = ChapterState(ws, chapter)
    if choose:
        st.require("сгенерировано")
        writer.choose_variant(ws, chapter, st.draft, choose)
        secho(f"Вариант «{choose}» → черновик_{st.draft}.md (прежний основной — alt0). Далее: `konveyer verify1 {chapter}`.",
              fg=colors.GREEN)
        return st.draft
    st.require("собрано", "сгенерировано")
    k = st.draft + 1
    labels = writer.variant_labels(variants)
    from .. import accounting, pins

    pin_warning = pins.warn_if_changed(ws, cfg, ("писатель",))
    if pin_warning:
        secho(f"⚠ {pin_warning}", fg=colors.YELLOW)
    window_path = ws.window_path(chapter)
    prompt_chars = len(window_path.read_text(encoding="utf-8")) if window_path.exists() else None
    est = accounting.estimate_before(cfg.writer, prompt_chars) if prompt_chars and not manual else None
    if est is not None:
        echo(f"Оценка стоимости вызова Писателя: ≈ {est * max(1, variants):.3f} $ (по ценам конфига).")
    for w in accounting.warnings(ws, cfg, chapter, prompt_chars, cfg.writer if not manual else None):
        secho(f"⚠ {w}", fg=colors.YELLOW)
    if manual:
        missing = [writer.variant_path(ws, chapter, k, lb).name for lb in labels if not writer.variant_path(ws, chapter, k, lb).exists()]
        if missing:
            raise StepError(
                f"нет файла {ws.chapter_rel(chapter)}/{missing[0]} — скопируйте окно в чат модели, "
                f"сохраните ответ этим файлом и повторите (ручной режим)."
            )
    elif variants > 1:
        try:
            writer.write_variants(ws, cfg, chapter, k, variants)
        except adapters.ManualModeNeeded:
            echo(
                f"Окно для всех вариантов одно: {ws.chapter_rel(chapter)}/окно.md — прогоните его {variants} раз(а), "
                f"сохраните ответы как {', '.join(writer.variant_path(ws, chapter, k, lb).name for lb in labels)} "
                f"и выполните `konveyer write {chapter} --manual --варианты {variants}`."
            )
            raise
    else:
        writer.write_chapter(ws, cfg, chapter, k)
    st.set_draft(k)
    st.reset_retries()  # свежая генерация — бюджет авто-повторов §5.4 заново
    st.transition("сгенерировано", "write" + (" (manual)" if manual else "") + (f" (варианты: {variants})" if variants > 1 else ""))
    secho(f"Черновик {'принят' if manual else 'получен'}: {ws.draft_path(chapter, k)}", fg=colors.GREEN)
    if variants > 1:
        summary = verifier1.variants_summary(ws, chapter, k, labels)
        _print_variants(summary)
        echo(f"Основной — черновик_{k}.md; выбрать другой: `konveyer write {chapter} --выбрать alt1`.")
    return k


def verify1(chapter: int):
    """Формальные проверки Э1 (FR-V1.*). Брак метрик → авто-повтор генерации (≤2, §5.4).
    Возвращает вердикт; брак после всех авто-повторов — `StepExit(1)` (вердикт автору)."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("сгенерировано")
    while True:
        verdict = verifier1.run_verify1(ws, chapter, st.draft)
        echo(f"Вердикт Э1 (глава {chapter}, черновик {st.draft}):")
        _print_verdict(verdict)
        if not verdict.has_brak:
            st.transition("верифицировано-1", "verify1")
            secho("Э1 пройден.", fg=colors.GREEN)
            return verdict
        retries = st.bump_retries()
        if retries > cfg.auto_retries_verify1:
            secho(
                f"БРАК метрик после {cfg.auto_retries_verify1} авто-повторов — стоп, вердикт автору "
                f"({ws.chapter_rel(chapter)}/вердикт.json).",
                fg=colors.RED,
            )
            raise StepExit(1)
        secho(f"БРАК метрик — авто-повтор генерации №{retries} (§5.4)…", fg=colors.YELLOW)
        cancel.check(f"авто-повтор Э1 №{retries}")
        k = st.draft + 1
        writer.write_chapter(ws, cfg, chapter, k)
        st.set_draft(k)


def verify2(chapter: int, manual: bool = False, taste: bool = False, again: bool = False) -> list:
    """Смысловые проверки Э2 (FR-V2.*). `again` — второй прогон после правок (advisory). Возвращает флаги."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    if again:
        return _verify2_again(ws, cfg, st, manual)
    st.require("верифицировано-1")
    if manual:
        if not (ws.chapter_dir(chapter) / "флаги.json").exists():
            raise StepError(
                f"нет файла {ws.chapter_rel(chapter)}/флаги.json — сохраните в него JSON-ответ модели "
                f"(промпт: промпт_э2.md), затем повторите `konveyer verify2 {chapter} --manual`."
            )
        flags = verifier2.load_flags(ws, chapter)
        echo(f"Принят ручной флаги.json: {len(flags)} флагов.")
    else:
        try:
            flags = verifier2.run_verify2(ws, cfg, chapter, st.draft)
        except adapters.ManualModeNeeded:
            echo(
                f"Промпт сохранён: {ws.chapter_rel(chapter)}/промпт_э2.md — прогоните вручную, "
                f"сохраните JSON в {ws.chapter_rel(chapter)}/флаги.json и выполните `konveyer verify2 {chapter} --manual`."
            )
            raise
        except ValueError as e:
            raise StepError(str(e)) from e
    st.transition("верифицировано-2", "verify2")
    sam = sum(1 for f in flags if f.kind == "samovolka")
    secho(f"Э2 завершён: {len(flags)} флагов, из них самоволок: {sam}.", fg=colors.GREEN)
    if taste:
        try:
            advice = verifier2.run_taste(ws, cfg, chapter, st.draft)
            echo(f"Вкус (совещательно, 02 §6.1): замечаний {len(advice)} → {ws.chapter_rel(chapter)}/вкус.json")
        except adapters.ManualModeNeeded:
            echo(f"Промпт вкуса сохранён: {ws.chapter_rel(chapter)}/промпт_вкуса.md (ответ — в вкус.json).")
        except ValueError as e:
            secho(f"⚠ Вкус: {e}", fg=colors.YELLOW)
    return flags


def _verify2_again(ws: Workspace, cfg: Config, st: ChapterState, manual: bool) -> list:
    """Повторный Э2 после правок (аудит 2, п. 24а): по текущему черновику, без смены состояния."""
    chapter = st.chapter
    st.require("правки", "дифф-контроль")
    if manual:
        if not (ws.chapter_dir(chapter) / verifier2.AGAIN_FLAGS).exists():
            raise StepError(
                f"нет файла {ws.chapter_rel(chapter)}/{verifier2.AGAIN_FLAGS} — сохраните в него JSON-ответ модели "
                f"(промпт: {verifier2.AGAIN_PROMPT}), затем повторите `konveyer verify2 {chapter} --повторно --manual`."
            )
        черновик_k, flags = verifier2.load_flags_again(ws, chapter)
        if черновик_k is None:
            verifier2.save_flags_again(ws, chapter, flags, st.draft)
        echo(f"Принят ручной {verifier2.AGAIN_FLAGS}: {len(flags)} флагов.")
    else:
        try:
            flags = verifier2.run_verify2_again(ws, cfg, chapter, st.draft)
        except adapters.ManualModeNeeded:
            echo(
                f"Промпт сохранён: {ws.chapter_rel(chapter)}/{verifier2.AGAIN_PROMPT} — прогоните вручную, "
                f"сохраните JSON в {ws.chapter_rel(chapter)}/{verifier2.AGAIN_FLAGS} и выполните "
                f"`konveyer verify2 {chapter} --повторно --manual`."
            )
            raise
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
    """Пакет приёмки автора: приёмка.md + правки.md + решения.json (FR-E1). Возвращает путь приёмка.md."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("верифицировано-2")
    path = review_mod.build_review_pack(ws, chapter, st.draft)
    from .. import htmlreview

    html_path = htmlreview.build_review_html(ws, chapter, st.draft)
    st.data["база_приёмки"] = st.draft  # FR-E3: каждый цикл правок стартует от текста, принятого на приёмке
    st.transition("на-приёмке", "review")
    secho(f"Пакет приёмки: {path}", fg=colors.GREEN)
    secho(f"Чтение с флагами (браузер): {html_path}", fg=colors.GREEN)
    echo(
        f"Дальше: правки — в правки.md (`konveyer edits {chapter}` — предпросмотр); "
        f"решения по самоволкам — `konveyer resolve {chapter}`; затем `konveyer apply-edits {chapter}`."
    )
    return path


def apply_edits(chapter: int, manual: bool = False) -> int:
    """Внесение правок: дословные БЫЛО/СТАЛО — кодом (Р-023), свободные указания — Писателем (FR-W2, FR-E3).
    Возвращает номер нового черновика."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("на-приёмке", "дифф-контроль")
    edits = review_mod.parse_edits_md(ws, chapter)
    # база правок — черновик приёмки (FR-E3): повторный цикл не наследует самоволия прошлой итерации
    base = int(st.data.get("база_приёмки", st.draft))
    new_k = st.draft + 1
    base_path = ws.draft_path(chapter, base)
    local = None
    if not manual and base_path.exists():
        local = writer.apply_edits_text(base_path.read_text(encoding="utf-8"), edits)
    over = st.data.get("итераций_правок", 0) >= cfg.edit_cycle_max_iterations
    if not manual and over and (local is None or local.needs_model):
        raise StepError(
            f"итераций правок уже {st.data['итераций_правок']} (лимит FR-E3) — внесите правки вручную: "
            f"сохраните исправленный текст как черновик_{st.draft + 1}.md, выполните "
            f"`konveyer apply-edits {chapter} --manual`, затем `konveyer diff-check {chapter} --авторская-правка`."
        )
    st.data["база_правок"] = base
    n_local = n_model = 0
    if manual:
        # завершение сорвавшейся автоматической итерации либо ручная правка автора —
        # бюджет итераций FR-E3 (для циклов Писателя) не расходуется
        if not ws.draft_path(chapter, new_k).exists():
            raise StepError(f"нет файла {ws.draft_path(chapter, new_k)} (ручной режим).")
    elif not edits:
        # правок нет — черновик приёмки переходит дальше без вызова Писателя
        shutil.copyfile(base_path, ws.draft_path(chapter, new_k))
    elif local is None:
        raise StepError(f"нет базового черновика {base_path} (FR-E3: правки идут от черновика приёмки).")
    elif not local.needs_model:
        # Р-023: все пары найдены дословно ровно один раз — модель не нужна, бюджет итераций не расходуется
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
        except adapters.ManualModeNeeded:
            echo(
                f"Правок кодом: {n_local} (уже в тексте промпта), Писателю: {n_model}. "
                f"Промпт правок сохранён: {ws.chapter_rel(chapter)}/промпт_правок.md — прогоните вручную, "
                f"сохраните ответ как черновик_{st.draft + 1}.md и выполните `konveyer apply-edits {chapter} --manual`."
            )
            raise
        st.bump_edit_iterations()  # итерация Писателя состоялась
    st.set_draft(new_k)
    st.transition("правки", "apply-edits" + (" (manual)" if manual else "") + (" (код)" if n_local and not n_model else ""))
    how = f"применено кодом {n_local}, Писателю {n_model}" if not manual else "ручной режим"
    secho(f"Правки внесены ({len(edits)} шт.: {how}) → черновик_{new_k}.md. Далее: `konveyer diff-check {chapter}`.",
          fg=colors.GREEN)
    return new_k


def diff_check(chapter: int, author_fix: bool = False, fragments: list[str] | None = None):
    """Дифф-контроль до/после правок (FR-V1.10, FR-E3). Возвращает отчёт дифф-контроля."""
    ws, cfg, lib = _ctx()
    if not isinstance(fragments, list):
        fragments = []
    st = ChapterState(ws, chapter)
    st.require("правки", "дифф-контроль")  # повторный прогон/подтверждение разрешён
    edits = review_mod.load_edits(ws, chapter)
    base = int(st.data.get("база_правок", st.draft - 1))
    report = verifier1.diff_check(ws, chapter, base, st.draft, edits)
    if author_fix and report.unauthorized:
        # 2.5: снятые самоволия фиксируются в дифф.json; с --фрагмент — только перечисленные
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
                f"подтвердите `konveyer diff-check {chapter} --авторская-правка`."
            )
        echo(f"Цикл повторяется: поправьте правки.md и выполните `konveyer apply-edits {chapter}` (≤{cfg.edit_cycle_max_iterations} итераций).")
    elif report.not_applied:
        secho("Часть правок не внесена — повторите цикл.", fg=colors.YELLOW)
    else:
        secho(f"Дифф-контроль чист. Далее: `konveyer accept {chapter}`.", fg=colors.GREEN)
    return report


def accept(chapter: int, yes: bool = False, confirm: Confirm | None = None) -> None:
    """Приёмка главы автором (FR-E4): только из «дифф-контроль: чисто», с явным подтверждением."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    st.require("дифф-контроль")
    report_path = ws.chapter_dir(chapter) / "дифф.json"
    data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    if data.get("not_applied") or data.get("unauthorized"):
        raise StepError("дифф-контроль не чист — приёмка недоступна (FR-E4).")
    unresolved = review_mod.unresolved_samovolki(ws, chapter)
    if unresolved:
        raise StepError(f"не решены самоволки: {', '.join(unresolved)} (решения.json).")
    green = regression_mod.is_green(ws)
    if green is False:
        secho("⚠ Регрессия КРАСНАЯ (FR-R3) — смена конфигурации запрещена, приёмка под вашу ответственность.", fg=colors.YELLOW)
    confirm_or_reject(yes, confirm, f"Принять главу {chapter}? (y)")
    st.transition("принято", "accept")
    secho(f"Глава {chapter} принята. Далее: `konveyer canonize {chapter}`.", fg=colors.GREEN)


def canonize(
    chapter: int, apply: bool = False, yes: bool = False, redo: bool = False, confirm: Confirm | None = None,
) -> str | Path | None:
    """Канонист: пакет записей в канон (FR-K1); применение — только после подписи (FR-K2).
    Без `apply` возвращает путь пакета, с `apply` — SHA коммита приёмки."""
    ws, cfg, lib = _ctx()
    if not isinstance(redo, bool):
        redo = False
    st = ChapterState(ws, chapter)
    st.require("принято")
    batch_path = ws.chapter_dir(chapter) / "пакет_канона.md"
    if not apply:
        if batch_path.exists() and not redo:
            # 2.11: отредактированный автором пакет не перезаписывается (и вызов LLM не тратится)
            current = _sha256(batch_path)
            if st.data.get("пакет_хэш") != current:
                raise StepError(
                    f"пакет {ws.chapter_rel(chapter)}/пакет_канона.md уже правился автором — "
                    f"примените его (`konveyer canonize {chapter} --apply`) или пересоберите явно "
                    f"(`konveyer canonize {chapter} --заново`, правки пропадут)."
                )
        try:
            path = canonist.build_batch(ws, cfg, chapter, st.draft)
        except RuntimeError as e:
            raise StepError(str(e)) from e
        st.data["пакет_хэш"] = _sha256(path)
        st._save()
        secho(f"Пакет на подпись: {path}", fg=colors.GREEN)
        echo(f"Проверьте/поправьте пакет и примените: `konveyer canonize {chapter} --apply`.")
        return path
    if not batch_path.exists():
        raise StepError(f"нет пакета пакет_канона.md — сначала `konveyer canonize {chapter}`.")
    if not gitops.is_repo(lib):
        # 2.6: без git нет коммита приёмки и отката — отказ ДО записи и без перевода FSM
        raise StepError(
            "библиотека не под git — применение пакета невозможно (FR-K2: откат только git-revert'ом). "
            "Инициализируйте репозиторий в библиотеке (git init; git add -A; git commit), затем повторите."
        )
    # Идемпотентность (4.1): если приёмка уже закоммичена, а состояние не успело смениться
    # (сбой между коммитом и записью состояние.yaml), повтор НЕ применяет пакет второй раз —
    # он восстанавливает состояние по действующему коммиту «[глава N]».
    existing = gitops.find_chapter_commit(lib, chapter)
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
    confirm_or_reject(yes, confirm, f"Применить пакет главы {chapter} к Библиотека/ и закоммитить? (Д-8) (y)")
    commit = canonist.apply_batch(ws, cfg, lib, chapter, st.draft)
    st.data["коммит_приёмки"] = commit  # откат зафиксированной главы — строго по этому SHA
    st.transition("зафиксировано", "canonize --apply")
    secho(f"Глава {chapter} зафиксирована. Коммит: {commit}", fg=colors.GREEN)
    _after_canonize(ws, cfg, lib, chapter, commit)
    return commit


def _after_canonize(ws: Workspace, cfg: Config, lib: Path, chapter: int, commit: str) -> None:
    """После приёмки (аудит 2, п. 28–29): тег версии канона `глава-N` (повторная приёмка после отката —
    `глава-N-2`) и архив рабочей области, если в конфиг.yaml задан backup_dir. Ни то, ни другое не может
    сорвать приёмку: она уже закоммичена и состояние сменено; сбой — предупреждение."""
    name = gitops.tag_chapter(lib, chapter, commit)
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


def run(chapter: int) -> None:
    """Такт целиком с паузами на шагах автора (FR-O1): review, accept, canonize."""
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
            echo("⏸ Пауза такта: заполните правки.md и решения.json, затем снова `konveyer run N`.")
            return
        elif state == "на-приёмке":
            apply_edits(chapter, manual=False)
        elif state == "правки":
            diff_check(chapter, author_fix=False)
            st = ChapterState(ws, chapter)
            data = json.loads((ws.chapter_dir(chapter) / "дифф.json").read_text(encoding="utf-8"))
            if data.get("not_applied") or data.get("unauthorized"):
                echo("⏸ Пауза такта: дифф-контроль не чист — решите и продолжите `konveyer run N`.")
                return
        elif state == "дифф-контроль":
            echo(f"⏸ Пауза такта: приёмка автора — `konveyer accept {chapter}`, затем `konveyer run {chapter}`.")
            return
        elif state == "принято":
            if not (ws.chapter_dir(chapter) / "пакет_канона.md").exists():
                canonize(chapter, apply=False, yes=False)
            echo(f"⏸ Пауза такта: подпишите пакет — `konveyer canonize {chapter} --apply`.")
            return
        elif state == "зафиксировано":
            secho(f"Глава {chapter} зафиксирована — такт завершён.", fg=colors.GREEN)
            return
