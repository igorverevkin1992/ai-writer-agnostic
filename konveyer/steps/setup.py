"""Настройка: init — каркас рабочей области (NFR-1), `demo` — демо-библиотека и золотые тесты."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..paths import Workspace
from .common import colors, echo, secho


def init(demo: bool = False) -> Workspace:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, папки (FR-LC-3). Возвращает рабочую область."""
    ws = Workspace(Path.cwd())
    if not (ws.root / "конфиг.yaml").exists():
        shutil.copyfile(Path(__file__).parent.parent / "data" / "конфиг.пример.yaml", ws.root / "конфиг.yaml")
    for d in (ws.exports, ws.chapters, ws.logs, ws.templates, ws.regression / "золотые"):
        d.mkdir(parents=True, exist_ok=True)
    env_example = ws.root / ".env.example"
    if not env_example.exists():
        env_example.write_text("GEMINI_API_KEY=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
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
            "Демо развёрнуто. Попробуйте: `konveyer export` → `konveyer compile 1` → `konveyer status` → `konveyer regress`.",
            fg=colors.GREEN,
        )
        echo("Ключи API не обязательны: без них каждый шаг подскажет ручной режим (NFR-3).")
        return ws
    secho("Рабочая область готова. Заполните конфиг.yaml и .env (Д-9), положите Библиотека/.", fg=colors.GREEN)
    echo("Хотите пощупать конвейер на примере — `konveyer init --демо`. Диагностика: `konveyer doctor`.")
    return ws


def project_create(
    path: str | None = None, name: str | None = None, volumes: int | None = None, modules: str | None = None,
    methodic: str | None = None, profile: str | None = None, starter: bool = True, git: bool = True,
    yes: bool = False, prompt=None,
):
    """Мастер создания проекта (этап 1, сценарии Б и Д). Ответы можно передать флагами; остальное спрашивается
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
    echo("Дальше: заполните каркасы (строки «⚠ заполнить»), затем в папке проекта — `konveyer doctor` и `konveyer export`.")
    return created
