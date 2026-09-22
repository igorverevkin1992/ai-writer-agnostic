"""Инлайн-SVG-графики для дашборда — без внешних библиотек и сети (§1.3, NFR-1).

Оформление по методике dataviz: одна серия на график (легенда не нужна —
серию называет заголовок), тонкие маркеры с нативными тултипами <title>,
ненавязчивая сетка, пороги — пунктиром, выбросы — статусным цветом
только вместе с подписью. Цвета — CSS-переменные (светлая/тёмная тема).
"""

from __future__ import annotations

import html
from dataclasses import dataclass

W, H = 680, 230
PAD_L, PAD_R, PAD_T, PAD_B = 46, 14, 10, 26


@dataclass
class Threshold:
    value: float
    label: str
    kind: str = "norm"  # norm (нейтральный порог) | critical


def _fmt(v: float) -> str:
    return f"{v:.3g}"


def _scales(xs: list[float], ys: list[float], thresholds: list[Threshold], *, zero_floor: bool = False):
    all_y = ys + [t.value for t in thresholds]
    y_min, y_max = min(all_y), max(all_y)
    if y_min == y_max:
        y_min, y_max = y_min - 1, y_max + 1
    span = y_max - y_min
    y_min -= span * 0.12
    y_max += span * 0.12
    if zero_floor and min(all_y) >= 0:
        y_min = 0.0  # столбцы растут от нуля — усечённая ось врёт масштабом
    x_min, x_max = min(xs), max(xs)
    if x_min == x_max:
        x_min, x_max = x_min - 1, x_max + 1

    def sx(x: float) -> float:
        return PAD_L + (x - x_min) / (x_max - x_min) * (W - PAD_L - PAD_R)

    def sy(y: float) -> float:
        return H - PAD_B - (y - y_min) / (y_max - y_min) * (H - PAD_T - PAD_B)

    return sx, sy, y_min, y_max


def _grid(sy, y_min: float, y_max: float) -> str:
    parts = []
    for i in range(4):
        v = y_min + (y_max - y_min) * i / 3
        y = sy(v)
        parts.append(f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 6}" y="{y + 3:.1f}" text-anchor="end">{_fmt(v)}</text>')
    return "".join(parts)


def _threshold_lines(sy, thresholds: list[Threshold]) -> str:
    parts = []
    for t in thresholds:
        y = sy(t.value)
        cls = "thr-critical" if t.kind == "critical" else "thr"
        parts.append(f'<line class="{cls}" x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>')
        # подпись порога — слева, чтобы не сталкиваться с меткой последней точки справа
        parts.append(f'<text class="thr-lbl" x="{PAD_L + 4}" y="{y - 4:.1f}">{html.escape(t.label)}</text>')
    return "".join(parts)


def line_chart(
    xs: list[float],
    ys: list[float],
    *,
    thresholds: list[Threshold] | None = None,
    outliers: set[int] | None = None,
    x_label: str = "",
    tooltip: str = "{x}: {y}",
) -> str:
    """Линия одной серии с маркерами; outliers — индексы точек-выбросов."""
    if not xs:
        return '<p class="empty">Нет данных.</p>'
    thresholds = thresholds or []
    outliers = outliers or set()
    sx, sy, y_min, y_max = _scales([float(x) for x in xs], ys, thresholds)
    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(zip(xs, ys, strict=True)))
    markers = "".join(
        f'<circle class="{"marker-out" if i in outliers else "marker"}" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4">'
        f"<title>{html.escape(tooltip.format(x=x, y=_fmt(y)))}"
        f"{' — отклонение >20% от среднего' if i in outliers else ''}</title></circle>"
        for i, (x, y) in enumerate(zip(xs, ys, strict=True))
    )
    # подпись последней точки — над маркером, якорь к правому краю (не обрезается рамкой)
    last_x, last_y = xs[-1], ys[-1]
    direct = (
        f'<text class="lbl" x="{min(sx(last_x), W - PAD_R):.1f}" y="{sy(last_y) - 8:.1f}" '
        f'text-anchor="end">{_fmt(last_y)}</text>'
    )
    x_ticks = "".join(
        f'<text class="tick" x="{sx(x):.1f}" y="{H - 8}" text-anchor="middle">{x:g}</text>'
        for x in xs
    )
    xl = f'<text class="tick" x="{W - PAD_R}" y="{H - 8}" text-anchor="end">{html.escape(x_label)}</text>' if x_label else ""
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img">{_grid(sy, y_min, y_max)}{_threshold_lines(sy, thresholds)}'
        f'<path class="series" d="{path}"/>{markers}{direct}{x_ticks if len(xs) <= 15 else xl}</svg>'
    )


def bar_chart(
    labels: list[str],
    values: list[float],
    *,
    thresholds: list[Threshold] | None = None,
    tooltip: str = "{x}: {y}",
) -> str:
    """Столбцы одной серии: скруглённый верх (4px у конца данных), зазор 2px."""
    if not labels:
        return '<p class="empty">Нет данных.</p>'
    thresholds = thresholds or []
    _, sy, y_min, y_max = _scales(list(range(len(labels))), [*values, 0.0], thresholds, zero_floor=True)
    y0 = sy(max(0.0, y_min))
    inner = W - PAD_L - PAD_R
    step = inner / len(labels)
    bw = max(6.0, min(48.0, step - 2))
    bars = []
    for i, (lab, v) in enumerate(zip(labels, values, strict=True)):
        x = PAD_L + i * step + (step - bw) / 2
        y = sy(v)
        r = min(4.0, bw / 2, abs(y0 - y))
        d = (
            f"M{x:.1f},{y0:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + bw - r:.1f},{y:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} L{x + bw:.1f},{y0:.1f} Z"
        )
        bars.append(f'<path class="bar" d="{d}"><title>{html.escape(tooltip.format(x=lab, y=_fmt(v)))}</title></path>')
        if len(labels) <= 12:
            bars.append(
                f'<text class="tick" x="{x + bw / 2:.1f}" y="{H - 8}" text-anchor="middle">{html.escape(str(lab)[:12])}</text>'
            )
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img">{_grid(sy, y_min, y_max)}'
        f'{_threshold_lines(sy, thresholds)}{"".join(bars)}</svg>'
    )


def data_table(headers: list[str], rows: list[list]) -> str:
    """Табличный дублёр графика (доступность: данные читаемы без цвета)."""
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c if c is not None else '—'))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<details><summary>Данные таблицей</summary><table><tr>{head}</tr>{body}</table></details>"
