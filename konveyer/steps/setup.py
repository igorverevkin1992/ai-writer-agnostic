"""Настройка: init — каркас рабочей области (NFR-3), `demo` — демо-библиотека и золотые тесты."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

import yaml

from .. import guard, manifest as manifest_mod
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

CONTRADICTIONS_FILE = "противоречия.yaml"


def demo_root() -> Path:
    """Папка демо-проекта в пакете: библиотека, манифест, регрессионный корпус, список противоречий."""
    return Path(str(resources.files("konveyer").joinpath("data/демо")))


def demo_contradictions(root: Path | None = None) -> list[dict]:
    """Заведомые противоречия демо-канона (NFR-9): список записей `{код, что, файл, было?, стало, манифест?, следствия?}`
    из `противоречия.yaml` демо — единый источник для `init --демо --противоречия` и тестов линтера."""
    path = (root or demo_root()) / CONTRADICTIONS_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: ожидался список противоречий")
    return [dict(item) for item in data]


def apply_contradiction(root: Path, library: Path, item: dict) -> Path:
    """Вносит одно противоречие в библиотеку `library` (замена первого вхождения «было» → «стало»; без «было» —
    файл создаётся целиком, а запись «манифест» добавляется в проект.yaml). Возвращает изменённый документ."""
    path = library / item["файл"]
    old = item.get("было")
    if old is None:
        text = str(item["стало"])
    else:
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"{item['код']}: в {item['файл']} нет ожидаемого фрагмента «{old[:40]}»")
        text = text.replace(old, str(item["стало"]), 1)
    guard.write_text(path, text)
    entry = item.get("манифест")
    if entry:
        man = manifest_mod.load(root)
        if man is not None and man.entry_for(item["файл"]) is None:
            man.библиотека.append(manifest_mod.LibraryEntry(файл=item["файл"], тип=entry["тип"], том=entry.get("том")))
            manifest_mod.save(root, man)
    return path


def apply_contradictions(root: Path, library: Path, items: list[dict] | None = None) -> list[str]:
    """Вносит все противоречия демо по порядку списка; возвращает коды внесённых."""
    codes: list[str] = []
    for item in (items if items is not None else demo_contradictions()):
        apply_contradiction(root, library, item)
        codes.append(str(item["код"]))
    return codes


def init(demo: bool = False, contradictions: bool = False) -> Workspace:
    """Создать каркас рабочей области: конфиг.yaml, .env.example, .gitignore, папки (NFR-1). Возвращает рабочую область.
    `demo` — развернуть демо-проект (библиотека, манифест, золотые тесты); `contradictions` — внести в него
    заведомые противоречия для знакомства с линтером (NFR-9)."""
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
        src = demo_root()
        fresh = not (ws.root / "Библиотека").exists()
        if fresh:
            shutil.copytree(src / "Библиотека", ws.root / "Библиотека")
        if not manifest_mod.path_of(ws.root).exists():
            shutil.copyfile(src / manifest_mod.path_of(src).name, manifest_mod.path_of(ws.root))
        for f in (src / "регрессия").glob("*.json"):
            target = ws.regression / "золотые" / f.name
            if not target.exists():
                shutil.copyfile(f, target)
        if contradictions and fresh:
            codes = apply_contradictions(ws.root, ws.root / "Библиотека")
            secho(f"В демо-канон внесено противоречий: {len(codes)} ({', '.join(codes)}).", fg=colors.YELLOW)
            echo("Найдите их: `konveyer export` → `konveyer линтер`; чистый демо-канон даёт ноль находок.")
        elif contradictions:
            echo("Библиотека уже есть — противоречия не вносились (они только для свежей копии демо).")
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
