"""Канон и бэкап: lint (линтер канона, FR-LT-*), snapshot (срез тома, FR-VL-2), rollback (откат, FR-SC-4), retest
(пере-тест, FR-RT-*), canon_commit (правка канона автором, FR-CN-*), backup (сохранность, FR-BK-*), library_split (FR-BK-4)."""

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


def lint(llm: bool = False, files: list[str] | None = None, watch: bool = False, max_calls: int = 40,
         budget: float | None = None) -> int:
    """Проверка канона на противоречия и ошибки логики повествования (машинный слой; `llm` — модель).
    Возвращает число ошибок канона последнего прогона (код возврата 1 при `--strict` ставит CLI);
    `watch` — следить за библиотекой и перепроверять при каждом изменении (до Ctrl+C);
    `budget` — бюджет модельного слоя в долларах (FR-LT-3; без него — `бюджет_линтера` из конфиг.yaml, 0 — без лимита)."""
    from .. import lint as lint_mod

    ws, cfg, lib = _ctx()
    if not isinstance(files, list):
        files = []
    if not isinstance(max_calls, int):
        max_calls = 40
    if not isinstance(budget, (int, float)):
        budget = cfg.lint_budget_usd or None
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
                     + (f", ≈ ${est:.2f}" if est is not None else "")
                     + (f", бюджет {budget:.2f} $" if budget else ""))
                try:
                    extra, prompts = lint_mod.run_lint_llm(ws, cfg, lib, llm_docs, max_calls=max_calls, max_cost_usd=budget)
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
    """Черновик снапшота тома (срез мира и знаний на конец тома): кто что знает, закладки, хронология.
    В канон снапшот вносит `konveyer том закрыть N`. Возвращает путь черновика."""
    from .. import snapshot as snapshot_mod

    ws, cfg, lib = _ctx()
    volume = volume or ws.volume
    if volume != ws.volume:
        raise StepError(f"выгрузки — тома {ws.volume}; для среза тома {volume} переключитесь: `konveyer том открыть {volume}`.")
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    path = snapshot_mod.build_snapshot(ws, volume)
    secho(f"Срез тома {volume}: {path}", fg=colors.GREEN)
    echo(f"В канон срез вносит `konveyer том закрыть {volume}` (при закрытии тома); раньше закрытия — правкой библиотеки "
         "и `konveyer канон-коммит` (FR-K3 соблюдён).")
    return path


def rollback(chapter: int, to: str | None = None, yes: bool = False, confirm: Confirm | None = None) -> str:
    """Откат главы в предыдущее состояние (сценарий Г); без `to` — на шаг назад по цепочке состояний §5.4.
    Возвращает новое состояние."""
    ws, cfg, lib = _ctx()
    st = ChapterState(ws, chapter)
    if to is None:
        # «предыдущее» — по цепочке STATES, а не по истории (после отката история
        # указывала бы вперёд, а не назад)
        idx = STATES.index(st.state)
        if idx == 0:
            raise StepError(f"глава {chapter} ещё не начата — откатывать некуда.")
        to = STATES[idx - 1]
        echo(f"Откат на шаг назад: «{st.state}» → «{to}».")
    # Проверка цели ДО любых побочных эффектов (FR-SC-4): опечатка в --to не должна стоить git revert'а
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


RETEST_DIR = "пере-тест"


def _retest_packs(ws: Workspace) -> list[Path]:
    """Папки пакетов пере-теста по возрастанию (имя = дата[_глN]); старые пакеты «<дата>» тоже считаются."""
    root = ws.root / RETEST_DIR
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "ПРОМПТ_раунд1.md").exists())


def _latest_pack_with_answers(ws: Workspace, cfg: Config) -> Path | None:
    """Свежий пакет, где есть ответ модели-Писателя текущего пина (или хотя бы один ответ, если Писатель ручной)."""
    for pack in reversed(_retest_packs(ws)):
        answers = list(pack.glob("ответ_*.md"))
        if not answers:
            continue
        if cfg.writer.manual or (pack / f"ответ_{cfg.writer.model}.md").exists():
            return pack
    return None


def retest(chapter: int = 1, fix: bool = False, no_pack: bool = False) -> Path:
    """Пере-тест моделей (FR-RT-1, Д-19): пакет сравнения на свежем брифе главы `chapter` в
    `пере-тест/<дата>_гл<N>/` — ответы доступных моделей, флаги Э2 по ним, сводка метрик; прогон полуручной.
    `fix` — зафиксировать пины (FR-RT-2): нужна зелёная регрессия и пакет с ответом модели-Писателя
    (`no_pack` — по решению автора без пакета, это записывается в черновик журнала). Возвращает папку пакета."""
    ws, cfg, lib = _ctx()
    if fix:
        return _retest_fix(ws, cfg, no_pack)
    exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = ws.root / RETEST_DIR / f"{stamp}_гл{chapter}"
    # окно собирается во временную рабочую область — окно.md главы в работе не трогается
    prompt_path = _compile_window_to(ws, cfg, lib, chapter, _ensure_dir(dest / "ПРОМПТ_раунд1.md"))
    guard.write_text(dest / "пакет.json", json.dumps({"глава": chapter, "том": ws.volume, "дата": stamp}, ensure_ascii=False) + "\n")
    ran, skipped = retest_run_models(ws, cfg, chapter, prompt_path, dest)
    e2_done, e2_manual = retest_run_e2(ws, cfg, chapter, dest)
    summary = retest_summary(ws, chapter, dest)
    guard.write_text(dest / "СВОДКА.md", summary)
    guard.write_text(
        dest / "РЕЗУЛЬТАТЫ.md",
        f"# Результаты раунда 1 · глава {chapter}\n\n"
        + (f"Прогнано автоматически: {', '.join(ran)}.\n" if ran else "Автоматический прогон не состоялся — ключей/моделей нет.\n")
        + (f"Ручной прогон: {'; '.join(skipped)}.\n" if skipped else "")
        + (f"Флаги Э2 получены: {', '.join(e2_done)}.\n" if e2_done else "")
        + (f"Э2 вручную (промпт сохранён): {'; '.join(e2_manual)}.\n" if e2_manual else "")
        + "Ответы других моделей положите файлами `ответ_<модель>.md` в эту папку и повторите `konveyer пере-тест` —\n"
        "флаги Э2 и сводка метрик Э1 пересчитаются (СВОДКА.md); решение — записью в журнал решений "
        "(`konveyer пере-тест --зафиксировать`).\n",
    )
    secho(f"Пакет пере-теста готов: {dest}/ — сводка в СВОДКА.md" + (f"; ручной прогон: {len(skipped)}" if skipped else ""),
          fg=colors.GREEN)
    return dest


def _retest_fix(ws: Workspace, cfg: Config, no_pack: bool) -> Path:
    green = regression_mod.is_green(ws)
    if green is not True:
        why = (
            "регрессия КРАСНАЯ" if green is False
            else "отчёт регрессии устарел (изменились конфиг.yaml, шаблоны или нормы)"
            if regression_mod.is_stale(ws) else "регрессия не запускалась"
        )
        raise StepError(f"фиксация retest запрещена: {why} (FR-RG-3). Сначала `konveyer регрессия` с непустым корпусом.")
    pack = _latest_pack_with_answers(ws, cfg)
    if pack is None and not no_pack:
        raise StepError(
            f"нет пакета пере-теста с ответом модели-Писателя ({cfg.writer.model}) — сначала `konveyer пере-тест` "
            "(ответы ручного прогона положите в папку пакета файлами `ответ_<модель>.md`); зафиксировать без пакета "
            "по решению автора — `--без-пакета`."
        )
    from .. import pins

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = pack or (ws.root / RETEST_DIR / f"{stamp}_фиксация")
    basis = f"пакет {pack.name}" if pack else "без пакета сравнения (решение автора, --без-пакета)"
    pins.record(ws, cfg, note=f"пере-тест {stamp}: {basis}", extra={"пакет": pack.name if pack else None})
    roles = "\n".join(
        f"  - {r}: {p['провайдер']}/{p['модель']}, режим без обучения — {'да' if p['режим_без_обучения'] else 'НЕТ'}"
        for r, p in pins.privacy(cfg).items()
    )
    guard.write_text(
        dest / "журнал_запись.md",
        "# Запись в журнал решений (внесите в библиотеку через правку канона и `konveyer канон-коммит`)\n\n"
        f"- Дата: {stamp}\n- Решение: пере-тест моделей, результаты приняты автором ({basis}).\n"
        + (f"- Основание: {RETEST_DIR}/{pack.name}/СВОДКА.md\n" if pack else "- Основание: пакет сравнения не собирался.\n")
        + f"- Конфигурация (провайдер/модель, приватность — FR-SC-10):\n{roles}\n",
    )
    secho(f"Пины зафиксированы (журналы/{pins.PINS}). Черновик записи журнала: {RETEST_DIR}/{dest.name}/журнал_запись.md — "
          f"внесите в журнал решений.", fg=colors.GREEN)
    return dest


def _answer_name(model: str, role: str, taken: dict[str, str], fp: str) -> str:
    """Имя файла ответа: `<модель>`; та же модель с другими параметрами у другой роли — `<модель>_<роль>`."""
    if model not in taken or taken[model] == fp:
        return model
    return f"{model}_{role}"


def retest_run_models(ws: Workspace, cfg: Config, chapter: int, prompt_path: Path, dest: Path) -> tuple[list[str], list[str]]:
    """Прогон пакета по доступным моделям ролей: одна модель с одними параметрами — один ответ `ответ_<модель>.md`
    (дедупликация по отпечатку пина, не по имени); без ключа/SDK — модель остаётся для ручного прогона (FR-RT-1)."""
    from .. import adapters, pins

    prompt = prompt_path.read_text(encoding="utf-8")
    fps = pins.fingerprint(cfg)
    seen: set[str] = set()
    taken: dict[str, str] = {}
    ran: list[str] = []
    skipped: list[str] = []
    for role, mc in cfg.roles().items():
        fp = fps[role]
        if mc.manual or fp in seen:
            continue
        seen.add(fp)
        name = _answer_name(mc.model, role, taken, fp)
        taken.setdefault(mc.model, fp)
        target = dest / f"ответ_{name}.md"
        if target.exists():
            ran.append(f"{name} (уже есть)")
            continue
        try:
            text = adapters.call_model(mc, cfg.api, "", prompt, ws.logs, role=f"пере-тест ({role})", chapter=chapter)
        except adapters.ManualModeNeeded as e:
            skipped.append(f"{name} — {e.reason}")
            continue
        except Exception as e:  # noqa: BLE001 — сбой одной модели не срывает пакет
            skipped.append(f"{name} — {adapters.explain_error(e, role)}")
            continue
        guard.write_text(target, text)
        ran.append(name)
    return ran, skipped


def retest_run_e2(ws: Workspace, cfg: Config, chapter: int, dest: Path) -> tuple[list[str], list[str]]:
    """Флаги Э2 по каждому ответу пакета (FR-RT-1): Верификатор-2 по тому же брифу → `флаги_<модель>.json`;
    без API — промпт `э2_промпт_<модель>.md` для ручного прогона (ответ положите как `флаги_<модель>.json`);
    нечитаемый ответ — `э2_сырой_<модель>.md`. Возвращает (получены, вручную)."""
    from .. import adapters, verifier2

    done: list[str] = []
    manual: list[str] = []
    for answer in sorted(dest.glob("ответ_*.md")):
        model = answer.stem[len("ответ_"):]
        flags_path = dest / f"флаги_{model}.json"
        if flags_path.exists():
            continue
        try:
            system, user = verifier2.build_prompt_text(ws, chapter, answer.read_text(encoding="utf-8"), cfg)
        except FileNotFoundError as e:  # нет выгрузок главы — Э2 по пакету невозможен, пакет при этом цел
            manual.append(f"{model} — {e}")
            continue
        try:
            raw = adapters.call_role(cfg, "верификатор2", system, user, ws.logs, role="верификатор-2 (пере-тест)", chapter=chapter)
        except adapters.ManualModeNeeded as e:
            guard.write_text(dest / f"э2_промпт_{model}.md", f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n")
            manual.append(f"{model} — {e.reason}; промпт: э2_промпт_{model}.md")
            continue
        except Exception as e:  # noqa: BLE001 — сбой Э2 по одному ответу не срывает пакет
            manual.append(f"{model} — {adapters.explain_error(e, 'верификатор2')}")
            continue
        try:
            flags = verifier2.parse_flags(raw, cfg.e2_quote_words)
        except ValueError as e:
            guard.write_text(dest / f"э2_сырой_{model}.md", raw)
            manual.append(f"{model} — ответ Э2 не разобран ({e}); сырой ответ: э2_сырой_{model}.md")
            continue
        guard.write_text(flags_path, json.dumps([f.model_dump() for f in flags], ensure_ascii=False, indent=2) + "\n")
        done.append(model)
    return done, manual


def retest_summary(ws: Workspace, chapter: int, dest: Path) -> str:
    """Сводная таблица метрик Э1 по ответам моделей (`ответ_<модель>.md`) и флагов Э2
    (`флаги_<модель>.json`: всего / самоволок) — FR-RT-1."""
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
                if isinstance(flags, list):
                    sam = sum(1 for f in flags if isinstance(f, dict) and f.get("kind") == "samovolka")
                    rows[model]["флаги Э2"] = f"{len(flags)} (самоволок {sam})"
                else:
                    rows[model]["флаги Э2"] = "?"
            except ValueError:
                rows[model]["флаги Э2"] = "не разобраны"
        elif (dest / f"э2_промпт_{model}.md").exists():
            rows[model]["флаги Э2"] = "вручную (э2_промпт)"
    cols = ids + (["флаги Э2"] if any("флаги Э2" in r for r in rows.values()) else [])
    lines += ["| модель | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for model, r in rows.items():
        lines.append(f"| {model} | " + " | ".join(r.get(c, "—") for c in cols) + " |")
    lines += ["", "Флаги Э2 считаются Верификатором-2 по каждому ответу; без API — ответ по `э2_промпт_<модель>.md` "
              "сохраните как `флаги_<модель>.json`, и столбец заполнится при следующем `konveyer пере-тест`."]
    return "\n".join(lines) + "\n"


def _compile_window_to(ws: Workspace, cfg: Config, lib: Path, chapter: int, target: Path) -> Path:
    """Собирает окно главы во временную рабочую область (копия выгрузок и шаблонов) и кладёт
    результат в target: главы/N/окно.md главы в работе остаётся нетронутым."""
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
    ref = _journal_reference(ws, lib)

    def ask(result: canonchange.ChangeResult) -> bool:
        """Между линтом и коммитом: предупреждения автору и вопрос (Д-8)."""
        if (
            old_norms_hash is not None
            and result.export_hashes.get("norms.json") != old_norms_hash
            and not gitops.check_norm_change_message(message, ref["регэксп"])
        ):
            secho(
                f"⚠ Изменены нормы ({ref['стиль']}), но в сообщении коммита нет ссылки {ref['образец']} на запись "
                f"в {ref['журнал']} — предупреждение, не блокировка (сценарий Б).",
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


def _journal_reference(ws: Workspace, lib: Path) -> dict[str, str | None]:
    """Как ссылаться на запись журнала решений — из каталога типов и манифеста (П-1: имена документов и формат
    номера записи не в коде): {регэксп, образец, журнал (имя документа), стиль (имя документа норм)}."""
    from .. import catalog

    types = catalog.load_types(ws.root)

    def doc_name(тип: str) -> str:
        try:
            docs = exporter.docs_of_type(lib, тип, ws.volume, ws.root)
        except Exception:  # noqa: BLE001 — манифест/библиотека нечитаемы: имя типа вместо файла
            docs = []
        if docs:
            return docs[0].name
        spec = types.get(тип)
        return spec.default_name if spec and spec.default_name else f"документ «{тип}»"

    spec = types.get("журнал_решений")
    ref = (spec.raw.get("ссылка_на_запись") or {}) if spec else {}
    return {
        "регэксп": str(ref["регэксп"]) if ref.get("регэксп") else None,
        "образец": str(ref.get("образец") or "на номер записи"),
        "журнал": doc_name("журнал_решений"),
        "стиль": doc_name("стиль"),
    }


def library_split(
    target: str | None = None, show: bool = False, with_history: bool = False, yes: bool = False,
    confirm: Confirm | None = None,
) -> None:
    """Вынести библиотеку канона в отдельный git-репозиторий рядом с рабочей областью (FR-BK-4):
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
    """Сохранность (FR-BK-1…FR-BK-3): состояние копий; `push` — ветка и теги во все remotes; `archive` — zip
    рабочей области (и библиотеки, если она не под git); `add_remote` — второе место хранения (папка на внешнем
    диске = bare-репозиторий без облака, §1.3 ТЗ). Git нужен только для remotes/push; архив делается всегда (П-5)."""
    ws, cfg, lib = _ctx()
    repo = gitops.is_repo(lib)
    if (add_remote or push) and not repo:
        raise StepError("библиотека не под git — удалённые копии невозможны; инициализируйте репозиторий "
                        "(git init в библиотеке или `konveyer библиотека-отделить` — как отдельный).")
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
        secho(f"Удалённое место «{name}» добавлено: {url}. Отправка — `konveyer бэкап --push`.", fg=colors.GREEN)
    remotes = gitops.remotes(lib) if repo else []
    if repo:
        echo(f"Удалённых мест: {len(remotes)} ({', '.join(remotes) or 'нет'}); требуется ≥{cfg.backup_remotes_min}.")
        if len(remotes) < cfg.backup_remotes_min:
            secho("⚠ Добавьте удалённые репозитории/внешние копии (FR-BK-1): `konveyer бэкап --добавить-remote <имя> <url|папка>`.",
                  fg=colors.YELLOW)
        if gitops.dirty(lib):
            secho("⚠ В библиотеке незакоммиченные изменения (`konveyer канон-коммит`).", fg=colors.YELLOW)
        age = gitops.last_commit_age_days(lib)
        if age is not None:
            echo(f"Последний коммит: {age:.1f} дн. назад.")
    else:
        secho("⚠ Библиотека не под git: версий канона и удалённых копий нет — архив включает саму библиотеку. "
              "Заведите репозиторий: git init в библиотеке (`konveyer доктор` подскажет).", fg=colors.YELLOW)
    arch_dir = backup_mod.archive_dir(ws, cfg, folder)
    if archive:
        path, removed = backup_mod.make_archive(ws, cfg, arch_dir)
        secho(f"Архив рабочей области: {path}", fg=colors.GREEN)
        if removed:
            echo(f"Удалено старых архивов: {len(removed)} (хранится последних {cfg.backup_keep}, хранить_архивов).")
    else:
        arch_age = backup_mod.archive_age_days(arch_dir)
        echo(
            f"Архив рабочей области: {arch_age:.1f} дн. назад ({backup_mod.latest_archive(arch_dir)})." if arch_age is not None
            else f"Архив рабочей области ещё не делался ({arch_dir}): `konveyer бэкап --архив`."
        )
    if push:
        if not remotes:
            raise StepError("нет удалённых репозиториев — добавьте: `konveyer бэкап --добавить-remote <имя> <url|папка>`.")
        confirm_or_reject(yes, confirm, f"Отправить ветку и теги в {len(remotes)} удалённых мест? (y)")
        for remote in remotes:
            try:
                gitops.push(lib, remote)
                secho(f" ✓ {remote}", fg=colors.GREEN)
            except RuntimeError as e:
                secho(f" ✗ {remote}: {e}", fg=colors.RED)
