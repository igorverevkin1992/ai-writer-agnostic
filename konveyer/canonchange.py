"""Единый конвейер изменения канона (этап 5, п. 25; аудит 4.9).

ЛЮБАЯ запись в `Библиотека/` проходит через `canon_change()`:

    проверки git → сессия записи guard → writer() → экспорт → линт → коммит
                                                              └→ или явное «незакоммичено»

Только этот модуль открывает `guard.canon_write_session()` (статический тест): приёмка главы (канонист), внесение кругов истории,
правка документа и исправление линтера из панели, `konveyer канон-коммит` — все идут здесь.
Подтверждение автора (FR-K2, Д-8) вызывающий даёт явно флагом `author_confirmed`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import exporter, gitops, guard, lint
from .config import Config
from .paths import Workspace
from .schemas import LintReport

UNCOMMITTED_TEXT = "в библиотеке незакоммиченные изменения"
_PORCELAIN_RE = re.compile(r"^\s*[MADRCUT?!]{1,2}\s+(.+)$")


@dataclass
class ChangeResult:
    """Итог изменения канона для вызывающего (CLI печатает, панель отдаёт в JSON)."""

    commit: str | None = None            # SHA коммита; None — коммита не было
    uncommitted: bool = False            # в библиотеке остались незакоммиченные изменения
    message: str = ""                    # текст для автора («закоммичено …» / «незакоммичено …»)
    export_hashes: dict[str, str] = field(default_factory=dict)
    lint: LintReport | None = None
    dirty_files: list[str] = field(default_factory=list)


def dirty_files(library: Path) -> list[str]:
    """Незакоммиченные файлы папки библиотеки (`git status --porcelain -- .`), пути относительно
    репозитория; пусто — если библиотека не под git или чиста."""
    if not gitops.is_repo(library):
        return []
    out = gitops._git(library, "status", "--porcelain", "--", ".", check=False)
    files: list[str] = []
    for line in out.splitlines():
        # «XY путь»; вывод _git обрезан strip() — у первой строки может не быть ведущего пробела статуса
        m = _PORCELAIN_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if " -> " in name:  # переименование: показываем новое имя
            name = name.split(" -> ", 1)[1]
        files.append(name.strip().strip('"'))
    return files


def check_git(library: Path, *, commit: bool, require_clean: bool = True, action: str = "изменение канона") -> bool:
    """Проверки ДО любой записи. Возвращает, была ли библиотека чистой на входе (тогда при сбое
    её можно откатить к HEAD, 2.6)."""
    if not gitops.is_repo(library):
        return False
    in_progress = gitops.in_progress(library)
    if in_progress:
        raise RuntimeError(
            f"в библиотеке незавершённая операция git ({in_progress}) — в документах могут быть маркеры "
            "конфликта «<<<<<<<»; завершите или отмените её (`git revert --abort` / `git merge --abort`), затем повторите."
        )
    clean = not gitops.dirty(library)
    if commit and require_clean and not clean:
        raise RuntimeError(
            f"{UNCOMMITTED_TEXT} — {action} требует чистого git (защита от двойного применения). "
            "Закоммитьте их (`konveyer канон-коммит`) или откатите (`git restore .`), затем повторите."
        )
    if commit and not gitops.has_identity(library):
        raise RuntimeError(
            "git не настроен: задайте user.name/user.email в библиотеке "
            "(git config user.email …) — иначе коммит сорвётся после записи в канон."
        )
    return clean


def _rollback(library: Path, ws: Workspace, error: BaseException) -> None:
    """Сбой после открытия сессии записи (2.6): библиотека — к HEAD, выгрузки — пересчитать."""
    try:
        if gitops.has_commits(library):
            gitops.restore_library(library)
    except RuntimeError as e:
        raise RuntimeError(
            f"изменение канона сорвалось ({error}), и откат библиотеки не удался: {e}. "
            f"Восстановите вручную: git -C «{library}» checkout -- . && git clean -fd -- ."
        ) from error
    try:
        exporter.run_export(library, ws.exports, ws.logs, ws.volume, ws.root)
    except Exception:  # noqa: BLE001 — выгрузки пересчитает `konveyer экспорт`; важнее показать исходную ошибку
        pass


def canon_change(
    ws: Workspace,
    cfg: Config,
    library: Path,
    writer: Callable[[], None],
    message: str,
    *,
    commit: bool,
    author_confirmed: bool,
    require_clean: bool = True,
    action: str = "изменение канона",
    confirm: Callable[[ChangeResult], bool] | None = None,
    require_docs: bool = True,
) -> ChangeResult:
    """Единственный путь записи в библиотеку канона.

    * `writer` выполняется внутри `guard.canon_write_session()` и пишет через `guard.write_text`/`append_text`;
      `writer=lambda: None` — изменения уже на диске (автор правил документы сам, `konveyer канон-коммит`).
    * затем экспорт (валидация Д-1) и линт по свежим выгрузкам (сбой линтера — находка ЛИНТ-0, не откат);
    * `commit=True` — `git commit` всех изменений папки библиотеки с авторством `cfg.commit_author` (Д-8);
      `confirm(result)` — последняя возможность отказаться от коммита уже после линта (вопрос автору);
      `commit=False` — изменения остаются на диске, результат несёт `uncommitted=True`;
    * исключение в `writer`/экспорте откатывает библиотеку к HEAD (2.6) — только если она под git
      и была чистой на входе (иначе откат стёр бы правки автора, сделанные до вызова).
    * `require_clean=False` — коммит поверх незакоммиченных правок автора допустим (сценарий Б).
    """
    if not author_confirmed:
        raise PermissionError("изменение канона без подтверждения автора запрещено (FR-K2, Д-8).")
    repo = gitops.is_repo(library)
    clean_at_entry = check_git(library, commit=commit, require_clean=require_clean, action=action)
    try:
        with guard.canon_write_session():
            writer()
        hashes = exporter.run_export(library, ws.exports, ws.logs, ws.volume, ws.root,
                                     require_docs=require_docs)  # разбирает ВСЁ до записи выгрузок
    except BaseException as e:
        if repo and clean_at_entry:
            _rollback(library, ws, e)
        raise
    try:
        report = lint.run_lint(library, ws.exports, ws.logs, export=False, volume=ws.volume, root=ws.root, use_cache=False)  # выгрузки только что пересобраны
    except Exception as e:  # noqa: BLE001 — сбой проверки не должен потерять уже записанное изменение
        report = lint.error_report(e, ws.logs)
    result = ChangeResult(export_hashes=hashes, lint=report)
    if not repo:
        result.uncommitted = False
        result.message = "библиотека не под git — коммит пропущен, настройте git (git init в библиотеке)!"
        return result
    if commit and (confirm is None or confirm(result)):
        result.commit = gitops.commit_all(library, message, author=cfg.commit_author)
        if result.commit is None:
            result.message = "изменений в каноне нет — коммитить нечего."
        else:
            result.message = f"канон закоммичен: {result.commit}"
        return result
    result.dirty_files = dirty_files(library)
    result.uncommitted = bool(result.dirty_files)
    result.message = (
        f"{UNCOMMITTED_TEXT} ({len(result.dirty_files)} файл(ов)) — закоммитьте их (`konveyer канон-коммит` или "
        "кнопка «Закоммитить канон» в панели)." if result.uncommitted else "изменений в каноне нет."
    )
    return result
