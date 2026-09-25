"""Создание проекта (этап 1 жизненного цикла, FR-LC-*): папка проекта по раскладке §6.1, манифест `проект.yaml`,
стартовый комплект документов из каркасов каталога типов, git внутри библиотеки.

Стартовый комплект нейтрален: каркасы документов берутся из типов движка (`каркас:` в `типы/*.yaml`), а для типов
без каркаса строится пустая таблица по колонкам первого табличного формата. Профиль (`data/профили/<имя>` или
папка автора) добавляет свои типы, модули, методики и парсеры, а фрагментом `манифест.yaml` — служебные маски
файлов и прочие умолчания манифеста. Индекс библиотеки (FR-DM-3) создаётся вместе с проектом и пересобирается
командой `konveyer проект индекс`. Готовность проекта к первому такту проверяет `readiness()` (FR-LC-2, FR-ON-20) —
её же печатает `konveyer доктор`; туда же попадают ошибки манифеста (FR-MF-2) и сообщение о его миграции (FR-MF-4).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import catalog, gitops, manifest as manifest_mod
from .manifest import LibraryEntry, Manifest, Methodics, Passport
from .onboarding.apply import index_doc_path, render_index

# папки проекта по раскладке §6.1 (кроме библиотеки — она создаётся отдельно)
PROJECT_DIRS = ("сырьё", "онбординг", "главы", "выгрузки", "журналы", "регрессия/золотые", "рукопись", "архивы",
                "драматургия", "снапшоты", "шаблоны", "промпты")
# модули, включаемые по умолчанию в нейтральном комплекте (остальные — по выбору автора)
DEFAULT_MODULES = ("фокализация", "эпистемика", "информрежим", "закладки", "континуити")
DEFAULT_METHODIC = "круг_хармона"
# типы стартового комплекта, не зависящие от модулей (обязательные для такта — из каталога)
CORE_TYPES = ("стиль", "план_глав", "персонажи", "мир", "журнал_решений")
INDEX_TYPE = "индекс_библиотеки"   # обязательный документ любого проекта (FR-DM-3), генерируется из манифеста
JOURNAL_TYPE = "журнал_решений"    # обязательный документ любого проекта (FR-DM-3): первая запись — о приватности
PROFILE_DIRS = ("типы", "модули", "методики", "линтер", "промпты", "шаблоны")
PROFILE_MANIFEST = "манифест.yaml"  # фрагмент манифеста профиля: служебные маски, умолчания


@dataclass
class ProjectSpec:
    root: Path
    name: str
    volumes: int = 1
    modules: tuple[str, ...] = DEFAULT_MODULES
    methodic: str = DEFAULT_METHODIC          # методика драматургии (если включён модуль «драматургия»)
    profile: str = ""                         # имя профиля из data/профили или путь к папке профиля
    starter: bool = True                      # создавать стартовый комплект документов
    git: bool = True
    author: str | None = None                 # «Имя <email>» для коммита, если git без авторства


@dataclass
class CreatedProject:
    root: Path
    library: Path
    manifest: Path
    documents: list[Path] = field(default_factory=list)
    commit: str | None = None
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ каркасы документов


def skeleton_for(spec: catalog.TypeSpec, volume: int = 1) -> str:
    """Каркас пустого документа типа: из `каркас:` типа, иначе — заголовок и пустая таблица по колонкам первого
    табличного извлечения (заголовки таблицы — канонические имена колонок каталога)."""
    if spec.skeleton.strip():
        return spec.skeleton.replace("{том}", str(volume)).rstrip("\n") + "\n"
    title = _title(spec, volume)
    lines = [f"# {title}", "", f"<!-- {spec.purpose or spec.name}. Заполните таблицу; строки с «⚠ заполнить» машина не читает. -->", ""]
    for ext in spec.extractions:
        fmt = next((f for f in ext.get("форматы", []) if f.get("вид") == "таблица" and f.get("колонки")), None)
        if fmt is None:
            continue
        cols = list(fmt["колонки"].keys())
        lines += [f"## {ext.get('имя', 'таблица')}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols), ""]
    if len(lines) == 4:
        lines += ["⚠ заполнить", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _title(spec: catalog.TypeSpec, volume: int) -> str:
    """«NN_Имя_Том{том}.md» → «NN. Имя · Том 1»; без имени по умолчанию — имя типа."""
    if not spec.default_name:
        return spec.name
    stem = spec.default_name.split("/")[0].rsplit(".", 1)[0]
    m = re.match(r"^(\d+(?:\.\d+)?)_(.+)$", stem)
    num, rest = (m.group(1), m.group(2)) if m else ("", stem)
    rest = re.sub(r"_?Том\{том\}", f" · Том {volume}", rest).replace("_", " ").strip()
    return f"{num}. {rest}" if num else rest


def _doc_name(spec: catalog.TypeSpec, volume: int) -> str:
    name = spec.default_name or f"{spec.name}.md"
    if name.endswith("/"):
        return name + "Имя_персонажа.md" if spec.name == "персонажи" else name + f"{spec.name}.md"
    return name.replace("{том}", str(volume))


def starter_types(types: dict[str, catalog.TypeSpec], modules: dict[str, catalog.ModuleSpec],
                  enabled: set[str]) -> list[catalog.TypeSpec]:
    """Типы стартового комплекта: ядро + требуемые типы включённых модулей (без прозы и индекса — он генерируется)."""
    names: list[str] = [t for t in CORE_TYPES if t in types]
    for m in sorted(modules.values(), key=lambda m: m.name):
        if m.base or m.name in enabled:
            for t in m.requires_types:
                if t in types and t not in names:
                    names.append(t)
    return [types[n] for n in names if n not in ("проза", INDEX_TYPE)]


def _keyed_labels(spec: catalog.TypeSpec) -> dict[str, str]:
    """{поле: подпись «- Ключ:»} первого формата «секции_с_ключами» типа — чтобы заполнить каркас записи по полям."""
    for ext in spec.extractions:
        for fmt in ext.get("форматы") or []:
            if fmt.get("вид") == "секции_с_ключами" and fmt.get("ключи"):
                out = {}
                for field, ks in fmt["ключи"].items():
                    out[field] = (ks.get("ключ", field) if isinstance(ks, dict) else str(ks))
                return out
    return {}


def journal_first_entry(spec: catalog.TypeSpec, values: dict[str, str], volume: int = 1) -> str:
    """Каркас журнала решений с заполненной первой записью: значения подставляются в строки «- Ключ: ⚠ заполнить»
    по полям формата типа (дата, формулировка, обоснование); тип без такого формата — каркас как есть."""
    text = skeleton_for(spec, volume)
    from .declparse import PLACEHOLDER

    for name, value in values.items():
        label = _keyed_labels(spec).get(name)
        if not label:
            continue
        text = re.sub(rf"^(\s*-\s*{re.escape(label)}\s*:\s*){re.escape(PLACEHOLDER)}.*$", lambda m: m.group(1) + value,
                      text, count=1, flags=re.M)
    return text


# ------------------------------------------------------------------ профили


def profile_dir(name_or_path: str) -> Path | None:
    if not name_or_path:
        return None
    p = Path(name_or_path)
    if p.is_dir():
        return p
    bundled = Path(str(resources.files("konveyer").joinpath(f"data/профили/{name_or_path}")))
    return bundled if bundled.is_dir() else None


def available_profiles() -> list[str]:
    base = Path(str(resources.files("konveyer").joinpath("data/профили")))
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def _copy_profile(src: Path, root: Path, notes: list[str]) -> None:
    for sub in PROFILE_DIRS:
        folder = src / sub
        if folder.is_dir():
            shutil.copytree(folder, root / sub, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            notes.append(f"профиль: скопирована папка {sub}/")


def profile_manifest(prof: Path | None) -> dict:
    """Фрагмент манифеста профиля (`манифест.yaml`): `служебные` — маски файлов библиотеки вне карты (ТЗ, инструменты,
    тестовые промпты эталона); движок сам таких соглашений не знает (П-1)."""
    if prof is None or not (prof / PROFILE_MANIFEST).exists():
        return {}
    data = catalog.load_yaml(prof / PROFILE_MANIFEST) or {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ создание


def create(spec: ProjectSpec) -> CreatedProject:
    """Создаёт проект: папки, конфиг.yaml, проект.yaml, стартовый комплект, git библиотеки. Папка проекта
    должна быть пустой или не существовать (второй проект рядом — сценарий Д)."""
    root = spec.root
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"папка «{root}» не пуста — проект создаётся в пустой или новой папке")
    root.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    for d in PROJECT_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    library = root / "Библиотека"
    library.mkdir(exist_ok=True)

    prof = profile_dir(spec.profile)
    if spec.profile and prof is None:
        raise FileNotFoundError(f"профиль «{spec.profile}» не найден; доступные: {', '.join(available_profiles()) or '—'}")
    if prof is not None:
        _copy_profile(prof, root, notes)

    # конфиг.yaml и .env.example
    sample = Path(str(resources.files("konveyer").joinpath("data/конфиг.пример.yaml")))
    shutil.copyfile(sample, root / "конфиг.yaml")
    (root / ".env.example").write_text("GEMINI_API_KEY=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    # из версионирования исключается только производное и секреты: журналы такта и сырьё — артефакты (П-7, NFR-5)
    (root / ".gitignore").write_text(".env\nвыгрузки/\nархивы/\n", encoding="utf-8")

    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    unknown = [m for m in spec.modules if m not in modules]
    if unknown:
        raise ValueError(f"неизвестные модули: {', '.join(unknown)}; доступные: "
                         f"{', '.join(sorted(m for m in modules if not modules[m].base))}")
    enabled = {m for m in spec.modules if not modules[m].base}

    # стартовый комплект
    docs: list[Path] = []
    entries: list[LibraryEntry] = []
    if spec.starter:
        for t in starter_types(types, modules, enabled):
            volumes = range(1, spec.volumes + 1) if t.per_volume else [1]
            for v in volumes:
                name = _doc_name(t, v)
                path = library / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(skeleton_for(t, v), encoding="utf-8")
                docs.append(path)
            entry_name = t.default_name if t.default_name and t.default_name.endswith("/") else None
            if entry_name:
                entries.append(LibraryEntry(файл=entry_name, тип=t.name))
            else:
                for v in volumes:
                    entries.append(LibraryEntry(файл=_doc_name(t, v), тип=t.name, том=v if t.per_volume else None))
        prose = types.get("проза")
        prose_dir = (prose.default_name.rstrip("/") if prose and prose.default_name else "проза")
        (library / prose_dir).mkdir(exist_ok=True)
        entries.append(LibraryEntry(файл=prose_dir + "/", тип="проза"))
        journal_spec = types.get(JOURNAL_TYPE)
        journal = next((library / e.файл for e in entries if e.тип == JOURNAL_TYPE), None) if journal_spec else None
        if journal is not None and journal.is_file():
            from datetime import date

            from .config import load_config
            from .paths import Workspace

            cfg = load_config(Workspace(root))
            roles = "; ".join(f"{r} — {m.provider}/{m.model}" for r, m in cfg.roles().items())
            journal.write_text(journal_first_entry(journal_spec, {
                "дата": date.today().strftime("%d.%m.%Y"),
                "формулировка": "тексты серии уходят только в объявленные в конфиг.yaml API, провайдеры работают в "
                                f"режиме без обучения на данных автора (FR-SC-10). Роли: {roles}.",
                "обоснование": "приватность рукописи; смена провайдера или режима — новой записью журнала.",
            }), encoding="utf-8")

    # манифест
    methodics = Methodics()
    if "драматургия" in enabled:
        methodics = Methodics(том=spec.methodic, акт=spec.methodic, глава=spec.methodic)
    fragment = profile_manifest(prof)
    man = Manifest(
        проект=Passport(имя=spec.name, томов_план=max(1, spec.volumes), текущий_том=1,
                        профиль=spec.profile if prof is not None else ""),
        модули={m: ("вкл" if m in enabled else "выкл") for m in sorted(modules) if not modules[m].base},
        методики=methodics,
        библиотека=entries,
        служебные=[str(x) for x in (fragment.get("служебные") or [])],
    )
    # индекс библиотеки — обязательный документ (FR-DM-3), генерируется из манифеста и сам входит в карту
    if INDEX_TYPE in types:
        idx = index_doc_path(man, library, types)
        man.библиотека.insert(0, LibraryEntry(файл=idx.relative_to(library).as_posix(), тип=INDEX_TYPE))
        idx.write_text(render_index(man, types), encoding="utf-8")
        docs.insert(0, idx)
    manifest_path = manifest_mod.save(root, man)

    # git внутри библиотеки (§5.1): свой репозиторий канона
    commit = None
    if spec.git:
        try:
            gitops.init_repo(library, initial_branch="main")
            if spec.author or gitops.identity_available(library):
                commit = gitops.commit_all(library, f"[проект] стартовый комплект серии «{spec.name}»", spec.author)
            else:
                notes.append("git: авторство не настроено — первый коммит не сделан "
                             "(git config user.name/user.email или commit_author в конфиг.yaml, Д-8)")
        except (FileNotFoundError, RuntimeError, OSError) as e:  # git не установлен
            notes.append(f"git недоступен ({type(e).__name__}) — библиотека без версионирования; поставьте git")
    return CreatedProject(root=root, library=library, manifest=manifest_path, documents=docs, commit=commit, notes=notes)


# ------------------------------------------------------------------ готовность (FR-LC-2, FR-ON-20)


@dataclass
class Check:
    ok: bool | None            # True — есть, False — нет, None — неполно/предупреждение
    label: str
    hint: str = ""


def _collect(library: Path, root: Path, volume: int = 1):
    """Разбор канона тома до экспорта (проверка должна работать и без выгрузок); сломанная разметка — None."""
    from . import exporter

    try:
        return exporter.collect(library, volume, root)
    except Exception:  # noqa: BLE001 — об ошибке разметки скажут экспорт и линтер
        return None


def manifest_checks(root: Path, library: Path, types: dict[str, catalog.TypeSpec],
                    modules: dict[str, catalog.ModuleSpec]) -> list[Check]:
    """Манифест глазами доктора: миграция прежней версии схемы (FR-MF-4), ошибки схемы и карты (FR-MF-2)."""
    out: list[Check] = []
    mpath = manifest_mod.path_of(root)
    if not mpath.exists():
        return out
    try:
        if manifest_mod.schema_version(root) < manifest_mod.SCHEMA_VERSION:
            done, note = manifest_mod.migrate(root)
            if done:
                out.append(Check(None, f"проект.yaml: {note}", "проверьте манифест после миграции (`konveyer проект`)"))
        man = manifest_mod.load(root)
    except ValueError as e:
        return [*out, Check(False, str(e), "поправьте проект.yaml — до этого карта библиотеки не читается")]
    if man is None:
        return out
    from . import methodics as methodics_mod

    names = set(methodics_mod.load_all(root))
    for err in manifest_mod.validate(man, library, types, modules, mpath.read_text(encoding="utf-8"), methodics=names):
        out.append(Check(False, err, "поправьте проект.yaml (блоки проект/модули/методики/библиотека)"))
    return out


def readiness(root: Path, library: Path) -> list[Check]:
    """Минимальный комплект для первого такта (FR-LC-2) и укомплектованность модулей (FR-ON-20); проверки идут по
    текущему тому манифеста. Первыми — ошибки манифеста и сообщение о его миграции."""
    out: list[Check] = []
    if not library.is_dir():
        return [Check(False, "библиотеки нет", "`konveyer проект создать` или положите Библиотека/")]
    types = catalog.load_types(root)
    modules = catalog.load_modules(root)
    out += manifest_checks(root, library, types, modules)
    if any(c.ok is False for c in out):
        return out
    man = manifest_mod.effective(root, library, types)
    volume = man.проект.текущий_том

    # (а) стиль с нормой объёма главы
    style = man.docs(library, "стиль", None, types)
    if not style:
        out.append(Check(False, "документ стиля с нормами (FR-LC-2а)", "создайте документ типа «стиль» (каркас: `konveyer типы стиль`)"))
    else:
        col = _collect(library, root, volume)
        norms = (col.data.get("norms.json") or {}) if col else {}
        n = norms.get("объём_главы")
        has_norm = n is not None and (n.min is not None or n.max is not None)
        briefs = [b for b in (col.data.get("briefs.json") or [])] if col else []
        in_plan = bool(briefs) and all(getattr(b, "volume_words", None) for b in briefs)
        if has_norm or in_plan:
            out.append(Check(True, f"объём главы задан ({'норма стиля' if has_norm else 'в плане глав'})"))
        else:
            out.append(Check(None if col is None else False, f"нормы стиля: объём главы не задан ({style[0].name})",
                             "впишите в таблицу норм строку «объём_главы» с мин/макс (или объём в плане глав)"))
    # (б) план глав хотя бы с одной записью
    plans = man.docs(library, "план_глав", volume, types)
    if not plans:
        out.append(Check(False, f"план глав тома {volume} (FR-LC-2б)", "создайте документ типа «план_глав»"))
    else:
        filled = any("⚠ заполнить" not in p.read_text(encoding="utf-8", errors="replace") for p in plans)
        out.append(Check(True if filled else None, f"план глав: {', '.join(p.name for p in plans)}"
                         + ("" if filled else " — каркас не заполнен"),
                         "" if filled else "заполните хотя бы одну главу: номер, дата, фокал, что происходит"))
    # (в) мир или персонажи, если план ссылается на имена
    people = man.docs(library, "персонажи", None, types) + man.docs(library, "мир", None, types)
    if plans and not people:
        out.append(Check(None, "документ мира или персонажей (FR-LC-2в)", "нужен, если план глав называет имена: досье или документ мира"))
    # модули (FR-ON-20)
    for mod, absent in man.missing_types(library, types, modules, None).items():
        out.append(Check(False, f"модуль «{mod}» включён, но нет документов: {', '.join(absent)}",
                         "добавьте документ типа или выключите модуль в проект.yaml"))
    # методики: неизвестная, не поддерживающая уровень, несколько на уровне, уровень «серия» (FR-DR-3, П-5)
    from . import circles, methodics

    for problem in methodics.problems(man, root, circles.MATERIAL_SECTIONS):
        out.append(Check(None, f"методики: {problem}", "поправьте блок «методики» в проект.yaml"))
    # дубли: у типа с множественностью «один»/«по_тому» два документа (обычно каркас комплекта рядом с документом онбординга)
    for name, spec in sorted(types.items()):
        if spec.multiplicity not in ("один", "по_тому"):
            continue
        groups: dict[int | None, list[Path]] = {}
        for p in man.docs(library, name, None, types):
            groups.setdefault(manifest_mod.doc_volume(p) if spec.per_volume else None, []).append(p)
        for vol, paths in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            if len(paths) < 2:
                continue
            rel = [p.relative_to(library).as_posix() for p in paths]
            blank = [r for r, p in zip(rel, paths, strict=True) if "⚠ заполнить" in p.read_text(encoding="utf-8", errors="replace")]
            where = f" тома {vol}" if vol is not None else ""
            out.append(Check(False, f"тип «{name}»{where} представлен дважды: {', '.join(rel)}",
                             (f"удалите незаполненный каркас: {', '.join(blank)}" if blank else "оставьте один документ этого типа")
                             + " (проект с готовыми материалами создавайте с --без-комплекта)"))
    # документы вне карты
    unmapped = manifest_mod.unmapped(man, library)
    if unmapped:
        out.append(Check(None, f"документов вне карты манифеста: {len(unmapped)} ({', '.join(unmapped[:3])}{'…' if len(unmapped) > 3 else ''})",
                         "`konveyer онбординг` — сопоставить типы, или добавить в проект.yaml"))
    # документы выключенных модулей с ошибками разметки (экспорт их пропустил, П-5)
    col = _collect(library, root, volume)
    for w in (col.warnings if col else []):
        out.append(Check(None, f"документ выключенного модуля не разобран: {_export_warning(w, library)}",
                         "поправьте документ или удалите его из карты манифеста"))
    # индекс библиотеки отстал от манифеста (FR-DM-3)
    if INDEX_TYPE in types and index_outdated(root, library) is not None:
        out.append(Check(None, "индекс библиотеки не совпадает с манифестом", "`konveyer проект индекс` — пересобрать из манифеста"))
    # незаполненные каркасы
    todo = [p.relative_to(library).as_posix() for p in sorted(library.rglob("*.md"))
            if "⚠ заполнить" in p.read_text(encoding="utf-8", errors="replace")]
    if todo:
        out.append(Check(None, f"каркасы с «⚠ заполнить»: {len(todo)} ({', '.join(todo[:3])}{'…' if len(todo) > 3 else ''})",
                         "заполните или удалите строки-заглушки"))
    return out


def _export_warning(w: object, library: Path) -> str:
    from . import exporter

    return exporter.relative_message(w, library)  # type: ignore[arg-type]


def ready_for_tact(checks: list[Check]) -> bool:
    return not any(c.ok is False for c in checks)


# ------------------------------------------------------------------ индекс библиотеки (FR-DM-3)


def index_text(root: Path, library: Path) -> tuple[Path, str]:
    """(путь индекса, текст): индекс генерируется из манифеста; правится только через манифест."""
    types = catalog.load_types(root)
    man = manifest_mod.effective(root, library, types)
    return index_doc_path(man, library, types), render_index(man, types)


def index_outdated(root: Path, library: Path) -> Path | None:
    """Путь индекса, если его нет или он отстал от манифеста; None — индекс актуален."""
    path, text = index_text(root, library)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        return path
    return None
