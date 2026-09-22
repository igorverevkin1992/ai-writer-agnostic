import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

/** Защита текста автора (аудит 5.1–5.3).
 *
 *  1. Реестр «не сохранено» на уровне App: каждое поле ввода регистрирует себя по ключу,
 *     App спрашивает подтверждение при смене вида, ChapterView — при смене вкладки,
 *     beforeunload — при закрытии страницы.
 *  2. Черновики переживают перезагрузку: несохранённый текст пишется в localStorage
 *     (ключ = документ канона / глава+вкладка), при открытии восстанавливается с пометкой,
 *     чистится после успешного сохранения или осознанного «уйти и потерять». */

export interface DirtyEntry {
  /** фраза в предложном падеже: «в документе «X»», «во вкладке «Правки» главы 3» */
  label: string;
  /** сбросить несохранённый текст (в т.ч. черновик в localStorage) — автор выбрал «потерять» */
  discard: () => void;
}

export interface DirtyRegistry {
  set(key: string, entry: DirtyEntry | null): void;
  entries(prefix?: string): [string, DirtyEntry][];
  /** сообщение для confirm или null, если терять нечего */
  question(prefix?: string): string | null;
  /** автор согласился уйти: сбросить черновики и очистить реестр */
  leave(prefix?: string): void;
  /** подписка на изменения (для индикаторов «не сохранено»); возвращает отписку */
  subscribe(fn: () => void): () => void;
}

export function createDirtyRegistry(): DirtyRegistry {
  const map = new Map<string, DirtyEntry>();
  const listeners = new Set<() => void>();
  const entries = (prefix?: string) => [...map.entries()].filter(([k]) => !prefix || k.startsWith(prefix));
  const emit = () => listeners.forEach((fn) => fn());
  return {
    set(key, entry) {
      const had = map.has(key);
      if (entry) map.set(key, entry);
      else map.delete(key);
      if (had !== map.has(key)) emit();
    },
    entries,
    question(prefix) {
      const es = entries(prefix);
      if (es.length === 0) return null;
      const where = es.map(([, e]) => e.label).join("; ");
      return `${where.charAt(0).toUpperCase()}${where.slice(1)} есть несохранённые правки. Уйти и потерять их?`;
    },
    leave(prefix) {
      for (const [k, e] of entries(prefix)) {
        try {
          e.discard();
        } catch {
          /* сброс — лучшее из возможного */
        }
        map.delete(k);
      }
      emit();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export const DirtyContext = createContext<DirtyRegistry>(createDirtyRegistry());

/** Реактивный список ключей «не сохранено» с данным префиксом. */
export function useDirtyKeys(prefix: string): string[] {
  const registry = useContext(DirtyContext);
  const [keys, setKeys] = useState<string[]>(() => registry.entries(prefix).map(([k]) => k));
  useEffect(() => {
    const update = () => setKeys(registry.entries(prefix).map(([k]) => k));
    update();
    return registry.subscribe(update);
  }, [registry, prefix]);
  return keys;
}

// ------------------------------------------------------------ localStorage

const PREFIX = "konveyer.draft:";

export interface StoredDraft {
  text: string;
  ts: number;
  /** произвольная метка основы (для канона — версия документа, на которой писался черновик) */
  base?: string;
}

/** Все обращения к localStorage — в try/catch: приватный режим, квота, отключённое хранилище. */
export function readDraft(key: string): StoredDraft | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<StoredDraft>;
    if (typeof d.text !== "string") return null;
    return { text: d.text, ts: typeof d.ts === "number" ? d.ts : 0, base: typeof d.base === "string" ? d.base : undefined };
  } catch {
    return null;
  }
}

export function writeDraft(key: string, text: string, base?: string): void {
  try {
    const d: StoredDraft = { text, ts: Date.now(), base };
    window.localStorage.setItem(PREFIX + key, JSON.stringify(d));
  } catch {
    /* квота или хранилище недоступно — черновик живёт только в памяти */
  }
}

export function removeDraft(key: string): void {
  try {
    window.localStorage.removeItem(PREFIX + key);
  } catch {
    /* ничего */
  }
}

export function fmtTime(ts: number): string {
  if (!ts) return "?";
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const sameDay = new Date().toDateString() === d.toDateString();
  return sameDay ? `${hh}:${mm}` : `${d.toLocaleDateString("ru-RU")} ${hh}:${mm}`;
}

// ------------------------------------------------------------------ хук

export interface DraftState {
  text: string;
  setText: (t: string) => void;
  /** текст отличается от сохранённого на сервере */
  dirty: boolean;
  /** если текст восстановлен из localStorage — метка времени черновика (и основа, если была) */
  restored: { ts: number; base?: string } | null;
  /** вернуть серверный текст, черновик стереть */
  discard: () => void;
  /** после успешного сохранения: черновик стереть, текст оставить */
  clear: () => void;
}

/** Текстовое поле с черновиком в localStorage и регистрацией «не сохранено».
 *  `key` — null, когда поля нет (документ не открыт); `baseline` — текст с сервера
 *  (когда он меняется извне, а черновика нет, поле подхватывает его). */
export function useDraft(key: string | null, baseline: string, label: string, base?: string): DraftState {
  const registry = useContext(DirtyContext);
  const [text, setTextRaw] = useState(baseline);
  const [restored, setRestored] = useState<DraftState["restored"]>(null);
  const baseRef = useRef(base);
  baseRef.current = base;

  // открытие поля / новый серверный текст: восстановить черновик, если он есть и отличается
  useEffect(() => {
    if (key === null) {
      setTextRaw(baseline);
      setRestored(null);
      return;
    }
    const s = readDraft(key);
    if (s && s.text !== baseline) {
      setTextRaw(s.text);
      setRestored({ ts: s.ts, base: s.base });
    } else {
      if (s) removeDraft(key); // черновик совпал с сервером — больше не нужен
      setTextRaw(baseline);
      setRestored(null);
    }
  }, [key, baseline]);

  const setText = useCallback(
    (t: string) => {
      setTextRaw(t);
      if (key === null) return;
      if (t === baseline) removeDraft(key);
      else writeDraft(key, t, baseRef.current);
    },
    [key, baseline],
  );

  const discard = useCallback(() => {
    if (key !== null) removeDraft(key);
    setTextRaw(baseline);
    setRestored(null);
  }, [key, baseline]);

  const clear = useCallback(() => {
    if (key !== null) removeDraft(key);
    setRestored(null);
  }, [key]);

  const dirty = key !== null && text !== baseline;
  useEffect(() => {
    if (key === null) return;
    registry.set(key, dirty ? { label, discard } : null);
    return () => registry.set(key, null);
  }, [registry, key, dirty, label, discard]);

  return useMemo(() => ({ text, setText, dirty, restored, discard, clear }), [text, setText, dirty, restored, discard, clear]);
}

/** Пометка «восстановлено из черновика» с кнопкой отбросить. */
export function RestoredNote({ state, extra }: { state: DraftState; extra?: string }) {
  if (!state.restored) return null;
  return (
    <div className="draft-note" role="status">
      восстановлено из черновика от {fmtTime(state.restored.ts)}
      {extra ? ` — ${extra}` : ""}{" "}
      <button type="button" onClick={state.discard}>отбросить черновик</button>
    </div>
  );
}
