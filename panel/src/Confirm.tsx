import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Подтверждение необратимого действия: показывает текст (в т.ч. Д-8), ждёт «Да»/«Отмена». */
export type Confirm = (text: string) => Promise<boolean>;

interface Ask {
  text: string;
  resolve: (ok: boolean) => void;
}

/** Собственный модальный диалог вместо window.confirm (аудит 5.7): браузер
 *  не может его подавить и превратить отказ в «тихое ничего». Esc, клик по
 *  фону и «Отмена» — отказ; фокус на «Отмене», чтобы случайный Enter не
 *  подтверждал необратимое. */
export function useConfirm(): [Confirm, ReactNode] {
  const [ask, setAsk] = useState<Ask | null>(null);
  const confirm = useCallback<Confirm>(
    (text) =>
      new Promise((resolve) => {
        setAsk((prev) => {
          prev?.resolve(false); // второй вопрос поверх первого — первый считается отклонённым
          return { text, resolve };
        });
      }),
    [],
  );
  const close = useCallback((ok: boolean) => {
    setAsk((prev) => {
      prev?.resolve(ok);
      return null;
    });
  }, []);
  return [confirm, ask ? <ConfirmDialog text={ask.text} onClose={close} /> : null];
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Ловушка фокуса (аудит 2, 5.8): диалог рисуется порталом в body, всё приложение (#root)
 *  на время диалога получает `inert` + aria-hidden, Tab/Shift+Tab ходят по кругу внутри
 *  диалога, Esc — отмена; фокус возвращается туда, откуда открыли. */
export function ConfirmDialog({ text, onClose }: { text: string; onClose: (ok: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    root?.setAttribute("aria-hidden", "true");
    cancelRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current(false);
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = active !== null && dialogRef.current.contains(active);
      if (e.shiftKey) {
        if (!inside || active === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (!inside || active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    // фокус ушёл наружу (браузер без inert) — возвращаем в диалог
    const onFocusIn = (e: FocusEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) cancelRef.current?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocusIn);
      root?.removeAttribute("inert");
      root?.removeAttribute("aria-hidden");
      opener?.focus?.();
    };
  }, []);

  return createPortal(
    <div className="modal-backdrop" onClick={() => onClose(false)}>
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-text"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title">Подтверждение</h2>
        <p id="confirm-text">{text}</p>
        <div className="actions">
          <button ref={cancelRef} onClick={() => onClose(false)}>Отмена</button>
          <button className="primary" onClick={() => onClose(true)}>Да</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
