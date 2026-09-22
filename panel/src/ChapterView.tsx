import { useCallback, useContext, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { apiGet, apiPost } from "./api";
import type { Notify, RunCommand, TabRequest } from "./App";
import type { Confirm } from "./Confirm";
import { DirtyContext, readDraft, removeDraft, RestoredNote, useDirtyKeys, useDraft } from "./drafts";
import { appendPair, countOccurrences, describeFound, hasParagraphBreak, parseEdits } from "./edits";
import { highlight, type Mark } from "./highlight";
import { usePending } from "./hooks";
import { JOB_LABEL, nextStep, TAB_LABEL, TABS, type Tab } from "./nextstep";
import type { ChapterDetail, Flag, Job, Resolution } from "./types";

const AUTHOR_FIX_CONFIRM =
  "Считать расхождения текущего черновика с правками АВТОРСКОЙ правкой, а не самоволием Писателя? " +
  "Самовольные изменения будут исключены из отчёта дифф-контроля (FR-E3), и глава сможет пройти приёмку. (Д-8)";

// кнопки такта по состоянию FSM (сценарий А, §3.2)
const ACTIONS: Record<string, { label: string; cmd: string; primary?: boolean; confirm?: string }[]> = {
  "не-начато": [{ label: "Собрать окно", cmd: "compile", primary: true }],
  "собрано": [
    { label: "Написать главу", cmd: "write", primary: true },
    { label: "Пересобрать окно", cmd: "compile" },
  ],
  "сгенерировано": [{ label: "Проверить Э1", cmd: "verify1", primary: true }],
  "верифицировано-1": [{ label: "Проверить Э2", cmd: "verify2", primary: true }],
  "верифицировано-2": [{ label: "Пакет приёмки", cmd: "review", primary: true }],
  "на-приёмке": [{ label: "Внести правки Писателем", cmd: "apply-edits", primary: true }],
  "правки": [
    { label: "Дифф-контроль", cmd: "diff-check", primary: true },
    { label: "Дифф-контроль как авторская правка", cmd: "diff-check-author", confirm: AUTHOR_FIX_CONFIRM },
  ],
  "дифф-контроль": [
    { label: "Повторить правки", cmd: "apply-edits" },
    { label: "Дифф-контроль как авторская правка", cmd: "diff-check-author", confirm: AUTHOR_FIX_CONFIRM },
  ],
  "принято": [
    { label: "Пакет в канон", cmd: "canonize", primary: true },
    {
      label: "Применить пакет + коммит",
      cmd: "canonize-apply",
      confirm: "Применить пакет к УГАР_Библиотеке и сделать git-коммит? (Д-8)",
    },
  ],
  "зафиксировано": [],
};

// состояния, где «Продолжить такт» выполняет машинные шаги до паузы автора (FR-O1)
const MACHINE_STATES = new Set([
  "не-начато", "собрано", "сгенерировано", "верифицировано-1", "верифицировано-2", "правки",
]);

const editsKey = (chapter: number) => `глава:${chapter}:правки`;

export function ChapterView(props: {
  chapter: number;
  job: Job | null;
  offline: boolean;
  refreshTick: number;
  tabRequest: TabRequest | null;
  runCommand: RunCommand;
  notify: Notify;
  confirm: Confirm;
}) {
  const { chapter, job, offline, refreshTick, tabRequest, runCommand, notify, confirm } = props;
  const [d, setD] = useState<ChapterDetail | null>(null);
  const [tab, setTab] = useState<Tab>("чтение");
  const [pending, run] = usePending();
  // несохранённый текст во вкладках (аудит 5.3): смена вкладки — с подтверждением,
  // черновики в localStorage помечаются точкой на вкладке
  const dirtyRegistry = useContext(DirtyContext);
  const dirtyPrefix = `глава:${chapter}:`;
  const dirtyKeys = useDirtyKeys(dirtyPrefix);
  const draftTabs = useMemo(() => {
    const set = new Set<string>();
    for (const t of TABS) if (readDraft(`${dirtyPrefix}${t}`)) set.add(t);
    for (const k of dirtyKeys) set.add(k.slice(dirtyPrefix.length));
    return set;
  }, [dirtyPrefix, dirtyKeys]);

  const switchTab = (t: Tab) =>
    run(async () => {
      if (t === tab) return;
      const q = dirtyRegistry.question(dirtyPrefix);
      if (q && !(await confirm(q))) return;
      if (q) dirtyRegistry.leave(dirtyPrefix);
      setTab(t);
    });
  const switchTabRef = useRef(switchTab);
  switchTabRef.current = switchTab;

  // просьба извне открыть вкладку (карточка задачи → «Окно / ручной режим»)
  useEffect(() => {
    if (tabRequest && tabRequest.n === chapter) switchTabRef.current(tabRequest.tab);
  }, [tabRequest, chapter]);

  const load = useCallback(() => {
    apiGet<ChapterDetail>(`/api/chapter/${chapter}`).then(setD).catch((e) => notify(String(e)));
  }, [chapter, notify]);

  useEffect(load, [load, refreshTick]);

  if (!d) return <p>Загрузка главы {chapter}…</p>;

  const busy = job?.status === "выполняется" || pending || offline;
  const actions = ACTIONS[d.state] ?? [];
  const diffClean = d.diff_report
    ? d.diff_report.not_applied.length === 0 && d.diff_report.unauthorized.length === 0
    : null;
  const unresolved = d.resolutions.filter((r) => !r.decision).length;
  const ns = nextStep(d.state, {
    unresolved, diffClean, hasEdits: d.edits_parsed.length > 0, hasBatch: d.canon_batch != null,
  });
  // задача другой главы — видна с пометкой; глобальные (без главы) — только в карточке вверху (5.5)
  const otherJob = job && job.status === "выполняется" && job.chapter != null && job.chapter !== chapter ? job : null;

  const act = (a: { cmd: string; confirm?: string }) =>
    run(async () => {
      if (a.confirm && !(await confirm(a.confirm))) return;
      await runCommand(a.cmd, chapter);
    });

  const accept = () =>
    run(async () => {
      if (!(await confirm(`Принять главу ${chapter}? (FR-E4, явное подтверждение)`))) return;
      try {
        await apiPost(`/api/chapter/${chapter}/accept`);
        load();
      } catch (e) {
        notify(String(e));
      }
    });

  const rollback = () =>
    run(async () => {
      if (!(await confirm(`Откатить главу ${chapter} на шаг назад?`))) return;
      try {
        await apiPost(`/api/chapter/${chapter}/rollback`, {});
        load();
      } catch (e) {
        notify(String(e));
      }
    });

  const strikeAll = () =>
    run(async () => {
      if (!(await confirm(`Вычеркнуть все самоволки без решения (${unresolved})? Они не попадут в канон; Писатель уберёт их при внесении правок.`))) return;
      try {
        const r = await apiPost<{ resolved: number }>(`/api/chapter/${chapter}/resolve-all`, { decision: "вычеркнуть" });
        notify(`Вычеркнуто самоволок: ${r.resolved}.`, "ok");
        load();
      } catch (e) {
        notify(String(e));
      }
    });

  return (
    <>
      <h1>
        Глава {chapter} <span className={`badge b-${d.state}`}>{d.state}</span>
      </h1>
      <div className="muted">
        черновик {d.draft} · авто-повторов {d.retries} · итераций правок {d.iterations}
        {(d.author_min > 0 || d.machine_min > 0) && (
          <>
            {" "}· время автора {d.author_min} мин {d.author_min > 40 ? "⚠ (цель ≤40)" : ""} · машинное {d.machine_min} мин
          </>
        )}
      </div>
      {ns.label && (
        <div className="nextstep" data-testid="nextstep">
          <strong>Дальше:</strong> {ns.label}{" "}
          {ns.tab && ns.tab !== tab && (
            <button type="button" className="link" onClick={() => switchTab(ns.tab as Tab)}>
              открыть вкладку «{TAB_LABEL[ns.tab]}»
            </button>
          )}
        </div>
      )}

      <div className="actions">
        {MACHINE_STATES.has(d.state) && (
          <button className="primary" disabled={busy} onClick={() => run(() => runCommand("run", chapter))}
            title="Выполнить машинные шаги такта до следующей паузы автора (FR-O1)">
            Продолжить такт ▶
          </button>
        )}
        {actions.map((a) => (
          <button key={a.cmd} className={a.primary ? "primary" : ""} disabled={busy} onClick={() => act(a)}>
            {a.label}
          </button>
        ))}
        {d.state === "дифф-контроль" && (
          <button className="primary" disabled={busy || !diffClean || unresolved > 0} onClick={accept}
            title={!diffClean ? "дифф-контроль не чист" : unresolved ? "есть самоволки без решения" : ""}>
            Принять главу
          </button>
        )}
        {d.state !== "не-начато" && d.state !== "зафиксировано" && (
          <button className="danger" disabled={busy} onClick={rollback}>Откат на шаг</button>
        )}
        {d.state === "зафиксировано" && <span className="ok">Такт завершён ✓</span>}
      </div>

      {otherJob && (
        <div className="jobnote" role="status">
          Идёт задача «{JOB_LABEL[otherJob.name] ?? otherJob.name}» главы {otherJob.chapter} — кнопки этой главы ждут её завершения.
        </div>
      )}

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t}
            className={(tab === t ? "on" : "") + (ns.tab === t && tab !== t ? " hint" : "")}
            onClick={() => switchTab(t)}
            title={draftTabs.has(t) ? "есть несохранённый текст" : ns.tab === t ? "следующий шаг — здесь" : undefined}>
            {ns.tab === t && tab !== t && <span className="tab-arrow" aria-hidden="true">→ </span>}
            {TAB_LABEL[t]}
            {draftTabs.has(t) && <span className="tab-dot" aria-label="не сохранено">●</span>}
          </button>
        ))}
      </div>

      {tab === "чтение" && (
        <Reading d={d} reload={load} notify={notify} unresolved={unresolved} onStrikeAll={strikeAll} busy={busy} />
      )}
      {tab === "правки" && <Edits d={d} reload={load} notify={notify} />}
      {tab === "приёмка" && (
        <Acceptance d={d} reload={load} notify={notify} unresolved={unresolved} onStrikeAll={strikeAll} busy={busy} />
      )}
      {tab === "ручной" && <ManualTab d={d} reload={load} notify={notify} busy={busy} />}
      {tab === "история" && <History d={d} />}
    </>
  );
}

// ------------------------------------------------------------ Чтение с флагами

interface SelectionBox {
  text: string;
  top: number;
  left: number;
}

function Reading(props: {
  d: ChapterDetail; reload: () => void; notify: Notify; unresolved: number; onStrikeAll: () => void; busy: boolean;
}) {
  const { d, reload, notify, unresolved, onStrikeAll, busy } = props;
  const html = useMemo(() => {
    if (!d.text) return null;
    // один проход по позициям сырого текста, пересечения отбрасываются (5.3)
    const marks: Mark[] = [];
    for (const c of d.verdict?.checks ?? []) {
      if (c.status === "PASS") continue;
      c.quotes.slice(0, 3).forEach((q, j) =>
        marks.push({ quote: q, cls: c.status, id: `a-${c.check_id}-${j}`, title: `${c.check_id}: порог ${c.threshold}, факт ${c.actual}` }),
      );
    }
    for (const f of d.flags) {
      marks.push({ quote: f.quote, cls: f.kind, id: `a-${f.flag_id}`, title: `${f.flag_id} · ${f.type}: ${f.rule}. ${f.recommendation}` });
    }
    return highlight(d.text, marks);
  }, [d]);

  // решения возможны только когда есть решения.json — его создаёт «Пакет приёмки» (5.6)
  const hasResolutions = d.resolutions.length > 0;

  // «выделил фрагмент → Заменить на…» (аудит 2, 5.6)
  const proseWrap = useRef<HTMLDivElement>(null);
  const [sel, setSel] = useState<SelectionBox | null>(null);
  const [editor, setEditor] = useState<{ before: string; source: string } | null>(null);

  const readSelection = useCallback(() => {
    const s = window.getSelection();
    const wrap = proseWrap.current;
    if (!s || s.isCollapsed || !wrap || s.rangeCount === 0) return setSel(null);
    const range = s.getRangeAt(0);
    if (!wrap.contains(range.startContainer) || !wrap.contains(range.endContainer)) return setSel(null);
    const text = s.toString();
    if (!text.trim()) return setSel(null);
    const rect = range.getBoundingClientRect();
    const base = wrap.getBoundingClientRect();
    setSel({ text, top: rect.top - base.top - 34, left: Math.max(0, rect.left - base.left) });
  }, []);

  useEffect(() => {
    // выделение мышью или с клавиатуры (Shift+стрелки) — кнопка следует за ним; снято — исчезает
    document.addEventListener("selectionchange", readSelection);
    return () => document.removeEventListener("selectionchange", readSelection);
  }, [readSelection]);

  const openEditor = (before: string, source: string) => {
    if (hasParagraphBreak(before)) {
      notify("Выделите фрагмент внутри одного абзаца: пустая строка завершает «БЫЛО» в правки.md.");
      return;
    }
    setEditor({ before: before.trim(), source });
    setSel(null);
  };

  return (
    <>
      {d.verdict && (
        <>
          <h2>Формальные проверки (Э1)</h2>
          <table>
            <thead><tr><th></th><th>Проверка</th><th>Факт</th><th>Порог</th></tr></thead>
            <tbody>
              {d.verdict.checks.map((c) => (
                <tr key={c.check_id + c.actual}>
                  <td><span className={`badge b-${c.status}`}>{c.status}</span></td>
                  <td>{c.check_id}</td>
                  <td>{c.actual}</td>
                  <td>{c.threshold}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>
        Смысловые флаги (Э2)
        {hasResolutions && unresolved > 0 && (
          <button type="button" className="danger h2btn" disabled={busy} onClick={onStrikeAll}
            title="Одно решение для всех самоволок без решения">
            Вычеркнуть все ({unresolved})
          </button>
        )}
      </h2>
      {d.flags.length === 0 && <p className="muted">Флагов нет{d.state === "сгенерировано" || d.state === "собрано" ? " (Э2 ещё не запускался)" : ""}.</p>}
      {d.flags.map((f) => (
        <FlagCard key={f.flag_id} f={f} chapter={d.chapter}
          resolution={d.resolutions.find((r) => r.flag_id === f.flag_id)}
          canResolve={hasResolutions} reload={reload} notify={notify}
          onEdit={d.text ? () => openEditor(f.quote, `флаг ${f.flag_id}`) : undefined} />
      ))}

      {editor && d.text != null && (
        <ReplaceEditor key={editor.before + editor.source} chapter={d.chapter} text={d.text} before={editor.before}
          source={editor.source} editsMd={d.edits_md} onClose={() => setEditor(null)} reload={reload} notify={notify} />
      )}

      {html ? (
        <>
          <h2>Текст главы (черновик {d.draft})</h2>
          <p className="muted">Выделите фрагмент — появится кнопка «Заменить на…»: пара БЫЛО/СТАЛО уйдёт в правки.md.</p>
          <div className="prose-wrap" ref={proseWrap} onMouseUp={readSelection}>
            {sel && (
              <button type="button" className="primary sel-btn" style={{ top: sel.top, left: sel.left }}
                onMouseDown={(e: ReactMouseEvent) => e.preventDefault()} // не сбрасывать выделение
                onClick={() => openEditor(sel.text, "выделение")}>
                Заменить на…
              </button>
            )}
            <div className="prose" dangerouslySetInnerHTML={{ __html: html }} />
          </div>
        </>
      ) : (
        <p className="muted">Черновика ещё нет — начните такт кнопками выше.</p>
      )}
    </>
  );
}

/** Пара «БЫЛО (из выделения или цитаты флага) → СТАЛО (ввод)» → в конец правки.md через API правок.
 *  Если во вкладке «Правки» лежит несохранённый черновик — пара добавляется к нему, черновик сохраняется. */
function ReplaceEditor(props: {
  chapter: number; text: string; before: string; source: string; editsMd: string | null;
  onClose: () => void; reload: () => void; notify: Notify;
}) {
  const { chapter, text, before, source, editsMd, onClose, reload, notify } = props;
  const [after, setAfter] = useState("");
  const [pending, run] = usePending();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const found = describeFound(countOccurrences(text, before));
  const key = editsKey(chapter);
  const draft = readDraft(key);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const save = () =>
    run(async () => {
      const base = draft?.text ?? editsMd ?? "";
      const next = appendPair(base, before, after);
      try {
        const r = await apiPost<{ parsed: number }>(`/api/chapter/${chapter}/edits`, { text: next });
        removeDraft(key); // черновик вкладки «Правки» ушёл на сервер вместе с новой парой
        notify(`Правка добавлена в правки.md (распознано правок — ${r.parsed}).`, "ok");
        onClose();
        reload();
      } catch (e) {
        notify(String(e));
      }
    });

  return (
    <div className="card replace" role="region" aria-label="Новая правка" data-testid="replace-editor">
      <div className="row">
        <strong>Новая правка</strong>
        <span className="muted">источник: {source}{draft ? " · добавится к несохранённому черновику правок" : ""}</span>
      </div>
      <div className="muted">БЫЛО <span className={found.cls}>({found.text})</span>:</div>
      <blockquote>{before}</blockquote>
      <label className="muted" htmlFor="replace-after">СТАЛО (пусто — удалить фрагмент):</label>
      <textarea id="replace-after" ref={inputRef} style={{ minHeight: 70 }} value={after}
        onChange={(e) => setAfter(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save();
        }} />
      <div className="actions">
        <button className="primary" disabled={pending} onClick={save}>Добавить в правки (Ctrl+Enter)</button>
        <button disabled={pending} onClick={onClose}>Отмена</button>
      </div>
    </div>
  );
}

const REGISTRIES = ["3.1", "3.2", "3.3", "1.2"];

function FlagCard(props: {
  f: Flag; chapter: number; resolution?: Resolution; canResolve: boolean; reload: () => void; notify: Notify;
  onEdit?: () => void;
}) {
  const { f, chapter, resolution, canResolve, reload, notify, onEdit } = props;
  const [registry, setRegistry] = useState(REGISTRIES[0]);
  const [pending, run] = usePending();
  const decide = (decision: string) =>
    run(async () => {
      try {
        await apiPost(`/api/chapter/${chapter}/resolve`, { flag_id: f.flag_id, decision, registry });
        reload();
      } catch (e) {
        notify(String(e));
      }
    });
  const badge = f.kind === "samovolka" ? "самоволка" : f.severity;
  const showType = f.type.trim().toLowerCase() !== badge.toLowerCase();
  return (
    <div className="card">
      <span className={`badge b-${f.kind}`}>{badge}</span>{" "}
      <strong>{f.flag_id}</strong>
      {showType && <> · {f.type}</>} <a href={`#a-${f.flag_id}`}>¶</a>
      {onEdit && f.quote.trim() && (
        <button type="button" className="h2btn" onClick={onEdit} title="БЫЛО = цитата флага, СТАЛО — введите">
          Сделать правку из флага
        </button>
      )}
      <blockquote>{f.quote}</blockquote>
      <div className="muted">{f.rule}. {f.recommendation}</div>
      {f.kind === "samovolka" && (
        resolution?.decision ? (
          <div className="resolved">
            решение: {resolution.decision}
            {resolution.target_registry ? ` → ${resolution.target_registry}` : ""}
          </div>
        ) : canResolve && resolution ? (
          <div className="resolvebtns">
            <span className="unresolved">решение автора:</span>
            <button disabled={pending} onClick={() => decide("вычеркнуть")}>Вычеркнуть</button>
            <button disabled={pending} onClick={() => decide("канонизировать")}>Канонизировать →</button>
            <select value={registry} aria-label="Реестр для канонизации" onChange={(e) => setRegistry(e.target.value)}>
              {REGISTRIES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </div>
        ) : (
          <div className="muted">решение автора — после «Пакета приёмки» (решения.json ещё нет)</div>
        )
      )}
    </div>
  );
}

// ------------------------------------------------------------------- Правки

const EDITS_TEMPLATE = "БЫЛО: \nСТАЛО: \n\nУКАЗАНИЕ: \n";

function Edits({ d, reload, notify }: { d: ChapterDetail; reload: () => void; notify: Notify }) {
  // черновик правки.md переживает перезагрузку и смену вида (аудит 5.3)
  const ds = useDraft(editsKey(d.chapter), d.edits_md ?? EDITS_TEMPLATE, `во вкладке «Правки» главы ${d.chapter}`);
  const text = ds.text;
  const [k1, setK1] = useState<number | null>(null);
  const [k2, setK2] = useState<number | null>(null);
  const [diff, setDiff] = useState<string[] | null>(null);
  const [pending, run] = usePending();
  // живая проверка по тексту поля и черновика главы — без сохранения (5.6)
  const parsed = useMemo(() => parseEdits(text), [text]);
  const live = useMemo(
    () => parsed.edits.map((e) => ({ ...e, count: e.before ? countOccurrences(d.text ?? "", e.before) : -1 })),
    [parsed, d.text],
  );

  const save = () =>
    run(async () => {
      try {
        const r = await apiPost<{ parsed: number }>(`/api/chapter/${d.chapter}/edits`, { text });
        ds.clear();
        notify(`Сохранено: распознано правок — ${r.parsed}`, "ok");
        reload();
      } catch (e) {
        notify(String(e));
      }
    });

  const showDiff = () =>
    run(async () => {
      const a = k1 ?? d.drafts[d.drafts.length - 2];
      const b = k2 ?? d.draft;
      if (a == null || b == null) return;
      try {
        const r = await apiGet<{ lines: string[] }>(`/api/chapter/${d.chapter}/diff/${a}/${b}`);
        setDiff(r.lines);
      } catch (e) {
        notify(String(e));
      }
    });

  const problems = live.filter((e) => e.count === 0 || e.count > 1).length + parsed.errors.length;

  return (
    <>
      <h2>правки.md — пары «БЫЛО/СТАЛО» и строки «УКАЗАНИЕ:»{ds.dirty && <span className="tab-dot"> · не сохранено</span>}</h2>
      <p className="muted">
        «БЫЛО» — дословная цитата из черновика {d.draft}; проверка ниже идёт по мере ввода, до сохранения.
        Быстрее: во вкладке «Чтение с флагами» выделите фрагмент → «Заменить на…».
      </p>
      <RestoredNote state={ds} />
      <textarea aria-label="правки.md" value={text} onChange={(e) => ds.setText(e.target.value)} />
      <div className="actions">
        <button className="primary" disabled={pending || !ds.dirty} onClick={save}>Сохранить правки</button>
        {ds.dirty && <button disabled={pending} onClick={ds.discard}>Отменить правки</button>}
      </div>

      {(live.length > 0 || parsed.errors.length > 0) && (
        <>
          <h2>
            Проверка правок по тексту поля{" "}
            <span className={problems ? "bad" : "ok"}>({problems ? `замечаний: ${problems}` : "всё найдено дословно"})</span>
          </h2>
          {parsed.errors.map((e, i) => (
            <div className="editrow" key={`err-${i}`}>
              <span className="bad">✗</span>
              <span className="bad">ошибка формата — {e}</span>
            </div>
          ))}
          {live.map((e) => {
            const f = e.before ? describeFound(e.count) : null;
            return (
              <div className="editrow" key={e.seq} data-testid="editrow">
                <span className={f ? f.cls : "ok"}>{f ? (f.cls === "ok" ? "✓" : f.cls === "bad" ? "✗" : "‼") : "✓"}</span>
                <span>
                  <strong>{e.seq}.</strong>{" "}
                  {e.before ? <>БЫЛО: {e.before} → СТАЛО: {e.after || <em className="muted">(удалить)</em>}</> : <>УКАЗАНИЕ: {e.after}</>}
                  {f && <span className={f.cls}> — {f.text}</span>}
                  {!f && <span className="muted"> — свободное указание, проверяется глазами</span>}
                </span>
              </div>
            );
          })}
        </>
      )}

      {d.drafts.length >= 2 && (
        <>
          <h2>Дифф черновиков</h2>
          <div className="actions">
            <select aria-label="Старый черновик" value={k1 ?? d.drafts[d.drafts.length - 2]} onChange={(e) => setK1(+e.target.value)}>
              {d.drafts.map((k) => <option key={k} value={k}>черновик_{k}</option>)}
            </select>
            →
            <select aria-label="Новый черновик" value={k2 ?? d.draft} onChange={(e) => setK2(+e.target.value)}>
              {d.drafts.map((k) => <option key={k} value={k}>черновик_{k}</option>)}
            </select>
            <button disabled={pending} onClick={showDiff}>Показать дифф</button>
          </div>
          {diff && (
            <div className="diff">
              {diff.map((l, i) => (
                <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>
                  {l}
                </div>
              ))}
              {diff.length === 0 && "черновики идентичны"}
            </div>
          )}
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Приёмка

function Acceptance(props: {
  d: ChapterDetail; reload: () => void; notify: Notify; unresolved: number; onStrikeAll: () => void; busy: boolean;
}) {
  const { d, reload, notify, unresolved, onStrikeAll, busy } = props;
  // пакет канониста: черновик в localStorage, пока пакет существует (аудит 5.3)
  const ds = useDraft(d.canon_batch != null ? `глава:${d.chapter}:приёмка` : null, d.canon_batch ?? "",
    `во вкладке «Приёмка» главы ${d.chapter}`);
  const batch = ds.text;
  const [pending, run] = usePending();

  const saveBatch = () =>
    run(async () => {
      try {
        await apiPost(`/api/chapter/${d.chapter}/canon-batch`, { text: batch });
        ds.clear();
        notify("Пакет сохранён.", "ok");
        reload();
      } catch (e) {
        notify(String(e));
      }
    });

  return (
    <>
      <h2>Дифф-контроль</h2>
      {d.diff_report ? (
        <ul>
          <li>внесено правок: {(d.diff_report.applied_share * 100).toFixed(0)}%</li>
          <li className={d.diff_report.not_applied.length ? "bad" : "ok"}>
            не внесено: {d.diff_report.not_applied.join(", ") || "—"}
          </li>
          <li className={d.diff_report.unauthorized.length ? "bad" : "ok"}>
            самовольных изменений: {d.diff_report.unauthorized.length}
          </li>
          {d.diff_report.unverifiable.length > 0 && (
            <li>свободные указания {d.diff_report.unverifiable.join(", ")} — проверьте глазами</li>
          )}
        </ul>
      ) : (
        <p className="muted">Дифф-контроль ещё не выполнялся.</p>
      )}
      {d.diff_report?.unauthorized.map((u, i) => <div className="card" key={i}>{u}</div>)}
      {d.diff_report && d.diff_report.unauthorized.length > 0 && (
        <p className="muted">
          Если текст правили вы сами — «Дифф-контроль как авторская правка» в кнопках такта (с подтверждением).
        </p>
      )}

      {unresolved > 0 && (
        <p className="unresolved">
          Самоволок без решения: {unresolved} — вкладка «Чтение с флагами».{" "}
          <button type="button" className="danger" disabled={busy} onClick={onStrikeAll}>Вычеркнуть все</button>
        </p>
      )}

      <h2>Пакет записей в канон (пакет_канона.md){ds.dirty && <span className="tab-dot"> · не сохранено</span>}</h2>
      {d.canon_batch != null ? (
        <>
          <p className="muted">Удалите строки, которые не принимаете, и сохраните — затем «Применить пакет + коммит».</p>
          <RestoredNote state={ds} />
          <textarea aria-label="пакет_канона.md" style={{ minHeight: 260 }} value={batch} onChange={(e) => ds.setText(e.target.value)} />
          <div className="actions">
            <button className="primary" disabled={pending || !ds.dirty} onClick={saveBatch}>Сохранить пакет</button>
            {ds.dirty && <button disabled={pending} onClick={ds.discard}>Отменить правки</button>}
          </div>
        </>
      ) : (
        <p className="muted">Пакета ещё нет — после приёмки нажмите «Пакет в канон».</p>
      )}
    </>
  );
}

// ------------------------------------------------------------------- История

function History({ d }: { d: ChapterDetail }) {
  return (
    <>
      <h2>История переходов FSM</h2>
      <table>
        <thead><tr><th>Время</th><th>Из</th><th>В</th><th>Команда</th></tr></thead>
        <tbody>
          {d.history.slice().reverse().map((h, i) => (
            <tr key={i}>
              <td>{h.время?.slice(0, 19).replace("T", " ")}</td>
              <td>{h.из}</td>
              <td>{h.в}</td>
              <td>{h.команда}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.history.length === 0 && <p className="muted">Переходов ещё не было.</p>}
    </>
  );
}


// -------------------------------------------------- Окно контекста и ручной режим

function ManualTab({ d, reload, notify, busy }: { d: ChapterDetail; reload: () => void; notify: Notify; busy: boolean }) {
  const [win, setWin] = useState<{ text: string | null; size_flag: string | null } | null>(null);
  // вставленный черновик/ответ модели (20–30 КБ) — в localStorage до приёма (аудит 5.3)
  const ds = useDraft(`глава:${d.chapter}:ручной`, "", `во вкладке «Окно / ручной режим» главы ${d.chapter}`);
  const pasted = ds.text;
  const setPasted = ds.setText;
  const [pending, run] = usePending();
  const locked = busy || pending;

  useEffect(() => {
    apiGet<{ text: string | null; size_flag: string | null }>(`/api/chapter/${d.chapter}/window`)
      .then(setWin)
      .catch(() => setWin({ text: null, size_flag: null }));
  }, [d.chapter, d.state]);

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notify(`${what} — скопировано в буфер.`, "ok");
    } catch {
      notify("Буфер обмена недоступен — выделите текст ниже вручную.");
    }
  };

  // POST: промпт строится и СОХРАНЯЕТСЯ в папку главы (GET ничего не пишет — аудит 4.3)
  const copyPrompt = (kind: "verify2" | "edits", what: string) =>
    run(async () => {
      try {
        const r = await apiPost<{ text: string }>(`/api/chapter/${d.chapter}/prompt/${kind}`);
        await copy(r.text, what);
      } catch (e) {
        notify(String(e));
      }
    });

  const sendDraft = () =>
    run(async () => {
      try {
        const r = await apiPost<{ draft: number }>(`/api/chapter/${d.chapter}/manual-draft`, { text: pasted });
        notify(`Черновик принят как draft_${r.draft}.`, "ok");
        ds.discard(); // принято сервером — черновик больше не нужен
        reload();
      } catch (e) {
        notify(String(e));
      }
    });

  const sendFlags = () =>
    run(async () => {
      try {
        const r = await apiPost<{ flags: number }>(`/api/chapter/${d.chapter}/manual-flags`, { text: pasted });
        notify(`Принято флагов Э2: ${r.flags}.`, "ok");
        ds.discard();
        reload();
      } catch (e) {
        notify(String(e));
      }
    });

  const needDraft = ["собрано", "сгенерировано", "на-приёмке", "дифф-контроль"].includes(d.state);
  const needFlags = d.state === "верифицировано-1";

  return (
    <>
      <p className="muted">
        Ручной режим (NFR-3): если API недоступен, скопируйте промпт в чат модели и вставьте её ответ сюда —
        такт продолжится штатно, FSM и проверки сохраняются.
      </p>

      {needDraft && (
        <>
          <h2>{d.state === "собрано" || d.state === "сгенерировано" ? "Генерация главы вручную" : "Внесение правок вручную"}</h2>
          <div className="actions">
            {(d.state === "собрано" || d.state === "сгенерировано") && win?.text && (
              <button onClick={() => copy(win.text!, "Окно контекста")}>Скопировать окно контекста</button>
            )}
            {(d.state === "на-приёмке" || d.state === "дифф-контроль") && (
              <button disabled={locked} onClick={() => copyPrompt("edits", "Промпт правок")}>Скопировать промпт правок</button>
            )}
          </div>
          <RestoredNote state={ds} />
          <textarea aria-label="Текст черновика" placeholder={`Вставьте текст главы — будет сохранён как draft_${d.draft + 1}.md`}
            value={pasted} onChange={(e) => setPasted(e.target.value)} />
          <div className="actions">
            <button className="primary" disabled={locked || !pasted.trim()} onClick={sendDraft}>
              {pending ? "Регистрируется…" : `Принять как draft_${d.draft + 1}`}
            </button>
          </div>
        </>
      )}

      {needFlags && (
        <>
          <h2>Проверка Э2 вручную</h2>
          <div className="actions">
            <button disabled={locked} onClick={() => copyPrompt("verify2", "Промпт Верификатора-2")}>
              Сформировать и скопировать промпт Э2
            </button>
          </div>
          <RestoredNote state={ds} />
          <textarea aria-label="Ответ Верификатора-2" placeholder="Вставьте JSON-ответ модели (можно вместе с пояснениями — массив будет найден)"
            value={pasted} onChange={(e) => setPasted(e.target.value)} />
          <div className="actions">
            <button className="primary" disabled={locked || !pasted.trim()} onClick={sendFlags}>Принять флаги Э2</button>
          </div>
        </>
      )}

      {!needDraft && !needFlags && (
        <>
          <p className="muted">В состоянии «{d.state}» ручной ввод не требуется.</p>
          {pasted.trim() && (
            <div className="draft-note" role="status">
              есть невставленный текст ({pasted.length} симв.) из прошлого состояния{" "}
              <button type="button" onClick={ds.discard}>отбросить</button>
            </div>
          )}
        </>
      )}

      {win?.size_flag && <div className="card bad">{win.size_flag}</div>}
      {win?.text ? (
        <>
          <h2>Окно контекста (окно.md)</h2>
          <pre className="window">{win.text}</pre>
        </>
      ) : (
        <p className="muted">Окно ещё не собрано.</p>
      )}
    </>
  );
}
