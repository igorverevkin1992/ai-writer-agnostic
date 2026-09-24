"""Качество и регрессия: check (Э1 по произвольному файлу), circles (круги истории),
regress (золотые тесты, FR-RG-2), add_golden (FR-RG-1)."""

from __future__ import annotations

from pathlib import Path

from .. import exporter, guard, regression as regression_mod, verifier1
from ..errors import StepError, StepExit
from ..schemas import GoldenTest
from .common import Confirm, _ctx, _print_verdict, colors, confirm_or_reject, echo, secho


def check(
    file: Path, chapter: int | None = None, focal: str = "", year: int | None = None, volume_words: int | None = None,
) -> str:
    """Прогнать проверки Э1 по произвольному файлу — вне такта и FSM (ручной режим, NFR-3).
    Возвращает итог: PASS / FLAG / BRAK."""
    ws, cfg, lib = _ctx()
    from ..schemas import Brief

    own = None
    part_range = None
    if chapter is not None:
        brief = exporter.load_brief(ws.exports, chapter)
        window_path = ws.window_path(chapter)
        window = window_path.read_text(encoding="utf-8") if window_path.exists() else ""
        # принятая глава уже лежит в корпусе — не сравнивать текст с самим собой (аудит 3.4)
        own = exporter.find_corpus_file(ws.corpus, chapter, brief.volume)
        part_range = verifier1.part_range_for(ws.exports, chapter)
    else:
        brief = Brief(chapter=0, focal=focal, year=year, volume_words=volume_words)
        window = ""
    checks = verifier1.analyze(
        file.read_text(encoding="utf-8"),
        window,
        brief,
        exporter.load_norms(ws.exports),
        exporter.load_stoplists(ws.exports),
        corpus_dir=ws.corpus,
        own_stem=own.stem if own else None,
        extra_abbr=ws.root / "сокращения.txt",
        part_range=part_range,
    )
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
    yes: bool = False, confirm: Confirm | None = None,
) -> dict | None:
    """Круги истории (8 шагов) — каркас драматургии: книга → четыре акта → главы; черновики в драматургия/.
    `to_canon` — внести черновики в документ 2.1 библиотеки и закоммитить (Д-8). Возвращает результат прогона."""
    from .. import circles as circles_mod

    ws, cfg, lib = _ctx()
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    if to_canon:
        n = len(circles_mod.drafts(ws))
        if not n:
            raise StepError("черновиков кругов нет — сначала `konveyer circles`.")
        confirm_or_reject(
            yes, confirm,
            f"Внести {n} круг(ов) в {circles_mod.canon_doc_name(ws.volume)} библиотеки и закоммитить? (Д-8) (y)",
        )
        try:
            path, commit = circles_mod.commit_to_canon(ws, cfg, lib)
        except RuntimeError as e:
            raise StepError(str(e)) from e
        secho(f"Круги внесены в канон: {path}. Коммит: {commit}", fg=colors.GREEN)
        echo("Окна глав теперь содержат секцию «Драматургия»; пересоберите начатые главы (`konveyer compile N`).")
        return None
    result = circles_mod.run(ws, cfg, scope, chapter, only_missing=not redo, library=lib)
    for path in result["готово"]:
        secho(f"  ✓ {path}", fg=colors.GREEN)
    if result["ручной_режим"]:
        secho(f"⚠ {result['ручной_режим']}", fg=colors.YELLOW)
        echo(f"Промпты для ручного прогона ({len(result['промпты'])}): драматургия/промпты/ — "
             "ответ модели вставьте в панели («Круги истории») или сохраните JSON рядом.")
        raise StepExit(2)
    if not result["готово"]:
        echo("Все круги уже есть — `--заново` для пересчёта.")
    return result


def regress(llm: bool = False) -> dict:
    """Прогон регрессионного корпуса золотых тестов (FR-RG-2). Красная регрессия — `StepExit(1)`."""
    ws, cfg, lib = _ctx()
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
    echelon: str = "Э1", ignore: list[str] | None = None,
) -> Path:
    """Добавить золотой тест из пойманной автором ошибки (FR-RG-1). Возвращает путь теста."""
    ws, cfg, lib = _ctx()
    test = GoldenTest(
        test_id=test_id,
        fragment=fragment_file.read_text(encoding="utf-8"),
        context_slice={"focal": focal, "year": year},
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
            m = metrics_mod.REGISTRY.get(nid)
            echo(f"  {nid}: {metrics_mod.corridor(n)} — {m.description if m else '⚠ неизвестная метрика'}")
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
