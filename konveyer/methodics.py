"""Методики драматургии как плагины (FR-DR-1…FR-DR-3).

Методика — папка `методики/<имя>/`: `методика.yaml` (уровни применения, шаги, обязательность), `промпт.md`
(системный промпт аналитика), `схема.json` (формат ответа), `в_окно.j2` (как каркас показывается Писателю),
`в_э2.md` (как проверяется). В комплекте движка: круг Хармона, арки персонажей, сцена и сиквел, трёхактная
структура, «пустая» (каркас формулирует автор в манифесте). Проект добавляет свои в `методики/` проекта —
без правки движка (FR-DR-1, тест «методика своя из проекта»).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from . import manifest as manifest_mod

LEVELS = ("серия", "том", "акт", "глава")
RESULT_KINDS = ("шаги", "арки")


@dataclass(frozen=True)
class Step:
    n: int
    name: str
    description: str = ""


@dataclass(frozen=True)
class Methodic:
    name: str
    title: str
    levels: tuple[str, ...]
    steps: tuple[Step, ...]
    required: dict[str, list[int]]     # уровень → обязательные шаги (пусто = все)
    heading: str                        # слово заголовка в документе каркасов («Круг», «Каркас»)
    prompt: str
    schema: dict
    window_template: str
    e2_text: str
    folder: Path
    raw: dict = field(default_factory=dict)
    unset_note: str = ""                # пометка Писателю о незаданном необязательном шаге главы ({n}, {name})
    result_kind: str = "шаги"           # «шаги» — каркас из шагов (документ каркасов); «арки» — строки арок (документ арок)
    material: dict[str, list[str]] = field(default_factory=dict)  # уровень → секции материала аналитика (FR-DR-1)

    def material_for(self, level: str) -> list[str] | None:
        """Секции материала для уровня (`материал:` в методика.yaml — список для всех уровней или словарь по уровням);
        None — состав по умолчанию (`circles.DEFAULT_MATERIAL`)."""
        if not self.material:
            return None
        return list(self.material.get(level) or self.material.get("*") or []) or None

    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]

    def required_steps(self, level: str, manifest: manifest_mod.Manifest | None = None) -> set[int]:
        """Обязательные шаги уровня: манифест (`обязательные_шаги`) сильнее методики; пусто в методике — все."""
        if manifest is not None and manifest.методики.обязательные_шаги.get(level):
            return set(int(x) for x in manifest.методики.обязательные_шаги[level])
        req = self.required.get(level)
        if req:
            return set(req)
        return {s.n for s in self.steps}

    def optional_steps(self, level: str, manifest: manifest_mod.Manifest | None = None) -> set[int]:
        return {s.n for s in self.steps} - self.required_steps(level, manifest)


def _engine_dir() -> Path:
    return Path(str(resources.files("konveyer").joinpath("методики")))


def _read(folder: Path, name: str, default: str = "") -> str:
    p = folder / name
    return p.read_text(encoding="utf-8") if p.exists() else default


def _load_one(folder: Path, override: Path | None = None) -> Methodic:
    data = yaml.safe_load((folder / "методика.yaml").read_text(encoding="utf-8")) or {}
    steps: list[Step] = []
    for i, st in enumerate(data.get("шаги") or [], start=1):
        if isinstance(st, dict):
            steps.append(Step(int(st.get("n", i)), str(st.get("имя", f"шаг {i}")), str(st.get("описание", ""))))
        else:
            steps.append(Step(i, str(st), ""))
    required = {k: [int(x) for x in v] for k, v in (data.get("обязательность") or {}).items() if isinstance(v, list)}
    raw_material = data.get("материал")
    if isinstance(raw_material, dict):
        material = {str(k): [str(x) for x in (v or [])] for k, v in raw_material.items()}
    elif isinstance(raw_material, list):
        material = {"*": [str(x) for x in raw_material]}
    else:
        material = {}

    def text(name: str) -> str:
        if override is not None and (override / name).exists():
            return (override / name).read_text(encoding="utf-8")
        return _read(folder, name)

    schema_text = text("схема.json")
    return Methodic(
        name=str(data.get("методика", folder.name)), title=str(data.get("название", folder.name)),
        levels=tuple(data.get("уровни") or LEVELS), steps=tuple(steps), required=required,
        heading=str(data.get("заголовок_документа", "Каркас")), prompt=text("промпт.md"),
        schema=json.loads(schema_text) if schema_text.strip() else {}, window_template=text("в_окно.j2"),
        e2_text=text("в_э2.md"), folder=folder, raw=data, unset_note=str(data.get("незаданный_шаг", "") or ""),
        result_kind=str(data.get("вид_результата", "шаги") or "шаги"), material=material,
    )


def load_all(project_root: Path | None = None) -> dict[str, Methodic]:
    """Методики движка + методики проекта (`методики/` проекта; одноимённая — переопределяет движковую;
    папка проекта только с промптом/шаблоном — переопределяет эти файлы у движковой)."""
    out: dict[str, Methodic] = {}
    engine = _engine_dir()
    project = (project_root / "методики") if project_root else None
    for folder in sorted(p for p in engine.iterdir() if p.is_dir() and (p / "методика.yaml").exists()):
        override = project / folder.name if project and (project / folder.name).is_dir() else None
        if override is not None and (override / "методика.yaml").exists():
            continue  # полная замена проектом ниже
        out[folder.name] = _load_one(folder, override)
    if project and project.is_dir():
        for folder in sorted(p for p in project.iterdir() if p.is_dir() and (p / "методика.yaml").exists()):
            out[folder.name] = _load_one(folder)
    return out


def for_level(level: str, manifest: manifest_mod.Manifest, project_root: Path | None = None) -> list[Methodic]:
    """Методики, выбранные в манифесте для уровня (в порядке объявления); отсутствующая — пропускается."""
    all_m = load_all(project_root)
    out: list[Methodic] = []
    for name in manifest.методики.for_level(level):
        m = all_m.get(name)
        if m is not None and (level in m.levels or not m.levels):
            out.append(m)
    return out


def primary(level: str, manifest: manifest_mod.Manifest, project_root: Path | None = None) -> Methodic | None:
    ms = for_level(level, manifest, project_root)
    return ms[0] if ms else None


def empty_from_manifest(manifest: manifest_mod.Manifest, base: Methodic, level: str | None = None) -> Methodic:
    """«Пустая» методика: шаги формулирует автор в манифесте (`методики.шаги_пустой` — список, либо словарь
    уровень → список); без них методика остаётся без шагов."""
    steps = manifest.методики.empty_steps(level)
    if not steps:
        return base
    return Methodic(**{**base.__dict__, "steps": tuple(Step(i, str(st)) for i, st in enumerate(steps, start=1))})


def problems(manifest: manifest_mod.Manifest, project_root: Path | None = None,
             material_sections: tuple[str, ...] | None = None) -> list[str]:
    """Расхождения манифеста с методиками — для доктора (П-5: с пометкой, а не молча): неизвестная методика,
    методика, не поддерживающая уровень, несколько методик на уровне, уровень «серия» (в этой версии каркас
    серии не строится)."""
    all_m = load_all(project_root)
    out: list[str] = []
    for level in LEVELS:
        names = manifest.методики.for_level(level)
        if not names:
            continue
        if level == "серия":
            out.append(f"методики.серия = {', '.join(names)}: уровень «серия» в этой версии не поддержан — каркас серии не строится")
            continue
        for name in names:
            m = all_m.get(name)
            if m is None:
                out.append(f"методика «{name}» (уровень «{level}») не найдена ни в движке, ни в методики/ проекта")
            elif m.levels and level not in m.levels:
                out.append(f"методика «{name}» не поддерживает уровень «{level}» (её уровни: {', '.join(m.levels)}) — "
                           f"каркас уровня не строится")
        if len(names) > 1:
            out.append(f"на уровне «{level}» задано несколько методик ({', '.join(names)}): применяется первая — «{names[0]}», "
                       "остальные не участвуют в окне, Э2 и линтере")
    for m in all_m.values():
        if m.result_kind not in RESULT_KINDS:
            out.append(f"методика «{m.name}»: неизвестный вид результата «{m.result_kind}» (допустимо: {', '.join(RESULT_KINDS)})")
        if material_sections is not None:
            unknown = sorted({x for names_ in m.material.values() for x in names_ if x not in material_sections})
            if unknown:
                out.append(f"методика «{m.name}»: неизвестный материал {unknown} (доступно: {', '.join(material_sections)})")
    return out
