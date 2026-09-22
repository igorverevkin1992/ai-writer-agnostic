// Подсветка цитат Э1/Э2 в тексте главы (аудит 5.3).
// Та же логика, что в konveyer/htmlreview.py (plan_marks/highlight) для приёмка.html.

export interface Mark {
  quote: string;
  cls: string;
  id: string;
  title: string;
}

export interface Placed {
  start: number;
  end: number;
  mark: Mark;
}

export function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function attr(s: string): string {
  return esc(s).replace(/"/g, "&quot;");
}

/** Позиции подсветок в СЫРОМ тексте: первое вхождение каждой цитаты;
 *  пересекающиеся и вложенные отбрасываются — остаётся более ранняя,
 *  при равном начале — более длинная; повтор якоря тоже отбрасывается. */
export function planMarks(text: string, marks: Mark[]): Placed[] {
  const found: { start: number; len: number; order: number; mark: Mark }[] = [];
  marks.forEach((mark, order) => {
    const q = mark.quote.trim();
    if (!q) return;
    const start = text.indexOf(q);
    if (start < 0) return;
    found.push({ start, len: q.length, order, mark });
  });
  found.sort((a, b) => a.start - b.start || b.len - a.len || a.order - b.order);
  const chosen: Placed[] = [];
  const seen = new Set<string>();
  let end = 0;
  for (const f of found) {
    if (f.start < end || seen.has(f.mark.id)) continue;
    chosen.push({ start: f.start, end: f.start + f.len, mark: f.mark });
    seen.add(f.mark.id);
    end = f.start + f.len;
  }
  return chosen;
}

/** HTML экранированного текста с <mark> за один проход по позициям.
 *  Поиск идёт по сырому тексту, а не по HTML: цитата, совпадающая с куском
 *  тултипа, не попадёт внутрь атрибута; никаких String.replace с «$&». */
export function highlight(text: string, marks: Mark[]): string {
  const out: string[] = [];
  let cur = 0;
  for (const { start, end, mark } of planMarks(text, marks)) {
    out.push(esc(text.slice(cur, start)));
    out.push(
      `<mark id="${attr(mark.id)}" class="m-${attr(mark.cls)}" title="${attr(mark.title)}">${esc(text.slice(start, end))}</mark>`,
    );
    cur = end;
  }
  out.push(esc(text.slice(cur)));
  return out.join("");
}
