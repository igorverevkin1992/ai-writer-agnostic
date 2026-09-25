"""Обзор: status (FR-D2), log (журнал API §6.3), find (поиск по канону), doctor (NFR-1), dashboard (FR-D1)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import adapters, backup as backup_mod, dashboard as dashboard_mod, exporter, gitops, regression as regression_mod
from .. import review as review_mod, timing, verifier2
from ..errors import StepError
from ..fsm import ChapterState, all_states
from ..paths import Workspace
from .common import _chapter_flags_summary, _ctx, _print_verdict, cmd, colors, echo, next_step, secho


def status(chapter: int | None = None, volume: int | None = None) -> list:
    """Состояния глав и следующий шаг (FR-D2); с `chapter` — карточка главы; `volume` — главы тома N.
    Возвращает состояния глав тома (для карточки — список из одной главы)."""
    ws, cfg, lib = _ctx()
    if volume is not None and int(volume) < 1:
        raise StepError(f"номер тома должен быть ≥ 1, получено: {volume}.")
    if volume is not None and volume != ws.volume:
        ws = ws.for_volume(volume)
    if chapter is not None:
        if not ws.chapter_dir(chapter).exists():
            try:
                plan = {b.chapter for b in exporter.load_briefs(ws.exports)}
            except Exception:  # noqa: BLE001 — нет выгрузок: план неизвестен, карточка «не-начато» допустима (П-5)
                plan = None
            if plan is not None and chapter not in plan:
                # как в панели (404): глава вне плана — ошибка номера, а не карточка «не-начато» с подсказкой собрать окно
                raise StepError(f"главы {chapter} нет в плане глав тома {ws.volume} — проверьте номер или поглавник.")
        return [_status_detail(ws, chapter, cfg)]
    states = all_states(ws)
    if not states:
        echo(f"Глав тома {ws.volume} в работе нет. Начните: `{cmd('compile', 'N')}`.")
        return states
    echo(f"Том {ws.volume} · главы в {ws.chapters_root().relative_to(ws.root).as_posix()}/")
    echo(f"{'Глава':>6} | {'Состояние':<18} | {'Чернов.':>7} | {'Э1':<16} | {'Э2':<22} | Дальше")
    echo("-" * 110)
    for st in states:
        e1, e2 = _chapter_flags_summary(ws, st.chapter)
        hint = next_step(ws, st, cfg)
        echo(f"{st.chapter:>6} | {st.state:<18} | {st.draft:>7} | {e1:<16} | {e2:<22} | {hint}")
    echo(f"Сегодня: {timing.today_author_minutes(ws):g} мин автора (ожидание действий автора по всем главам).")
    return states


def _in_plan(ws: Workspace, chapter: int) -> bool:
    """Есть ли бриф главы в выгрузках текущего тома; нет выгрузок — считаем, что есть (не ложная тревога, П-5)."""
    from .. import exporter

    try:
        briefs = exporter.load_briefs(ws.exports)
    except (FileNotFoundError, ValueError):
        return True
    return any(b.chapter == chapter and b.volume == ws.volume for b in briefs)


def _status_detail(ws: Workspace, chapter: int, cfg=None) -> ChapterState:
    """Карточка главы: метрики вердикта, флаги, самоволки, следующий шаг."""
    st = ChapterState(ws, chapter)
    secho(f"Глава {chapter} · состояние «{st.state}» · черновик {st.draft}", bold=True)
    if st.state == "не-начато" and not _in_plan(ws, chapter):
        secho(f"⚠ Главы {chapter} нет в плане глав тома {ws.volume} (выгрузки/briefs.json) — проверьте номер "
              "или заведите бриф в документе плана глав.", fg=colors.YELLOW)
    echo(
        f"Авто-повторов Э1: {st.data.get('авто_повторов', 0)}; итераций правок: {st.data.get('итераций_правок', 0)}"
    )
    machine_s, author_s = timing.chapter_times(st.data.get("история", []))
    if machine_s or author_s:
        over = " ⚠ цель ≤40 мин" if author_s > 40 * 60 else ""
        echo(
            f"Время такта: автора {timing.fmt_minutes(author_s)}{over} · машинное {timing.fmt_minutes(machine_s)}"
        )
    verdict_path = ws.chapter_dir(chapter) / "вердикт.json"
    if verdict_path.exists():
        from ..schemas import Verdict

        verdict = Verdict.model_validate(json.loads(verdict_path.read_text(encoding="utf-8")))
        echo(f"\nВердикт Э1 (черновик {verdict.draft}):")
        _print_verdict(verdict)
    flags = verifier2.load_flags(ws, chapter)
    if flags:
        echo("\nФлаги Э2:")
        for f in flags:
            mark = "самоволка" if f.kind == "samovolka" else f.severity
            echo(f"  [{mark}] {f.flag_id} · {f.type}: {f.quote[:80]}")
    unresolved = review_mod.unresolved_samovolki(ws, chapter)
    if unresolved:
        secho(
            f"\nБез решения автора: {', '.join(unresolved)} — `{cmd('resolve', chapter, '<флаг> <решение>')}`",
            fg=colors.YELLOW,
        )
    hint = next_step(ws, st, cfg)
    secho(f"\nДальше: {hint}", fg=colors.GREEN)
    return st


def log(n: int = 15) -> list[dict]:
    """Последние API-вызовы: роль, модель, токены, стоимость (журнал §6.3). Возвращает показанные строки."""
    from .. import apilog

    ws, cfg, lib = _ctx()
    rows = apilog.read_log(ws.logs)[-n:]
    warning = apilog.corrupt_warning(ws.logs)
    if warning:
        secho(f"⚠ {warning}", fg=colors.YELLOW)
    if not rows:
        echo("Журнал API пуст.")
        return rows
    total_cost = 0.0
    for r in rows:
        cost = r.get("cost_est")
        total_cost += cost or 0
        status = f"ОШИБКА: {r['error'][:40]}" if r.get("error") else (
            f"in {r.get('tokens_in') or '?'} / out {r.get('tokens_out') or '?'}"
            + (f" · ${cost:.4f}" if cost else "")
        )
        echo(
            f"  {r['ts'][:19]} · {r.get('role', '?'):<22} · {r.get('model', ''):<20} "
            f"· гл. {r.get('chapter') or '—'} · {r.get('duration') or '?'} с · {status}"
        )
    if total_cost:
        echo(f"Стоимость показанных вызовов: ${total_cost:.4f}")
    return rows


def find(query: str) -> dict:
    """Поиск по канону и выгрузкам: факты, закладки, правила, брифы, досье, проза. Возвращает группы находок."""
    from .. import search

    ws, cfg, lib = _ctx()
    groups = search.grouped(search.find(ws.exports, lib, query))
    if not groups:
        echo(f"«{query}»: ничего не найдено.")
        return groups
    for kind, hits in groups.items():
        secho(f"{kind} ({len(hits)}):", bold=True)
        for h in hits:
            echo(f"  [{h.ref}] {h.text}")
    return groups


def doctor() -> None:
    """Диагностика установки и готовности конвейера (NFR-1); работает и вне проекта."""
    import importlib.util

    ws, cfg, lib = _ctx(require_project=False)

    def item(ok: bool | None, label: str, hint: str = "") -> None:
        mark, color = {True: ("✓", colors.GREEN), False: ("✗", colors.RED), None: ("~", colors.YELLOW)}[ok]
        secho(f" {mark} {label}", fg=color)
        if hint and ok is not True:
            echo(f"   → {hint}")

    secho(f"Рабочая область: {ws.root}", bold=True)
    item((ws.root / "конфиг.yaml").exists(), "конфиг.yaml", "создайте: `konveyer начать`")
    item(lib.exists(), f"библиотека канона: {lib}", "положите Библиотека/ или поправьте library_dir в конфиг.yaml")
    if lib.exists():
        lay = backup_mod.layout(lib, ws.root)
        item(lay.kind != "no-git", "библиотека под git", "git init внутри библиотеки (версионирование канона, §5.1)")
        if lay.kind != "no-git":
            item(lay.ok, lay.label, lay.hint)  # три раскладки (FR-BK-4): своя / внутри репозитория кода / не под git
        if gitops.is_repo(lib):
            item(gitops.has_identity(lib) or bool(cfg.commit_author), "авторство git настроено",
                 "git config user.email/user.name или commit_author в конфиг.yaml (Д-8)")
            item(gitops.in_progress(lib) is None, "нет незавершённых операций git в библиотеке",
                 f"завершите или отмените: git {gitops.in_progress(lib) or ''} --abort (в документах могут быть маркеры конфликта)")
            remotes = gitops.remotes(lib)
            item(len(remotes) >= cfg.backup_remotes_min, f"удалённых копий: {len(remotes)} (нужно ≥{cfg.backup_remotes_min})",
                 "`konveyer бэкап --добавить-remote <имя> <url|папка>` — папка на внешнем диске подходит (FR-BK-1, §1.3)")
            for remote in remotes:
                lag = gitops.remote_lag(lib, remote)
                if lag is None:
                    item(None, f"копия {remote}: состояние неизвестно (в неё ещё не отправляли)", "`konveyer бэкап --push`")
                else:
                    item(lag == 0, f"копия {remote}: " + ("актуальна" if lag == 0 else f"отстаёт на {lag} коммит(ов)"),
                         "`konveyer бэкап --push` (или push_после_приёмки: да в конфиг.yaml)")
    arch_dir = backup_mod.archive_dir(ws, cfg)
    arch_age = backup_mod.archive_age_days(arch_dir)
    if arch_age is None:
        item(None if cfg.backup_dir is None else False, f"архив рабочей области: ещё не делался ({arch_dir})",
             "`konveyer бэкап --архив`; папка_архива (backup_dir) в конфиг.yaml — архив после каждой приёмки главы (FR-BK-2)")
    else:
        item(arch_age <= 7, f"архив рабочей области: {arch_age:.1f} дн. назад ({backup_mod.latest_archive(arch_dir)})",
             "`konveyer бэкап --архив`")
    if lib.exists():
        from .. import project as project_mod

        checks = project_mod.readiness(ws.root, lib)
        secho("Готовность к такту (FR-LC-2) и комплектность модулей (FR-ON-20):", bold=True)
        for c in checks:
            item(c.ok, c.label, c.hint)
        if not checks:
            item(True, "минимальный комплект на месте, модули укомплектованы")
    training = [r for r, m in cfg.roles().items() if not m.manual and not m.no_training]
    item(not training, "провайдеры в режиме без обучения на данных автора (FR-SC-10)"
         + (f": роли с обучением — {', '.join(training)}" if training else ""),
         "включите режим без обучения у провайдера и отразите его в конфиге (режим_без_обучения) и журнале решений")
    manifest = ws.exports / "индекс.json"
    item(manifest.exists(), "выгрузки выгрузки/", "выполните `konveyer экспорт`")
    from .. import apilog

    bad_lines = apilog.corrupt_lines(ws.logs)
    if bad_lines:
        item(None, f"журнал API: нечитаемых строк {bad_lines} (пропускаются в сводках)",
             "удалите оборванные строки из журналы/api.jsonl, если нужен чистый журнал")
    providers = {m.provider for m in cfg.roles().values() if not m.manual}
    for provider in sorted(providers):
        names = adapters.KEY_ENV.get(provider, ())
        roles_of = ", ".join(r for r, m in cfg.roles().items() if m.provider == provider)
        item(bool(adapters.api_key_for(provider)) if names else None, f"ключ провайдера {provider} ({roles_of})",
             f"задайте {' или '.join(names) or 'ключ'} в .env — иначе ручной режим")
    def has_module(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:  # нет пакета-родителя (google.*)
            return False

    if "gemini" in providers:
        item(has_module("google.genai"), "SDK google-genai", "pip install 'konveyer[llm]'")
    if "anthropic" in providers:
        item(has_module("anthropic"), "SDK anthropic", "pip install 'konveyer[llm]'")
    # пины моделей против API (FR-RT-3): только чтение метаданных, ни одной генерации
    seen: set[tuple[str, str]] = set()
    labels = {"писатель": "Писатель", "верификатор2": "Верификатор-2", "канонист": "Канонист",
              "аналитик": "аналитик", "линтер": "линтер", "архивариус": "архивариус"}
    explicit = {r: m for r, m in cfg.roles().items()
                if r in ("писатель", "верификатор2", "канонист") or getattr(cfg, {"аналитик": "analyst", "линтер": "linter", "архивариус": "archivist"}[r], None) is not None}
    for role, mc in explicit.items():
        if (mc.provider, mc.model) in seen or mc.manual:
            continue
        seen.add((mc.provider, mc.model))
        ok, note = adapters.probe_model(mc)
        roles = "/".join(labels.get(r, r) for r, m in explicit.items() if (m.provider, m.model) == (mc.provider, mc.model))
        label = f"модель {mc.model} ({roles}): " + (f"есть в API ({note})" if ok else note)
        item(ok, label, "смените пин в конфиг.yaml через пере-тест (`konveyer пере-тест`, FR-RT-1, Д-19)" if ok is False else "")
    green = regression_mod.is_green(ws)
    if green is None:
        label = (
            "отчёт регрессии устарел (изменились конфиг.yaml, шаблоны или нормы)"
            if regression_mod.is_stale(ws) else "регрессия ещё не запускалась"
        )
    else:
        label = "регрессия зелёная" if green else "регрессия КРАСНАЯ"
    item(green, label, "`konveyer регрессия`" if green is None else "пропущенные флаги блокируют смену конфигурации (FR-RG-3)")
    n_tests = len(regression_mod.load_tests(ws)) if ws.regression.exists() else 0
    item(n_tests > 0, f"золотых тестов: {n_tests}", "корпус пуст — регрессия не может быть зелёной; пополните: `konveyer золотой` (FR-RG-1)")


def dashboard() -> Path:
    """Собрать дашборд.html (FR-D1). Возвращает путь файла."""
    ws, cfg, lib = _ctx()
    path = dashboard_mod.build_dashboard(ws)
    secho(f"Дашборд: {path}", fg=colors.GREEN)
    return path


def accounting(volume: int | None = None) -> str:
    """`konveyer учёт`: сводка стоимости и времени по тому (FR-CT-3), файл журналы/учёт_томN.md."""
    from .. import accounting as accounting_mod

    ws, cfg, lib = _ctx()
    if volume is None or volume == ws.volume:
        try:
            exporter.run_export(lib, ws.exports, ws.logs, ws.volume, ws.root)  # поглавник тома — для плана и прогноза
        except Exception as e:  # noqa: BLE001 — сводка без поглавника, не отказ (П-5)
            secho(f"⚠ выгрузки не пересобраны: {e}", fg=colors.YELLOW)
    acc = accounting_mod.volume_account(ws, volume, library=lib)
    text = accounting_mod.render(acc, cfg, ws)
    echo(text)
    path = accounting_mod.save(ws, acc, cfg)
    for w in accounting_mod.warnings(ws, cfg):
        secho(f"⚠ {w}", fg=colors.YELLOW)
    from .. import apilog

    warning = apilog.corrupt_warning(ws.logs)
    if warning:
        secho(f"⚠ {warning}", fg=colors.YELLOW)
    echo(f"Сохранено: {path.relative_to(ws.root)}")
    return text
