"""Шаги онбординга: импорт материалов, предложение «файл → тип», решения автора, применение, отчёт (раздел 5)."""

from __future__ import annotations

from pathlib import Path

from ..onboarding import apply as apply_mod, importer, propose, report
from .common import _ctx, colors, echo, secho
from .. import steps as _steps  # noqa: F401 — StepError через пакет шагов


def import_materials(source: str) -> importer.ImportReport:
    """`konveyer импорт <путь>`: файл, папка или .zip → сырьё/ (FR-ON-4…FR-ON-6)."""
    ws, cfg, lib = _ctx()
    rep = importer.import_path(ws, Path(source))
    secho(f"Импорт: новых {len(rep.added)}, новых версий {len(rep.changed)}, уже были {len(rep.skipped)}, "
          f"без извлечения {len(rep.rejected)} → {rep.index_path}", fg=colors.GREEN)
    bad: list[importer.RawEntry] = []
    for e in rep.added + rep.changed:
        q = e.качество.get("оценка", "—")
        line = f"  {e.файл} [{e.формат}] — {q}" + (f"; {e.причина}" if e.причина else "")
        secho(line, fg=colors.YELLOW if not e.извлечено_в or q == "плохо" else None)
        if e.извлечено_в and q == "плохо":
            bad.append(e)
    if rep.rejected:
        echo("Файлы без извлечения остаются в сырьё/оригиналы; конвертируйте их в .md/.docx и повторите импорт.")
    if bad:  # FR-ON-3: для «плохо» — совет конвертировать вручную
        secho("Извлечение «плохо» (таблицы или строки потеряны): " + ", ".join(e.файл for e in bad)
              + " — конвертируйте в .docx/.md вручную и повторите импорт.", fg=colors.YELLOW)
    echo("Дальше: `konveyer онбординг` — предложение «файл → тип».")
    return rep


def propose_types(use_model: bool | None = None, decisions: list[str] | None = None,
                  answers: list[str] | None = None) -> tuple[list[propose.Proposal], str]:
    """`konveyer онбординг`: предложение по сырью; `--решение файл=решение` — решения автора (FR-ON-12);
    `--ответ файл=путь` — ответ Архивариуса, полученный вручную (FR-RL-3). Без `--модель/--без-модели` модельный
    слой включается по `onboarding_model_layer` конфига (FR-ON-8)."""
    ws, cfg, lib = _ctx()
    if use_model is None:
        use_model = bool(getattr(cfg, "onboarding_model_layer", False))
    for a in answers or []:
        if "=" not in a:
            raise ValueError(f"ответ задаётся как файл=путь_к_ответу, получено: «{a}»")
        f, path = a.split("=", 1)
        answer_path = Path(path.strip()).expanduser()
        if not answer_path.is_file():
            raise FileNotFoundError(f"файл ответа не найден: {path.strip()}")
        item = propose.manual_answer(ws, f.strip(), answer_path.read_text(encoding="utf-8", errors="replace"))
        echo(f"Ответ Архивариуса по «{f.strip()}» принят: тип «{item.get('тип', '—')}» ({float(item.get('уверенность', 0) or 0):.0%})")
        use_model = True
    proposals, note = propose.build(ws, cfg=cfg, use_model=use_model, library=lib)
    pj, pm = propose.save(ws, proposals, note)
    for d in decisions or []:
        if "=" not in d:
            raise ValueError(f"решение задаётся как файл=решение, получено: «{d}»")
        f, dec = d.split("=", 1)
        propose.set_decision(ws, f.strip(), dec.strip())
    proposals = propose.load(ws)
    secho(f"Предложение: {len(proposals)} файлов → {pm.relative_to(ws.root)} ({note})", fg=colors.GREEN)
    for p in proposals:
        recs = (p.предпросмотр or {}).get("records", "—") if p.предпросмотр else "—"
        echo(f"  {p.файл} → {p.тип} ({p.уверенность:.0%}; записей: {recs})" + (f" · решение: {p.решение}" if p.решение else ""))
        for q in p.вопросы[:3]:
            secho(f"    ? {q}", fg=colors.YELLOW)
    rp = report.save(ws, lib)
    echo(f"Отчёт готовности: {rp.relative_to(ws.root)}. Решения — в предложение.json (поле «решение») или "
         f"`konveyer онбординг --решение <файл>=<тип:имя|принять|сырьё|отклонить|разбить|склеить:<файл>|колонка:<поле>=<заголовок>>`; "
         f"затем `--применить`.")
    return proposals, note


def apply_onboarding(yes: bool, confirm=None, commit: bool = True) -> apply_mod.ApplyResult:
    """`konveyer онбординг --применить`: одна транзакция — документы, манифест, индекс, выгрузки, коммит (FR-ON-17)."""
    from .common import confirm_or_reject

    ws, cfg, lib = _ctx()
    proposals = propose.load(ws)
    todo = [p for p in proposals if (p.решение and p.решение not in ("сырьё", "отклонить")) or (not p.решение and p.тип != "сырьё")]
    confirm_or_reject(yes, confirm, f"Внести в библиотеку {len(todo)} документов и закоммитить?")
    res = apply_mod.apply(ws, cfg, lib, author_confirmed=True, commit=commit)
    secho(f"Онбординг применён: документов {len(res.written)}, обновлено {len(res.updated)}, сырьём {len(res.raw_kept)}, "
          f"отклонено {len(res.rejected)}; {res.message}", fg=colors.GREEN)
    for d in res.written + res.updated:
        echo(f"  + {d}")
    for q in res.questions:
        secho(f"  ? {q}", fg=colors.YELLOW)
    for c in res.conflicts:
        secho(f"  ⚠ конфликт повторного импорта: {c} — канон не тронут; решение `--решение <файл>=источник|канон`", fg=colors.YELLOW)
    if res.lint_errors:
        secho(f"  линтер нашёл ошибок: {res.lint_errors} — см. журналы/линтер.md", fg=colors.YELLOW)
    rp = report.save(ws, lib)
    echo(f"Отчёт готовности: {rp.relative_to(ws.root)}; затем `konveyer доктор`.")
    return res
