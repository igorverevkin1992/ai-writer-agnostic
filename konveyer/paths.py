"""Раскладка рабочей области конвейера (NFR-4: артефакты такта — в главы/N/).

Тома (аудит 2, п. 27): рабочая область ведёт ОДИН текущий том (`конфиг.yaml: volume`).
Главы тома 1 лежат в `главы/001` (как и раньше — без миграции), главы тома N ≥ 2 —
в `главы/ТN/001`. Все пути глав берут том из `Workspace.volume`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    """Пути рабочей области. Корень — папка проекта автора (где лежит конфиг.yaml).
    `volume` — текущий том (из конфиг.yaml, выставляется в `steps.common._ctx()`); по умолчанию 1."""

    root: Path
    volume: int = 1

    @property
    def library(self) -> Path:
        # Может быть переопределён конфигом; см. config.load_config().
        return self.root / "Библиотека"

    @property
    def exports(self) -> Path:
        return self.root / "выгрузки"

    @property
    def chapters(self) -> Path:
        return self.root / "главы"

    @property
    def corpus(self) -> Path:
        return self.root / "выгрузки" / "корпус"

    @property
    def logs(self) -> Path:
        return self.root / "журналы"

    @property
    def templates(self) -> Path:
        return self.root / "шаблоны"

    @property
    def regression(self) -> Path:
        return self.root / "регрессия"

    @property
    def manuscript(self) -> Path:
        return self.root / "рукопись"

    @property
    def snapshots(self) -> Path:
        return self.root / "снапшоты"

    def for_volume(self, volume: int) -> "Workspace":
        """Та же рабочая область, но с другим текущим томом (`konveyer том закрыть N`, снапшот тома)."""
        return dataclasses.replace(self, volume=int(volume))

    def chapters_root(self, volume: int | None = None) -> Path:
        """Папка глав тома: том 1 — `главы/` (совместимость), том N ≥ 2 — `главы/ТN/`."""
        v = self.volume if volume is None else int(volume)
        return self.chapters if v == 1 else self.chapters / f"Т{v}"

    def chapter_dirs(self, volume: int | None = None) -> list[tuple[int, Path]]:
        """Папки глав текущего (или указанного) тома по возрастанию номера: [(N, путь)]."""
        root = self.chapters_root(volume)
        if not root.exists():
            return []
        out = []
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name.isdigit():
                out.append((int(d.name), d))
        return out

    def chapter_dir(self, n: int, volume: int | None = None) -> Path:
        return self.chapters_root(volume) / f"{n:03d}"

    def chapter_rel(self, n: int) -> str:
        """Относительный путь папки главы для сообщений: `главы/005` или `главы/Т2/005`."""
        return self.chapter_dir(n).relative_to(self.root).as_posix()

    def draft_path(self, n: int, k: int) -> Path:
        return self.chapter_dir(n) / f"черновик_{k}.md"

    def window_path(self, n: int) -> Path:
        return self.chapter_dir(n) / "окно.md"

    def status_path(self, n: int) -> Path:
        return self.chapter_dir(n) / "состояние.yaml"


def find_workspace(start: Path | None = None) -> Workspace:
    """Ищет конфиг.yaml или проект.yaml вверх от текущей папки. Нет ни того ни другого — FileNotFoundError
    (команды не должны молча работать над пустой «областью» с несуществующей библиотекой)."""
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "конфиг.yaml").exists() or (p / "проект.yaml").exists():
            return Workspace(p)
    raise FileNotFoundError(f"рабочая область не найдена — здесь нет проекта конвейера: нет конфиг.yaml/проект.yaml "
                            f"в {cur.name}/ и выше. Перейдите в папку проекта или создайте его: `konveyer проект создать` "
                            "(либо `konveyer начать --демо` здесь)")
