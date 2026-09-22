"""Наблюдатель библиотеки канона: изменения файлов → перепроверка (линтер) в реальном времени.

Без сторонних зависимостей (NFR-1): опрос mtime раз в `interval` секунд с задержкой на
«дозапись» (файл считается изменённым, когда его mtime не менялся один цикл подряд).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class CanonWatcher:
    def __init__(self, library: Path, on_change: Callable[[list[str]], None], interval: float = 2.0) -> None:
        self.library = library
        self.on_change = on_change
        self.interval = interval
        self._snapshot = self._scan()
        self._pending: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_changed: list[str] = []

    def _scan(self) -> dict[str, int]:
        snap: dict[str, int] = {}
        if not self.library.exists():
            return snap
        for p in self.library.rglob("*.md"):
            try:
                snap[str(p.relative_to(self.library)).replace("\\", "/")] = p.stat().st_mtime_ns
            except OSError:
                continue
        return snap

    def poll(self) -> list[str]:
        """Один цикл: возвращает список изменённых файлов, когда они «устоялись»."""
        now = self._scan()
        changed = [k for k in set(now) | set(self._snapshot) if now.get(k) != self._snapshot.get(k)]
        if not changed:
            settled = list(self._pending)
            self._pending.clear()
            if settled:
                self.last_changed = sorted(settled)
                return self.last_changed
            return []
        for k in changed:
            self._pending[k] = now.get(k, 0)
        self._snapshot = now
        return []

    def run_forever(self) -> None:
        while not self._stop.is_set():
            changed = self.poll()
            if changed:
                try:
                    self.on_change(changed)
                except Exception:  # наблюдатель не должен умирать из-за ошибки проверки
                    pass
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="canon-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
