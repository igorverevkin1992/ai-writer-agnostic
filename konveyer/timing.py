"""Время такта из истории FSM: машинное против авторского (FR-CT-2; критерий приёмки 7 — такт ≤ 40 минут автора).

Такт главы должен укладываться в ≤40 минут работы автора (§14.3, п. 7).
История переходов в состояние.yaml хранит таймстемпы; правило разделения — по задаче:

* интервал между двумя переходами, записанными ВНУТРИ ОДНОЙ задачи (`konveyer run`, кнопка панели,
  авто-повторы Э1 одной команды) — машинное время;
* любой другой интервал — авторское: это ожидание действия автора (клик, правки, приёмка,
  подпись пакета), включая простой в «собрано»/«сгенерировано», когда шаги запускались по одному.

Задача помечается полем «задача» в записи перехода (`ChapterState._record`): `<имя>@<ISO старта>`.
Ставит его контекст `timing.job(...)` — команды CLI входят в него сами (`_friendly`), сервер панели
может выставить `timing.current_job` на время фоновой задачи. Если следующий переход принадлежит
другой задаче, но известен её старт, интервал делится: до старта — авторское, после — машинное.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

# Идентификатор текущей задачи (одна фоновая задача за раз) — пусто вне задачи.
current_job: str | None = None


def new_job_id(name: str) -> str:
    return f"{name}@{datetime.now(timezone.utc).isoformat()}"


@contextmanager
def job(name: str) -> Iterator[str | None]:
    """Контекст задачи: вложенные вызовы (команды внутри `run`) наследуют внешнюю задачу."""
    global current_job
    if current_job is not None:
        yield current_job
        return
    current_job = new_job_id(name)
    try:
        yield current_job
    finally:
        current_job = None


def _parse(ts: str) -> datetime:
    """ISO-время записи истории; запись без зоны (правка руками, старый формат) считается UTC —
    иначе вычитание «наивного» и «зонного» времени роняло бы весь учёт (`учёт`, `том статус`, дашборд)."""
    dt = datetime.fromisoformat(str(ts))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _job_start(rec: dict) -> datetime | None:
    job_id = rec.get("задача")
    if not job_id or "@" not in str(job_id):
        return None
    try:
        return _parse(str(job_id).rsplit("@", 1)[1])
    except ValueError:
        return None


# Авторская пауза длиннее этого — перерыв (ушёл, ночь, другой день), а не работа над главой:
# критерий «≤ 40 мин автора на такт» измеряет работу, а не время между сеансами.
MAX_AUTHOR_PAUSE_S = 2 * 3600


def set_pause_threshold(minutes: int | float) -> None:
    """Порог перерыва из конфига (`пауза_автора_мин`, FR-CT-2); выставляется в `steps.common._ctx()`."""
    global MAX_AUTHOR_PAUSE_S
    MAX_AUTHOR_PAUSE_S = float(minutes) * 60


def _is_break(t0: datetime, t1: datetime) -> bool:
    if (t1 - t0).total_seconds() > MAX_AUTHOR_PAUSE_S:
        return True
    a = t0.astimezone() if t0.tzinfo else t0
    b = t1.astimezone() if t1.tzinfo else t1
    return a.date() != b.date()


def intervals(history: list[dict]) -> list[tuple[str, float, datetime]]:
    """Завершённые интервалы истории: (вид «машинное»|«авторское»|«перерыв», секунды, момент конца).

    Текущее незакрытое состояние не учитывается — пауза ещё идёт. Авторская пауза дольше
    MAX_AUTHOR_PAUSE_S или через границу дня — «перерыв», в время такта не входит."""
    out: list[tuple[str, float, datetime]] = []
    for cur, nxt in zip(history, history[1:], strict=False):  # пары соседей
        try:
            t0, t1 = _parse(cur["время"]), _parse(nxt["время"])
        except (KeyError, ValueError, TypeError):
            continue
        delta = (t1 - t0).total_seconds()
        if delta < 0:
            continue
        same_job = bool(cur.get("задача")) and cur.get("задача") == nxt.get("задача")
        if same_job:
            out.append(("машинное", delta, t1))
            continue
        start = _job_start(nxt)
        if start is not None and t0 <= start <= t1:
            out.append(("перерыв" if _is_break(t0, start) else "авторское", (start - t0).total_seconds(), start))
            out.append(("машинное", (t1 - start).total_seconds(), t1))
        else:
            out.append(("перерыв" if _is_break(t0, t1) else "авторское", delta, t1))
    return out


def chapter_times(history: list[dict]) -> tuple[float, float]:
    """(машинное_с, авторское_с) по завершённым интервалам истории."""
    machine = 0.0
    author = 0.0
    for kind, secs, _ in intervals(history):
        if kind == "авторское":
            author += secs
        elif kind == "машинное":
            machine += secs
    return machine, author


def today_author_minutes(ws, today=None) -> float:
    """Сумма авторского времени за сегодня (локальная дата) по всем главам, в минутах.
    Интервал относится к дню, в котором он закончился."""
    from .fsm import ChapterState, StatusFileError

    today = today or datetime.now().astimezone().date()
    total = 0.0
    for n, _ in ws.chapter_dirs():  # главы текущего тома
        try:
            st = ChapterState(ws, n)
        except StatusFileError:
            continue  # одна повреждённая глава не ломает сводку
        for kind, secs, end in intervals(st.data.get("история", [])):
            if kind != "авторское":
                continue
            end_local = end.astimezone() if end.tzinfo else end
            if end_local.date() == today:
                total += secs
    return round(total / 60, 1)


def fmt_minutes(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} с"
    return f"{seconds / 60:.1f} мин"
