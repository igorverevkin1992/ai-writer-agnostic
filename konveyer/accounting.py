"""Учёт времени и денег (FR-CT-1…FR-CT-3, FR-EC-1…FR-EC-4): сводки по главе, тому и ролям, прогноз остатка тома,
предупреждения по порогам конфига (стоимость главы, расход за сутки, размер окна).

Стоимость — только из журнала `журналы/api.jsonl` (токены × цены конфига); время автора — из истории состояний
глав (`timing`): переход внутри одной задачи — машинное, пауза дольше порога или через границу суток — перерыв.
Стоимость тома — сумма ВСЕХ строк журнала тома (и вызовов без номера главы: линтер, регрессия, круги истории,
пере-тест), так что сводка сходится с журналом (приёмка FR-CT-3). Ориентиры экономики (FR-EC-3) пересчитываются
из конфига и фактов журнала, а не зашиты.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import apilog, timing
from .config import ROLE_ALIASES, ROLES, Config, ModelConfig
from .fsm import ChapterState, StatusFileError
from .paths import Workspace
from .schemas import Brief

OUTSIDE = "— (вне глав)"   # строка сводки для вызовов без номера главы


@dataclass
class ChapterAccount:
    chapter: int
    cost: float = 0.0
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    machine_s: float = 0.0
    author_s: float = 0.0
    state: str = "не-начато"


@dataclass
class VolumeAccount:
    volume: int
    chapters: dict[int, ChapterAccount] = field(default_factory=dict)
    by_role: dict[str, float] = field(default_factory=dict)
    calls_by_role: dict[str, int] = field(default_factory=dict)
    chapters_total: int | None = None      # None — поглавник тома недоступен (нет выгрузок/документов)
    other_cost: float = 0.0                # вызовы без номера главы (линтер, регрессия, круги, пере-тест)
    other_calls: int = 0
    other_tokens_out: int = 0

    @property
    def cost(self) -> float:
        """Стоимость тома = сумма всех строк журнала тома (по главам + вне глав)."""
        return round(sum(c.cost for c in self.chapters.values()) + self.other_cost, 4)

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.chapters.values()) + self.other_calls

    @property
    def fixed(self) -> list[int]:
        return sorted(n for n, c in self.chapters.items() if c.state == "зафиксировано")

    @property
    def author_s(self) -> float:
        return sum(c.author_s for c in self.chapters.values())

    @property
    def machine_s(self) -> float:
        return sum(c.machine_s for c in self.chapters.values())

    def forecast(self) -> dict:
        """Прогноз остатка тома: средняя стоимость и авторское время по зафиксированным главам × оставшиеся главы."""
        done = [self.chapters[n] for n in self.fixed]
        if self.chapters_total is None:
            return {"осталось_глав": None, "стоимость": None, "время_автора_с": None,
                    "основание": "поглавник тома не выгружен — прогноз недоступен (`konveyer экспорт`)"}
        left = max(0, self.chapters_total - len(done))
        if not done:
            return {"осталось_глав": left, "стоимость": None, "время_автора_с": None,
                    "основание": "нет зафиксированных глав — прогноза пока нет"}
        avg_cost = sum(c.cost for c in done) / len(done)
        avg_author = sum(c.author_s for c in done) / len(done)
        return {"осталось_глав": left, "стоимость": round(avg_cost * left, 2), "время_автора_с": round(avg_author * left),
                "основание": f"среднее по {len(done)} зафиксированным главам: {avg_cost:.2f} $ и "
                             f"{timing.fmt_minutes(avg_author)} автора на главу"}


# Имена ролей в журнале API → роль конфига (`config.ROLES`): журнал пишет человеческие имена
# («верификатор-2 (повторно)», «аналитик драматургии», «линтер канона»), сводка — по ролям конфига.
_ROLE_BY_LOG_NAME = {
    "верификатор-2": "верификатор2", "верификатор 2": "верификатор2", "вкус": "верификатор2",
    "аналитик драматургии": "аналитик", "линтер канона": "линтер",
}


def normalize_role(name: str) -> str:
    """«писатель (правки)» → «писатель», «верификатор-2 (регрессия)» → «верификатор2», «пере-тест (писатель)» → «писатель»."""
    base = str(name or "—").strip()
    sub = ""
    if " (" in base and base.endswith(")"):
        base, sub = base[: base.index(" (")], base[base.index(" (") + 2:-1]
    base = base.strip().lower()
    if base == "пере-тест" and sub:
        base = sub.strip().lower()
    base = ROLE_ALIASES.get(base, _ROLE_BY_LOG_NAME.get(base, base))
    if base in ROLES:
        return base
    compact = base.replace("-", "").replace(" ", "")
    for role in ROLES:
        if compact == role or compact.startswith(role):
            return role
    return base or "—"


def _rows(ws: Workspace, volume: int) -> list[dict]:
    out = []
    for row in apilog.read_log(ws.logs):
        vol = row.get("volume")
        try:
            row_vol = int(vol if vol is not None else 1)
        except (TypeError, ValueError):
            continue
        if row_vol != volume:
            continue
        out.append(row)
    return out


@contextmanager
def volume_exports(ws: Workspace, library: Path | None, volume: int) -> Iterator[Path | None]:
    """Папка выгрузок тома `volume`: рабочие `выгрузки/`, если они этого тома; иначе — экспорт тома во
    временную папку (документы тома читаются из библиотеки, рабочие выгрузки и журналы не трогаются).
    None — выгрузок тома получить нельзя (нет библиотеки, нет документов тома, сломана разметка): сводка
    без поглавника, а не отказ и не нули как факт (П-5)."""
    from . import exporter

    if exporter.export_volume(ws.exports) == volume and (ws.exports / "briefs.json").exists():
        yield ws.exports
        return
    if library is None or not library.exists():
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="konveyer_том_") as tmp:
        root = Path(tmp)
        try:
            exporter.run_export(library, root / "выгрузки", root / "журналы", volume, ws.root)
        except Exception:  # noqa: BLE001 — документов тома нет или разметка сломана
            yield None
            return
        yield root / "выгрузки"


def volume_briefs(ws: Workspace, library: Path | None, volume: int) -> list[Brief] | None:
    """Поглавник тома (см. `volume_exports`); None — недоступен."""
    from . import exporter

    with volume_exports(ws, library, volume) as exports:
        if exports is None:
            return None
        try:
            return [b for b in exporter.load_briefs(exports) if b.volume == volume]
        except FileNotFoundError:
            return None


def volume_account(ws: Workspace, volume: int | None = None, chapters_total: int | None = None,
                   library: Path | None = None) -> VolumeAccount:
    volume = ws.volume if volume is None else int(volume)
    acc = VolumeAccount(volume=volume)
    vws = ws.for_volume(volume)
    for n, _ in vws.chapter_dirs():
        try:
            st = ChapterState(vws, n)
        except StatusFileError:  # битый состояние.yaml одной главы не должен ронять учёт тома (П-5)
            acc.chapters[n] = ChapterAccount(chapter=n, state="повреждено")
            continue
        machine, author = timing.chapter_times(st.data.get("история", []))
        acc.chapters[st.chapter] = ChapterAccount(chapter=st.chapter, machine_s=machine, author_s=author, state=st.state)
    for row in _rows(ws, volume):
        ch = row.get("chapter")
        cost = float(row.get("cost_est") or 0.0)
        role = normalize_role(row.get("role"))
        acc.by_role[role] = round(acc.by_role.get(role, 0.0) + cost, 4)
        acc.calls_by_role[role] = acc.calls_by_role.get(role, 0) + 1
        if ch is None:
            acc.other_cost = round(acc.other_cost + cost, 4)
            acc.other_calls += 1
            acc.other_tokens_out += int(row.get("tokens_out") or 0)
            continue
        c = acc.chapters.setdefault(int(ch), ChapterAccount(chapter=int(ch)))
        c.cost = round(c.cost + cost, 4)
        c.calls += 1
        c.tokens_in += int(row.get("tokens_in") or 0)
        c.tokens_out += int(row.get("tokens_out") or 0)
    if chapters_total is None:
        briefs = volume_briefs(ws, library, volume)
        chapters_total = len(briefs) if briefs is not None else None
    acc.chapters_total = chapters_total
    return acc


def _local_date(ts: datetime):
    return (ts.astimezone() if ts.tzinfo else ts).date()


def today_cost(ws: Workspace, today: datetime | None = None) -> float:
    """Расход за текущие сутки по всем томам — по ЛОКАЛЬНОЙ дате (как минуты автора в `timing`, FR-EC-2)."""
    day = _local_date(today) if today is not None else datetime.now().astimezone().date()
    total = 0.0
    for row in apilog.read_log(ws.logs):
        try:
            ts = datetime.fromisoformat(str(row.get("ts", "")))
        except ValueError:
            continue
        if _local_date(ts) == day:
            total += float(row.get("cost_est") or 0.0)
    return round(total, 4)


def estimate_before(mc: ModelConfig, prompt_chars: int, expected_out_tokens: int = 2000) -> float | None:
    """Оценка стоимости до вызова (FR-EC-1): по размеру промпта и ценам конфига; без цен — None."""
    from .adapters import estimate_cost_before

    return estimate_cost_before(mc, prompt_chars, expected_out_tokens)


def warnings(ws: Workspace, cfg: Config, chapter: int | None = None, prompt_chars: int | None = None,
             role_model: ModelConfig | None = None) -> list[str]:
    """Предупреждения экономики (FR-EC-2): стоимость главы выше порога, расход за сутки, окно выше лимита.
    Порог 0 — не предупреждать."""
    out: list[str] = []
    th = cfg.thresholds
    if chapter is not None and th.chapter_cost_usd:
        acc = volume_account(ws, chapters_total=0)
        spent = acc.chapters.get(chapter, ChapterAccount(chapter=chapter)).cost
        est = estimate_before(role_model, prompt_chars) if (role_model and prompt_chars) else None
        expected = spent + (est or 0.0)
        if expected > th.chapter_cost_usd:
            out.append(f"стоимость главы {chapter}: потрачено {spent:.2f} $"
                       + (f" + оценка вызова {est:.2f} $" if est else "") + f" > порога {th.chapter_cost_usd:.2f} $")
    if th.daily_cost_usd:
        spent_today = today_cost(ws)
        if spent_today > th.daily_cost_usd:
            out.append(f"расход за сутки {spent_today:.2f} $ > порога {th.daily_cost_usd:.2f} $")
    if prompt_chars is not None and prompt_chars > cfg.window_soft_limit_chars:
        out.append(f"окно {prompt_chars} знаков > мягкого лимита {cfg.window_soft_limit_chars} (сократите секции окна)")
    return out


# ------------------------------------------------------------------ ориентиры экономики (FR-EC-3)


@dataclass
class Guidelines:
    """Ориентиры планирования, пересчитанные из конфига и фактов (FR-EC-3); None — цен нет."""

    window_chars: int
    out_tokens: int
    window_source: str
    out_source: str
    generation: float | None       # одна генерация Писателя
    cycle: float | None            # один цикл правок: правки Писателем + повторная проверка Э2
    chapter_simple: float | None   # одна генерация + один цикл + приёмка
    chapter_real: float | None     # две генерации + два цикла + приёмка
    volume: float | None           # том по поглавнику
    series: float | None           # серия по плану томов манифеста
    chapters_total: int | None
    volumes_total: int | None
    writer_share_est: float | None  # доля Писателя в оценке главы
    writer_share_fact: float | None  # доля Писателя в фактических расходах тома
    frames_fact: float | None        # каркасы тома — факт по журналу (роль аналитика)
    unpriced: list[str]              # роли без цен в конфиге


def _avg_window_chars(ws: Workspace) -> int | None:
    sizes = []
    for _, d in ws.chapter_dirs():
        p = d / "окно.md"
        if p.exists():
            try:
                sizes.append(len(p.read_text(encoding="utf-8")))
            except OSError:
                continue
    return round(sum(sizes) / len(sizes)) if sizes else None


def _avg_writer_out_tokens(ws: Workspace, volume: int) -> int | None:
    outs = [int(r["tokens_out"]) for r in _rows(ws, volume)
            if normalize_role(r.get("role")) == "писатель" and r.get("tokens_out") and not r.get("error")]
    return round(sum(outs) / len(outs)) if outs else None


def guidelines(ws: Workspace, cfg: Config, acc: VolumeAccount) -> Guidelines:
    window = _avg_window_chars(ws)
    out = _avg_writer_out_tokens(ws, acc.volume)
    window_chars = window or cfg.guidelines.window_chars
    out_tokens = out or cfg.guidelines.out_tokens
    window_source = "среднее по главы/*/окно.md" if window else "конфиг (ориентиры.окно_знаков)"
    out_source = "среднее по журналу API" if out else "конфиг (ориентиры.выход_токенов)"
    w, v2, canon = cfg.writer, cfg.verifier2, cfg.canonist
    unpriced = [r for r, m in cfg.roles().items() if r in ("писатель", "верификатор2", "канонист")
                and not (m.price_in_per_1m or m.price_out_per_1m)]
    gen = estimate_before(w, window_chars, out_tokens)
    if gen is None:
        return Guidelines(window_chars, out_tokens, window_source, out_source, None, None, None, None, None, None,
                          acc.chapters_total, _volumes_total(ws), None, _writer_share(acc), _frames_fact(acc), unpriced)
    text_chars = out_tokens * 3
    check = estimate_before(v2, window_chars + text_chars, cfg.guidelines.check_out_tokens) or 0.0
    edits = estimate_before(w, window_chars + text_chars, out_tokens) or 0.0
    accept = estimate_before(canon, window_chars + text_chars, cfg.guidelines.check_out_tokens) or 0.0
    cycle = edits + check
    simple = gen + check + cycle + accept
    real = 2 * (gen + check) + 2 * cycle + accept
    writer_est = (2 * gen + 2 * edits) / real if real else None
    volumes_total = _volumes_total(ws)
    vol = round(real * acc.chapters_total, 2) if acc.chapters_total else None
    series = round(vol * volumes_total, 2) if vol is not None and volumes_total else None
    return Guidelines(window_chars, out_tokens, window_source, out_source, round(gen, 4), round(cycle, 4),
                      round(simple, 3), round(real, 3), vol, series, acc.chapters_total, volumes_total,
                      round(writer_est, 2) if writer_est is not None else None, _writer_share(acc), _frames_fact(acc), unpriced)


def _volumes_total(ws: Workspace) -> int | None:
    from . import manifest as manifest_mod

    try:
        man = manifest_mod.load(ws.root)
    except ValueError:
        return None
    return int(man.проект.томов_план) if man is not None else None


def _writer_share(acc: VolumeAccount) -> float | None:
    return round(acc.by_role.get("писатель", 0.0) / acc.cost, 2) if acc.cost > 0 else None


def _frames_fact(acc: VolumeAccount) -> float | None:
    return acc.by_role.get("аналитик") if "аналитик" in acc.by_role else None


def _money(v: float | None, digits: int = 2) -> str:
    return f"{v:.{digits}f} $" if v is not None else "—"


def render_guidelines(g: Guidelines) -> list[str]:
    lines = ["## Ориентиры экономики (FR-EC-3, пересчитаны из конфига и журнала)", ""]
    lines.append(f"- Окно Писателя ≈ {g.window_chars} знаков ({g.window_source}); выход ≈ {g.out_tokens} токенов ({g.out_source}).")
    if g.generation is None:
        lines.append("- Цены Писателя в конфиг.yaml не заданы (price_in_per_1m / price_out_per_1m или цена_вход_1м / "
                     "цена_выход_1м) — стоимостные ориентиры не считаются.")
    else:
        lines.append(f"- Одна генерация Писателя ≈ {_money(g.generation, 4)}; один цикл правок (правки + повторная проверка Э2) "
                     f"≈ {_money(g.cycle, 4)}.")
        lines.append(f"- Глава при одной генерации и одном цикле правок ≈ {_money(g.chapter_simple, 3)}; "
                     f"реалистично (две генерации, два цикла) ≈ {_money(g.chapter_real, 3)}.")
        lines.append(
            f"- Том: {'≈ ' + _money(g.volume) + f' ({g.chapters_total} гл. по поглавнику)' if g.volume is not None else 'поглавник тома не выгружен — не считается'}"
            + (f"; серия из {g.volumes_total} т. (томов_план манифеста) ≈ {_money(g.series)}." if g.series is not None else ".")
        )
        if g.writer_share_est is not None:
            lines.append(f"- Доля Писателя в оценке главы ≈ {g.writer_share_est:.0%}"
                         + (f"; по факту журнала тома — {g.writer_share_fact:.0%}." if g.writer_share_fact is not None else "."))
        if g.unpriced:
            lines.append(f"- Без цен в конфиге (в оценке считаются нулём): {', '.join(g.unpriced)}.")
    if g.frames_fact is not None:
        lines.append(f"- Каркасы драматургии тома (роль аналитика) по журналу: {_money(g.frames_fact)}.")
    return lines


def render(acc: VolumeAccount, cfg: Config | None = None, ws: Workspace | None = None) -> str:
    plan = str(acc.chapters_total) if acc.chapters_total is not None else "поглавник не выгружен"
    lines = [f"# Учёт · Том {acc.volume}", "",
             f"Глав в плане: {plan}; зафиксировано: {len(acc.fixed)}; стоимость тома: {acc.cost:.2f} $ "
             f"(вызовов {acc.calls}); время автора: {timing.fmt_minutes(acc.author_s)}; машинное: {timing.fmt_minutes(acc.machine_s)}.",
             "", "## По главам", "", "| глава | состояние | вызовов | токены вх/вых | стоимость, $ | автор | машина |",
             "|---|---|---|---|---|---|---|"]
    for n in sorted(acc.chapters):
        c = acc.chapters[n]
        lines.append(f"| {n} | {c.state} | {c.calls} | {c.tokens_in}/{c.tokens_out} | {c.cost:.2f} | "
                     f"{timing.fmt_minutes(c.author_s)} | {timing.fmt_minutes(c.machine_s)} |")
    if acc.other_calls:
        lines.append(f"| {OUTSIDE} | — | {acc.other_calls} | —/{acc.other_tokens_out} | {acc.other_cost:.2f} | — | — |")
    lines += ["", "## По ролям", "", "| роль | вызовов | стоимость, $ |", "|---|---|---|"]
    for role in sorted(acc.by_role, key=lambda r: (-acc.by_role[r], r)):
        lines.append(f"| {role} | {acc.calls_by_role.get(role, 0)} | {acc.by_role[role]:.2f} |")
    f = acc.forecast()
    lines += ["", "## Прогноз остатка тома", ""]
    if f["осталось_глав"] is None:
        lines.append(f"Прогноз недоступен: {f['основание']}.")
    else:
        lines.append(f"Осталось глав: {f['осталось_глав']}; стоимость ≈ {f['стоимость'] if f['стоимость'] is not None else '—'} $; "
                     f"время автора ≈ {timing.fmt_minutes(f['время_автора_с']) if f['время_автора_с'] is not None else '—'} "
                     f"({f['основание']}).")
    if cfg is not None and ws is not None:
        lines += ["", *render_guidelines(guidelines(ws, cfg, acc))]
    return "\n".join(lines) + "\n"


def save(ws: Workspace, acc: VolumeAccount, cfg: Config | None = None) -> Path:
    from . import guard

    path = ws.logs / f"учёт_том{acc.volume}.md"
    guard.write_text(path, render(acc, cfg, ws))
    return path
