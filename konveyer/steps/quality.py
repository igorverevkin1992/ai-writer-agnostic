"""Качество и регрессия: check (Э1 по произвольному файлу), circles (каркасы драматургии, FR-DR-1…FR-DR-4),
regress (золотые тесты, FR-RG-2), add_golden (FR-RG-1), norms (нормы и калибровка, FR-V1-7)."""

from __future__ import annotations

from pathlib import Path

from .. import exporter, guard, regression as regression_mod, verifier1
from ..errors import StepError, StepExit
from ..mdparse import MarkupError
from ..schemas import GoldenTest
from .common import Confirm, _ctx, _print_verdict, colors, confirm_or_reject, echo, secho


def check(
    file: Path, chapter: int | None = None, focal: str = "", year: int | None = None, volume_words: int | None = None,
) -> str:
    """Прогнать проверки Э1 по произвольному файлу — вне такта и FSM (ручной режим, FR-RL-3).
    Возвращает итог: PASS / FLAG / BRAK."""
    ws, cfg, lib = _ctx()
    from ..schemas import Brief

    if not Path(file).exists():
        raise StepError(f"нет файла {Path(file).name} — укажите существующий файл с текстом для проверки.")
    if chapter is not None:
        # тот же контекст, что у такта: окно главы, корпус без самой главы, язык и словарь проекта
        brief = exporter.load_brief(ws.exports, chapter)
        checks = verifier1.analyze_text(ws, chapter, file.read_text(encoding="utf-8"))
    else:
        brief = Brief(chapter=0, focal=focal, year=year, volume_words=volume_words)
        checks = verifier1.analyze_text(ws, 0, file.read_text(encoding="utf-8"), brief=brief, window_raw="")
    from ..schemas import Verdict

    _print_verdict(Verdict(chapter=brief.chapter, draft=0, checks=checks))
    worst = "BRAK" if any(c.status == "BRAK" for c in checks) else (
        "FLAG" if any(c.status == "FLAG" for c in checks) else "PASS"
    )
    color = {"PASS": colors.GREEN, "FLAG": colors.YELLOW, "BRAK": colors.RED}[worst]
    secho(f"Итог: {worst}", fg=color)
    return worst


def circles(
    scope: str = "всё", chapter: int | None = None, redo: bool = False, to_canon: bool = False,
    yes: bool = False, confirm: Confirm | None = None, accept: str | None = None, answer_file: Path | None = None,
) -> dict | None:
    """Каркасы драматургии по методике проекта (FR-DR-1…FR-DR-6): том → акты → главы; шаги — по методике уровня;
    черновики аналитика в драматургия/. `to_canon` — внести черновики в документ каркасов тома (тип «каркасы»)
    и закоммитить по подтверждению автора (FR-DR-4). `accept` + `answer_file` — принять ответ модели из файла как
    каркас `книга` / `акт_N` / `глава_NN` (ручной режим, как в панели — FR-PN-7). Возвращает результат прогона."""
    import re

    from .. import circles as circles_mod

    ws, cfg, lib = _ctx()
    if accept is not None:
        m = re.fullmatch(r"(книга|акт|глава)_?(\d+)?", accept)
        if not m or (m.group(1) == "книга") == bool(m.group(2)):
            raise StepError("--принять: имя промпта «книга», «акт_N» или «глава_NN» (как файл в драматургия/промпты/).")
        if answer_file is None or not answer_file.is_file():
            raise StepError("--принять требует файл с ответом модели (JSON): `konveyer каркас --принять акт_1 ответ.json`.")
        try:
            path = circles_mod.accept_manual(ws, m.group(1), int(m.group(2)) if m.group(2) else None,
                                             answer_file.read_text(encoding="utf-8"))
        except ValueError as e:
            raise StepError(f"ответ модели не разобран: {e}") from e
        secho(f"Каркас принят: {path.relative_to(ws.root).as_posix()}", fg=colors.GREEN)
        return {"готово": [path], "ручной_режим": "", "промпты": []}
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    if to_canon:
        try:
            preview = circles_mod.canon_preview(ws, lib)
        except RuntimeError as e:
            raise StepError(str(e)) from e
        # FR-DR-4: подтверждение — с предпросмотром и диффом, а не только с числом каркасов
        for stem, status in sorted(preview["status"].items()):
            echo(f"  {stem}: {status}")
        if preview["diff"]:
            echo(preview["diff"].rstrip("\n"))
        elif not preview["exists"]:
            echo(preview["text"].rstrip("\n"))
        else:
            echo("(документ каркасов не изменится)")
        confirm_or_reject(
            yes, confirm,
            f"Внести {preview['n']} каркас(ов) в {circles_mod.canon_doc_name(ws.volume, ws.root)} библиотеки "
            "и закоммитить (см. дифф выше)? (y)",
        )
        try:
            path, commit = circles_mod.commit_to_canon(ws, cfg, lib)
        except RuntimeError as e:
            raise StepError(str(e)) from e
        secho(f"Каркасы внесены в канон: {path}. Коммит: {commit}", fg=colors.GREEN)
        echo("Окна глав теперь содержат секцию «Драматургия»; пересоберите начатые главы (`konveyer собрать N`).")
        return None
    result = circles_mod.run(ws, cfg, scope, chapter, only_missing=not redo, library=lib)
    for path in result["готово"]:
        secho(f"  ✓ {path}", fg=colors.GREEN)
    for line in result.get("сбои", []):
        secho(f"  ✗ ответ модели не принят: {line}", fg=colors.RED)
    if result["ручной_режим"]:
        secho(f"⚠ {result['ручной_режим']}", fg=colors.YELLOW)
        echo(f"Промпты для ручного прогона ({len(result['промпты'])}): драматургия/промпты/ — "
             "ответ модели вставьте в панели («Круги истории») или сохраните JSON рядом.")
        raise StepExit(2)
    if not result["готово"]:
        echo("Все круги уже есть — `--заново` для пересчёта.")
    return result


def circles_preview() -> dict:
    """Предпросмотр внесения каркасов в канон: дифф и статусы черновиков без записи (FR-DR-4)."""
    from .. import circles as circles_mod

    ws, cfg, lib = _ctx()
    try:
        return circles_mod.canon_preview(ws, lib)
    except RuntimeError as e:
        raise StepError(str(e)) from e


def regress(llm: bool = False) -> dict:
    """Прогон регрессионного корпуса золотых тестов (FR-RG-2). Красная регрессия — `StepExit(1)`."""
    ws, cfg, lib = _ctx()
    try:  # выгрузки пересобираются, как перед сборкой окна: регрессия на свежем проекте не падает без norms.json
        exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    except MarkupError as e:
        secho(f"⚠ выгрузки не пересобраны: {e}", fg=colors.YELLOW)
    report = regression_mod.run_regression(ws, llm=llm, cfg=cfg)
    if not report["всего"]:
        secho(
            "⚠ Корпус золотых тестов ПУСТ (регрессия/золотые/) — регрессия ничего не проверила и зелёной "
            "считаться не может (FR-RG-3). Пополните корпус: `konveyer золотой` (FR-RG-1).",
            fg=colors.YELLOW,
        )
    elif not report.get("выполнено"):
        secho(
            "⚠ Ни один тест не выполнен (все Э2 пропущены: нужен --llm и ключ API) — регрессия не зелёная.",
            fg=colors.YELLOW,
        )
    for r in report["результаты"]:
        if r.get("skipped"):
            echo(f"  ~ {r['test_id']}: пропущен ({r['skipped']})")
        elif r.get("пропущено"):
            secho(f"  ✗ {r['test_id']}: пропущено {r['пропущено']}", fg=colors.RED)
        else:
            extra = f", лишние: {r['лишние']}" if r.get("лишние") else ""
            secho(f"  ✓ {r['test_id']}: поймано {r['поймано']}{extra}", fg=colors.GREEN)
    if report["зелёная"]:
        secho("Регрессия ЗЕЛЁНАЯ.", fg=colors.GREEN)
    else:
        why = report.get("причина") or "пропущены ожидаемые флаги"
        secho(
            f"Регрессия КРАСНАЯ: {why}{' ' + str(report['провалено']) if report['провалено'] else ''} "
            "(FR-RG-3: смена конфигурации заблокирована).",
            fg=colors.RED,
        )
        raise StepExit(1)
    return report


def add_golden(
    test_id: str, fragment_file: Path, expect: list[str] | None = None, focal: str = "", year: int | None = None,
    echelon: str = "Э1", chapter: int | None = None, window_file: Path | None = None, use_corpus: bool = False,
    volume_words: int | None = None, ignore: list[str] | None = None,
) -> Path:
    """Добавить золотой тест из пойманной автором ошибки одной командой (FR-RG-1). Срез контекста: `chapter` берёт
    бриф главы (фокал, год, том, объём, «НЕ знает») и её окно из папки главы; `window_file` — своё окно (утечка окна);
    `use_corpus` — прогон против корпуса (межглавные повторы); `volume_words` — объём брифа; `ignore` — проверки,
    срабатывание которых на этом фрагменте не считается «лишним». Возвращает путь теста."""
    ws, cfg, lib = _ctx()
    if not Path(fragment_file).exists():
        raise StepError(f"нет файла фрагмента {Path(fragment_file).name} — укажите существующий файл.")
    test = GoldenTest(
        test_id=test_id,
        fragment=fragment_file.read_text(encoding="utf-8"),
        context_slice=regression_mod.context_slice(ws, focal=focal, year=year, chapter=chapter, window_file=window_file,
                                                   use_corpus=use_corpus, volume_words=volume_words),
        expected_flags=list(expect or []),
        ignore_flags=list(ignore or []),
        echelon=echelon,  # type: ignore[arg-type]
    )
    path = regression_mod.add_test(ws, test)
    secho(f"Золотой тест добавлен: {path}", fg=colors.GREEN)
    return path


def norms(calibrate_files: list[Path] | None = None, approve: bool = False, yes: bool = False,
          confirm: Confirm | None = None, from_corpus: bool = False) -> dict | None:
    """`konveyer нормы`: показать нормы стиля; `--калибровать [файлы]` (без файлов — принятые главы) — предложить
    коридоры (FR-V1-7); `--утвердить` — записать таблицу норм и решение в журнал (по подтверждению)."""
    from .. import calibrate, lang as lang_mod, metrics as metrics_mod

    ws, cfg, lib = _ctx()
    current = exporter.load_norms(ws.exports)
    if calibrate_files is None and not from_corpus:
        secho("Нормы стиля (из выгрузок):", bold=True)
        for nid, n in current.items():
            echo(f"  {nid}: {metrics_mod.corridor(n)} — {metrics_mod.describe(nid, n) or '⚠ неизвестная метрика'}")
        echo("Калибровка: `konveyer нормы --калибровать <файлы…>` или `--калибровать` без файлов (по принятым главам).")
        return {"нормы": {k: v.model_dump() for k, v in current.items()}}
    samples = calibrate.samples_from_files(calibrate_files) if calibrate_files else calibrate.samples_from_corpus(ws, lib)
    if not samples:
        raise StepError("образцов нет: укажите файлы прозы или примите хотя бы одну главу.")
    pr = calibrate.propose(samples, current, lang_mod.for_project(ws.root), exporter.load_stoplists(ws.exports))
    out = ws.logs / "калибровка.md"
    _ensure = out.parent.mkdir(parents=True, exist_ok=True)  # noqa: F841
    guard.write_text(out, pr.report)
    echo(pr.report)
    secho(f"Отчёт: {out.relative_to(ws.root)}", fg=colors.GREEN)
    if not approve:
        echo("Утвердить и записать в документ стиля + журнал решений: добавьте `--утвердить`.")
        return {"коридоры": {k: v.model_dump() for k, v in pr.corridors.items()}}
    confirm_or_reject(yes, confirm, f"Записать {len(pr.corridors)} норм в документ стиля и решение в журнал?")
    res = calibrate.apply(ws, cfg, lib, pr, author_confirmed=True)
    secho(f"Нормы записаны: {res.message}", fg=colors.GREEN)
    return {"коридоры": {k: v.model_dump() for k, v in pr.corridors.items()}, "commit": res.commit}


def metrics_doc() -> str:
    """`konveyer метрики`: реестр метрик Э1 (FR-V1-1)."""
    from .. import metrics as metrics_mod

    text = metrics_mod.documentation()
    echo(text)
    return text
