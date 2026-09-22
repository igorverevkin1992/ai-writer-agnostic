// Правки БЫЛО/СТАЛО на клиенте (аудит 2, 5.6): живая проверка «найдено дословно» по тексту
// черновика без сохранения и сборка пары из выделения. Разбор повторяет автомат
// konveyer/review.py::parse_edits_text (маркеры в начале строки, значение до пустой строки).

export interface ParsedEdit {
  seq: number;
  before: string;
  after: string;
  note: boolean;
  line: number;
}

export interface ParseResult {
  edits: ParsedEdit[];
  /** ошибки формата с номером строки — как их выдал бы сервер при сохранении */
  errors: string[];
}

const MARKER = /^\s*(БЫЛО|СТАЛО|УКАЗАНИЕ)\s*:\s?(.*)$/;

export function parseEdits(text: string): ParseResult {
  // примеры формата в ограждённых код-блоках — не правки; номера строк сохраняем
  const src = text.replace(/```[\s\S]*?```/g, (m) => "\n".repeat((m.match(/\n/g) || []).length));
  const edits: ParsedEdit[] = [];
  const errors: string[] = [];
  let state: "idle" | "before" | "before_done" | "after" | "note" = "idle";
  let before: string[] = [];
  let value: string[] = [];
  let beforeLine = 0;

  const flush = () => {
    if (state === "after") {
      edits.push({ seq: edits.length + 1, before: before.join("\n").trim(), after: value.join("\n").trim(), note: false, line: beforeLine });
    } else if (state === "note") {
      const note = value.join("\n").trim();
      if (note) edits.push({ seq: edits.length + 1, before: "", after: note, note: true, line: beforeLine });
    }
    state = "idle";
  };
  const requireAfter = (n: number, what: string) => {
    if (state === "before" || state === "before_done") {
      errors.push(`${what}: у «БЫЛО:» (строка ${beforeLine}) нет своего «СТАЛО:»`);
      state = "idle";
    }
  };

  const lines = src.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const n = i + 1;
    const line = lines[i];
    const m = MARKER.exec(line);
    if (m) {
      const [, kind, rest] = m;
      if (kind === "БЫЛО") {
        requireAfter(n, `строка ${n}: новое «БЫЛО:»`);
        flush();
        if (!rest.trim()) errors.push(`строка ${n}: «БЫЛО:» пустое — нужна точная цитата из черновика`);
        before = [rest];
        beforeLine = n;
        state = "before";
      } else if (kind === "СТАЛО") {
        if (state !== "before" && state !== "before_done") {
          errors.push(`строка ${n}: «СТАЛО:» без предшествующего «БЫЛО:»`);
          continue;
        }
        value = [rest];
        state = "after";
      } else {
        requireAfter(n, `строка ${n}: «УКАЗАНИЕ:»`);
        flush();
        value = [rest];
        beforeLine = n;
        state = "note";
      }
      continue;
    }
    if (!line.trim()) {
      if (state === "before") state = "before_done";
      else if (state === "after" || state === "note") flush();
      continue;
    }
    if (state === "before") before.push(line);
    else if (state === "after" || state === "note") value.push(line);
    else if (state === "before_done") {
      errors.push(`строка ${n}: у «БЫЛО:» (строка ${beforeLine}) нет своего «СТАЛО:»`);
      state = "idle";
    }
  }
  if (state === "before" || state === "before_done") {
    errors.push(`строка ${beforeLine}: у «БЫЛО:» нет своего «СТАЛО:» до конца файла`);
    state = "idle";
  }
  flush();
  return { edits, errors };
}

/** Сколько раз цитата встречается в тексте дословно (0 — не найдена, >1 — неоднозначна). */
export function countOccurrences(text: string, quote: string): number {
  if (!quote) return 0;
  let n = 0;
  let i = text.indexOf(quote);
  while (i >= 0) {
    n++;
    i = text.indexOf(quote, i + quote.length);
  }
  return n;
}

export function describeFound(n: number): { text: string; cls: "ok" | "bad" | "warn" } {
  if (n === 1) return { text: "найдено дословно", cls: "ok" };
  if (n === 0) return { text: "не найдено в черновике дословно", cls: "bad" };
  return { text: `найдено ${n} раз — уточните цитату`, cls: "warn" };
}

/** Строка правки.md для пары «было → стало». Многострочная цитата допустима, но без пустых строк
 *  (пустая строка завершает значение в парсере). */
export function editPair(before: string, after: string): string {
  return `БЫЛО: ${before.trim()}\nСТАЛО: ${after.trim()}\n`;
}

/** Добавляет пару в конец текста правки.md, отделяя пустой строкой. */
export function appendPair(text: string, before: string, after: string): string {
  const base = text.replace(/\s+$/, "");
  return (base ? base + "\n\n" : "") + editPair(before, after);
}

export function hasParagraphBreak(s: string): boolean {
  return /\n\s*\n/.test(s);
}
