// Клиент локального API. Изменяющие запросы несут X-Konveyer-Panel (см. server.py).

/** Ошибка API: кроме текста несёт HTTP-статус и машинный код сервера
 *  (например 409 + «конфликт» при расхождении версии документа канона, аудит 5.2).
 *  status 0 — обрыв связи с сервером панели (аудит 2, 5.4). */
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function isConflict(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 409 || e.code === "конфликт");
}

export function isOffline(e: unknown): boolean {
  return e instanceof ApiError && e.status === 0;
}

export const OFFLINE_MESSAGE = "Сервер панели недоступен — перезапустите `konveyer panel`";

/** Русское сообщение по коду ответа (5.4): автор видит, что случилось, а не сырое исключение. */
export function describeError(status: number, message: string): string {
  const msg = message.trim();
  switch (status) {
    case 404:
      return `нет такой главы или документа: ${msg}`;
    case 409:
      return `конфликт (уже есть или изменено): ${msg}`;
    case 423:
      return `сервер занят задачей: ${msg}`;
    case 413:
      return `слишком большой запрос: ${msg}`;
    case 403:
      return `запрос отклонён защитой панели: ${msg}`;
    case 500:
      return msg.startsWith("внутренняя ошибка") ? msg : `внутренняя ошибка сервера — подробности в журналы/панель.log: ${msg}`;
    default:
      return msg;
  }
}

// ---------------------------------------------------------------- связь

/** Состояние связи с сервером: одно на приложение, подписка через useConnection().
 *  Обрыв связи (fetch бросил TypeError) — баннер и блокировка кнопок, а не тост на каждый опрос. */
type Listener = () => void;
let online = true;
const listeners = new Set<Listener>();

function setOnline(v: boolean) {
  if (online === v) return;
  online = v;
  listeners.forEach((fn) => fn());
}

export const connection = {
  isOnline: () => online,
  subscribe(fn: Listener): () => void {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
};

async function handle<T>(r: Response): Promise<T> {
  setOnline(true);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data as { error?: string; code?: string };
    throw new ApiError(describeError(r.status, d.error || r.statusText || `HTTP ${r.status}`), r.status, d.code);
  }
  return data as T;
}

function offline(): never {
  setOnline(false);
  throw new ApiError(OFFLINE_MESSAGE, 0, "нет-связи");
}

export function apiGet<T>(url: string): Promise<T> {
  return fetch(url).then((r) => handle<T>(r), offline);
}

export function apiPost<T>(url: string, body: unknown = {}): Promise<T> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Konveyer-Panel": "1" },
    body: JSON.stringify(body),
  }).then((r) => handle<T>(r), offline);
}
