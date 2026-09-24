"""Сохранность (FR-BK-1…FR-BK-4): раскладка библиотеки относительно git, архив рабочей
области, переезд библиотеки в отдельный репозиторий.

Ничего здесь не пишет в канон: архив только читает рабочую область, переезд переносит папку
библиотеки целиком (содержимое документов не меняется) и правит конфиг.yaml/.gitignore
рабочей области. Все решения «где хранить» — за автором.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import gitops, guard
from .config import Config, library_dir
from .paths import Workspace

# Состав архива рабочей области (FR-BK-2; П-7/NFR-5 — ничто не теряется): манифест и конфиг, артефакты такта
# (главы/, журналы/), круги истории, снапшоты, рукопись, регрессионный корпус, пакеты пере-теста, сырьё и решения
# онбординга, переопределения проекта (промпты, шаблоны, типы, модули, методики, языки, линтер).
# НЕ входят: выгрузки/ (производные от канона — пересчитываются `konveyer экспорт`), архивы/, .env (секреты, Д-18).
# Библиотека под git хранится своими копиями (remotes); библиотека НЕ под git добавляется в архив (`LIBRARY_IN_ARCHIVE`).
ARCHIVE_ITEMS = (
    "проект.yaml", "конфиг.yaml", ".env.example",
    "главы", "журналы", "драматургия", "снапшоты", "рукопись", "регрессия", "пере-тест",
    "онбординг", "сырьё", "промпты", "шаблоны", "типы", "модули", "методики", "языки", "линтер",
)
LIBRARY_IN_ARCHIVE = "библиотека"   # папка библиотеки в архиве, если она лежит вне рабочей области
ARCHIVE_PREFIX = "рабочая_область_"
DEFAULT_ARCHIVE_DIR = "../архивы"
DEFAULT_SPLIT_TARGET = "../Библиотека"


def volumes_present(ws: Workspace) -> list[int]:
    """Тома, у которых есть папки глав: `главы/` — том 1, `главы/ТN/` — том N."""
    vols: set[int] = set()
    if ws.chapters.exists():
        if any(d.is_dir() and d.name.isdigit() for d in ws.chapters.iterdir()):
            vols.add(1)
        for d in ws.chapters.iterdir():
            if d.is_dir() and d.name.startswith("Т") and d.name[1:].isdigit():
                vols.add(int(d.name[1:]))
    return sorted(vols)


# ------------------------------------------------------------------ раскладка библиотеки (FR-BK-4)


@dataclass(frozen=True)
class Layout:
    """Где библиотека относительно git.

    kind: «own» — корень собственного репозитория (норма); «shared» — подпапка чужого репозитория
    (репозитория кода конвейера или рабочей области автора); «no-git» — не под git; «missing» — папки нет."""

    kind: str
    toplevel: Path | None = None
    prefix: str = ""
    code_repo: bool = False

    @property
    def ok(self) -> bool | None:
        return {"own": True, "shared": None, "no-git": False, "missing": False}[self.kind]

    @property
    def label(self) -> str:
        if self.kind == "own":
            return "библиотека — корень собственного репозитория"
        if self.kind == "shared":
            where = "репозитория кода" if self.code_repo else "чужого репозитория"
            return f"библиотека внутри {where} ({self.toplevel})"
        if self.kind == "no-git":
            return "библиотека не под git"
        return "библиотеки нет"

    @property
    def hint(self) -> str:
        if self.kind == "shared":
            if self.code_repo:
                return (
                    "коммиты канона перемешаны с коммитами кода: `git pull` обновления конвейера принесёт конфликты "
                    "с каноническими коммитами автора, а откат кода откатит и канон. "
                    "Переезд: `konveyer библиотека-отделить --показать` (план), затем `konveyer библиотека-отделить` "
                    "(латинский синоним — library-split)."
                )
            return (
                "коммиты канона перемешаны с другими файлами репозитория; откат и бэкап канона теряют точность. "
                "Переезд: `konveyer библиотека-отделить --показать`, затем `konveyer библиотека-отделить`."
            )
        if self.kind == "no-git":
            return "git init внутри библиотеки (версионирование канона) или `konveyer библиотека-отделить` — отдельный репозиторий"
        return ""


def _is_code_repo(top: Path) -> bool:
    return (top / "pyproject.toml").exists() and (top / "konveyer").is_dir()


def layout(lib: Path, ws_root: Path | None = None) -> Layout:
    if not lib.exists():
        return Layout("missing")
    if not gitops.is_repo(lib):
        return Layout("no-git")
    prefix = gitops.prefix(lib)
    top = gitops.toplevel(lib)
    if not prefix:
        return Layout("own", top, "")
    return Layout("shared", top, prefix, code_repo=bool(top and _is_code_repo(top)))


# ------------------------------------------------------------------ архив рабочей области (FR-BK-2)


def archive_dir(ws: Workspace, cfg: Config, explicit: str | Path | None = None) -> Path:
    raw = explicit or cfg.backup_dir or DEFAULT_ARCHIVE_DIR
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else (ws.root / p)).resolve()


def list_archives(dest: Path) -> list[Path]:
    if not dest.is_dir():
        return []
    return sorted(p for p in dest.glob(f"{ARCHIVE_PREFIX}*.zip") if p.is_file())


def latest_archive(dest: Path) -> Path | None:
    items = list_archives(dest)
    return items[-1] if items else None


def archive_age_days(dest: Path) -> float | None:
    last = latest_archive(dest)
    if last is None:
        return None
    return (time.time() - last.stat().st_mtime) / 86400


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.endswith(".tmp") and ".git" not in path.relative_to(root).parts:
            yield path


def archive_items(ws: Workspace, cfg: Config) -> list[tuple[Path, str]]:
    """Что попадёт в архив: [(путь на диске, путь в архиве)] — перечисленные папки/файлы рабочей области и
    библиотека, если она не под git (иначе у канона нет ни одной копии, FR-BK-1)."""
    out: list[tuple[Path, str]] = []
    for item in ARCHIVE_ITEMS:
        src = ws.root / item
        if src.is_file():
            out.append((src, item))
        elif src.is_dir():
            out += [(f, f.relative_to(ws.root).as_posix()) for f in _iter_files(src)]
    lib = library_dir(ws, cfg)
    if lib.is_dir() and not gitops.is_repo(lib):
        try:
            prefix = lib.resolve().relative_to(ws.root.resolve()).as_posix()
        except ValueError:
            prefix = LIBRARY_IN_ARCHIVE
        out += [(f, f"{prefix}/{f.relative_to(lib).as_posix()}") for f in _iter_files(lib)]
    return out


def make_archive(ws: Workspace, cfg: Config, dest: Path | None = None, *, keep: int | None = None) -> tuple[Path, list[Path]]:
    """Zip рабочей области (`archive_items`) в dest с датой в имени; хранит последние `keep`.
    Возвращает (путь архива, удалённые старые архивы). Архив собирается во временный файл и
    подменяется атомарно — полуархива на диске не остаётся."""
    dest = dest or archive_dir(ws, cfg)
    keep = cfg.backup_keep if keep is None else keep
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final = dest / f"{ARCHIVE_PREFIX}{stamp}.zip"
    n = 2
    while final.exists():  # два архива в одну секунду: суффикс «_2» сортируется ПОСЛЕ «.zip» («-2» стоял бы раньше)
        final = dest / f"{ARCHIVE_PREFIX}{stamp}_{n}.zip"
        n += 1
    tmp = final.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src, name in archive_items(ws, cfg):
                zf.write(src, name)
        guard.replace(tmp, final)
    except BaseException:
        guard.remove(tmp)
        raise
    return final, rotate(dest, keep)


def rotate(dest: Path, keep: int) -> list[Path]:
    """Оставляет `keep` самых свежих архивов (по имени = по дате, суффикс `_N` для одной секунды сортируется
    после основного имени); keep ≤ 0 — не удалять."""
    if keep <= 0:
        return []
    items = list_archives(dest)
    removed = items[:-keep] if len(items) > keep else []
    for p in removed:
        guard.remove(p)
    return removed


# ------------------------------------------------------------------ переезд библиотеки (FR-BK-4)


class SplitError(RuntimeError):
    pass


@dataclass
class SplitPlan:
    lib: Path
    target: Path
    layout: Layout
    with_history: bool
    library_dir_value: str          # что запишем в конфиг.yaml
    gitignore_entry: str | None     # строка для .gitignore репозитория, откуда уезжает библиотека
    fixed_chapters: list[tuple[int, int]] = field(default_factory=list)  # (том, глава) «зафиксировано» — их SHA приёмки сменятся

    def lines(self) -> list[str]:
        out = [
            f"1. Перенести {self.lib} → {self.target}"
            + (" с историей папки (git subtree split)" if self.with_history else " (без истории git)"),
            f"2. В {self.target}: git init" + (" и ветка из перенесённой истории" if self.with_history else
                                              " + первый коммит «Библиотека канона: отдельный репозиторий»"),
            f"3. конфиг.yaml: library_dir: \"{self.library_dir_value}\"",
        ]
        if self.gitignore_entry:
            out.append(f"4. .gitignore в {self.layout.toplevel}: добавить «{self.gitignore_entry}»")
        else:
            out.append("4. .gitignore: без изменений (библиотека была не под git)")
        if self.fixed_chapters:
            chapters = ", ".join(f"т.{v} гл. {c}" for v, c in self.fixed_chapters)
            out.append(
                f"5. состояние.yaml глав ({chapters}): SHA коммита приёмки "
                + ("будет найден заново по сообщению «[глава N]» в новой истории"
                   if self.with_history else
                   "будет снят (старая история остаётся в прежнем репозитории; откат этих глав git-revert'ом "
                   "после переезда невозможен — либо `--с-историей`, либо считайте их закрытыми)")
            )
        return out


def _rel_for_config(target: Path, root: Path) -> str:
    try:
        rel = os.path.relpath(target, root)
    except ValueError:  # Windows: другой диск
        return target.as_posix()
    return Path(rel).as_posix()


def _fixed_chapters(ws: Workspace) -> list[tuple[int, int]]:
    """(том, глава) для всех томов рабочей области, у которых записан SHA коммита приёмки."""
    out = []
    for v in volumes_present(ws):
        for n, d in ws.chapter_dirs(v):
            status = d / "состояние.yaml"
            if status.exists() and "коммит_приёмки" in status.read_text(encoding="utf-8"):
                out.append((v, n))
    return out


def plan_split(ws: Workspace, cfg: Config, lib: Path, target: Path | None = None, *, with_history: bool = False) -> SplitPlan:
    """Проверки ДО любых действий: план либо строится целиком, либо SplitError."""
    lay = layout(lib, ws.root)
    if lay.kind == "missing":
        raise SplitError(f"библиотеки нет: {lib}")
    if lay.kind == "own":
        raise SplitError(f"библиотека уже корень собственного репозитория ({lib}) — переезд не нужен.")
    target = (target or (ws.root / DEFAULT_SPLIT_TARGET)).resolve()
    if target.exists():
        raise SplitError(f"папка назначения уже существует: {target} — укажите другую (`--в`).")
    lib_resolved = lib.resolve()
    if target == lib_resolved or lib_resolved in target.parents:
        raise SplitError("папка назначения не может лежать внутри самой библиотеки.")
    if lay.kind == "shared":
        if gitops.dirty(lib):
            raise SplitError("в библиотеке незакоммиченные изменения — сначала `konveyer canon-commit`, затем переезд.")
        if gitops.in_progress(lib):
            raise SplitError(f"в репозитории незавершённая операция git ({gitops.in_progress(lib)}).")
    if with_history:
        if lay.kind != "shared":
            raise SplitError("`--с-историей` возможен только для библиотеки внутри репозитория (иначе истории нет).")
        if not gitops.has_subtree(lib):
            raise SplitError("`git subtree` недоступен в этой установке git — переезд без истории или установите git-subtree.")
    if not (gitops.identity_available(ws.root) or cfg.commit_author):
        raise SplitError("нет авторства git для первого коммита: git config --global user.email/user.name или commit_author в конфиг.yaml (Д-8).")
    entry = None
    if lay.kind == "shared" and lay.toplevel:
        entry = "/" + lay.prefix.strip("/") + "/"
    return SplitPlan(lib_resolved, target, lay, with_history, _rel_for_config(target, ws.root), entry, _fixed_chapters(ws))


def _update_config_library_dir(ws: Workspace, value: str) -> None:
    path = ws.root / "конфиг.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f'library_dir: "{value}"'
    if re.search(r"^library_dir:.*$", text, flags=re.M):
        text = re.sub(r"^library_dir:.*$", line, text, count=1, flags=re.M)
    else:
        text = line + "\n" + text
    guard.write_text(path, text)


def _append_gitignore(top: Path, entry: str) -> bool:
    path = top / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(ln.strip() in (entry, entry.rstrip("/"), entry.lstrip("/"), entry.strip("/")) for ln in text.splitlines()):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"# библиотека канона вынесена в отдельный репозиторий (konveyer library-split)\n{entry}\n"
    guard.write_text(path, text)
    return True


def _remap_fixed_chapters(ws: Workspace, plan: SplitPlan) -> list[str]:
    from .fsm import ChapterState

    notes = []
    for v, n in plan.fixed_chapters:
        st = ChapterState(ws.for_volume(v), n)
        old = st.data.get("коммит_приёмки")
        new = gitops.find_chapter_commit(plan.target, n) if plan.with_history else None
        where = f"т.{v} гл. {n}"
        if new:
            st.data["коммит_приёмки"] = new
            notes.append(f"{where}: коммит приёмки {str(old)[:10]} → {new[:10]}")
        else:
            st.data.pop("коммит_приёмки", None)
            st.data["коммит_приёмки_до_переезда"] = old
            notes.append(f"{where}: коммит приёмки {str(old)[:10]} остался в прежней истории — снят")
        st._save()
    return notes


def split_library(ws: Workspace, cfg: Config, plan: SplitPlan) -> list[str]:
    """Выполняет план. Порядок такой, чтобы сбой на любом шаге оставлял понятное состояние:
    сначала новый репозиторий, потом конфиг.yaml (с этого момента конвейер смотрит на новое место)."""
    notes: list[str] = []
    lib, target = plan.lib, plan.target
    branch = (gitops.current_branch(lib) if plan.layout.kind == "shared" else "") or "main"
    if plan.with_history:
        assert plan.layout.toplevel is not None
        tmp_branch = f"библиотека-переезд-{int(time.time())}"
        gitops.subtree_split(plan.layout.toplevel, plan.layout.prefix, tmp_branch)
        try:
            target.mkdir(parents=True)
            gitops.init_repo(target)
            gitops.copy_identity(lib, target)
            gitops.fetch_branch(target, plan.layout.toplevel, tmp_branch, branch)
        finally:
            gitops.delete_branch(plan.layout.toplevel, tmp_branch)
        shutil.rmtree(lib)  # содержимое уже в новом репозитории (проверено: библиотека была чистой)
        notes.append(f"история папки перенесена: {gitops.head(target)[:10]} (ветка {branch})")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        old_repo = plan.layout.toplevel if plan.layout.kind == "shared" else None
        shutil.move(str(lib), str(target))
        gitops.init_repo(target, initial_branch=branch)
        if old_repo is not None:
            gitops.copy_identity(old_repo, target)
        commit = gitops.commit_all(target, "Библиотека канона: отдельный репозиторий (konveyer library-split)", author=cfg.commit_author)
        notes.append(f"новый репозиторий: {target} (первый коммит {str(commit)[:10]}, ветка {branch})")
    guard.set_library_dir(target)
    _update_config_library_dir(ws, plan.library_dir_value)
    notes.append(f"конфиг.yaml: library_dir: \"{plan.library_dir_value}\"")
    if plan.gitignore_entry and plan.layout.toplevel:
        if _append_gitignore(plan.layout.toplevel, plan.gitignore_entry):
            notes.append(f".gitignore ({plan.layout.toplevel}): добавлено «{plan.gitignore_entry}»")
    notes += _remap_fixed_chapters(ws, plan)
    return notes


def after_split_advice(plan: SplitPlan) -> list[str]:
    out = []
    if plan.layout.kind == "shared" and plan.layout.toplevel:
        out.append(
            f"В репозитории {plan.layout.toplevel} папка библиотеки теперь удалена — закоммитьте это: "
            f"`git add -A -- \"{plan.layout.prefix.rstrip('/')}\" .gitignore && git commit -m \"Библиотека канона вынесена в отдельный репозиторий\"`."
        )
        out.append(
            "История канона до переезда остаётся в прежнем репозитории"
            + (" (и скопирована в новый через subtree split)." if plan.with_history else
               "; в новом репозитории история начинается с первого коммита. Перенести её позже: "
               "`git subtree split --prefix=<папка> -b библиотека` в прежнем репозитории и `git pull <прежний> библиотека` в новом.")
        )
    out.append("Добавьте удалённые копии (FR-BK-1, ≥ 2): `konveyer бэкап --добавить-remote <имя> <url|папка>`; проверка — `konveyer доктор`.")
    return out
