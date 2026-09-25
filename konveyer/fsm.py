"""Конечный автомат главы (FR-TK-2…4). Состояние — главы/N/состояние.yaml (Д-3).

Имена состояний в коде — исторические (панель, документы и артефакты существующих проектов уже их несут);
цепочка ТЗ «запланирована → … → проверено-машинно → проверено-моделью → …» принимается как синонимы
(`STATE_ALIASES`): `transition("проверено-машинно")` и `rollback("запланирована")` работают. Кроме состояния
и счётчиков файл несёт блок `артефакты` — пути текущих артефактов такта (окно, черновик, вердикт, флаги,
приёмка, дифф, пакет), чтобы ход работы восстанавливался по одному файлу (NFR-5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import guard, timing
from .paths import Workspace

STATES = [
    "не-начато",
    "собрано",
    "сгенерировано",
    "верифицировано-1",
    "верифицировано-2",
    "на-приёмке",
    "правки",
    "дифф-контроль",
    "принято",
    "зафиксировано",
]

# имена состояний по ТЗ (FR-TK-2) → имена в коде; остальные совпадают
STATE_ALIASES = {
    "запланирована": "не-начато",
    "проверено-машинно": "верифицировано-1",
    "проверено-моделью": "верифицировано-2",
}


def canonical_state(name: str) -> str:
    """Имя состояния в коде по имени из ТЗ или из кода (неизвестное имя возвращается как есть)."""
    return STATE_ALIASES.get(name, name)


# текущие артефакты такта в состояние.yaml: имя поля → файл в папке главы (`{k}` — номер черновика)
ARTIFACT_FILES = {
    "окно": "окно.md", "черновик": "черновик_{k}.md", "вердикт": "вердикт.json", "флаги": "флаги.json",
    "приёмка": "приёмка.md", "правки": "правки.md", "решения": "решения.json", "дифф": "дифф.json",
    "пакет": "пакет_канона.md",
}

TRANSITIONS: dict[str, set[str]] = {
    "не-начато": {"собрано"},
    "собрано": {"сгенерировано", "собрано"},
    "сгенерировано": {"верифицировано-1", "сгенерировано"},
    # брак метрик → назад в «сгенерировано» (авто-повтор, FR-TK-3)
    "верифицировано-1": {"верифицировано-2", "сгенерировано"},
    "верифицировано-2": {"на-приёмке"},
    "на-приёмке": {"правки"},
    # self-loop — новая итерация правок после грязного дифф-контроля (FR-TK-3)
    "правки": {"дифф-контроль", "правки"},
    # самовольные изменения или невнесённые правки → назад в «правки» (FR-TK-3); self-loop — повторный прогон
    "дифф-контроль": {"принято", "правки", "дифф-контроль"},
    "принято": {"зафиксировано"},
    "зафиксировано": set(),  # терминальное; изменения только через git-revert
}


# Артефакты, копия которых сохраняется за номером черновика при входе в состояние (П-7, NFR-5):
# вердикт.json/флаги.json/дифф.json остаются «текущими», вердикт_k.json — историей повторов.
DRAFT_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "верифицировано-1": ("вердикт.json",),
    "верифицировано-2": ("флаги.json",),
    "дифф-контроль": ("дифф.json",),
}


class TransitionError(RuntimeError):
    pass


class StatusFileError(RuntimeError):
    """состояние.yaml главы нечитаем: одна повреждённая глава не должна ломать обзор всех остальных."""


class ChapterState:
    """Состояние главы ТЕКУЩЕГО тома рабочей области (`ws.volume`); другой том — `ChapterState(ws.for_volume(v), n)`."""

    def __init__(self, ws: Workspace, chapter: int):
        self.ws = ws
        self.chapter = chapter
        self.volume = ws.volume
        self.path: Path = ws.status_path(chapter)
        rel = ws.chapter_rel(chapter)
        if self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                raise StatusFileError(f"{rel}/состояние.yaml повреждён (не YAML): {e}") from None
            if not isinstance(data, dict) or data.get("состояние") not in STATES:
                raise StatusFileError(
                    f"{rel}/состояние.yaml повреждён: нет допустимого поля «состояние» "
                    f"(файл пуст или усечён). Восстановите его из истории/бэкапа или удалите папку главы."
                )
            self.data = data
        else:
            self.data = {"глава": chapter, "состояние": "не-начато", "черновик": 0, "авто_повторов": 0, "итераций_правок": 0, "история": []}
            if self.volume != 1:
                self.data["том"] = self.volume  # том 1 — без поля (совместимость старых состояние.yaml)

    @property
    def state(self) -> str:
        return self.data["состояние"]

    @property
    def draft(self) -> int:
        return int(self.data.get("черновик", 0))

    def _save(self) -> None:
        self.data["артефакты"] = self.artifacts()
        guard.write_text(self.path, yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False))

    def artifacts(self) -> dict[str, str]:
        """Ссылки на текущие артефакты такта (FR-TK-2): только существующие файлы, пути от корня проекта,
        в порядке шагов такта — детерминированно."""
        chdir = self.ws.chapter_dir(self.chapter)
        out: dict[str, str] = {}
        for key, name in ARTIFACT_FILES.items():
            path = chdir / name.format(k=self.draft)
            if self.draft or key != "черновик":
                if path.exists():
                    out[key] = path.relative_to(self.ws.root).as_posix()
        return out

    def transition(self, to: str, cmd: str = "") -> None:
        to = canonical_state(to)
        if to not in STATES:
            raise TransitionError(f"Неизвестное состояние: {to}")
        if to not in TRANSITIONS.get(self.state, set()):
            raise TransitionError(
                f"Глава {self.chapter}: переход «{self.state}» → «{to}» недопустим."
            )
        self._move(to, cmd)

    def rollback(self, to: str, cmd: str = "rollback") -> None:
        """Откат в любое ПРЕДЫДУЩЕЕ состояние (FR-SC-4). «зафиксировано» — только git-revert."""
        if self.state == "зафиксировано":
            raise TransitionError(
                "Состояние «зафиксировано» терминально: откат только git-revert'ом коммита приёмки (`konveyer откат N --to принято` выполнит revert)."
            )
        to = canonical_state(to)
        if to not in STATES:
            raise TransitionError(f"Неизвестное состояние: {to}")
        if STATES.index(to) >= STATES.index(self.state):
            raise TransitionError(
                f"Откат возможен только назад: «{self.state}» → «{to}» не является откатом."
            )
        # Счётчики FR-TK-3 привязаны к циклу, который откат прерывает: откат раньше цикла правок
        # обнуляет бюджет итераций, откат раньше верификации — бюджет авто-повторов. Иначе после
        # трёх итераций любой будущий цикл правок главы отказывал бы навсегда.
        if STATES.index(to) < STATES.index("на-приёмке"):
            self.data["итераций_правок"] = 0
        if STATES.index(to) < STATES.index("верифицировано-1"):
            self.data["авто_повторов"] = 0
        self._move(to, cmd)

    def _record(self, frm: str, to: str, cmd: str) -> None:
        rec = {"из": frm, "в": to, "время": datetime.now(timezone.utc).isoformat(), "команда": cmd}
        if timing.current_job:
            # переходы одной задачи (run, кнопка панели) → интервал между ними машинный (timing.py)
            rec["задача"] = timing.current_job
        self.data.setdefault("история", []).append(rec)

    def _move(self, to: str, cmd: str) -> None:
        self._record(self.state, to, cmd)
        self.data["состояние"] = to
        self._save()
        self.snapshot_artifacts(*DRAFT_ARTIFACTS.get(to, ()))

    def snapshot_artifacts(self, *names: str) -> list[Path]:
        """Копия «текущего» артефакта под номером черновика: вердикт.json → вердикт_k.json (П-7, NFR-5).
        Повторы (авто-повтор Э1, второй цикл правок) больше не затирают прошлые вердикты."""
        chdir = self.ws.chapter_dir(self.chapter)
        copies: list[Path] = []
        for name in names:
            src = chdir / name
            if not src.exists():
                continue
            dst = chdir / f"{src.stem}_{self.draft}{src.suffix}"
            guard.write_text(dst, src.read_text(encoding="utf-8"))
            copies.append(dst)
        return copies

    def set_draft(self, k: int) -> None:
        self.data["черновик"] = k
        self._save()

    def bump_retries(self) -> int:
        """Авто-повтор Э1 (FR-TK-3): брак метрик → новая генерация. Попадает в историю как
        петля «сгенерировано → сгенерировано»; вердикт бракованного черновика сохраняется как вердикт_k.json."""
        self.data["авто_повторов"] = int(self.data.get("авто_повторов", 0)) + 1
        self._record(self.state, self.state, f"verify1 (авто-повтор №{self.data['авто_повторов']}, брак метрик)")
        self._save()
        self.snapshot_artifacts("вердикт.json")
        return self.data["авто_повторов"]

    def reset_retries(self) -> None:
        """Новая генерация (в том числе черновик, внесённый автором файлом) = новый цикл верификации:
        бюджет авто-повторов FR-TK-3 заново. Ручной Писатель счётчик не расходует (см. steps.tact.verify1)."""
        self.data["авто_повторов"] = 0
        self._save()

    def bump_edit_iterations(self) -> int:
        self.data["итераций_правок"] = int(self.data.get("итераций_правок", 0)) + 1
        self._save()
        return self.data["итераций_правок"]

    def require(self, *states: str) -> None:
        states = tuple(canonical_state(s) for s in states)
        if self.state not in states:
            raise TransitionError(
                f"Глава {self.chapter} в состоянии «{self.state}», команда требует: {', '.join(states)}."
            )


def all_states(ws: Workspace, volume: int | None = None) -> list[ChapterState]:
    """Главы текущего тома рабочей области (или тома `volume`) по возрастанию номера."""
    if volume is not None and volume != ws.volume:
        ws = ws.for_volume(volume)
    return [ChapterState(ws, n) for n, _ in ws.chapter_dirs()]
