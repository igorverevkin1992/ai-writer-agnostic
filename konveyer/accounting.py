"""Учёт времени и денег (FR-CT-1…FR-CT-3, FR-EC-1…FR-EC-4): сводки по главе, тому и ролям, прогноз остатка тома,
предупреждения по порогам конфига (стоимость главы, расход за сутки, размер окна).

Стоимость — только из журнала `журналы/api.jsonl` (токены × цены конфига); время автора — из истории состояний
глав (`timing`): переход внутри одной задачи — машинное, пауза дольше порога или через границу суток — перерыв.
Ориентиры экономики (FR-EC-3) пересчитываются из конфига и журнала, а не зашиты.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import apilog, timing
from .config import Config, ModelConfig
from .fsm import all_states
from .paths import Workspace


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
    chapters_total: int = 0

    @property
    def cost(self) -> float:
        return round(sum(c.cost for c in self.chapters.values()), 4)

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
        left = max(0, self.chapters_total - len(done))
        if not done:
            return {"осталось_глав": left, "стоимость": None, "время_автора_с": None,
                    "основание": "нет зафиксированных глав — прогноза пока нет"}
        avg_cost = sum(c.cost for c in done) / len(done)
        avg_author = sum(c.author_s for c in done) / len(done)
        return {"осталось_глав": left, "стоимость": round(avg_cost * left, 2), "время_автора_с": round(avg_author * left),
                "основание": f"среднее по {len(done)} зафиксированным главам: {avg_cost:.2f} $ и "
                             f"{timing.fmt_minutes(avg_author)} автора на главу"}


def _rows(ws: Workspace, volume: int) -> list[dict]:
    out = []
    for row in apilog.read_log(ws.logs):
        vol = row.get("volume")
        if int(vol if vol is not None else 1) != volume:
            continue
        out.append(row)
    return out


def volume_account(ws: Workspace, volume: int | None = None, chapters_total: int | None = None) -> VolumeAccount:
    volume = ws.volume if volume is None else int(volume)
    acc = VolumeAccount(volume=volume)
    vws = ws.for_volume(volume)
    for st in all_states(vws):
        machine, author = timing.chapter_times(st.data.get("история", []))
        acc.chapters[st.chapter] = ChapterAccount(chapter=st.chapter, machine_s=machine, author_s=author, state=st.state)
    for row in _rows(ws, volume):
        ch = row.get("chapter")
        cost = float(row.get("cost_est") or 0.0)
        role = str(row.get("role") or "—").split(" (")[0]
        acc.by_role[role] = round(acc.by_role.get(role, 0.0) + cost, 4)
        acc.calls_by_role[role] = acc.calls_by_role.get(role, 0) + 1
        if ch is None:
            continue
        c = acc.chapters.setdefault(int(ch), ChapterAccount(chapter=int(ch)))
        c.cost = round(c.cost + cost, 4)
        c.calls += 1
        c.tokens_in += int(row.get("tokens_in") or 0)
        c.tokens_out += int(row.get("tokens_out") or 0)
    if chapters_total is None:
        try:
            from . import exporter

            chapters_total = len([b for b in exporter.load_briefs(ws.exports) if b.volume == volume])
        except FileNotFoundError:
            chapters_total = len(acc.chapters)
    acc.chapters_total = chapters_total
    return acc


def today_cost(ws: Workspace, today: datetime | None = None) -> float:
    """Расход за текущие сутки (UTC-дата строки журнала) по всем томам."""
    day = (today or datetime.now(timezone.utc)).date()
    total = 0.0
    for row in apilog.read_log(ws.logs):
        try:
            ts = datetime.fromisoformat(str(row.get("ts", "")))
        except ValueError:
            continue
        if ts.date() == day:
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
        acc = volume_account(ws)
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


def render(acc: VolumeAccount, cfg: Config | None = None) -> str:
    lines = [f"# Учёт · Том {acc.volume}", "",
             f"Глав в плане: {acc.chapters_total}; зафиксировано: {len(acc.fixed)}; стоимость тома: {acc.cost:.2f} $; "
             f"время автора: {timing.fmt_minutes(acc.author_s)}; машинное: {timing.fmt_minutes(acc.machine_s)}.", "",
             "## По главам", "", "| глава | состояние | вызовов | токены вх/вых | стоимость, $ | автор | машина |",
             "|---|---|---|---|---|---|---|"]
    for n in sorted(acc.chapters):
        c = acc.chapters[n]
        lines.append(f"| {n} | {c.state} | {c.calls} | {c.tokens_in}/{c.tokens_out} | {c.cost:.2f} | "
                     f"{timing.fmt_minutes(c.author_s)} | {timing.fmt_minutes(c.machine_s)} |")
    lines += ["", "## По ролям", "", "| роль | вызовов | стоимость, $ |", "|---|---|---|"]
    for role in sorted(acc.by_role, key=lambda r: -acc.by_role[r]):
        lines.append(f"| {role} | {acc.calls_by_role.get(role, 0)} | {acc.by_role[role]:.2f} |")
    f = acc.forecast()
    lines += ["", "## Прогноз остатка тома", "",
              f"Осталось глав: {f['осталось_глав']}; стоимость ≈ {f['стоимость'] if f['стоимость'] is not None else '—'} $; "
              f"время автора ≈ {timing.fmt_minutes(f['время_автора_с']) if f['время_автора_с'] is not None else '—'} "
              f"({f['основание']})."]
    if cfg is not None:
        lines += ["", "## Ориентиры из конфига", ""]
        w = cfg.writer
        if w.price_in_per_1m or w.price_out_per_1m:
            per_chapter = estimate_before(w, 13_000, 1_200) or 0.0
            lines.append(f"- Писатель {w.model}: окно ≈ 13 тыс. знаков + 1 200 токенов выхода ≈ {per_chapter:.3f} $ за генерацию; "
                         f"глава с одной генерацией и одним циклом правок ≈ {per_chapter * 2:.2f} $.")
        else:
            lines.append("- Цены моделей в конфиг.yaml не заданы (price_in_per_1m / price_out_per_1m) — ориентиры не считаются.")
    return "\n".join(lines) + "\n"


def save(ws: Workspace, acc: VolumeAccount, cfg: Config | None = None) -> Path:
    from . import guard

    path = ws.logs / f"учёт_том{acc.volume}.md"
    guard.write_text(path, render(acc, cfg))
    return path
