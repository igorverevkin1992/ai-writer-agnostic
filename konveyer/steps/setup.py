"""Настройка: init — каркас рабочей области (NFR-3), `demo` — демо-библиотека и золотые тесты."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import guard
from ..paths import Workspace
from .common import cmd, colors, echo, secho


# что не должно попадать в git рабочей области: ключи (FR-AD-6, Д-18) и производные артефакты
GITIGNORE_ENTRIES = (".env", "выгрузки/", "журналы/", "архивы/")


def ensure_gitignore(root: Path) -> list[str]:
    """Создаёт .gitignore или дописывает недостающие строки (`.env` — обязательно: ключ не должен уехать в
    репозиторий). Возвращает добавленные строки."""
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    present = {ln.strip() for ln in existing}
    added = [e for e in GITIGNORE_ENTRIES if e not in present and e.rstrip("/") not in present]
    if added:
        text = "\n".join(existing).rstrip("\n")
        text = (text + "\n" if text else "") + "\n".join(added) + "\n"
        guard.write_text(path, text)
    return added


def init(demo: bool = False) -> Workspace:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, .gitignore, папки (NFR-3). Возвращает рабочую область."""
    ws = Workspace(Path.cwd())
    if not (ws.root / "конфиг.yaml").exists():
        shutil.copyfile(Path(__file__).parent.parent / "data" / "конфиг.пример.yaml", ws.root / "конфиг.yaml")
    for d in (ws.exports, ws.chapters, ws.logs, ws.templates, ws.regression / "золотые"):
        d.mkdir(parents=True, exist_ok=True)
    env_example = ws.root / ".env.example"
    if not env_example.exists():
        env_example.write_text("GEMINI_API_KEY=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    ensure_gitignore(ws.root)
    if demo:
        from importlib import resources

        demo_root = Path(str(resources.files("konveyer").joinpath("data/демо")))
        if not (ws.root / "Библиотека").exists():
            shutil.copytree(demo_root / "Библиотека", ws.root / "Библиотека")
        for f in (demo_root / "регрессия").glob("*.json"):
            target = ws.regression / "золотые" / f.name
            if not target.exists():
                shutil.copyfile(f, target)
        secho(
            f"Демо развёрнуто. Попробуйте: `{cmd('export')}` → `{cmd('compile', 1)}` → `{cmd('status')}` → `{cmd('regress')}`.",
            fg=colors.GREEN,
        )
        echo("Ключи API не обязательны: без них каждый шаг подскажет ручной режим.")
        return ws
    secho("Рабочая область готова. Заполните конфиг.yaml и .env (ключи — только там, .env в .gitignore), положите Библиотека/.", fg=colors.GREEN)
    echo(f"Хотите пощупать конвейер на примере — `{cmd('init', '--демо')}`. Диагностика: `{cmd('doctor')}`.")
    return ws


def project_create(
    path: str | None = None, name: str | None = None, volumes: int | None = None, modules: str | None = None,
    methodic: str | None = None, profile: str | None = None, starter: bool = True, git: bool = True,
    yes: bool = False, prompt=None,
):
    """Мастер создания проекта (FR-LC-1). Ответы можно передать флагами; остальное спрашивается
    (`prompt(вопрос, умолчание) -> str`), а с `yes` берутся умолчания."""
    from .. import catalog, project as project_mod

    def ask(question: str, default: str) -> str:
        if yes or prompt is None:
            return default
        answer = prompt(question, default)
        return (answer or default).strip()

    all_modules = catalog.load_modules(None)
    optional = sorted(m for m, s in all_modules.items() if not s.base)
    path = path or ask("Папка проекта", "серия")
    name = name or ask("Название серии", Path(path).name)
    volumes = int(volumes or ask("Сколько томов в плане", "1"))
    if modules is None:
        modules = ask(f"Модули через запятую (доступны: {', '.join(optional)})", ", ".join(project_mod.DEFAULT_MODULES))
    mods = tuple(m.strip() for m in modules.split(",") if m.strip())
    if "драматургия" in mods and methodic is None:
        methodic = ask("Методика драматургии", project_mod.DEFAULT_METHODIC)
    if profile is None:
        avail = project_mod.available_profiles()
        profile = ask(f"Профиль серии (пусто — нейтральный комплект; доступны: {', '.join(avail) or '—'})", "")
    spec = project_mod.ProjectSpec(root=Path(path).resolve(), name=name, volumes=volumes, modules=mods,
                                   methodic=methodic or project_mod.DEFAULT_METHODIC, profile=profile or "",
                                   starter=starter, git=git)
    created = project_mod.create(spec)
    secho(f"Проект «{name}» создан: {created.root}", fg=colors.GREEN)
    echo(f"  манифест: {created.manifest.name}; библиотека: {created.library.relative_to(created.root)}/ "
         f"({len(created.documents)} документов стартового комплекта)")
    if created.commit:
        echo(f"  git библиотеки: первый коммит {created.commit[:7]}")
    for n in created.notes:
        secho(f"  ~ {n}", fg=colors.YELLOW)
    echo(f"Дальше: заполните каркасы (строки «⚠ заполнить»), затем в папке проекта — `{cmd('doctor')}` и `{cmd('export')}`.")
    return created


def project_index(yes: bool = False, confirm=None, commit: bool = True) -> Path | None:
    """`konveyer проект индекс`: пересобрать индекс библиотеки из манифеста (FR-DM-3) — единственный способ его
    править; запись идёт через `canon_change` (экспорт, линтер, коммит по подтверждению)."""
    from .. import canonchange, guard, project as project_mod
    from .common import _ctx, confirm_or_reject

    ws, cfg, lib = _ctx()
    path = project_mod.index_outdated(ws.root, lib)
    if path is None:
        echo("Индекс библиотеки актуален — пересобирать нечего.")
        return None
    confirm_or_reject(yes, confirm, f"Пересобрать индекс библиотеки {path.name} из манифеста?")

    def writer() -> None:
        _, text = project_mod.index_text(ws.root, lib)
        guard.write_text(path, text)

    canonchange.canon_change(ws, cfg, lib, writer, "[проект] индекс библиотеки из манифеста", commit=commit,
                             author_confirmed=True, require_clean=False, action="индекс библиотеки", require_docs=False)
    secho(f"Индекс библиотеки пересобран: {path.name}", fg=colors.GREEN)
    return path
