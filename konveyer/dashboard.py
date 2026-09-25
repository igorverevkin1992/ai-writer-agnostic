"""Дашборд (FR-D1): самодостаточный HTML c инлайн-SVG — работает без сети (§1.3).

Графики: правки/1000 слов по главам; метрики Э1 с коридорами норм и флагом
отклонения >20% от среднего части; TTR нарастающим окном; расход токенов и
стоимость по ролям. Плюс: таблица глав с состояниями FSM и временем автора
(критерий приёмки 1: такт ≤40 минут работы автора).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from . import exporter, guard, textutils, timing
from .apilog import read_log
from .fsm import all_states
from .paths import Workspace
from .svgchart import Threshold, bar_chart, data_table, line_chart

METRIC_KEYS = [
    ("V1.2a_средняя_длина", "Средняя длина фразы", "средняя_длина"),
    ("V1.2b_доля_коротких", "Доля коротких фраз", "доля_коротких"),
    ("V1.2c_доля_длинных", "Доля длинных фраз", "доля_длинных"),
    ("V1.3_был", "«был» на 250 слов", "был_на_250"),
    ("V1.4_усилители", "Усилители на 1000 слов", "усилители_на_1000"),
]

CSS = """
:root { color-scheme: light dark; }
body { margin: 0; font-family: system-ui, sans-serif; background: var(--surface-1); color: var(--text-primary); }
.viz-root {
  --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --grid: #e3e2dd; --series-1: #2a78d6; --critical: #d03b3b; --good: #0ca30c;
  max-width: 960px; margin: 0 auto; padding: 24px 20px 80px;
}
@media (prefers-color-scheme: dark) {
  .viz-root { --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
              --grid: #3a3a38; --series-1: #3987e5; }
}
h1 { font-size: 1.35rem; } h2 { font-size: 1.05rem; margin: 2rem 0 0.4rem; }
.meta, .tick, .thr-lbl { fill: var(--text-secondary); color: var(--text-secondary); font-size: 11px; }
svg { width: 100%; height: auto; display: block; }
.grid { stroke: var(--grid); stroke-width: 1; }
.series { fill: none; stroke: var(--series-1); stroke-width: 2; }
.marker { fill: var(--series-1); }
.marker-out { fill: var(--critical); }
.bar { fill: var(--series-1); }
.thr { stroke: var(--text-secondary); stroke-width: 1; stroke-dasharray: 4 3; }
.thr-critical { stroke: var(--critical); stroke-width: 1; stroke-dasharray: 4 3; }
.lbl { fill: var(--text-primary); font-size: 11px; font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; margin-top: 0.4rem; }
th, td { border: 1px solid var(--grid); padding: 5px 9px; text-align: left; }
details { margin: 0.3rem 0 1rem; color: var(--text-secondary); }
.ok { color: var(--good); } .over { color: var(--critical); font-weight: 600; }
.empty { color: var(--text-secondary); }
"""


def _read_metrics(ws: Workspace) -> list[dict]:
    path = ws.logs / "метрики.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    latest: dict[int, dict] = {}
    for r in rows:
        latest[r["chapter"]] = r  # последняя запись главы побеждает
    return [latest[k] for k in sorted(latest)]


def _figure(title: str, svg: str, table: str = "") -> str:
    return f"<h2>{html.escape(title)}</h2>{svg}{table}"


def _chapters_block(ws: Workspace) -> str:
    states = all_states(ws)
    if not states:
        return ""
    rows = []
    for st in states:
        machine_s, author_s = timing.chapter_times(st.data.get("история", []))
        over = author_s > 40 * 60
        rows.append(
            f"<tr><td>{st.chapter}</td><td>{html.escape(st.state)}</td>"
            f"<td>{timing.fmt_minutes(machine_s)}</td>"
            f"<td class='{'over' if over else 'ok'}'>{timing.fmt_minutes(author_s)}</td></tr>"
        )
    return (
        "<h2>Главы: состояние и время такта</h2>"
        "<table><tr><th>Глава</th><th>Состояние</th><th>Машинное время</th>"
        "<th>Время автора (цель ≤40 мин)</th></tr>" + "".join(rows) + "</table>"
    )


def render_dashboard(ws: Workspace) -> str:
    """HTML дашборда в памяти — панель отдаёт его по GET без записи на диск (аудит 4.3)."""
    metrics = _read_metrics(ws)
    chapters = [m["chapter"] for m in metrics]
    try:
        norms = exporter.load_norms(ws.exports)
    except FileNotFoundError:
        norms = {}
    figures: list[str] = [_chapters_block(ws)]

    # правки/1000 слов
    values = [m.get("правок_на_1000") or 0 for m in metrics]
    figures.append(
        _figure(
            "Правки автора на 1000 слов",
            bar_chart([str(c) for c in chapters], values, tooltip="глава {x}: {y} правок/1000"),
            data_table(["Глава", "Правок/1000"], list(zip(chapters, values, strict=True))),
        )
    )

    # метрики Э1 с коридорами и выбросами >20% от среднего части
    for key, title, norm_id in METRIC_KEYS:
        pairs = [(c, m.get(key)) for c, m in zip(chapters, metrics, strict=True) if m.get(key) is not None]
        if not pairs:
            continue
        xs = [float(c) for c, _ in pairs]
        ys = [float(v) for _, v in pairs]
        mean = sum(ys) / len(ys)
        outliers = {i for i, v in enumerate(ys) if mean and abs(v - mean) / mean > 0.20}
        thresholds = []
        norm = norms.get(norm_id)
        if norm:
            if norm.min is not None:
                thresholds.append(Threshold(norm.min, f"мин {norm.min:g}"))
            if norm.max is not None:
                thresholds.append(Threshold(norm.max, f"макс {norm.max:g}"))
            if norm.brak is not None:
                thresholds.append(Threshold(norm.brak, f"брак {norm.brak:g}", "critical"))
        figures.append(
            _figure(
                title,
                line_chart(xs, ys, thresholds=thresholds, outliers=outliers, tooltip="глава {x}: {y}"),
                data_table(["Глава", title], pairs),
            )
        )

    # TTR нарастающим окном по корпусу части
    part_tokens: list[str] = []
    if ws.corpus.exists():
        for f in sorted(ws.corpus.glob("*.txt")):
            part_tokens.extend(f.read_text(encoding="utf-8").split())
    win = int(norms["ttr_окно_слов"].max) if "ttr_окно_слов" in norms and norms["ttr_окно_слов"].max else 10_000
    rolling = textutils.rolling_ttr(part_tokens, win)
    ttr_thr = []
    if "ttr_мин" in norms and norms["ttr_мин"].min is not None:
        ttr_thr.append(Threshold(norms["ttr_мин"].min, f"мин {norms['ttr_мин'].min:g}", "critical"))
    figures.append(
        _figure(
            f"TTR нарастающим окном {win} слов (по части)",
            line_chart(
                [float(p) for p, _ in rolling], [v for _, v in rolling],
                thresholds=ttr_thr, x_label="позиция, слов", tooltip="слово {x}: TTR {y}",
            ),
            data_table(["Позиция", "TTR"], [(p, f"{v:.3f}") for p, v in rolling]),
        )
    )

    # токены и стоимость по ролям
    api_rows = read_log(ws.logs)
    roles = sorted({r.get("role") for r in api_rows if r.get("role")})
    tokens = [
        sum((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in api_rows if r.get("role") == role)
        for role in roles
    ]
    costs = [round(sum(r.get("cost_est") or 0 for r in api_rows if r.get("role") == role), 4) for role in roles]
    figures.append(
        _figure(
            "Токены по ролям",
            bar_chart(roles, [float(t) for t in tokens], tooltip="{x}: {y} токенов"),
            data_table(["Роль", "Токены", "Стоимость, $"], list(zip(roles, tokens, costs, strict=True))),
        )
    )
    if any(costs):
        figures.append(
            _figure("Оценка стоимости по ролям, $", bar_chart(roles, costs, tooltip="{x}: ${y}"))
        )

    page = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>КОНВЕЙЕР · Дашборд</title><style>{CSS}</style></head>
<body><div class="viz-root">
<h1>КОНВЕЙЕР · метрики по главам</h1>
<p class="meta">Сгенерировано `konveyer дашборд`. Пороги — из norms.json (таблица норм документа стиля). Файл самодостаточен, сеть не нужна.</p>
{''.join(figures)}
</div></body></html>
"""
    return page


def build_dashboard(ws: Workspace) -> Path:
    """`konveyer дашборд`: пишет дашборд.html в рабочую область."""
    path = ws.root / "дашборд.html"
    guard.write_text(path, render_dashboard(ws))
    return path
