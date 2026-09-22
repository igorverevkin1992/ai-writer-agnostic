"""Локальный сервер панели (этап 3): JSON-API поверх пайплайна + статика React.

Контур остаётся локальным (§1.3): сервер слушает ТОЛЬКО 127.0.0.1, наружу
ничего не ходит, все операции — те же функции ядра `konveyer/steps`, что у CLI (FSM, guard и
подтверждения сохраняются). Защита от чужих сайтов (аудит 4.2/4.3):

* каждый запрос обязан нести `Host: 127.0.0.1:<порт>` или `localhost:<порт>`
  — DNS-rebinding приходит с чужим Host и получает 403;
* изменяющие запросы требуют заголовка `X-Konveyer-Panel: 1` (браузерный
  cross-origin не может его послать без CORS-preflight, который мы не
  разрешаем) и, если браузер прислал `Origin`, — локального Origin;
* GET ничего не пишет на диск: промпты и дашборд строятся в памяти,
  запись файла промпта — отдельный POST.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from . import cancel, canonchange, exporter, guard, review, steps, timing, verifier2
from .config import Config
from .fsm import ChapterState
from .paths import Workspace
from .steps import canon, quality, tact

# команды такта, доступные из панели (белый список)
COMMANDS = {
    "run", "export", "compile", "write", "verify1", "verify2", "review",
    "apply-edits", "diff-check", "diff-check-author", "regress", "canonize", "canonize-apply",
    "story-circles", "circles-canon",
    "lint", "lint-llm", "canon-commit",
}

# допустимые уровни кругов истории (Р-020) и виды промптов ручного режима
CIRCLE_SCOPES = ("книга", "акт", "глава")
PROMPT_KINDS = ("verify2", "edits")

# лимит тела POST: рукопись главы — десятки КБ, 50 МБ — заведомо чужой запрос
MAX_BODY = 50 * 1024 * 1024
# хвост лога задачи, который уезжает в /api/state при каждом опросе (5.5)
OUTPUT_TAIL = 2048
# прогресс задачи: последняя строка вида «[N/M]» в выводе (круги — 51 вызов, 5.5)
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")


class Busy(RuntimeError):
    """Сервер занят задачей или синхронной операцией → HTTP 423 (аудит 5.4)."""


class _LiveBuffer(io.TextIOBase):
    """Пишет вывод задачи сразу в job['output'] — панель видит лог по ходу."""

    def __init__(self, job: dict, lock: threading.Lock):
        self.job = job
        self.lock = lock

    def write(self, s: str) -> int:
        with self.lock:
            self.job["output"] += _strip_ansi(s)
            # счётчик «N из M» ищем в хвосте — строка могла прийти двумя кусками
            m = None
            for m in _PROGRESS_RE.finditer(self.job["output"][-512:]):
                pass
            if m:
                self.job["progress"] = [int(m.group(1)), int(m.group(2))]
        return len(s)


class JobRunner:
    """Одна операция за раз (Д-5: однопользовательский режим, без гонок FSM).

    redirect_stdout глобален для процесса, поэтому ЛЮБАЯ операция,
    захватывающая вывод (фоновая задача, accept/rollback/ручной режим),
    идёт под одной блокировкой `exclusive()`: вторая одновременная
    операция не ждёт, а сразу отклоняется («дождитесь завершения»).
    """

    def __init__(self, on_idle: Callable[[], None] | None = None) -> None:
        self._lock = threading.Lock()      # защита job['output']
        self._gate = threading.Lock()      # одна операция с захватом вывода
        self.job: dict | None = None
        self.on_idle = on_idle             # после любой задачи/синхронной операции (сброс кэшей панели, 26б)

    def _notify_idle(self) -> None:
        if self.on_idle is not None:
            with contextlib.suppress(Exception):
                self.on_idle()

    @property
    def busy(self) -> bool:
        return bool(self.job and self.job["status"] == "выполняется")

    def _busy_message(self) -> str:
        if self.busy:
            return f"дождитесь завершения задачи «{self.job['name']}»"  # type: ignore[index]
        return "дождитесь завершения текущей операции"

    def _acquire(self) -> None:
        """Неблокирующий захват: занято → Busy (HTTP 423), а не ожидание."""
        if not self._gate.acquire(blocking=False):
            raise Busy(self._busy_message())
        if self.busy:  # страховка: задача идёт, а замок по какой-то причине свободен
            self._gate.release()
            raise Busy(self._busy_message())

    def ensure_idle(self) -> None:
        if self.busy:
            raise Busy(self._busy_message())

    @contextlib.contextmanager
    def exclusive(self):
        """Контекст для синхронных операций (accept, rollback, ручной режим…)."""
        self._acquire()
        try:
            yield
        finally:
            self._gate.release()
            self._notify_idle()

    def start(self, name: str, chapter: int | None, fn) -> dict:
        self._acquire()  # замок держится всё время задачи, отпускает _run
        try:
            with self._lock:
                self.job = {
                    "name": name,
                    "chapter": chapter,
                    "status": "выполняется",
                    "output": "",
                    "started": datetime.now(timezone.utc).isoformat(),
                    "progress": None,
                    "cancel_requested": False,
                }
            cancel.clear()  # флаг прошлой (уже завершившейся) задачи не должен остановить новую
            threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        except BaseException:
            self._gate.release()
            raise
        return self.job

    def _run(self, fn) -> None:
        assert self.job is not None
        buf = _LiveBuffer(self.job, self._lock)
        status = "готово"
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                fn()
        except cancel.Cancelled as e:
            buf.write(f"\n{e}")
            status = "остановлено"
        except SystemExit as e:
            status = "готово" if not e.code else "ошибка"
        except Exception as e:  # показываем автору, не роняем сервер
            expected = steps.outcome(e)  # ожидаемый исход шага: текст как в CLI, статус по коду возврата
            if expected is None:
                buf.write(f"\nОШИБКА: {e}")
                status = "ошибка"
            else:
                text, code = expected
                if text:
                    buf.write(text + "\n")
                status = "готово" if code == 0 else ("ручной-режим" if code == 2 else "ошибка")
        finally:
            with self._lock:
                # автор нажал «Остановить», и задача завершилась не успехом (в т.ч. ошибкой шага,
                # в которую превратилась остановка между вызовами) — это остановка, не ошибка
                if status == "ошибка" and self.job.get("cancel_requested"):
                    status = "остановлено"
                self.job["status"] = status
                self.job["finished"] = datetime.now(timezone.utc).isoformat()
            cancel.clear()  # задача не дошла до точки отмены — флаг не должен пережить её
            self._gate.release()
            self._notify_idle()

    def cancel(self) -> dict:
        """«Остановить» из панели: флаг отмены проверяется между вызовами моделей (konveyer/cancel.py)."""
        with self._lock:
            if not (self.job and self.job["status"] == "выполняется"):
                raise ValueError("нет выполняющейся задачи — останавливать нечего")
            self.job["cancel_requested"] = True
        cancel.request()
        return self.summary()  # type: ignore[return-value]

    def summary(self) -> dict | None:
        """Задача для /api/state: без полного лога — хвост и длина (5.5)."""
        with self._lock:
            job = self.job
            if job is None:
                return None
            out = job["output"]
            summary = {k: v for k, v in job.items() if k != "output"}
        summary["output_tail"] = out[-OUTPUT_TAIL:]
        summary["output_len"] = len(out)
        return summary

    def full(self) -> dict:
        with self._lock:
            return dict(self.job) if self.job else {}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _job(fn, *args, **kwargs):
    """Вызов функции ядра как задачи: учёт времени такта и сброс запроса отмены (`steps.job_context`),
    как у команд CLI."""
    with steps.job_context(getattr(fn, "__name__", "задача")):
        return fn(*args, **kwargs)


def _captured(fn, refusal: str) -> str:
    """Выполняет функцию ядра с захватом вывода; ожидаемый исход с кодом ≠ 0 (ошибка шага, ручной режим,
    недопустимый переход FSM…) → RuntimeError с текстом, как его напечатал бы CLI."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            fn()
    except Exception as e:
        expected = steps.outcome(e)
        if expected is None:
            raise
        text, code = expected
        if text:
            buf.write(text + "\n")
        if code:
            raise RuntimeError(_strip_ansi(buf.getvalue()).strip() or refusal) from e
    return _strip_ansi(buf.getvalue())


class PanelAPI:
    """Собирает JSON-ответы; хендлер остаётся тонким."""

    def __init__(self, ws: Workspace, cfg: Config, library: Path):
        self.ws = ws
        self.cfg = cfg
        self.library = library
        self.jobs = JobRunner(on_idle=self.invalidate_caches)
        # кэш состояния панели (26б): сводки глав по mtime/size их файлов, регрессия по отчёту и отпечатку
        # шаблонов/норм, незакоммиченные файлы канона — по событиям и с коротким сроком годности
        self._cache_lock = threading.Lock()
        self._chapter_cache: dict[int, tuple[tuple, dict, list[tuple]]] = {}
        self._regression_cache: tuple[tuple, bool | None] | None = None
        self._canon_status_cache: tuple[float, list[str]] | None = None
        # линтер канона в реальном времени: наблюдатель за библиотекой → перепроверка (konveyer/lint.py)
        from . import canonwatch, lint as lint_mod

        self.lint_report = lint_mod.load_report(self.ws.logs)
        self.lint_changed: list[str] = []
        self.lint_pending = False
        self.lint_running = False
        self._lint_lock = threading.Lock()
        self._lint_wake = threading.Event()
        self._lint_done = threading.Event()
        self._lint_stop = threading.Event()
        self._lint_thread: threading.Thread | None = None
        self.watcher = canonwatch.CanonWatcher(self.library, self._on_canon_change)

    # ------------------------------------------------------------- чтение

    # ------------------------------------------------------- кэш состояния (26б)

    def invalidate_caches(self) -> None:
        """После любой задачи/синхронной операции и после изменения канона: сводки глав, регрессия и
        незакоммиченные файлы пересчитываются при следующем опросе (страховка к проверке по mtime)."""
        with self._cache_lock:
            self._chapter_cache.clear()
            self._regression_cache = None
            self._canon_status_cache = None

    @staticmethod
    def _stat_key(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    # файлы главы, от которых зависит её сводка в обзоре
    _CHAPTER_FILES = ("состояние.yaml", "вердикт.json", "флаги.json")

    def _chapter_summary(self, n: int) -> tuple[dict, list[tuple]]:
        """Карточка главы для обзора + авторские интервалы (локальная дата конца, секунды) для «сегодня»."""
        from .steps.common import NEXT_STEP, _chapter_flags_summary

        st = ChapterState(self.ws, n)
        e1, e2 = _chapter_flags_summary(self.ws, n)
        history = st.data.get("история", [])
        machine_s, author_s = timing.chapter_times(history)
        author_days = []
        for kind, secs, end in timing.intervals(history):
            if kind == "авторское":
                end_local = end.astimezone() if end.tzinfo else end
                author_days.append((end_local.date(), secs))
        return {
            "chapter": n,
            "state": st.state,
            "draft": st.draft,
            "e1": e1,
            "e2": e2,
            "author_min": round(author_s / 60, 1),
            "machine_min": round(machine_s / 60, 1),
            "next": NEXT_STEP.get(st.state, "").format(n=n),
        }, author_days

    def _chapters_cached(self) -> tuple[list[dict], float]:
        """Сводки всех глав из кэша; глава перечитывается, только если mtime/size её файлов изменились.
        Возвращает (карточки, авторские секунды за сегодня)."""
        today = datetime.now().astimezone().date()
        chapters: list[dict] = []
        author_today = 0.0
        # Одна повреждённая глава (пустой состояние.yaml, битый вердикт.json) — карточка «повреждено»
        # с причиной, а не 500 для всей панели (4.8).
        for n, d in self.ws.chapter_dirs():  # главы текущего тома (`config.volume`)
            key = tuple(self._stat_key(d / name) for name in self._CHAPTER_FILES)
            with self._cache_lock:
                cached = self._chapter_cache.get(n)
            if cached is None or cached[0] != key:
                try:
                    summary, author_days = self._chapter_summary(n)
                except Exception as e:  # noqa: BLE001 — обзор не должен падать из-за одного файла
                    summary = {"chapter": n, "state": "повреждено", "draft": 0, "e1": "—", "e2": "—",
                               "author_min": 0, "machine_min": 0, "next": f"файлы главы повреждены: {e}"}
                    author_days = []
                cached = (key, summary, author_days)
                with self._cache_lock:
                    self._chapter_cache[n] = cached
            chapters.append(dict(cached[1]))
            author_today += sum(secs for day, secs in cached[2] if day == today)
        return chapters, author_today

    def _regression_green(self) -> bool | None:
        """regression.is_green с кэшем: отчёт и отпечаток окружения (конфиг.yaml, шаблоны, нормы)
        пересчитываются только при изменении mtime/size этих файлов."""
        from . import regression

        parts: list = [self._stat_key(self.ws.regression / "report.json"),
                       self._stat_key(self.ws.root / "конфиг.yaml"),
                       self._stat_key(self.ws.exports / "norms.json")]
        if self.ws.templates.exists():
            for f in sorted(p for p in self.ws.templates.rglob("*") if p.is_file()):
                parts.append((f.relative_to(self.ws.templates).as_posix(), self._stat_key(f)))
        key = tuple(parts)
        with self._cache_lock:
            cached = self._regression_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            green = regression.is_green(self.ws)
        except Exception:  # noqa: BLE001 — битый report.json = «регрессия не запускалась»
            green = None
        with self._cache_lock:
            self._regression_cache = (key, green)
        return green

    CANON_STATUS_TTL = 10.0  # секунд: коммит из терминала виден панели без события

    def canon_status(self) -> list[str]:
        """Незакоммиченные файлы библиотеки (п. 25): `git status --porcelain` не чаще раза в CANON_STATUS_TTL,
        сброс — при любом изменении канона/задаче/событии наблюдателя."""
        now = time.monotonic()
        with self._cache_lock:
            cached = self._canon_status_cache
        if cached is not None and now - cached[0] < self.CANON_STATUS_TTL:
            return cached[1]
        try:
            files = canonchange.dirty_files(self.library)
        except Exception:  # noqa: BLE001 — нет git в PATH и т. п.: «неизвестно» = пусто
            files = []
        with self._cache_lock:
            self._canon_status_cache = (now, files)
        return files

    def state(self) -> dict:
        chapters, author_today_s = self._chapters_cached()
        try:
            briefs = [
                {"chapter": b.chapter, "volume": b.volume, "focal": b.focal, "date": b.date}
                for b in exporter.load_briefs(self.ws.exports)
            ]
        except Exception:  # noqa: BLE001 — нет выгрузок или устаревшая схема: «Экспорт канона» пересоберёт
            briefs = []
        uncommitted = self.canon_status()
        return {
            "workspace": str(self.ws.root),
            "volume": self.ws.volume,
            "chapters": chapters,
            "briefs": briefs,
            "regression_green": self._regression_green(),
            "models": {"writer": self.cfg.writer.model, "verifier2": self.cfg.verifier2.model},
            "job": self.jobs.summary(),
            "lint": self.lint_summary(),
            "author_today_min": round(author_today_s / 60, 1),
            "canon_uncommitted": bool(uncommitted),
            "canon_uncommitted_files": uncommitted,
        }

    def _author_today_seconds(self) -> float:
        """«Сегодня: N мин автора» (5.7) — та же арифметика, что у timing.today_author_minutes, по кэшу глав."""
        return self._chapters_cached()[1]

    def chapter(self, n: int) -> dict:
        st = ChapterState(self.ws, n)
        chdir = self.ws.chapter_dir(n)

        def read_json(name):
            p = chdir / name
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

        draft_path = self.ws.draft_path(n, st.draft)
        text = draft_path.read_text(encoding="utf-8") if draft_path.exists() else None
        edits_md_path = chdir / "правки.md"
        edits_parsed = []
        for e in review.load_edits(self.ws, n):
            found = bool(text and e.before and e.before in text)
            edits_parsed.append(
                {"seq": e.seq, "before": e.before, "after": e.after, "note": e.note, "found": found or not e.before}
            )
        machine_s, author_s = timing.chapter_times(st.data.get("история", []))
        canon_batch = chdir / "пакет_канона.md"
        from .steps.common import NEXT_STEP

        return {
            "chapter": n,
            "state": st.state,
            "draft": st.draft,
            "retries": st.data.get("авто_повторов", 0),
            "iterations": st.data.get("итераций_правок", 0),
            "history": st.data.get("история", []),
            "verdict": read_json("вердикт.json"),
            "flags": [f.model_dump() for f in verifier2.load_flags(self.ws, n)],
            "resolutions": [r.model_dump() for r in review.load_resolutions(self.ws, n)],
            "diff_report": read_json("дифф.json"),
            "text": text,
            "drafts": sorted(
                int(m.group(1)) for p in chdir.glob("черновик_*.md") if (m := re.match(r"draft_(\d+)\.md", p.name))
            ) if chdir.exists() else [],
            "edits_md": edits_md_path.read_text(encoding="utf-8") if edits_md_path.exists() else None,
            "edits_parsed": edits_parsed,
            "canon_batch": canon_batch.read_text(encoding="utf-8") if canon_batch.exists() else None,
            "author_min": round(author_s / 60, 1),
            "machine_min": round(machine_s / 60, 1),
            "next": NEXT_STEP.get(st.state, "").format(n=n),
        }

    def draft(self, n: int, k: int) -> dict:
        return {"chapter": n, "draft": k, "text": self.ws.draft_path(n, k).read_text(encoding="utf-8")}

    def diff(self, n: int, k1: int, k2: int) -> dict:
        import difflib

        a = self.ws.draft_path(n, k1).read_text(encoding="utf-8").splitlines()
        b = self.ws.draft_path(n, k2).read_text(encoding="utf-8").splitlines()
        return {"lines": list(difflib.unified_diff(a, b, f"черновик_{k1}", f"черновик_{k2}", lineterm="", n=2))}

    def api_log(self, n: int = 30) -> list[dict]:
        from .apilog import read_log

        return read_log(self.ws.logs)[-n:]

    def window(self, n: int) -> dict:
        """Окно контекста главы + флаг превышения лимита (FR-C5)."""
        path = self.ws.window_path(n)
        flag = self.ws.chapter_dir(n) / "window_size_флаг.md"
        return {
            "text": path.read_text(encoding="utf-8") if path.exists() else None,
            "size_flag": flag.read_text(encoding="utf-8") if flag.exists() else None,
        }

    def prompt(self, n: int, kind: str) -> dict:
        """Промпт для ручного прогона (NFR-3): строится в памяти, НИЧЕГО не пишет (4.3).

        Возвращает текст, имя файла ответа и имя файла промпта — записать его
        на диск можно отдельным POST (`save_prompt`).
        """
        if kind == "verify2":
            system, user = verifier2.build_prompt(self.ws, n, ChapterState(self.ws, n).draft)
            text = f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n"
            return {"text": text, "target": "флаги.json", "file": "промпт_э2.md"}
        if kind == "edits":
            p = self.ws.chapter_dir(n) / "промпт_правок.md"
            if not p.exists():
                # промпт правок собирается из текущих правок и черновика
                from . import writer

                edits = review.load_edits(self.ws, n)
                text = writer.edit_prompt(self.ws, n, ChapterState(self.ws, n).draft, edits)
            else:
                text = p.read_text(encoding="utf-8")
            return {"text": text, "target": f"черновик_{ChapterState(self.ws, n).draft + 1}.md", "file": p.name}
        raise ValueError(f"неизвестный промпт: {kind} (допустимо: {', '.join(PROMPT_KINDS)})")

    def find(self, query: str) -> dict:
        from . import search

        groups = search.grouped(search.find(self.ws.exports, self.library, query))
        return {k: [{"ref": h.ref, "text": h.text} for h in v] for k, v in groups.items()}

    # ---------------------------------------------------------- изменения

    def save_prompt(self, n: int, kind: str) -> dict:
        """POST: тот же промпт, но с записью в папку главы (для ручного прогона из файла)."""
        from . import guard

        with self.jobs.exclusive():
            data = self.prompt(n, kind)
            path = self.ws.chapter_dir(n) / data["file"]
            guard.write_text(path, data["text"])
        return {**data, "saved": str(path)}

    def save_edits(self, n: int, text: str) -> dict:
        from . import guard

        with self.jobs.exclusive():
            guard.write_text(self.ws.chapter_dir(n) / "правки.md", text)
            edits = review.parse_edits_md(self.ws, n)
        return {"parsed": len(edits)}

    @staticmethod
    def _check_decision(decision: str, registry: str | None) -> None:
        from .canonist import REGISTRY_GLOBS

        if decision not in ("вычеркнуть", "канонизировать"):
            raise ValueError("решение: «вычеркнуть» или «канонизировать»")
        if decision == "канонизировать" and registry not in REGISTRY_GLOBS:
            raise ValueError(f"реестр: один из {', '.join(REGISTRY_GLOBS)}")

    @staticmethod
    def _decide(r, decision: str, registry: str | None) -> None:
        r.decision = decision
        r.target_registry = registry if decision == "канонизировать" else None

    def resolve(self, n: int, flag_id: str, decision: str, registry: str | None) -> dict:
        self._check_decision(decision, registry)
        with self.jobs.exclusive():
            resolutions = review.load_resolutions(self.ws, n)
            for r in resolutions:
                if r.flag_id == flag_id:
                    self._decide(r, decision, registry)
                    review.save_resolutions(self.ws, n, resolutions)
                    return {"ok": True}
        raise ValueError(f"самоволка {flag_id} не найдена")

    def resolve_all(self, n: int, decision: str, registry: str | None) -> dict:
        """Одно решение для всех самоволок без решения (5.6, «Вычеркнуть все»); уже решённые не трогаются."""
        self._check_decision(decision, registry)
        with self.jobs.exclusive():
            resolutions = review.load_resolutions(self.ws, n)
            todo = [r for r in resolutions if r.decision is None]
            for r in todo:
                self._decide(r, decision, registry)
            if todo:
                review.save_resolutions(self.ws, n, resolutions)
        return {"ok": True, "resolved": len(todo), "flag_ids": [r.flag_id for r in todo]}

    def save_canon_batch(self, n: int, text: str) -> dict:
        from . import guard

        with self.jobs.exclusive():
            guard.write_text(self.ws.chapter_dir(n) / "пакет_канона.md", text)
        return {"ok": True}

    def manual_draft(self, n: int, text: str) -> dict:
        """Ручной режим (NFR-3): вставленный ответ Писателя → следующий черновик.

        По состоянию FSM выбирается регистрация: генерация (write --manual)
        или внесение правок (apply-edits --manual). Состояние проверяется
        ДО записи файла; вторая одновременная отправка (двойной клик)
        отклоняется замком `exclusive()` — дубликата черновик_k+1 не будет.
        """
        if not text.strip():
            raise ValueError("пустой текст черновика")
        from . import guard

        with self.jobs.exclusive():
            st = ChapterState(self.ws, n)
            if st.state in ("собрано", "сгенерировано"):
                register = lambda: _job(tact.write, n, manual=True)  # noqa: E731
            elif st.state in ("на-приёмке", "дифф-контроль"):
                register = lambda: _job(tact.apply_edits, n, manual=True)  # noqa: E731
            else:
                raise ValueError(f"из состояния «{st.state}» черновик руками не принимается")
            k = st.draft + 1
            guard.write_text(self.ws.draft_path(n, k), text)
            output = _captured(register, "черновик не принят")
        return {"ok": True, "draft": k, "output": output}

    def manual_flags(self, n: int, text: str) -> dict:
        """Ручной режим Э2: вставленный ответ Верификатора-2 → флаги.json + verify2 --manual."""
        flags = verifier2.parse_flags(text)  # понимает JSON в прозе/```-блоке
        with self.jobs.exclusive():
            st = ChapterState(self.ws, n)
            if st.state != "верифицировано-1":  # проверка ДО перезаписи флаги.json (4.6)
                raise ValueError(f"из состояния «{st.state}» флаги Э2 руками не принимаются")
            verifier2.save_flags(self.ws, n, flags)
            _captured(lambda: _job(tact.verify2, n, manual=True), "флаги не приняты")
        return {"ok": True, "flags": len(flags)}

    def accept(self, n: int) -> dict:
        """Приёмка: подтверждение автор дал кнопкой + диалогом в панели (Д-8)."""
        with self.jobs.exclusive():
            output = _captured(lambda: _job(tact.accept, n, yes=True), "приёмка отклонена")
        return {"ok": True, "output": output}

    def rollback(self, n: int, to: str | None) -> dict:
        with self.jobs.exclusive():
            output = _captured(lambda: _job(canon.rollback, n, to=to, yes=True), "откат отклонён")
        return {"ok": True, "output": output}

    def circles(self) -> dict:
        from . import circles as circles_mod

        prompts_dir = self.ws.root / "драматургия" / "промпты"
        try:
            parts = exporter.load_parts(self.ws.exports)
        except FileNotFoundError:
            parts = []
        return {
            "circles": circles_mod.list_circles(self.ws),
            "canon_status": circles_mod.canon_status(self.ws),
            "in_canon": len(circles_mod.canon_circles(self.ws)),
            "acts": [a.model_dump() for a in circles_mod.act_list(self.ws)],
            "parts": parts,
            "prompts": sorted(p.name for p in prompts_dir.glob("*.md")) if prompts_dir.exists() else [],
        }

    def circle_prompt(self, stem: str) -> dict:
        path = self.ws.root / "драматургия" / "промпты" / f"{stem}.md"
        return {"text": path.read_text(encoding="utf-8")}

    def manual_circle(self, scope: str, key, text: str) -> dict:
        from . import circles as circles_mod

        if scope not in CIRCLE_SCOPES:
            raise ValueError(f"уровень круга: один из {', '.join(CIRCLE_SCOPES)}")
        if key is not None and (isinstance(key, bool) or not isinstance(key, int)):
            raise ValueError("ключ круга: целое число (номер акта/главы) или null")
        if scope == "книга" and key is not None:
            raise ValueError("у круга книги нет ключа")
        if scope != "книга" and key is None:
            raise ValueError(f"для уровня «{scope}» нужен номер")
        if not text.strip():
            raise ValueError("пустой ответ модели")
        with self.jobs.exclusive():
            path = circles_mod.accept_manual(self.ws, scope, key, text)
        return {"ok": True, "path": str(path)}

    # ------------------------------------------------------- канон и линтер

    def _on_canon_change(self, changed: list[str]) -> None:
        with self._cache_lock:
            self._canon_status_cache = None  # правка на диске: «незакоммичено» пересчитать при опросе
        self.request_lint(changed)

    def request_lint(self, changed: list[str] | None = None, wait: float = 0.0) -> None:
        """Ставит перепроверку канона в очередь единственного рабочего потока. Ни HTTP-обработчик, ни
        наблюдатель линтер сами не запускают (4.4–4.5): один исполнитель, экспорт под общим замком задач,
        любое исключение — находка ЛИНТ-0. `wait` — подождать результата (секунд), чтобы ответ на
        сохранение уже содержал свежую сводку."""
        self.start_lint_worker()
        with self._lint_lock:
            if changed:
                self.lint_changed = changed
            self.lint_pending = True
            self._lint_done.clear()
        self._lint_wake.set()
        if wait:
            self._lint_done.wait(wait)

    def _lint_worker(self) -> None:
        from . import lint as lint_mod

        while not self._lint_stop.is_set():
            self._lint_wake.wait()
            if self._lint_stop.is_set():
                return
            self._lint_wake.clear()
            while self.lint_pending and not self._lint_stop.is_set():
                try:
                    with self.jobs.exclusive():
                        with self._lint_lock:
                            self.lint_running = True
                            self.lint_pending = False
                        try:
                            report = lint_mod.run_lint(self.library, self.ws.exports, self.ws.logs, volume=self.ws.volume)
                        except Exception as e:  # noqa: BLE001 — сбой виден как находка
                            report = lint_mod.error_report(e, self.ws.logs)
                        finally:
                            with self._lint_lock:
                                self.lint_running = False
                        self.lint_report = report
                except RuntimeError:
                    # занято задачей (JobRunner) — подождём и повторим, не теряя запроса
                    self._lint_stop.wait(1.0)
                    continue
            self._lint_done.set()

    def start_lint_worker(self) -> None:
        if self._lint_thread is None:
            self._lint_thread = threading.Thread(target=self._lint_worker, daemon=True, name="canon-lint")
            self._lint_thread.start()

    def stop_lint_worker(self) -> None:
        self._lint_stop.set()
        self._lint_wake.set()

    def run_lint_now(self) -> None:
        """Совместимость: синхронная перепроверка через очередь (ждём результата)."""
        self.request_lint(wait=30.0)

    def lint(self) -> dict:
        """GET без побочных эффектов (аудит 4.3): только текущий отчёт и флаги очереди."""
        from . import lint as lint_mod

        fresh = lint_mod.load_report(self.ws.logs)
        if fresh and (not self.lint_report or fresh.ts != self.lint_report.ts):
            self.lint_report = fresh  # отчёт обновила задача `lint`/`lint-llm` или CLI
        return {
            "report": self.lint_report.model_dump() if self.lint_report else None,
            "changed": self.lint_changed,
            "running": self.lint_running,
            "pending": self.lint_pending,
        }

    def lint_summary(self) -> dict | None:
        r = self.lint_report
        return {"errors": r.errors, "warnings": r.warnings, "notes": r.notes, "ts": r.ts} if r else None

    def _canon_path(self, rel: str, *, for_write: bool = False) -> Path:
        """Путь документа канона по относительному имени. `for_write` — запрет создавать НОВЫЕ файлы
        в `Проза/` и в корне библиотеки (4.9): новая проза попадает в корпус только через приёмку
        главы (FSM, `konveyer canonize --apply`), новый документ канона автор кладёт файлом на диск;
        правка существующих документов из панели — можно."""
        if not rel or not rel.endswith(".md") or ".." in rel.split("/"):
            raise ValueError("документ канона: относительный путь к .md внутри библиотеки")
        lib = self.library.resolve()
        path = (lib / rel).resolve()
        if lib not in path.parents:
            raise ValueError("путь вне библиотеки")
        if for_write and not path.exists() and path.parent in (lib, lib / "Проза"):
            raise ValueError(
                f"новый файл «{rel}» из панели не создаётся: проза попадает в библиотеку только через приёмку "
                "главы (`canonize --apply`), новый документ канона — файлом на диске; здесь правятся существующие."
            )
        return path

    def canon_docs(self) -> dict:
        docs = []
        for p in sorted(self.library.rglob("*.md")):
            st = p.stat()
            docs.append({"path": str(p.relative_to(self.library)).replace("\\", "/"), "name": p.name,
                         "mtime": st.st_mtime_ns, "size": st.st_size})
        return {"docs": docs}

    @staticmethod
    def _version(text: str) -> str:
        """Версия документа для оптимистичной блокировки — хэш содержимого, а не mtime:
        наносекунды mtime не переживают JSON/JavaScript (double, 2⁵³) и шаг времени FAT/NTFS."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def canon_doc(self, rel: str) -> dict:
        path = self._canon_path(rel)
        if not path.exists():
            raise FileNotFoundError(f"нет документа {rel}")
        text = path.read_text(encoding="utf-8")
        return {"path": rel, "text": text, "version": self._version(text), "mtime": path.stat().st_mtime_ns}

    def save_canon_doc(self, rel: str, text: str, version: str | None) -> dict:
        """Правка канона автором из панели (сценарий Б): подтверждение дано диалогом, запись — в сессии канониста.
        `version` — хэш содержимого, которое автор открыл; расхождение = документ изменён на диске."""
        path = self._canon_path(rel, for_write=True)
        text = text if text.endswith("\n") else text + "\n"
        with self.jobs.exclusive():
            if version is not None and path.exists() and self._version(path.read_text(encoding="utf-8")) != version:
                raise VersionConflict("документ изменён на диске после открытия — перечитайте его, чтобы не затереть чужую правку")
            result = self._canon_change(lambda: guard.write_text(path, text), f"правка {rel} из панели", [rel])
        return {"saved": rel, "version": self._version(text), "mtime": path.stat().st_mtime_ns, "lint": self.lint_summary(),
                "canon_uncommitted": result.uncommitted, "canon_uncommitted_files": result.dirty_files}

    def _canon_change(self, writer, message: str, changed: list[str]) -> canonchange.ChangeResult:
        """Изменение канона из панели — единым конвейером (п. 25) БЕЗ коммита: сессия записи → выгрузки →
        линт (сводка сразу в ответе, без очереди наблюдателя) → состояние «незакоммичено» в /api/state;
        коммит — отдельным действием автора («Закоммитить канон» / `konveyer canon-commit`).
        Вызывать под `jobs.exclusive()`; подтверждение автор дал диалогом в панели (Д-8)."""
        result = canonchange.canon_change(
            self.ws, self.cfg, self.library, writer, message, commit=False, author_confirmed=True,
        )
        self.watcher._snapshot = self.watcher._scan()  # своя запись — не «внешнее» изменение
        with self._lint_lock:
            self.lint_report = result.lint
            self.lint_changed = changed
        self.invalidate_caches()
        return result

    def apply_lint_fix(self, fix_data: dict) -> dict:
        """Применяет ровно то исправление, которое автор видел и подтвердил (file/line/old/new),
        а не элемент списка по индексу — отчёт мог перестроиться наблюдателем между показом и кликом."""
        from . import lint as lint_mod
        from .schemas import LintFix

        try:
            fix = LintFix(**{k: fix_data[k] for k in ("file", "line", "old", "new", "note") if fix_data.get(k) is not None})
        except (TypeError, ValidationError) as e:
            raise ValueError(f"некорректное исправление: {e}") from None
        with self.jobs.exclusive():
            result = self._canon_change(lambda: lint_mod.apply_fix(self.library, fix), f"исправление линтера: {fix.file}", [fix.file])
        return {"applied": fix.model_dump(), "lint": self.lint_summary(),
                "canon_uncommitted": result.uncommitted, "canon_uncommitted_files": result.dirty_files}

    def run_command(self, cmd: str, chapter: int | None, params: dict | None = None) -> dict:
        """Долгие шаги такта — фоновой задачей с захватом вывода."""
        if cmd not in COMMANDS:
            raise ValueError(f"неизвестная команда: {cmd}")
        if chapter is not None and (isinstance(chapter, bool) or not isinstance(chapter, int)):
            raise ValueError("номер главы: целое число")
        params = params if isinstance(params, dict) else {}
        # функции ядра (konveyer/steps) вызываются напрямую, как обычные; их исключения переводит `_run`
        fns = {
            "story-circles": lambda: _job(
                quality.circles, params.get("scope", "всё"), chapter=params.get("chapter"), redo=bool(params.get("redo")),
                to_canon=False, yes=True,
            ),
            # подтверждение автор дал диалогом в панели (Д-8)
            "circles-canon": lambda: _job(quality.circles, "всё", chapter=None, redo=False, to_canon=True, yes=True),
            "run": lambda: _job(tact.run, chapter),  # машинные шаги до паузы автора (FR-O1)
            "export": lambda: _job(tact.export),
            "compile": lambda: _job(tact.compile, chapter),
            "write": lambda: _job(tact.write, chapter, manual=False),
            "verify1": lambda: _job(tact.verify1, chapter),
            "verify2": lambda: _job(tact.verify2, chapter, manual=False),
            "review": lambda: _job(tact.review, chapter),
            "apply-edits": lambda: _job(tact.apply_edits, chapter, manual=False),
            "diff-check": lambda: _job(tact.diff_check, chapter, author_fix=False),
            # автор правил текст сам — расхождения не самоволия (подтверждено диалогом в панели)
            "diff-check-author": lambda: _job(tact.diff_check, chapter, author_fix=True),
            "regress": lambda: _job(quality.regress, llm=False),
            "canonize": lambda: _job(tact.canonize, chapter, apply=False, yes=True),
            # подтверждение автор дал кнопкой + диалогом в панели (Д-8)
            "canonize-apply": lambda: _job(tact.canonize, chapter, apply=True, yes=True),
            # без --strict: ошибки канона — находки в отчёте, а не «ошибка» задачи
            "lint": lambda: _job(canon.lint, llm=False, files=[], watch=False, max_calls=40),
            "lint-llm": lambda: _job(canon.lint, llm=True, files=list(params.get("files") or []), watch=False, max_calls=40),
            # подтверждение автор дал диалогом в панели (Д-8); сообщение — из поля панели
            "canon-commit": lambda: _job(canon.canon_commit, message=str(params.get("message") or "правка канона из панели"), yes=True),
        }
        self.jobs.start(cmd, chapter, fns[cmd])
        return self.jobs.summary()  # type: ignore[return-value]


def _static_root() -> Path:
    # resolve(): статика может лежать за симлинком (pip -e, venv) — иначе 403 на всё
    return Path(str(resources.files("konveyer").joinpath("data/панель"))).resolve()


MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".map": "application/json"}


class _BodyTooLarge(Exception):
    pass


class VersionConflict(RuntimeError):
    """Документ канона изменён на диске после того, как автор его открыл (версия = sha256 содержимого) → 409."""


def _local_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _sanitize(message: str, api: PanelAPI) -> str:
    """Сообщение об ошибке без абсолютных путей машины автора (5.4): корень рабочей области и
    библиотеки заменяются словами. Порядок — от длинного к короткому, чтобы вложенный путь
    библиотеки не превратился в «рабочая область/Библиотека»."""
    pairs: list[tuple[str, str]] = []
    for root, word in ((api.library, "библиотека"), (api.ws.root, "рабочая область")):
        forms = {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}
        # Windows: в тексте исключения путь бывает в виде repr — с удвоенными «\\»
        forms |= {v.replace("\\", "\\\\") for v in forms if "\\" in v}
        for variant in forms:
            if variant and variant not in ("/", "."):
                pairs.append((variant, word))
    for variant, word in sorted(pairs, key=lambda p: -len(p[0])):
        message = message.replace(variant + "/", word + "/").replace(variant + "\\", word + "/").replace(variant, word)
    return message


def _log_exception(api: PanelAPI, method: str, path: str) -> None:
    """Трейсбек 500-й ошибки — в журналы/панель.log рабочей области (лучшее из возможного: сбой записи лога
    не должен прятать сам ответ)."""
    try:
        api.ws.logs.mkdir(parents=True, exist_ok=True)
        with (api.ws.logs / "панель.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {method} {path}\n{traceback.format_exc()}\n")
    except Exception:  # noqa: BLE001
        pass


def make_handler(api: PanelAPI):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # тихий сервер
            pass

        # ------------------------------------------------------- helpers

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

        def _error(self, message: str, code: int = 400) -> None:
            self._json({"error": _sanitize(message, api)}, code)

        def _internal(self, e: Exception) -> None:
            """500: автору — короткое сообщение, трейсбек — в журналы/панель.log (не в браузер)."""
            _log_exception(api, self.command, self.path)
            self._error(f"внутренняя ошибка сервера: {e} — подробности в журналы/панель.log", 500)

        def _port(self) -> int:
            return int(self.server.server_address[1])

        def _host_ok(self) -> bool:
            """Host обязан быть локальным: DNS-rebinding приходит с чужим именем (4.2)."""
            host = (self.headers.get("Host") or "").strip().lower()
            return host in _local_hosts(self._port())

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            allowed = {f"http://{h}" for h in _local_hosts(self._port())}
            return origin.strip().lower() in allowed

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("некорректный Content-Length") from None
            if length > MAX_BODY:
                raise _BodyTooLarge()
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("тело запроса должно быть JSON-объектом")
            return data

        # --------------------------------------------------------- GET

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._error("запрос не с локального адреса панели (Host)", 403)
            try:
                path = self.path.split("?")[0]
                if path == "/api/state":
                    return self._json(api.state())
                m = re.fullmatch(r"/api/chapter/(\d+)", path)
                if m:
                    return self._json(api.chapter(int(m.group(1))))
                m = re.fullmatch(r"/api/chapter/(\d+)/draft/(\d+)", path)
                if m:
                    return self._json(api.draft(int(m.group(1)), int(m.group(2))))
                m = re.fullmatch(r"/api/chapter/(\d+)/diff/(\d+)/(\d+)", path)
                if m:
                    return self._json(api.diff(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                m = re.fullmatch(r"/api/chapter/(\d+)/window", path)
                if m:
                    return self._json(api.window(int(m.group(1))))
                m = re.fullmatch(r"/api/chapter/(\d+)/prompt/(\w+)", path)
                if m:
                    return self._json(api.prompt(int(m.group(1)), m.group(2)))
                if path == "/api/find":
                    from urllib.parse import parse_qs, urlparse

                    q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                    return self._json(api.find(q))
                if path == "/api/circles":
                    return self._json(api.circles())
                m = re.fullmatch(r"/api/circles/prompt/([\w\-]+)", path)
                if m:
                    return self._json(api.circle_prompt(m.group(1)))
                if path == "/api/lint":
                    return self._json(api.lint())
                if path == "/api/canon":
                    return self._json(api.canon_docs())
                if path == "/api/canon/doc":
                    from urllib.parse import parse_qs, urlparse

                    rel = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                    return self._json(api.canon_doc(rel))
                if path == "/api/log":
                    return self._json(api.api_log())
                if path == "/api/job":
                    return self._json(api.jobs.full())
                if path.startswith("/api/"):
                    return self._error("нет такого пути API", 404)  # не index.html с 200 (5.4)
                if path == "/dashboard":
                    from . import dashboard

                    # в памяти: GET не пишет дашборд.html (4.3); файл пишет `konveyer dashboard`
                    return self._send(200, dashboard.render_dashboard(api.ws).encode("utf-8"), "text/html")
                return self._static(path)
            except FileNotFoundError as e:
                self._error(str(e), 404)
            except Busy as e:
                self._error(str(e), 423)
            except ValueError as e:
                self._error(str(e), 400)
            except Exception as e:
                self._internal(e)

        def _static(self, path: str) -> None:
            root = _static_root()
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (root / rel).resolve()
            if root not in target.parents and target != root:
                return self._error("вне статики", 403)
            if not target.is_file():
                target = root / "index.html"  # SPA-роутинг
                if not target.is_file():
                    return self._error("панель не собрана (нет konveyer/data/панель) — соберите panel/ или переустановите пакет", 404)
            self._send(200, target.read_bytes(), MIME.get(target.suffix, "application/octet-stream"))

        # --------------------------------------------------------- POST

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._error("запрос не с локального адреса панели (Host)", 403)
            if self.headers.get("X-Konveyer-Panel") != "1":
                return self._error("нет заголовка X-Konveyer-Panel (защита от cross-origin)", 403)
            if not self._origin_ok():
                return self._error("чужой Origin (защита от cross-origin)", 403)
            try:
                body = self._body()
                path = self.path.split("?")[0]
                if path == "/api/command":
                    job = api.run_command(body.get("cmd", ""), body.get("chapter"), body.get("params"))
                    return self._json({"job": job})
                if path == "/api/job/cancel":
                    return self._json({"job": api.jobs.cancel()})
                m = re.fullmatch(r"/api/chapter/(\d+)/resolve-all", path)
                if m:
                    return self._json(api.resolve_all(int(m.group(1)), body.get("decision", ""), body.get("registry")))
                m = re.fullmatch(r"/api/chapter/(\d+)/edits", path)
                if m:
                    return self._json(api.save_edits(int(m.group(1)), str(body.get("text", ""))))
                m = re.fullmatch(r"/api/chapter/(\d+)/resolve", path)
                if m:
                    return self._json(
                        api.resolve(int(m.group(1)), body.get("flag_id", ""), body.get("decision", ""), body.get("registry"))
                    )
                m = re.fullmatch(r"/api/chapter/(\d+)/canon-batch", path)
                if m:
                    return self._json(api.save_canon_batch(int(m.group(1)), str(body.get("text", ""))))
                m = re.fullmatch(r"/api/chapter/(\d+)/prompt/(\w+)", path)
                if m:
                    return self._json(api.save_prompt(int(m.group(1)), m.group(2)))
                if path == "/api/canon/doc":
                    version = body.get("version")
                    return self._json(api.save_canon_doc(str(body.get("path", "")), str(body.get("text", "")),
                                                         str(version) if isinstance(version, str) else None))
                if path == "/api/lint/fix":
                    fix = body.get("fix")
                    if not isinstance(fix, dict):
                        raise ValueError("нужно исправление {file, line, old, new}")
                    return self._json(api.apply_lint_fix(fix))
                if path == "/api/circles/manual":
                    return self._json(api.manual_circle(body.get("scope", ""), body.get("key"), str(body.get("text", ""))))
                m = re.fullmatch(r"/api/chapter/(\d+)/manual-draft", path)
                if m:
                    return self._json(api.manual_draft(int(m.group(1)), str(body.get("text", ""))))
                m = re.fullmatch(r"/api/chapter/(\d+)/manual-flags", path)
                if m:
                    return self._json(api.manual_flags(int(m.group(1)), str(body.get("text", ""))))
                m = re.fullmatch(r"/api/chapter/(\d+)/accept", path)
                if m:
                    return self._json(api.accept(int(m.group(1))))
                m = re.fullmatch(r"/api/chapter/(\d+)/rollback", path)
                if m:
                    return self._json(api.rollback(int(m.group(1)), body.get("to")))
                return self._error("неизвестный путь", 404)
            except _BodyTooLarge:
                self.close_connection = True
                self._error(f"тело запроса больше {MAX_BODY // (1024 * 1024)} МБ", 413)
            except VersionConflict as e:
                # отдельный код: панель предлагает «различия / перечитать / перезаписать» (аудит 5.2)
                self._json({"error": _sanitize(str(e), api), "code": "конфликт"}, 409)
            except FileNotFoundError as e:
                self._error(str(e), 404)
            except Busy as e:
                # сервер занят задачей/операцией: панель показывает «занят», а не «ошибка ввода» (5.4)
                self._error(str(e), 423)
            except (ValueError, RuntimeError) as e:
                self._error(str(e), 400)
            except Exception as e:
                self._internal(e)

    return Handler


def serve(ws: Workspace, cfg: Config, library: Path, port: int = 8765, watch: bool = True) -> ThreadingHTTPServer:
    """Создаёт сервер на 127.0.0.1 (не запускает цикл — это делает вызывающий)."""
    api = PanelAPI(ws, cfg, library)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    server.api = api  # type: ignore[attr-defined]
    if watch:
        api.start_lint_worker()
        api.watcher.start()  # линтер канона в реальном времени
    return server
