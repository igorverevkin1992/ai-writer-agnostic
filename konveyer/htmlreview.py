"""HTML-пакет приёмки (`главы/N/приёмка.html`): «чтение с флагами» из этапа 3 —
без сервера и внешних ресурсов (весь контур локален, §1.3).

Текст главы с подсветкой цитат-флагов (тултип: правило и рекомендация),
панель флагов Э1/Э2, самоволки со статусом решений, дифф черновиков.
Тема светлая/тёмная — по настройке системы.
"""

from __future__ import annotations

import difflib
import html
import json
from pathlib import Path

from . import guard, verifier2, writer
from .fsm import ChapterState
from .paths import Workspace
from .schemas import CheckResult, Flag, Resolution, Verdict

NOT_FOUND_NOTE = "цитата не найдена в тексте — проверьте вручную"

# статусные цвета (référence-палитра dataviz; статус ходит с текстом, не в одиночку)
CSS = """
:root { color-scheme: light dark; }
body {
  margin: 0; background: #fcfcfb; color: #0b0b0b;
  font-family: system-ui, -apple-system, sans-serif; line-height: 1.55;
}
@media (prefers-color-scheme: dark) {
  body { background: #1a1a19; color: #f4f4ef; }
  .card, th, td { border-color: #3a3a38 !important; }
  .prose mark { color: inherit; }
}
.wrap { max-width: 900px; margin: 0 auto; padding: 24px 20px 80px; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
.meta { color: #52514e; } @media (prefers-color-scheme: dark) { .meta { color: #c3c2b7; } }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { border: 1px solid #e3e2dd; padding: 6px 10px; text-align: left; vertical-align: top; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 9px; font-size: 0.78rem; font-weight: 600; color: #fff; }
.b-PASS { background: #0ca30c; } .b-FLAG { background: #b97e00; } .b-BRAK { background: #d03b3b; }
.b-samovolka { background: #8a5ae0; } .b-violation { background: #b97e00; }
.card { border: 1px solid #e3e2dd; border-radius: 8px; padding: 10px 14px; margin: 10px 0; }
.card .rule { font-size: 0.85rem; color: #52514e; }
@media (prefers-color-scheme: dark) { .card .rule { color: #c3c2b7; } }
.prose { font-family: Georgia, "Times New Roman", serif; font-size: 1.05rem; white-space: pre-wrap; }
.prose mark { border-radius: 3px; padding: 0 2px; cursor: help; }
.m-FLAG, .m-violation { background: #fab21955; box-shadow: 0 0 0 1px #b97e00 inset; }
.m-BRAK { background: #d03b3b33; box-shadow: 0 0 0 1px #d03b3b inset; }
.m-samovolka { background: #8a5ae033; box-shadow: 0 0 0 1px #8a5ae0 inset; }
.diff { font-family: ui-monospace, monospace; font-size: 0.85rem; white-space: pre-wrap; }
.diff .add { background: #0ca30c22; } .diff .del { background: #d03b3b22; text-decoration: line-through; }
a.anchor { text-decoration: none; }
.resolved { color: #0ca30c; } .unresolved { color: #d03b3b; font-weight: 600; }
"""


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _norm_eq(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


# подсветка: (цитата, класс, якорь, тултип)
Mark = tuple[str, str, str, str]


def plan_marks(text: str, marks: list[Mark]) -> list[tuple[int, int, str, str, str]]:
    """Позиции подсветок в СЫРОМ тексте.

    Для каждой цитаты — первое вхождение (пробелы и переносы — с той же терпимостью, что при применении
    правок, `writer.find_quote`); пересекающиеся и вложенные отбрасываются: остаётся более ранняя,
    при равном начале — более длинная. Повтор якоря (та же цитата дважды) тоже отбрасывается.
    Возвращает (начало, конец, класс, якорь, тултип), отсортированные по началу.
    Та же логика — в панели (panel/src/highlight.ts).
    """
    found = []
    for order, (quote, cls, anchor, tooltip) in enumerate(marks):
        hits = writer.find_quote(text, quote)
        if not hits:
            continue
        pos, end = hits[0]
        found.append((pos, pos - end, order, cls, anchor, tooltip))
    found.sort()
    chosen: list[tuple[int, int, str, str, str]] = []
    seen: set[str] = set()
    end = 0
    for pos, neg_len, _order, cls, anchor, tooltip in found:
        stop = pos - neg_len
        if pos < end or anchor in seen:
            continue
        chosen.append((pos, stop, cls, anchor, tooltip))
        seen.add(anchor)
        end = stop
    return chosen


def highlight(text: str, marks: list[Mark]) -> str:
    """Экранированный текст с <mark> за один проход по позициям.

    Поиск идёт по сырому тексту, а не по HTML: цитата, совпадающая с куском
    тултипа или другой подсветки, не может попасть внутрь атрибута —
    разметка остаётся корректной при любых пересечениях.
    """
    out: list[str] = []
    cur = 0
    for pos, stop, cls, anchor, tooltip in plan_marks(text, marks):
        out.append(_esc(text[cur:pos]))
        out.append(
            f'<mark id="{html.escape(anchor, quote=True)}" class="m-{html.escape(cls, quote=True)}" '
            f'title="{html.escape(tooltip, quote=True)}">{_esc(text[pos:stop])}</mark>'
        )
        cur = stop
    out.append(_esc(text[cur:]))
    return "".join(out)


def build_review_html(ws: Workspace, chapter: int, draft: int) -> Path:
    chdir = ws.chapter_dir(chapter)
    raw = ws.draft_path(chapter, draft).read_text(encoding="utf-8")

    checks: list[CheckResult] = []
    verdict_path = chdir / "вердикт.json"
    if verdict_path.exists():
        checks = Verdict.model_validate(json.loads(verdict_path.read_text(encoding="utf-8"))).checks
    flags: list[Flag] = verifier2.load_flags(ws, chapter)
    resolutions: dict[str, Resolution] = {}
    res_path = chdir / "решения.json"
    if res_path.exists():
        for r in json.loads(res_path.read_text(encoding="utf-8")):
            resolutions[r["flag_id"]] = Resolution.model_validate(r)

    # ---- текст с подсветкой (один проход по позициям, см. highlight)
    marks: list[Mark] = []
    for c in checks:
        if c.status == "PASS":
            continue
        for j, q in enumerate(c.quotes[:3]):
            marks.append((q, c.status, f"a-{c.check_id}-{j}", f"{c.check_id}: порог {c.threshold}, факт {c.actual}"))
    for f in flags:
        marks.append((f.quote, f.kind, f"a-{f.flag_id}", f"{f.flag_id} · {f.type}: {f.rule}. {f.recommendation}"))
    text_html = highlight(raw, marks)

    # ---- таблица Э1
    e1_rows = "".join(
        f"<tr><td><span class='badge b-{c.status}'>{c.status}</span></td>"
        f"<td>{_esc(c.check_id)}</td><td>{_esc(c.actual)}</td>"
        f"<td>{_esc(c.threshold)}</td><td>{_esc(c.rule_source)}</td></tr>"
        for c in checks
    )

    # ---- карточки Э2
    def flag_card(f: Flag) -> str:
        res = resolutions.get(f.flag_id)
        decision = ""
        if f.kind == "samovolka":
            if res and res.decision:
                target = f" → {_esc(res.target_registry)}" if res.target_registry else ""
                decision = f'<div class="resolved">решение: {res.decision}{target}</div>'
            else:
                decision = f'<div class="unresolved">БЕЗ РЕШЕНИЯ — konveyer решение {chapter} {f.flag_id} …</div>'
        badge = "самоволка" if f.kind == "samovolka" else f.severity
        type_part = "" if _norm_eq(f.type, badge) else f" · {_esc(f.type)}"
        found = not f.quote.strip() or bool(writer.find_quote(raw, f.quote))
        anchor = (f'<a class="anchor" href="#a-{html.escape(f.flag_id, quote=True)}">¶</a>' if found
                  else f'<span class="unresolved">⚠ {_esc(NOT_FOUND_NOTE)}</span>')
        return (
            f'<div class="card"><span class="badge b-{f.kind}">{badge}</span> '
            f"<strong>{_esc(f.flag_id)}</strong>{type_part} {anchor}"
            f"<blockquote>{_esc(f.quote)}</blockquote>"
            f'<div class="rule">{_esc(f.rule)}. {_esc(f.recommendation)}</div>{decision}</div>'
        )

    e2_html = "".join(flag_card(f) for f in flags) or "<p>Флагов Э2 нет.</p>"

    # ---- дифф черновиков (если это цикл правок)
    st = ChapterState(ws, chapter)
    base = int(st.data.get("база_правок", draft - 1))
    diff_html = ""
    base_path = ws.draft_path(chapter, base)
    if base != draft and base_path.exists():
        old = base_path.read_text(encoding="utf-8").splitlines()
        new = raw.splitlines()
        rows = []
        for line in difflib.unified_diff(old, new, f"черновик_{base}", f"черновик_{draft}", lineterm="", n=1):
            cls = "add" if line.startswith("+") else "del" if line.startswith("-") else ""
            rows.append(f'<div class="{cls}">{_esc(line)}</div>')
        if rows:
            diff_html = f"<h2>Дифф черновик_{base} → черновик_{draft}</h2><div class='diff'>{''.join(rows)}</div>"

    page = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Приёмка · Глава {chapter}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Приёмка · Глава {chapter} · черновик {draft}</h1>
<p class="meta">Сгенерировано `konveyer приёмка {chapter}`. Правки — в правки.md; решения по самоволкам — `konveyer решение {chapter}`.</p>
<h2>Формальные проверки (Э1)</h2>
<table><tr><th></th><th>Проверка</th><th>Факт</th><th>Порог</th><th>Источник</th></tr>{e1_rows}</table>
<h2>Смысловые флаги (Э2)</h2>
{e2_html}
<h2>Текст главы</h2>
<div class="prose">{text_html}</div>
{diff_html}
</div></body></html>
"""
    path = chdir / "приёмка.html"
    guard.write_text(path, page)
    return path
