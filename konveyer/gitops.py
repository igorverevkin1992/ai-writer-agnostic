"""Git-операции над библиотекой канона (через subprocess, Д-8, сценарии Б/Г)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(repo), *args], capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",  # Windows: вывод git всегда UTF-8, не ANSI-страница
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def is_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree", check=False) == "true"
    except FileNotFoundError:
        return False


def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def has_commits(repo: Path) -> bool:
    """Есть ли в репозитории хотя бы один коммит (свежий `git init` — нет; откатывать к HEAD нечего)."""
    return _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip() != ""


def prefix(repo: Path) -> str:
    """Путь папки внутри репозитория («Библиотека/»); пусто, если папка — корень репозитория."""
    return _git(repo, "rev-parse", "--show-prefix", check=False)


def commit_all(repo: Path, message: str, author: str | None = None) -> str | None:
    """Атомарный коммит изменений ТОЛЬКО внутри папки библиотеки (5.1, FR-K2). Авторство — автор (Д-8).
    Возвращает SHA коммита; None — если коммитить было нечего."""
    _git(repo, "add", "-A", "--", ".")
    if not dirty(repo):
        return None
    args = ["commit", "-m", message]
    if author:
        args += ["--author", author]
    _git(repo, *args)
    return head(repo)


def restore_library(repo: Path) -> None:
    """Откат незакоммиченных изменений ТОЛЬКО в папке библиотеки (сбой apply_batch, 2.6):
    отслеживаемые файлы — к HEAD, новые файлы и папки — удаляются; остальной репозиторий не трогается."""
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fd", "--", ".")


def check_norm_change_message(message: str) -> bool:
    """Сценарий Б: изменение норм — только со ссылкой Р-№ в сообщении (предупреждение)."""
    return bool(re.search(r"Р-\d+", message))


def find_chapter_commit(repo: Path, chapter: int) -> str | None:
    """Ищет ДЕЙСТВУЮЩИЙ коммит приёмки главы по шаблонному сообщению (FR-K2).
    Если самый свежий коммит по главе — её откат (`Revert "[глава N] …"`), приёмки нет (None):
    иначе повторное применение пакета «нашло бы» уже откачённую приёмку."""
    out = _git(repo, "log", "--format=%H %s", check=False)
    for line in out.splitlines():
        sha, _, subject = line.partition(" ")
        if re.match(rf'Revert "\[глава {chapter}\]', subject):
            return None  # откат приёмки новее самой приёмки
        if subject.startswith("Revert "):
            continue
        if re.match(rf"\[глава {chapter}\]", subject):
            return sha
    return None


def in_progress(repo: Path) -> str | None:
    """Незавершённая операция git в репозитории библиотеки (revert/merge/cherry-pick/rebase):
    в документах могут быть маркеры конфликта — любая запись в канон до её завершения запрещена."""
    for marker, label in (("REVERT_HEAD", "revert"), ("MERGE_HEAD", "merge"),
                          ("CHERRY_PICK_HEAD", "cherry-pick"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase")):
        path = _git(repo, "rev-parse", "--git-path", marker, check=False)
        if path and (repo / path).exists():
            return label
    return None


def revert(repo: Path, commit: str, author: str | None = None) -> str:
    """Откат коммита приёмки; затрагивать можно только файлы библиотеки (иначе — отмена, ошибка).
    Любой сбой (конфликт, посторонние файлы, нечего откатывать, отказ коммита) снимает незавершённый
    revert: в документах канона не остаются маркеры `<<<<<<<`, индекс чист, REVERT_HEAD нет."""
    if in_progress(repo):
        raise RuntimeError(f"в библиотеке незавершённая операция git ({in_progress(repo)}) — завершите или отмените её (`git revert --abort`).")
    try:
        _git(repo, "revert", "--no-edit", "--no-commit", commit)
        touched = [p for p in _git(repo, "diff", "--cached", "--name-only", check=False).splitlines() if p]
        if not touched:
            raise RuntimeError(f"коммит {commit[:10]} уже откачен — нечего откатывать.")
        pfx = prefix(repo)
        outside = [p for p in touched if pfx and not p.startswith(pfx)]
        if outside:
            raise RuntimeError(
                f"коммит {commit[:10]} затрагивает файлы вне библиотеки ({', '.join(outside[:5])}) — "
                "откат отменён; разберите его вручную в git."
            )
        args = ["commit", "--no-edit", "-m", f'Revert "{_git(repo, "log", "-1", "--format=%s", commit, check=False)}"']
        if author:
            args += ["--author", author]  # авторство отката — автор (Д-8)
        _git(repo, *args)
    except BaseException:
        _git(repo, "revert", "--abort", check=False)
        _git(repo, "revert", "--quit", check=False)
        raise
    return head(repo)


def has_identity(repo: Path) -> bool:
    """Есть ли у git настроенное авторство (иначе коммит сорвётся)."""
    return bool(_git(repo, "config", "user.email", check=False).strip())


def push(repo: Path, remote: str) -> None:
    """Отправка текущей ветки в удалённое место (NFR-6, `konveyer backup --push`)."""
    _git(repo, "push", remote, "HEAD")


def remotes(repo: Path) -> list[str]:
    out = _git(repo, "remote", check=False)
    return [r for r in out.splitlines() if r.strip()]


def dirty(repo: Path) -> bool:
    """Есть ли незакоммиченные изменения в папке библиотеки (остальной репозиторий не учитывается)."""
    return bool(_git(repo, "status", "--porcelain", "--", ".", check=False))


def last_commit_age_days(repo: Path) -> float | None:
    out = _git(repo, "log", "-1", "--format=%ct", check=False)
    if not out:
        return None
    import time

    return (time.time() - int(out)) / 86400


# ------------------------------------------------------- сохранность (аудит 2, этап 5, п. 28)


def toplevel(repo: Path) -> Path | None:
    """Корень репозитория, в котором лежит папка; None — папка не под git."""
    out = _git(repo, "rev-parse", "--show-toplevel", check=False)
    return Path(out) if out else None


def tags(repo: Path, pattern: str | None = None) -> list[str]:
    args = ["tag", "--list"] + ([pattern] if pattern else [])
    return [t for t in _git(repo, *args, check=False).splitlines() if t.strip()]


def tag(repo: Path, name: str, sha: str) -> str:
    """Лёгкий тег на коммит (версии канона: `глава-N` при приёмке, `v1.N` релизов кода)."""
    _git(repo, "tag", name, sha)
    return name


def delete_tag(repo: Path, name: str) -> None:
    """Снять тег (перестановка `том-N` при `konveyer volume close N --заново`)."""
    _git(repo, "tag", "-d", name, check=False)


def tag_chapter(repo: Path, chapter: int, sha: str) -> str | None:
    """Тег приёмки главы: `глава-N`; повторная приёмка после отката — `глава-N-2`, `-3`…
    Идемпотентно: если на этот коммит тег главы уже стоит, возвращает его. Любой сбой git
    (нет прав, странный репозиторий) — None: тег не обязателен, приёмка уже закоммичена."""
    try:
        existing = tags(repo, f"глава-{chapter}") + tags(repo, f"глава-{chapter}-*")
        for t in existing:
            if _git(repo, "rev-list", "-n", "1", t, check=False) == sha:
                return t
        name = f"глава-{chapter}" if f"глава-{chapter}" not in existing else f"глава-{chapter}-{len(existing) + 1}"
        while name in existing:  # дыры в нумерации после ручного удаления тегов
            name += "-x"
        return tag(repo, name, sha)
    except (RuntimeError, FileNotFoundError):
        return None


def add_remote(repo: Path, name: str, url: str) -> None:
    _git(repo, "remote", "add", name, url)


def remote_url(repo: Path, name: str) -> str:
    return _git(repo, "remote", "get-url", name, check=False)


def init_bare(path: Path) -> Path:
    """Пустой bare-репозиторий (внешний диск как «удалённое место» без облака, §1.3 ТЗ)."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--bare", "-q")
    return path


def is_bare_repo(path: Path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-bare-repository", check=False) == "true"
    except FileNotFoundError:
        return False


def init_repo(path: Path, initial_branch: str | None = None) -> None:
    """`git init` в существующей папке (библиотека как собственный репозиторий)."""
    args = ["init", "-q"] + ([f"--initial-branch={initial_branch}"] if initial_branch else [])
    _git(path, *args)


def identity_available(path: Path) -> bool:
    """Есть ли авторство git, видимое из папки (локальное или глобальное); папка может быть не репозиторием."""
    try:
        return bool(_git(path, "config", "--get", "user.email", check=False).strip())
    except FileNotFoundError:
        return False


def subtree_split(repo: Path, prefix: str, branch: str) -> str:
    """История папки как отдельная цепочка коммитов (`git subtree split`) на временной ветке `branch`;
    возвращает SHA вершины. Ветка нужна: забрать из локального репозитория можно только достижимый коммит."""
    return _git(repo, "subtree", "split", "--prefix", prefix.rstrip("/"), "-b", branch)


def delete_branch(repo: Path, branch: str) -> None:
    _git(repo, "branch", "-D", branch, check=False)


def fetch_branch(repo: Path, source: Path, src_branch: str, branch: str) -> None:
    """Забрать ветку из другого локального репозитория и сделать её текущей веткой `branch`."""
    _git(repo, "fetch", "-q", str(source), src_branch)
    _git(repo, "checkout", "-q", "-b", branch, "FETCH_HEAD")


def copy_identity(src: Path, dst: Path) -> bool:
    """Локальное авторство git (user.name/user.email) из одного репозитория в другой, если у второго его нет
    (новый репозиторий библиотеки должен уметь коммитить там же, где умел прежний)."""
    if has_identity(dst):
        return False
    email = _git(src, "config", "--get", "user.email", check=False).strip()
    name = _git(src, "config", "--get", "user.name", check=False).strip()
    if not email:
        return False
    _git(dst, "config", "user.email", email)
    if name:
        _git(dst, "config", "user.name", name)
    return True


def current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)


def has_subtree(repo: Path) -> bool:
    """Доступна ли команда `git subtree` (contrib; есть в Git for Windows и большинстве дистрибутивов)."""
    try:
        result = subprocess.run(["git", "-C", str(repo), "subtree"], capture_output=True, text=True, check=False,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    return "not a git command" not in (result.stderr + result.stdout)
