"""Тома (FR-VL-1…FR-VL-3): volume_status — сводка тома, volume_close — закрытие тома (рукопись, статистика,
снапшот в канон, тег, переход к следующему), volume_open — переключение текущего тома.
Логика тома — в konveyer/volume.py; здесь только шаги команд."""

from __future__ import annotations

from .. import exporter, timing, volume as volume_mod
from ..config import set_volume
from ..errors import StepError
from ..mdparse import MarkupError
from .common import Confirm, _ctx, colors, confirm_or_reject, echo, secho


def volume_status(volume: int | None = None):
    """Сводка тома (FR-VL-3): главы по состояниям, слова принятых глав, метрики Э1 по актам, стоимость и время
    (по журналы/api.jsonl и историям глав — те же числа, что в `konveyer учёт`). Возвращает статистику тома."""
    ws, cfg, lib = _ctx()
    volume = volume or ws.volume
    if volume == ws.volume:
        try:
            exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
        except MarkupError as e:
            secho(f"⚠ выгрузки не пересобраны: {e}", fg=colors.YELLOW)
    stats = volume_mod.volume_stats(ws, lib, volume)
    secho(f"Том {volume}" + (" (текущий)" if volume == ws.volume else ""), bold=True)
    plan = str(stats.chapters_total) if stats.chapters_total is not None else "поглавник недоступен"
    echo(f"Главы в {ws.chapters_root(volume).relative_to(ws.root).as_posix()}/; глав в поглавнике: {plan}")
    if stats.chapters_total is None:
        secho(f"⚠ Поглавник тома {volume} не прочитан (нет документов тома или сломана разметка) — "
              f"сводка без плана глав; проверьте `konveyer том открыть {volume}`.", fg=colors.YELLOW)
    echo(f"Зафиксировано: {len(stats.fixed)}" + (f" ({', '.join(map(str, stats.fixed))})" if stats.fixed else ""))
    echo(f"В работе: {len(stats.in_work)}" + (" — " + "; ".join(f"гл. {n}: {st}" for n, st in stats.in_work.items()) if stats.in_work else ""))
    echo(f"Слов в принятых главах: {stats.words_total}")
    if stats.total_metrics:
        echo("Метрики Э1 (средние): " + "; ".join(f"{k}: {v:g}" for k, v in stats.total_metrics.items()))
    for a in stats.acts:
        m = stats.act_metrics.get(a.act)
        if m:
            echo(f"  акт {a.act} «{a.title}» (гл. {a.from_chapter}–{a.to_chapter}): " + "; ".join(f"{k}: {v:g}" for k, v in m.items()))
    echo(f"Вызовов моделей: {stats.calls}; оценка стоимости: ${stats.cost:.2f}")
    echo(f"Время автора: {timing.fmt_minutes(stats.author_s)}; машинное: {timing.fmt_minutes(stats.machine_s)}")
    if stats.missing_docs:
        secho("В библиотеке нет документов тома: " + "; ".join(stats.missing_docs), fg=colors.YELLOW)
    return stats


def _ask(confirm: Confirm | None, prompt: str) -> bool:
    """Вопрос автору; без терминала (EOF, Abort typer/click при перенаправленном stdin) — «нет», не сбой."""
    if confirm is None:
        return False
    try:
        return bool(confirm(prompt))
    except EOFError:
        return False
    except Exception as e:  # noqa: BLE001 — click.Abort при EOF; всё остальное — наверх
        if type(e).__name__ == "Abort":
            return False
        raise


def volume_close(
    volume: int | None = None, again: bool = False, yes: bool = False, next_volume: bool | None = None,
    confirm: Confirm | None = None,
):
    """Закрыть том (FR-VL-2): все главы «зафиксировано» → рукопись рукопись/ТомN.md (+ .docx при python-docx)
    и статистика → снапшот тома в библиотеку (коммит) → тег `том-N` → переход к тому N+1.
    Отказ от закрытия — `Rejected(abort=True)` («Aborted!»); переключение тома — только явным ответом
    (`next_volume`, флаги `--следующий/--без-переключения`) или вопросом через `confirm`; при `yes` без явного
    ответа вопрос не задаётся (скрипты без терминала), печатается подсказка. Возвращает результат закрытия."""
    ws, cfg, lib = _ctx()
    volume = volume or ws.volume
    pending = volume_mod.unfixed_chapters(ws, lib, volume) if volume == ws.volume else []
    if pending:
        raise StepError(f"том {volume} нельзя закрыть: не зафиксированы {', '.join(pending)}.")
    confirm_or_reject(
        yes, confirm,
        f"Закрыть том {volume}: собрать рукопись, внести снапшот тома в библиотеку и закоммитить, поставить тег том-{volume}? (Д-8)",
        abort=True,
    )
    try:
        res = volume_mod.close_volume(ws, cfg, lib, volume, again=again, author_confirmed=True)
    except (RuntimeError, OSError, LookupError) as e:  # OSError — рукопись/статистика: нет папки, нет прав
        raise StepError(str(e)) from e
    secho(f"Том {volume} закрыт.", fg=colors.GREEN)
    echo(f"  снапшот тома: {res.snapshot_doc.name} — {'; '.join(res.messages)}")
    echo(f"  тег: {res.tag or '—'}")
    echo(f"  рукопись: {res.manuscript_md.relative_to(ws.root).as_posix()}"
         + (f", {res.manuscript_docx.relative_to(ws.root).as_posix()}" if res.manuscript_docx else ""))
    if res.docx_hint:
        secho(f"  {res.docx_hint}", fg=colors.YELLOW)
    echo(f"  статистика: {res.stats_path.relative_to(ws.root).as_posix()}")
    nxt = volume + 1
    missing = volume_mod.open_volume(ws, lib, nxt)
    if missing:
        secho(
            f"Том {nxt} не открыт: в библиотеке нет его документов — заведите " + "; ".join(missing)
            + f", затем `konveyer том открыть {nxt}`.", fg=colors.YELLOW,
        )
        return res
    if next_volume is None and not yes:
        next_volume = _ask(confirm, f"Переключить рабочую область на том {nxt} (текущий том в проект.yaml/конфиг.yaml)?")
    if next_volume:
        set_volume(ws, nxt)
        exporter.run_export(lib, ws.exports, ws.logs, nxt, ws.root)
        _warn_missing(ws, lib, nxt)
        secho(f"Текущий том: {nxt} (главы — {ws.chapters_root(nxt).relative_to(ws.root).as_posix()}/, выгрузки пересобраны).",
              fg=colors.GREEN)
    else:
        echo(f"Текущий том остался {ws.volume}; переключить — `konveyer том открыть {nxt}`"
             + (" (или `--следующий` при закрытии)." if yes and next_volume is None else "."))
    return res


def _warn_missing(ws, lib, volume: int) -> None:
    warn = volume_mod.volume_warnings(ws, lib, volume)
    if warn:
        secho(f"Документов модулей для тома {volume} пока нет (модули работают вхолостую): " + "; ".join(warn),
              fg=colors.YELLOW)


def volume_open(volume: int) -> None:
    """Переключить текущий том рабочей области (проект.yaml: текущий_том; конфиг.yaml — совместимость) —
    с проверкой, что документы тома есть."""
    ws, cfg, lib = _ctx()
    try:
        missing = volume_mod.open_volume(ws, lib, volume)
    except ValueError as e:
        raise StepError(str(e)) from e
    if missing:
        raise StepError(f"в библиотеке нет документов тома {volume} — заведите: " + "; ".join(missing))
    if volume == ws.volume:
        echo(f"Том {volume} уже текущий.")
        return
    _warn_missing(ws, lib, volume)
    set_volume(ws, volume)
    try:
        exporter.run_export(lib, ws.exports, ws.logs, volume, ws.root)
    except MarkupError as e:
        raise StepError(f"том {volume} переключён, но выгрузки не собрались: {e}") from e
    secho(f"Текущий том: {volume}. Главы — {ws.chapters_root(volume).relative_to(ws.root).as_posix()}/; выгрузки пересобраны.",
          fg=colors.GREEN)
