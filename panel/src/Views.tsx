// Виды панели по этапам жизненного цикла (FR-PN-2): «Проект», «Онбординг», «Журналы», «Регрессия».
// Все данные — из локального API; действия — теми же функциями ядра, что и команды CLI (FR-PN-7).
import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";
import type { Notify, RunCommand } from "./App";
import type { Confirm } from "./Confirm";
import { usePending } from "./hooks";
import type { ApiLogRow } from "./types";

interface ViewProps { refreshTick: number; notify: Notify; busy: boolean; runCommand: RunCommand }

// ------------------------------------------------------------------ Проект

interface Readiness { ok: boolean | null; label: string; hint: string }
interface ModuleRow { имя: string; описание: string; базовый: boolean; включён: boolean; требует_типы: string[] }
interface ProjectData {
  паспорт: { имя: string; язык_прозы: string; томов_план: number; текущий_том: number; профиль: string };
  модули: ModuleRow[];
  готовность: Readiness[];
  готов_к_такту: boolean;
  карта: { файл: string; тип: string; том?: number; источник?: string; авто?: boolean }[];
  вне_карты: string[];
  git: { репозиторий: boolean; удалённых_копий: number; нужно: number };
  регрессия: boolean | null;
}

function mark(ok: boolean | null): string {
  return ok === true ? "✓" : ok === false ? "✗" : "~";
}

export function ProjectView({ refreshTick, notify, busy, runCommand }: ViewProps) {
  const [data, setData] = useState<ProjectData | null>(null);
  const [pending, run] = usePending();
  const load = useCallback(() => {
    apiGet<ProjectData>("/api/project").then(setData).catch((e) => notify(String(e)));
  }, [notify]);
  useEffect(load, [load, refreshTick]);
  if (!data) return <p>Загрузка…</p>;
  const p = data.паспорт;
  return (
    <>
      <h1>Проект «{p.имя}»</h1>
      <p className="muted">
        язык прозы {p.язык_прозы} · томов в плане {p.томов_план} · текущий том {p.текущий_том}
        {p.профиль && <> · профиль {p.профиль}</>} · git: {data.git.репозиторий ? `копий ${data.git.удалённых_копий} из ${data.git.нужно}` : "библиотека не под git"}
      </p>
      <div className="actions">
        <button disabled={busy || pending} onClick={() => run(() => runCommand("doctor"))}>Доктор</button>
        <button disabled={busy || pending} onClick={() => run(() => runCommand("export"))}>Экспорт канона</button>
        <button disabled={busy || pending} onClick={() => run(() => runCommand("backup-archive"))}>Архив рабочей области</button>
      </div>
      <h2>Готовность к такту {data.готов_к_такту ? "✓" : "✗"}</h2>
      {data.готовность.length === 0 && <p className="ok">Минимальный комплект на месте, модули укомплектованы.</p>}
      {data.готовность.map((c, i) => (
        <div key={i} className={"editrow " + (c.ok === false ? "bad" : c.ok === null ? "warn" : "ok")}>
          <strong>{mark(c.ok)}</strong> {c.label}
          {c.hint && c.ok !== true && <div className="muted">→ {c.hint}</div>}
        </div>
      ))}
      <h2>Модули</h2>
      <table>
        <thead><tr><th>Модуль</th><th>Состояние</th><th>Требует типы</th><th>Описание</th></tr></thead>
        <tbody>
          {data.модули.map((m) => (
            <tr key={m.имя}>
              <td>{m.имя}</td>
              <td>{m.базовый ? "базовый" : m.включён ? "вкл" : "выкл"}</td>
              <td>{m.требует_типы.join(", ") || "—"}</td>
              <td className="muted">{m.описание}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Карта библиотеки ({data.карта.length})</h2>
      <table>
        <thead><tr><th>Файл</th><th>Тип</th><th>Том</th><th>Источник</th></tr></thead>
        <tbody>
          {data.карта.map((e) => (
            <tr key={e.файл}>
              <td>{e.файл}{e.авто && <span className="muted"> (авто)</span>}</td>
              <td>{e.тип}</td>
              <td>{e.том ?? "—"}</td>
              <td className="muted">{e.источник ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.вне_карты.length > 0 && (
        <p className="warn">Вне карты манифеста: {data.вне_карты.join(", ")} — сопоставьте типы в онбординге.</p>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Онбординг

interface RawEntry {
  файл: string; формат: string; статус: string; извлечено_в: string | null; причина: string; тип?: string | null;
  документ_канона?: string | null; качество: { оценка?: string; подозрительных?: number };
}
interface Proposal {
  файл: string; тип: string; уверенность: number; источник_решения: string; решение: string; имя_документа: string;
  гипотезы: { тип: string; уверенность: number }[]; вопросы: string[];
  предпросмотр: { format: string; records: number; rows: Record<string, string>[]; to_window: string[]; internal: string[]; error: string } | null;
  колонки: { mapping: Record<string, string>; missing: string[]; generated: string } | null;
  разбить: { heading: string; type: string }[];
}
interface OnboardingData { сырьё: RawEntry[]; предложения: Proposal[]; отчёт: string; решения: string[] }

export function OnboardingView({ refreshTick, notify, busy, runCommand, confirm }: ViewProps & { confirm: Confirm }) {
  const [data, setData] = useState<OnboardingData | null>(null);
  const [path, setPath] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [pending, run] = usePending();
  const load = useCallback(() => {
    apiGet<OnboardingData>("/api/onboarding").then(setData).catch((e) => notify(String(e)));
  }, [notify]);
  useEffect(load, [load, refreshTick]);
  if (!data) return <p>Загрузка…</p>;
  const decide = (file: string, decision: string) =>
    run(async () => {
      try {
        await apiPost("/api/onboarding/decision", { file, decision });
        load();
      } catch (e) {
        notify(String(e));
      }
    });
  const toApply = data.предложения.filter((p) => (p.решение && p.решение !== "сырьё" && p.решение !== "отклонить") || (!p.решение && p.тип !== "сырьё"));
  return (
    <>
      <h1>Онбординг</h1>
      <form
        className="actions"
        onSubmit={(e) => {
          e.preventDefault();
          if (path.trim()) run(() => runCommand("import", undefined, { path: path.trim() }));
        }}
      >
        <input className="search" placeholder="Папка, файл или .zip с материалами автора" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Путь к материалам" />
        <button type="submit" disabled={busy || pending || !path.trim()}>Импортировать</button>
        <button type="button" disabled={busy || pending} onClick={() => run(() => runCommand("onboarding"))}>Предложить типы</button>
        <button type="button" disabled={busy || pending} onClick={() => run(() => runCommand("onboarding", undefined, { model: true }))}>… с моделью</button>
        <button
          type="button"
          className="primary"
          disabled={busy || pending || toApply.length === 0}
          onClick={() =>
            run(async () => {
              if (await confirm(`Внести в библиотеку ${toApply.length} документов, обновить манифест и закоммитить?`)) {
                await runCommand("onboarding-apply");
              }
            })
          }
        >
          Применить ({toApply.length})
        </button>
      </form>
      <h2>Карта сырья ({data.сырьё.length})</h2>
      {data.сырьё.length === 0 && <p className="muted">Сырья нет — импортируйте материалы.</p>}
      <table>
        <thead><tr><th>Файл</th><th>Формат</th><th>Качество</th><th>Статус</th><th>Тип / документ</th></tr></thead>
        <tbody>
          {data.сырьё.map((e) => (
            <tr key={e.файл}>
              <td>{e.файл}</td>
              <td>{e.формат}</td>
              <td>{e.извлечено_в ? e.качество?.оценка ?? "—" : <span className="bad">нет извлечения</span>}</td>
              <td>{e.статус}</td>
              <td className="muted">{e.документ_канона ?? e.тип ?? (e.причина || "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Предложение «файл → тип» ({data.предложения.length})</h2>
      {data.предложения.map((p) => (
        <div key={p.файл} className="card">
          <div className="row">
            <strong>{p.файл}</strong>
            <span>
              → <b>{p.тип}</b> ({Math.round(p.уверенность * 100)}%, {p.источник_решения}){p.решение && <> · решение: {p.решение}</>}
            </span>
          </div>
          <div className="muted">
            {p.имя_документа && <>документ канона: {p.имя_документа} · </>}
            записей: {p.предпросмотр?.records ?? "—"}
            {p.гипотезы.length > 1 && <> · гипотезы: {p.гипотезы.slice(0, 3).map((h) => `${h.тип} ${Math.round(h.уверенность * 100)}%`).join(", ")}</>}
          </div>
          <div className="resolvebtns">
            <button disabled={busy || pending} onClick={() => decide(p.файл, "принять")}>Принять</button>
            <select
              disabled={busy || pending}
              aria-label="Другой тип"
              value=""
              onChange={(e) => e.target.value && decide(p.файл, `тип:${e.target.value}`)}
            >
              <option value="">Другой тип…</option>
              {p.гипотезы.map((h) => (
                <option key={h.тип} value={h.тип}>{h.тип}</option>
              ))}
            </select>
            {p.разбить.length > 0 && <button disabled={busy || pending} onClick={() => decide(p.файл, "разбить")}>Разбить ({p.разбить.length})</button>}
            <button disabled={busy || pending} onClick={() => decide(p.файл, "сырьё")}>Оставить сырьём</button>
            <button disabled={busy || pending} onClick={() => decide(p.файл, "отклонить")}>Отклонить</button>
            <button onClick={() => setOpen(open === p.файл ? null : p.файл)}>{open === p.файл ? "Скрыть разбор" : "Что прочитает машина"}</button>
          </div>
          {open === p.файл && p.предпросмотр && (
            <div className="prose-wrap">
              {p.предпросмотр.error && <p className="warn">⚠ {p.предпросмотр.error}</p>}
              {p.предпросмотр.rows.map((r, i) => (
                <div key={i} className="editrow">
                  {Object.entries(r).filter(([, v]) => v).map(([k, v]) => (
                    <span key={k}><b>{k}</b>: {v}; </span>
                  ))}
                </div>
              ))}
              {p.колонки && (
                <p className="muted">
                  колонки: {Object.entries(p.колонки.mapping).map(([k, v]) => `${k} ← «${v}»`).join(", ")}
                  {p.колонки.generated && <> · «{p.колонки.generated}» будет сгенерирована</>}
                  {p.колонки.missing.length > 0 && <span className="bad"> · нет обязательных: {p.колонки.missing.join(", ")}</span>}
                </p>
              )}
              {p.предпросмотр.to_window.length > 0 && <p><b>В окно Писателя:</b> {p.предпросмотр.to_window.join("; ")}</p>}
              {p.предпросмотр.internal.length > 0 && <p><b>Останется внутренним:</b> {p.предпросмотр.internal.join("; ")}</p>}
            </div>
          )}
          {p.вопросы.length > 0 && (
            <ul className="warn">
              {p.вопросы.map((q, i) => <li key={i}>{q}</li>)}
            </ul>
          )}
        </div>
      ))}
      {data.отчёт && (
        <>
          <h2>Отчёт готовности</h2>
          <pre className="prose">{data.отчёт}</pre>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Журналы

interface JournalsData {
  том: number; стоимость: number; глав_в_плане: number; время_автора_мин: number; машинное_мин: number;
  главы: { глава: number; состояние: string; вызовов: number; токены_вх: number; токены_вых: number; стоимость: number; автор_мин: number; машина_мин: number }[];
  по_ролям: { роль: string; вызовов: number; стоимость: number }[];
  прогноз: { осталось_глав: number; стоимость: number | null; время_автора_с: number | null; основание: string };
  предупреждения: string[];
  api: ApiLogRow[];
}

export function JournalsView({ refreshTick, notify, busy, runCommand }: ViewProps) {
  const [data, setData] = useState<JournalsData | null>(null);
  const [pending, run] = usePending();
  const load = useCallback(() => {
    apiGet<JournalsData>("/api/journals").then(setData).catch((e) => notify(String(e)));
  }, [notify]);
  useEffect(load, [load, refreshTick]);
  if (!data) return <p>Загрузка…</p>;
  const f = data.прогноз;
  return (
    <>
      <h1>Журналы · том {data.том}</h1>
      <p className="muted">
        стоимость тома ${data.стоимость.toFixed(2)} · время автора {data.время_автора_мин} мин · машинное {data.машинное_мин} мин ·
        глав в плане {data.глав_в_плане}
      </p>
      <div className="actions">
        <button disabled={busy || pending} onClick={() => run(() => runCommand("accounting"))}>Сохранить сводку (учёт)</button>
      </div>
      {data.предупреждения.map((w, i) => <p key={i} className="warn">⚠ {w}</p>)}
      <h2>Прогноз остатка тома</h2>
      <p>
        осталось глав: {f.осталось_глав}; стоимость ≈ {f.стоимость !== null ? `$${f.стоимость}` : "—"}; время автора ≈{" "}
        {f.время_автора_с !== null ? `${Math.round(f.время_автора_с / 60)} мин` : "—"} <span className="muted">({f.основание})</span>
      </p>
      <h2>По главам</h2>
      <table>
        <thead><tr><th>Глава</th><th>Состояние</th><th>Вызовов</th><th>Токены вх/вых</th><th>$</th><th>Автор, мин</th><th>Машина, мин</th></tr></thead>
        <tbody>
          {data.главы.map((c) => (
            <tr key={c.глава}>
              <td>{c.глава}</td><td>{c.состояние}</td><td>{c.вызовов}</td><td>{c.токены_вх}/{c.токены_вых}</td>
              <td>{c.стоимость.toFixed(3)}</td><td>{c.автор_мин}</td><td>{c.машина_мин}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>По ролям</h2>
      <table>
        <thead><tr><th>Роль</th><th>Вызовов</th><th>$</th></tr></thead>
        <tbody>{data.по_ролям.map((r) => <tr key={r.роль}><td>{r.роль}</td><td>{r.вызовов}</td><td>{r.стоимость.toFixed(3)}</td></tr>)}</tbody>
      </table>
      <h2>Последние вызовы API</h2>
      <table>
        <thead><tr><th>Время</th><th>Роль</th><th>Модель</th><th>Глава</th><th>Токены</th><th>$</th><th>Сек</th><th></th></tr></thead>
        <tbody>
          {data.api.slice().reverse().map((r, i) => (
            <tr key={i}>
              <td>{r.ts?.slice(0, 19).replace("T", " ")}</td>
              <td>{r.role}</td>
              <td>{r.model}</td>
              <td>{r.chapter ?? "—"}</td>
              <td>{r.tokens_in ?? "?"} / {r.tokens_out ?? "?"}</td>
              <td>{r.cost_est != null ? r.cost_est.toFixed(4) : "—"}</td>
              <td>{r.duration ?? "—"}</td>
              <td className={r.error ? "bad" : "ok"}>{r.error ? "ошибка" : "✓"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.api.length === 0 && <p className="muted">Вызовов пока не было.</p>}
    </>
  );
}

// ------------------------------------------------------------------ Регрессия

interface RegressionData {
  отчёт: { ts?: string; всего?: number; зелёная?: boolean; причина?: string; результаты?: { test_id: string; поймано?: string[]; пропущено?: string[]; лишние?: string[]; skipped?: string }[] };
  зелёная: boolean | null;
  устарел: boolean;
  тесты: { id: string; эшелон: string; ожидаемые: string[]; фрагмент: string }[];
}

export function RegressionView({ refreshTick, notify, busy, runCommand }: ViewProps) {
  const [data, setData] = useState<RegressionData | null>(null);
  const [pending, run] = usePending();
  const load = useCallback(() => {
    apiGet<RegressionData>("/api/regression").then(setData).catch((e) => notify(String(e)));
  }, [notify]);
  useEffect(load, [load, refreshTick]);
  if (!data) return <p>Загрузка…</p>;
  const r = data.отчёт;
  return (
    <>
      <h1>Регрессия</h1>
      <p className={data.зелёная === false ? "bad" : data.зелёная ? "ok" : "muted"}>
        {data.зелёная === null ? (data.устарел ? "отчёт устарел — конфигурация изменилась" : "ещё не запускалась") : data.зелёная ? "зелёная ✓" : `КРАСНАЯ ✗ ${r.причина ?? ""}`}
        {r.ts && <span className="muted"> · {r.ts.slice(0, 19).replace("T", " ")}</span>}
      </p>
      <div className="actions">
        <button disabled={busy || pending} onClick={() => run(() => runCommand("regress"))}>Прогнать Э1</button>
      </div>
      <h2>Результаты</h2>
      <table>
        <thead><tr><th>Тест</th><th>Поймано</th><th>Пропущено</th><th>Лишние</th></tr></thead>
        <tbody>
          {(r.результаты ?? []).map((t) => (
            <tr key={t.test_id} className={t.пропущено?.length ? "bad" : ""}>
              <td>{t.test_id}{t.skipped && <span className="muted"> (пропущен: {t.skipped})</span>}</td>
              <td>{(t.поймано ?? []).join(", ") || "—"}</td>
              <td>{(t.пропущено ?? []).join(", ") || "—"}</td>
              <td>{(t.лишние ?? []).join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Золотые тесты ({data.тесты.length})</h2>
      {data.тесты.map((t) => (
        <div key={t.id} className="editrow">
          <strong>{t.id}</strong> [{t.эшелон}] ожидается: {t.ожидаемые.join(", ") || "—"}
          <div className="muted">{t.фрагмент}</div>
        </div>
      ))}
      <p className="muted">Пополнить корпус: `konveyer золотой &lt;id&gt; &lt;файл&gt; --expect &lt;флаг&gt;` (из ошибки, пойманной автором).</p>
    </>
  );
}
