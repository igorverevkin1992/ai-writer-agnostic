"""Канон и бэкап: lint (линтер канона), snapshot (срез тома 3.5), rollback (сценарий Г), retest (сценарий В),
canon_commit (сценарий Б), backup (NFR-6), library_split (аудит 2, п. 28)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .. import backup as backup_mod, canonchange, compiler, exporter, gitops, guard, regression as regression_mod
from ..config import Config
from ..errors import StepError
from ..fsm import STATES, ChapterState, TransitionError
from ..mdparse import MarkupError
from ..paths import Workspace
from .common import Confirm, _ctx, _ensure_dir, _is_git_url, colors, confirm_or_reject, echo, secho


def lint(llm: bool = False, files: list[str] | None = None, watch: bool = False, max_calls: int = 40) -> int:
    """Проверка канона на противоречия и ошибки логики повествования (машинный слой; `llm` — модель).
    Возвращает число ошибок канона последнего прогона (код возврата 1 при `--strict` ставит CLI);
    `watch` — следить за библиотекой и перепроверять при каждом изменении (до Ctrl+C)."""
    from .. import lint as lint_mod

    ws, cfg, lib = _ctx()
    if not isinstance(files, list):
        files = []
    if not isinstance(max_calls, int):
        max_calls = 40
    try:
        llm_docs = lint_mod.resolve_library_files(lib, files) if llm else []
    except ValueError as e:
        raise StepError(str(e)) from e

    def once() -> int:
        try:
            report = lint_mod.run_lint(lib, ws.exports, ws.logs, volume=ws.volume, root=ws.root, use_cache=False)
        except Exception as e:  # noqa: BLE001 — сбой линтера виден как находка, не как трейсбек
            report = lint_mod.error_report(e, ws.logs)
        if llm:
            if report.errors:
                secho(
                    f"⚠ Модельный слой пропущен: сначала устраните {report.errors} ошиб. машинного слоя "
                    "(выгрузки при ошибках разметки неполны — модель проверяла бы не тот канон).",
                    fg=colors.YELLOW,
                )
            else:
                est = lint_mod.estimate_llm_cost(cfg, len(llm_docs))
                echo(f"Модельный слой: документов {len(llm_docs)}, вызовов ≤ {len(llm_docs)}"
                     + (f", ≈ ${est:.2f}" if est is not None else ""))
                try:
                    extra, prompts = lint_mod.run_lint_llm(ws, cfg, lib, llm_docs, max_calls=max_calls)
                except ValueError as e:
                    raise StepError(str(e)) from e
                report = lint_mod.merge_llm(report, extra, ws.logs)
                if prompts:
                    secho(f"⚠ API недоступен: промпты модельного слоя сохранены ({len(prompts)}) в журналы/линтер_промпты/", fg=colors.YELLOW)
        for f in report.findings:
            color = {"ошибка": colors.RED, "предупреждение": colors.YELLOW, "заметка": colors.BLUE}[f.severity]
            where = f"{f.file}:{f.line}" if f.line else f.file
            secho(f"  [{f.severity}] {f.code} {where} — {f.message}", fg=color)
            if f.fix:
                echo(f"      исправление: «{f.fix.old}» → «{f.fix.new}»")
        secho(
            f"Канон: документов {report.files_checked}, ошибок {report.errors}, предупреждений {report.warnings}, "
            f"заметок {report.notes} → журналы/линтер.md",
            fg=colors.RED if report.errors else colors.GREEN,
        )
        return report.errors

    if not watch:
        return once()
    from .. import canonwatch

    errors = once()
    echo("Слежу за библиотекой (Ctrl+C — стоп)…")

    def on_change(changed: list[str]) -> None:
        echo(f"\nИзменено: {', '.join(changed)}")
        try:
            once()
        except Exception as e:  # noqa: BLE001 — наблюдение продолжается, причина видна
            secho(f"⚠ Проверка не выполнена: {e}", fg=colors.RED)

    watcher = canonwatch.CanonWatcher(lib, on_change)
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        echo("Остановлено.")
    return errors


def snapshot(volume: int | None = None) -> Path:
    """Черновик снапшота тома (реестр 3.5): кто что знает, закладки, хронология.
    В канон снапшот вносит `konveyer volume close N`. Возвращает путь черновика."""
    from .. import snapshot as snapshot_mod

    ws, cfg, lib = _ctx()
    volume = volume or ws.volume
    if volume != ws.volume:
        raise StepError(f"выгрузки — тома {ws.volume}; для среза тома {volume} переключитесь: `konveyer volume open {volume}`.")
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    path = snapshot_mod.build_snapshot(ws, volume)
    secho(f"Срез тома {volume}: {path}", fg=colors.GREEN)
    echo("Внесите его в библиотеку правкой канона и `konveyer canon-commit` (FR-K3 соблюдён).")
    return path


def rollback(chapter: int, to: str | None = None, yes: bool = False, confirm: Confirm | None = None) -> str:
    """Откат главы в предыдущее состояние (сценарий Г); без `to` — на шаг назад по цепочке состояний §5.4.
    Возвращает новое состояние."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    if to is None:
        # 2.11: «предыдущее» — по цепочке STATES, а не по истории (после отката история
        # указывала бы вперёд, а не назад)
        idx = STATES.index(st.state)
        if idx == 0:
            raise StepError(f"глава {chapter} ещё не начата — откатывать некуда.")
        to = STATES[idx - 1]
        echo(f"Откат на шаг назад: «{st.state}» → «{to}».")
    # Проверка цели ДО любых побочных эффектов (4.2): опечатка в --to не должна стоить git revert'а
    if to not in STATES:
        raise StepError(f"неизвестное состояние «{to}»; допустимые: {', '.join(STATES)}.")
    if STATES.index(to) >= STATES.index(st.state):
        raise StepError(f"откат возможен только назад: «{st.state}» → «{to}» не является откатом.")
    if st.state == "зафиксировано":
        # только git-revert коммита приёмки с пересчётом выгрузок и корпуса (FR-TK-4, FR-SC-4)
        if not gitops.is_repo(lib):
            raise StepError(f"библиотека не под git — откат зафиксированной главы {chapter} невозможен (только git-revert).")
        sha = st.data.get("коммит_приёмки") or gitops.find_chapter_commit(lib, chapter)
        if not sha:
            raise StepError(f"не найден коммит приёмки главы {chapter} в библиотеке.")
        # те же проверки, что у любой записи в канон (FR-SC-2): незавершённые операции, чистота,
        # авторство — ДО revert'а, иначе revert-коммит захватил бы чужие правки автора
        try:
            canonchange.check_git(lib, commit=True, action=f"откат главы {chapter}")
        except RuntimeError as e:
            raise StepError(f"откат не выполнен, библиотека не тронута: {e}") from e
        confirm_or_reject(yes, confirm, f"git revert {sha[:10]} (приёмка главы {chapter}) и пересчёт выгрузок? (y)")
        try:
            gitops.revert(lib, sha, author=cfg.commit_author)
        except RuntimeError as e:
            raise StepError(f"откат не выполнен, библиотека не тронута: {e}") from e
        # состояние — сразу после успешного реверта, чтобы повторный откат не «ревертил реверт»;
        # выход из терминального состояния сбрасывает счётчики и поля цикла приёмки (FR-TK-4)
        st.unfix()
        exported = True
        try:
            exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
        except MarkupError as e:
            exported = False
            secho(f"⚠ Откат выполнен, но выгрузки не пересчитаны: {e}. Поправьте канон и `konveyer export`.", fg=colors.YELLOW)
        if to != "принято":
            st.rollback(to)
        secho(
            f"Откат выполнен: глава {chapter} → «{st.state}», "
            + ("выгрузки и корпус пересчитаны." if exported else "выгрузки НЕ пересчитаны (см. выше)."),
            fg=colors.GREEN if exported else colors.YELLOW,
        )
        return st.state
    try:
        st.rollback(to)
    except TransitionError as e:
        raise StepError(str(e)) from e
    secho(f"Глава {chapter} → «{to}».", fg=colors.GREEN)
    return to


def retest(chapter: int = 1, fix: bool = False) -> Path:
    """Пере-тест моделей (сценарий В, Д-10): пакет раунда 1 протокола отбора; прогон полуручной.
    Возвращает папку пакета (или черновика записи журнала при `fix`)."""
    ws, cfg, lib = _ctx()
    if fix:
        green = regression_mod.is_green(ws)
        if green is not True:
            why = (
                "регрессия КРАСНАЯ" if green is False
                else "отчёт регрессии устарел (изменились конфиг.yaml, шаблоны или нормы)"
                if regression_mod.is_stale(ws) else "регрессия не запускалась"
            )
            raise StepError(f"фиксация retest запрещена: {why} (FR-R3). Сначала `konveyer regress` с непустым корпусом.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        from .. import pins

        pins.record(ws, cfg, note=f"пере-тест {stamp}")
        guard.write_text(
            ws.root / "пере-тест" / stamp / "журнал_запись.md",
            f"# Запись в журнал решений (внесите в библиотеку через правку канона)\n\n"
            f"- Дата: {stamp}\n- Решение: пере-тест моделей, результаты приняты автором.\n"
            f"- Конфигурация: " + "; ".join(f"{r} — {m.provider}/{m.model}" for r, m in cfg.roles().items()) + "\n",
        )
        secho(f"Пины зафиксированы (журналы/{pins.PINS}). Черновик записи журнала: пере-тест/{stamp}/журнал_запись.md — "
              f"внесите в журнал решений.", fg=colors.GREEN)
        return ws.root / "пере-тест" / stamp
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = ws.root / "пере-тест" / stamp
    # 2.10: окно собирается во временную рабочую область — окно.md главы в работе не трогается
    prompt_path = _compile_window_to(ws, cfg, lib, chapter, _ensure_dir(dest / "ПРОМПТ_раунд1.md"))
    ran, skipped = retest_run_models(ws, cfg, chapter, prompt_path, dest)
    summary = retest_summary(ws, chapter, dest)
    guard.write_text(dest / "СВОДКА.md", summary)
    guard.write_text(
        dest / "РЕЗУЛЬТАТЫ.md",
        "# Результаты раунда 1\n\n"
        + (f"Прогнано автоматически: {', '.join(ran)}.\n" if ran else "Автоматический прогон не состоялся — ключей/моделей нет.\n")
        + (f"Ручной прогон: {'; '.join(skipped)}.\n" if skipped else "")
        + "Ответы других моделей положите файлами `ответ_<модель>.md` в эту папку и повторите `konveyer пере-тест` —\n"
        "сводка метрик Э1 пересчитается (СВОДКА.md); решение — записью в журнал решений "
        "(`konveyer пере-тест --зафиксировать`).\n",
    )
    secho(f"Пакет пере-теста готов: {dest}/ — сводка в СВОДКА.md" + (f"; ручной прогон: {len(skipped)}" if skipped else ""),
          fg=colors.GREEN)
    return dest


def retest_run_models(ws: Workspace, cfg: Config, chapter: int, prompt_path: Path, dest: Path) -> tuple[list[str], list[str]]:
    """Прогон пакета по доступным моделям ролей (Писатель и все роли с отличающимся пином): ответ — `ответ_<модель>.md`;
    без ключа/SDK — модель остаётся для ручного прогона (FR-RT-1)."""
    from .. import adapters

    prompt = prompt_path.read_text(encoding="utf-8")
    seen: set[str] = set()
    ran: list[str] = []
    skipped: list[str] = []
    for role, mc in cfg.roles().items():
        if mc.manual or mc.model in seen:
            continue
        seen.add(mc.model)
        target = dest / f"ответ_{mc.model}.md"
        if target.exists():
            ran.append(f"{mc.model} (уже есть)")
            continue
        try:
            text = adapters.call_model(mc, cfg.api, "", prompt, ws.logs, role=f"пере-тест ({role})", chapter=chapter)
        except adapters.ManualModeNeeded as e:
            skipped.append(f"{mc.model} — {e.reason}")
            continue
        except Exception as e:  # noqa: BLE001 — сбой одной модели не срывает пакет
            skipped.append(f"{mc.model} — {adapters.explain_error(e, role)}")
            continue
        guard.write_text(target, text)
        ran.append(mc.model)
    return ran, skipped


def retest_summary(ws: Workspace, chapter: int, dest: Path) -> str:
    """Сводная таблица метрик Э1 по ответам моделей (`ответ_<модель>.md`) и флаги Э2, если сохранены
    (`флаги_<модель>.json`) — FR-RT-1."""
    import json

    from .. import verifier1

    answers = sorted(dest.glob("ответ_*.md"))
    lines = [f"# Сводка пере-теста · глава {chapter}", ""]
    if not answers:
        lines.append("Ответов моделей пока нет: положите `ответ_<модель>.md` в папку пакета.")
        return "\n".join(lines) + "\n"
    rows: dict[str, dict[str, str]] = {}
    ids: list[str] = []
    for a in answers:
        model = a.stem[len("ответ_"):]
        try:
            checks = verifier1.analyze_text(ws, chapter, a.read_text(encoding="utf-8"))
        except FileNotFoundError:
            checks = []
        rows[model] = {c.check_id: f"{c.actual} [{c.status}]" for c in checks}
        for c in checks:
            if c.check_id not in ids:
                ids.append(c.check_id)
        flags_path = dest / f"флаги_{model}.json"
        if flags_path.exists():
            try:
                flags = json.loads(flags_path.read_text(encoding="utf-8"))
                rows[model]["флаги Э2"] = str(len(flags)) if isinstance(flags, list) else "?"
            except ValueError:
                rows[model]["флаги Э2"] = "не разобраны"
    cols = ids + (["флаги Э2"] if any("флаги Э2" in r for r in rows.values()) else [])
    lines += ["| модель | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for model, r in rows.items():
        lines.append(f"| {model} | " + " | ".join(r.get(c, "—") for c in cols) + " |")
    lines += ["", "Флаги Э2: сохраните ответ Верификатора-2 по каждому тексту как `флаги_<модель>.json`, и столбец появится."]
    return "\n".join(lines) + "\n"


def _compile_window_to(ws: Workspace, cfg: Config, lib: Path, chapter: int, target: Path) -> Path:
    """Собирает окно главы во временную рабочую область (копия выгрузок и шаблонов) и кладёт
    результат в target: главы/N/окно.md главы в работе остаётся нетронутым (2.10)."""
    tmp_root = target.parent / "_сборка_окна"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    (tmp_root / "выгрузки").mkdir(parents=True)
    for f in ws.exports.glob("*.json"):
        shutil.copyfile(f, tmp_root / "выгрузки" / f.name)
    if ws.templates.exists():
        shutil.copytree(ws.templates, tmp_root / "шаблоны")
    try:
        path, _ = compiler.compile_window(Workspace(tmp_root), lib, chapter, cfg.window_soft_limit_chars)
        shutil.copyfile(path, target)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return target


def canon_commit(message: str, yes: bool = False, confirm: Confirm | None = None) -> str | None:
    """Правка канона автором (сценарий Б): валидация структуры, перегенерация выгрузок, коммит.
    Возвращает SHA коммита (None — коммитить было нечего)."""
    ws, cfg, lib = _ctx()
    if not gitops.is_repo(lib):
        raise StepError("библиотека не под git — инициализируйте репозиторий.")
    manifest = ws.exports / "индекс.json"
    old_norms_hash = None
    if manifest.exists():
        old_norms_hash = json.loads(manifest.read_text(encoding="utf-8"))["files"].get("norms.json")

    def ask(result: canonchange.ChangeResult) -> bool:
        """Между линтом и коммитом: предупреждения автору и вопрос (Д-8)."""
        if (
            old_norms_hash is not None
            and result.export_hashes.get("norms.json") != old_norms_hash
            and not gitops.check_norm_change_message(message)
        ):
            secho(
                "⚠ Изменены нормы (02 §5), но в сообщении коммита нет ссылки Р-№ на запись "
                "в 36_Журнал — предупреждение, не блокировка (сценарий Б).",
                fg=colors.YELLOW,
            )
        if not gitops.dirty(lib):
            return False
        if result.lint and (result.lint.errors or result.lint.warnings):
            secho(
                f"⚠ Проверка канона: ошибок {result.lint.errors}, предупреждений {result.lint.warnings} (журналы/линтер.md) — "
                "коммит не блокируется, решение за автором.",
                fg=colors.YELLOW,
            )
        confirm_or_reject(yes, confirm, f"Закоммитить изменения библиотеки: «{message}»? (Д-8) (y)")
        return True

    # единый конвейер изменения канона: правки автор уже сделал на диске (writer пуст) →
    # валидация Д-1 + выгрузки → линт → коммит; незавершённый revert блокирует до любой записи
    try:
        result = canonchange.canon_change(
            ws, cfg, lib, lambda: None, message, commit=True, author_confirmed=True,
            require_clean=False, action="коммит канона", confirm=ask,
        )
    except MarkupError as e:
        raise StepError(f"структура MD расходится с соглашениями Д-1 → {e}") from e
    if result.commit is None:
        echo("В библиотеке нет изменений — коммитить нечего.")
        return None
    secho(f"Канон закоммичен: {result.commit}", fg=colors.GREEN)
    return result.commit


def library_split(
    target: str | None = None, show: bool = False, with_history: bool = False, yes: bool = False,
    confirm: Confirm | None = None,
) -> None:
    """Вынести библиотеку канона в отдельный git-репозиторий рядом с рабочей областью (аудит 2, п. 28):
    перенос папки, git init + первый коммит, library_dir в конфиг.yaml, .gitignore в прежнем репозитории."""
    ws, cfg, lib = _ctx()
    try:
        plan = backup_mod.plan_split(ws, cfg, lib, Path(target) if target else None, with_history=with_history)
    except backup_mod.SplitError as e:
        raise StepError(str(e)) from e
    secho(f"Сейчас: {plan.layout.label}.", bold=True)
    echo("План переезда:")
    for line in plan.lines():
        echo(f"  {line}")
    if show:
        echo("Ничего не изменено (--показать). Выполнить: `konveyer library-split`" + (" --с-историей" if with_history else "") + ".")
        return
    confirm_or_reject(yes, confirm, "Выполнить переезд? (y)")
    for note in backup_mod.split_library(ws, cfg, plan):
        secho(f" ✓ {note}", fg=colors.GREEN)
    echo("Что дальше:")
    for line in backup_mod.after_split_advice(plan):
        echo(f"  • {line}")


def backup(
    folder: str | None = None, push: bool = False, archive: bool = False, add_remote: tuple[str, str] | None = None,
    yes: bool = False, confirm: Confirm | None = None,
) -> None:
    """Сохранность (NFR-6): состояние копий; `push` — во все remotes; `archive` — zip рабочей области;
    `add_remote` — второе место хранения (папка на внешнем диске = без облака, §1.3 ТЗ)."""
    ws, cfg, lib = _ctx()
    if not gitops.is_repo(lib):
        raise StepError("библиотека не под git — инициализируйте репозиторий (`konveyer library-split` — как отдельный).")
    if add_remote:
        name, url = add_remote
        if name in gitops.remotes(lib):
            raise StepError(f"удалённое место «{name}» уже есть: {gitops.remote_url(lib, name)}")
        if not _is_git_url(url):
            target = Path(url).expanduser()
            target = target if target.is_absolute() else (Path.cwd() / target)
            if target.exists() and not gitops.is_bare_repo(target):
                raise StepError(f"папка {target} существует, но это не bare-репозиторий git — укажите пустой путь.")
            if not target.exists():
                gitops.init_bare(target)
                echo(f"Создан bare-репозиторий: {target}")
            url = str(target)
        gitops.add_remote(lib, name, url)
        secho(f"Удалённое место «{name}» добавлено: {url}. Отправка — `konveyer backup --push`.", fg=colors.GREEN)
    remotes = gitops.remotes(lib)
    echo(f"Удалённых мест: {len(remotes)} ({', '.join(remotes) or 'нет'}); требуется ≥{cfg.backup_remotes_min}.")
    if len(remotes) < cfg.backup_remotes_min:
        secho("⚠ Добавьте удалённые репозитории/внешние копии (NFR-6): `konveyer backup --добавить-remote <имя> <url|папка>`.",
              fg=colors.YELLOW)
    if gitops.dirty(lib):
        secho("⚠ В библиотеке незакоммиченные изменения (`konveyer canon-commit`).", fg=colors.YELLOW)
    age = gitops.last_commit_age_days(lib)
    if age is not None:
        echo(f"Последний коммит: {age:.1f} дн. назад.")
    arch_dir = backup_mod.archive_dir(ws, cfg, folder)
    if archive:
        path, removed = backup_mod.make_archive(ws, cfg, arch_dir)
        secho(f"Архив рабочей области: {path}", fg=colors.GREEN)
        if removed:
            echo(f"Удалено старых архивов: {len(removed)} (хранится последних {cfg.backup_keep}, backup_keep).")
    else:
        arch_age = backup_mod.archive_age_days(arch_dir)
        echo(
            f"Архив рабочей области: {arch_age:.1f} дн. назад ({backup_mod.latest_archive(arch_dir)})." if arch_age is not None
            else f"Архив рабочей области ещё не делался ({arch_dir}): `konveyer backup --архив`."
        )
    if push:
        if not remotes:
            raise StepError("нет удалённых репозиториев — добавьте git remote.")
        confirm_or_reject(yes, confirm, f"Отправить в {len(remotes)} удалённых мест? (y)")
        for remote in remotes:
            try:
                gitops.push(lib, remote)
                secho(f" ✓ {remote}", fg=colors.GREEN)
            except RuntimeError as e:
                secho(f" ✗ {remote}: {e}", fg=colors.RED)
