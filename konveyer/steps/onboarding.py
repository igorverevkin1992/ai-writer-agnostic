"""Шаги онбординга: импорт материалов, предложение «файл → тип», решения автора, применение, отчёт (раздел 5)."""

from __future__ import annotations

import re
from pathlib import Path

from .. import guard
from ..onboarding import apply as apply_mod, importer, propose, report
from .common import _ctx, colors, echo, secho
from .. import steps as _steps


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


DECISION_FORMAT = ("файл=принять|тип:<имя>|сырьё|отклонить|разбить|склеить:<файл>|колонка:<поле>=<заголовок>|"
                   "источник|канон")


def apply_decisions(decisions: list[str] | None, ws=None) -> list[propose.Proposal]:
    """Решения автора из командной строки (`--решение файл=решение`, FR-ON-12) — в предложение.json.
    Один разбор для `онбординг` и `онбординг --применить`: неверный формат или неизвестный файл —
    `ValueError` с подсказкой, а не трейсбек Python."""
    if ws is None:
        ws = _ctx()[0]
    out: list[propose.Proposal] = []
    for d in decisions or []:
        if "=" not in d:
            raise ValueError(f"решение задаётся как {DECISION_FORMAT}, получено: «{d}»")
        f, dec = d.split("=", 1)
        if not f.strip() or not dec.strip():
            raise ValueError(f"решение задаётся как {DECISION_FORMAT}, получено: «{d}»")
        try:
            out.append(propose.set_decision(ws, f.strip(), dec.strip()))
        except KeyError as e:
            raise ValueError(e.args[0] if e.args else str(e)) from e
    return out


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
    apply_decisions(decisions, ws)
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


# ------------------------------------------------------------------ импорт готовой прозы (сценарий В)

_TEXT_SUFFIXES = (".md", ".txt", ".markdown")
_ANY_NUMBER_RE = re.compile(r"(\d+)")


def _prose_text(path: Path) -> str:
    """Текст главы из файла: .md/.txt — как есть; остальные форматы — через извлечение онбординга (FR-ON-3)."""
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8-sig")
    from ..onboarding import extract as extract_mod

    try:
        ex = extract_mod.extract(path)
    except extract_mod.ExtractError as e:
        raise _steps.StepError(f"«{path.name}»: {e}") from e
    if ex.markdown is None:
        raise _steps.StepError(f"«{path.name}»: извлечения нет ({ex.reason or 'формат не поддержан'}) — "
                               "сохраните главу как .md/.txt/.docx и повторите.")
    return ex.markdown


def _chapter_from_name(stem: str, rx: re.Pattern) -> int | None:
    """Номер главы из имени файла: по регэкспу типа «проза», иначе последнее число в имени."""
    m = rx.search(stem)
    if m:
        return int(m.group(m.lastindex or 1))
    nums = _ANY_NUMBER_RE.findall(stem)
    return int(nums[-1]) if nums else None


_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"»)]*\s+")


def _quote_around(text: str, start: int, width: int = 120) -> str:
    """Предложение, в котором встретилось имя (для заготовки континуити)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    line = text[line_start: line_end if line_end != -1 else len(text)]
    pos = start - line_start
    begin = 0
    for m in _SENTENCE_END_RE.finditer(line):
        if m.end() <= pos:
            begin = m.end()
        else:
            break
    m_end = _SENTENCE_END_RE.search(line, pos)
    sentence = line[begin: m_end.end() if m_end else len(line)].strip()
    return (sentence[:width] + "…") if len(sentence) > width else sentence


def _at_sentence_start(text: str, start: int) -> bool:
    before = text[:start].rstrip()
    return not before or before.endswith(("\n", ".", "!", "?", "…", "»", '"'))


def import_prose(files: list[str | Path], *, volume: int | None = None, start_chapter: int | None = None,
                 yes: bool = False, confirm=None, commit: bool = True, calibrate: bool = True) -> dict:
    """`konveyer импорт-прозы <файлы…>` — сценарий В «Серия, частично написанная» (§4.2 ТЗ).

    Готовые главы дословно кладутся в папку прозы библиотеки (документы типа «проза», имя по каталогу типов)
    одной сессией записи в канон (`canonchange.canon_change`): экспорт пересобирает корпус, линтер проверяет
    прозу против канона. Затем: черновик словаря имён и заготовка континуити по именам из прозы
    (`онбординг/проза_имена.md`, `онбординг/проза_континуити.md`), спорные факты списком
    (`онбординг/проза_спорное.md`: находки линтера по импортированным главам и имена, которых нет в каноне),
    предложение коридоров норм по импортированной прозе (`konveyer нормы --калибровать`, без утверждения).
    Ничего не сочиняется (FR-ON-23): решения по именам и фактам помечены «⚠ решение автора».
    Существующая глава не перезаписывается — пропуск с предупреждением (как повторный импорт, FR-ON-21).
    Возвращает сводку: внесённые и пропущенные главы, имена, спорные факты, коммит."""
    from .. import canonchange, catalog, exporter, lint as lint_mod, manifest as manifest_mod, names as names_mod
    from ..onboarding.propose import onboarding_dir
    from .common import confirm_or_reject
    from . import quality as quality_steps

    ws, cfg, lib = _ctx()
    volume = int(volume or ws.volume)
    if not files:
        raise _steps.StepError("укажите файлы готовых глав: `konveyer импорт-прозы глава1.md глава2.docx …`.")
    spec = catalog.load_types(ws.root).get("проза")
    raw = spec.raw if spec else {}
    name_pattern = raw.get("имя_главы") or "Том{том}_Глава{глава:02d}.md"
    rx = re.compile(raw.get("регэксп_главы") or r"Том0*(\d+)_Глава0*(\d+)")
    folder = exporter.prose_folder(lib, ws.root)

    plan: list[tuple[Path, int, Path, str]] = []  # (источник, глава, документ, текст)
    skipped: list[str] = []
    seen: dict[int, str] = {}
    next_ch = start_chapter
    for f in files:
        path = Path(f)
        if not path.is_file():
            raise _steps.StepError(f"файла «{path}» нет — укажите существующие файлы глав.")
        if start_chapter is not None:
            ch = next_ch
            next_ch += 1
        else:
            ch = _chapter_from_name(path.stem, rx)
            if ch is None:
                raise _steps.StepError(f"«{path.name}»: номер главы не распознан по имени файла — "
                                       "переименуйте (например «Глава 07.md») или задайте `--с-главы N` (главы получат номера по порядку).")
        if ch in seen:
            raise _steps.StepError(f"«{path.name}» и «{seen[ch]}» претендуют на главу {ch} — задайте номера по порядку (`--с-главы N`).")
        seen[ch] = path.name
        target = folder / name_pattern.format(том=volume, глава=ch)
        if target.exists():
            skipped.append(f"{path.name} → {target.name} уже есть в библиотеке — не перезаписан (глава {ch})")
            continue
        plan.append((path, ch, target, _prose_text(path)))
    for s in skipped:
        secho(f"  ⚠ {s}", fg=colors.YELLOW)
    if not plan:
        raise _steps.StepError("вносить нечего: все главы уже есть в библиотеке.")

    rel_folder = folder.relative_to(lib).as_posix() + "/"
    confirm_or_reject(yes, confirm, f"Внести {len(plan)} глав(ы) прозы тома {volume} в {rel_folder} библиотеки и закоммитить?")

    man = manifest_mod.load(ws.root)
    if man is not None and not man.entries_of_type("проза"):
        man.библиотека.append(manifest_mod.LibraryEntry(файл=rel_folder, тип="проза", множественность="папка"))
        manifest_mod.save(ws.root, man)

    def writer() -> None:
        for _src, _ch, target, text in plan:
            guard.write_text(target, text if text.endswith("\n") else text + "\n")

    chapters = ", ".join(str(ch) for _s, ch, _t, _x in plan)
    try:
        res = canonchange.canon_change(
            ws, cfg, lib, writer, f"импорт прозы: том {volume}, главы {chapters}",
            commit=commit, author_confirmed=True, action="импорт прозы", require_docs=False,
        )
    except (RuntimeError, PermissionError) as e:
        raise _steps.StepError(str(e)) from e
    for _src, ch, target, _x in plan:
        secho(f"  ✓ глава {ch}: {target.relative_to(lib).as_posix()}", fg=colors.GREEN)
    secho(f"Проза внесена: {len(plan)} глав(ы); {res.message}", fg=colors.GREEN if res.commit or not commit else colors.YELLOW)

    # имена из прозы: словарь имён и заготовка континуити (черновики для решения автора, не канон)
    known = exporter.known_names_of(ws.exports)
    pseudo = exporter.pseudo_subjects(ws.exports)
    by_name: dict[str, dict] = {}
    for _src, ch, _t, text in plan:
        for n in names_mod.find_names(text, known, pseudo):
            by_name.setdefault(n, {"главы": set(), "в_каноне": True, "цитата": ""})["главы"].add(ch)
        for m in lint_mod.PROSE_NAME_RE.finditer(text):
            if names_mod.find_names(m.group(1), known, pseudo):
                continue
            if _at_sentence_start(text, m.start()) and not re.search(r"(?:ич|ична|овна|евна)$", m.group(2)):
                continue  # «Потом Кузнецов»: слово в начале предложения + фамилия — не имя
            full = re.sub(r"\s+", " ", m.group(0)).strip()
            rec = by_name.setdefault(full, {"главы": set(), "в_каноне": False, "цитата": _quote_around(text, m.start())})
            rec["главы"].add(ch)
    unknown = sorted(n for n, r in by_name.items() if not r["в_каноне"])
    out_dir = onboarding_dir(ws)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Словарь имён из импортированной прозы (черновик)", "",
             f"Том {volume}, главы {chapters}. Имена сверены с картами канона (эпистемика, карточки, фокалы).", "",
             "| имя | главы | в каноне | решение |", "|---|---|---|---|"]
    for n in sorted(by_name):
        r = by_name[n]
        chs = ", ".join(str(c) for c in sorted(r["главы"]))
        lines.append(f"| {n} | {chs} | {'да' if r['в_каноне'] else 'нет'} | "
                     f"{'—' if r['в_каноне'] else '⚠ решение автора: карточка, континуити или убрать из прозы'} |")
    guard.write_text(out_dir / "проза_имена.md", "\n".join(lines) + "\n")
    cont = ["# Континуити из импортированной прозы (заготовка)", "",
            "Строки в формате таблицы континуити — перенесите нужные в документ континуити после проверки.", "",
            "| дата | событие | главы | примечание |", "|---|---|---|---|"]
    for n in unknown:
        r = by_name[n]
        first = min(r["главы"])
        cont.append(f"| т.{volume} гл.{first} | «{n}» — впервые в прозе: {r['цитата']} | "
                    f"{', '.join(str(c) for c in sorted(r['главы']))} | ⚠ решение автора |")
    guard.write_text(out_dir / "проза_континуити.md", "\n".join(cont) + "\n")

    # спорные факты: находки линтера по импортированным главам + имена вне канона
    targets = {t.relative_to(lib).as_posix() for _s, _c, t, _x in plan}
    findings = [f for f in (res.lint.findings if res.lint else []) if f.file in targets]
    disputed = ["# Спорные факты импортированной прозы", ""]
    disputed += [f"- [{f.severity}] {f.code} {f.file}{':' + str(f.line) if f.line else ''} — {f.message}" for f in findings]
    disputed += [f"- имя «{n}» (главы {', '.join(str(c) for c in sorted(by_name[n]['главы']))}) не найдено в каноне — ⚠ решение автора"
                 for n in unknown]
    if len(disputed) == 2:
        disputed.append("- расхождений не найдено")
    guard.write_text(out_dir / "проза_спорное.md", "\n".join(disputed) + "\n")
    echo(f"Имена из прозы: {len(by_name)}, вне канона: {len(unknown)} → онбординг/проза_имена.md, онбординг/проза_континуити.md")
    echo(f"Спорные факты: {len(findings) + len(unknown)} → онбординг/проза_спорное.md")
    for f in findings[:10]:
        secho(f"  [{f.severity}] {f.code} {f.file}{':' + str(f.line) if f.line else ''} — {f.message}", fg=colors.YELLOW)

    corridors = None
    if calibrate:
        try:
            r = quality_steps.norms(calibrate_files=[t for _s, _c, t, _x in plan], approve=False)
            corridors = r.get("коридоры") if r else None
        except _steps.StepError as e:
            secho(f"⚠ Калибровка норм пропущена: {e}", fg=colors.YELLOW)
        else:
            echo("Утвердить коридоры: `konveyer нормы --калибровать --утвердить <те же файлы>`; затем `konveyer доктор`.")
    return {
        "внесено": [ch for _s, ch, _t, _x in plan], "пропущено": skipped, "имена": sorted(by_name), "вне_канона": unknown,
        "спорных": len(findings) + len(unknown), "коммит": res.commit, "коридоры": corridors,
    }
