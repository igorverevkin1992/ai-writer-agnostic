import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPost, isConflict } from "./api";
import type { Notify, RunCommand } from "./App";
import type { Confirm } from "./Confirm";
import { compactDiff, lineDiff } from "./diff";
import { fmtTime, readDraft, removeDraft, RestoredNote, useDraft, writeDraft } from "./drafts";
import { usePending } from "./hooks";
import type { LintFinding, LintReport } from "./types";

interface CanonDoc { path: string; name: string; mtime: number; size: number }
interface Doc { path: string; text: string; version: string }
interface LintData { report: LintReport | null; changed: string[]; running: boolean; pending: boolean }

const SEV_CLASS: Record<string, string> = { ошибка: "b-BRAK", предупреждение: "b-FLAG", заметка: "" };

/** Вид «Канон»: правка документов библиотеки в реальном времени и подсветка противоречий (линтер). */
export function Canon(props: {
  busy: boolean;
  runCommand: RunCommand;
  notify: Notify;
  confirm: Confirm;
  refreshTick: number;
  /** незакоммиченные файлы библиотеки (из /api/state): правки ждут «Закоммитить канон» */
  uncommitted: string[];
}) {
  const { busy: jobBusy, runCommand, notify, confirm, refreshTick, uncommitted } = props;
  const [docs, setDocs] = useState<CanonDoc[]>([]);
  const [current, setCurrent] = useState<Doc | null>(null);
  const [lint, setLint] = useState<LintData | null>(null);
  const [filter, setFilter] = useState("");
  const [commitMsg, setCommitMsg] = useState("");
  // конфликт версии (409): на диске новая версия — различия / перечитать / перезаписать
  const [conflict, setConflict] = useState<Doc | null>(null);
  // с чем сравнивать текст редактора: версия с диска (конфликт или устаревший черновик)
  const [diffBase, setDiffBase] = useState<Doc | null>(null);
  // резервная копия правок после «Перечитать»
  const [stash, setStash] = useState<{ text: string; ts: number } | null>(null);
  const [pending, run] = usePending();
  const editor = useRef<HTMLTextAreaElement>(null);
  const busy = jobBusy || pending;
  // черновик документа: переживает перезагрузку (localStorage) и регистрируется в App как «не сохранено»
  const draftKey = current ? `канон:${current.path}` : null;
  const ds = useDraft(draftKey, current?.text ?? "", `в документе «${current?.path ?? ""}»`, current?.version);
  const draft = ds.text;
  const dirty = ds.dirty;
  const staleDraft = ds.restored !== null && ds.restored.base !== undefined && current !== null && ds.restored.base !== current.version;

  useEffect(() => {
    // смена документа: конфликт и дифф относятся к прошлому; резервная копия — по ключу
    setConflict(null);
    setDiffBase(null);
    setStash(draftKey ? readDraft(`резерв:${draftKey}`) : null);
  }, [draftKey]);

  const loadDocs = useCallback(() => {
    apiGet<{ docs: CanonDoc[] }>("/api/canon").then((r) => setDocs(r.docs)).catch((e) => notify(String(e)));
  }, [notify]);
  const loadLint = useCallback(() => {
    apiGet<LintData>("/api/lint").then(setLint).catch(() => undefined);
  }, []);

  useEffect(() => { loadDocs(); loadLint(); }, [loadDocs, loadLint, refreshTick]);
  useEffect(() => {
    const id = window.setInterval(loadLint, 3000); // наблюдатель сервера перепроверяет канон при правке файлов
    return () => window.clearInterval(id);
  }, [loadLint]);

  const open = useCallback(
    (path: string, line?: number) =>
      run(async () => {
        if (current && current.path === path) {
          // тот же документ: переход по строке в текущем буфере, правки не трогаем
          if (line) jumpTo(draft, line);
          return;
        }
        if (dirty) {
          if (!(await confirm(`В документе «${current?.path}» есть несохранённые правки. Открыть другой документ и потерять их?`))) return;
          ds.discard();
        }
        try {
          const d = await apiGet<Doc>(`/api/canon/doc?path=${encodeURIComponent(path)}`);
          setCurrent(d);
          if (line) window.setTimeout(() => jumpTo(editor.current?.value ?? d.text, line), 50);
        } catch (e) {
          notify(String(e));
        }
      }),
    [run, dirty, current, draft, ds, confirm, notify],
  );

  const jumpTo = (text: string, line: number) => {
    const el = editor.current;
    if (!el) return;
    const lines = text.split("\n");
    const start = lines.slice(0, line - 1).reduce((s, l) => s + l.length + 1, 0);
    const end = start + (lines[line - 1]?.length ?? 0);
    el.focus();
    el.setSelectionRange(start, end);
    const lineHeight = 18;
    el.scrollTop = Math.max(0, (line - 4) * lineHeight);
  };

  /** Запись документа; `force` — без проверки версии («Перезаписать всё равно»). */
  const send = async (doc: Doc, text: string, force: boolean) => {
    try {
      const r = await apiPost<{ saved: string; version: string; lint: LintReport | null }>("/api/canon/doc", {
        path: doc.path, text, ...(force ? {} : { version: doc.version }),
      });
      const saved = text.endsWith("\n") ? text : text + "\n"; // сервер дописывает перевод строки
      ds.clear(); // черновик в localStorage больше не нужен
      removeDraft(`резерв:канон:${doc.path}`);
      setStash(null);
      setConflict(null);
      setDiffBase(null);
      setCurrent({ path: doc.path, text: saved, version: r.version });
      notify(`Сохранено: ${r.saved}`, "ok");
      loadLint();
      loadDocs();
    } catch (e) {
      if (isConflict(e)) {
        // 409: на диске новая версия — показать варианты, ничего не терять
        try {
          const disk = await apiGet<Doc>(`/api/canon/doc?path=${encodeURIComponent(doc.path)}`);
          setConflict(disk);
        } catch (e2) {
          notify(String(e2));
        }
        return;
      }
      notify(String(e));
    }
  };

  const save = () =>
    run(async () => {
      if (!current || !dirty) return;
      const ok = await confirm(
        `Сохранить «${current.path}» в библиотеку канона? Это правка канона автором (сценарий Б): файл перезапишется, ` +
        "выгрузки и проверка противоречий обновятся. Коммит — отдельной кнопкой. (Д-8)",
      );
      if (!ok) return;
      await send(current, draft, false);
    });

  const overwrite = () =>
    run(async () => {
      if (!current || !conflict) return;
      const ok = await confirm(
        `Перезаписать «${current.path}» вашим текстом, затерев версию на диске? ` +
        "Правка, сделанная на диске после открытия документа, будет потеряна. (Д-8)",
      );
      if (!ok) return;
      await send(current, draft, true);
    });

  /** «Перечитать»: правки автора — в буфер обмена и в резервную копию (localStorage), в редактор — версия с диска. */
  const reread = () =>
    run(async () => {
      if (!current || !conflict || !draftKey) return;
      const mine = draft;
      let copied = true;
      try {
        await navigator.clipboard.writeText(mine);
      } catch {
        copied = false;
      }
      writeDraft(`резерв:${draftKey}`, mine, current.version);
      setStash({ text: mine, ts: Date.now() });
      removeDraft(draftKey); // иначе черновик восстановился бы поверх версии с диска
      setCurrent(conflict);
      setConflict(null);
      setDiffBase(null);
      notify(
        copied
          ? "Загружена версия с диска. Ваши правки скопированы в буфер обмена и сохранены как резервная копия."
          : "Загружена версия с диска. Ваши правки сохранены как резервная копия (буфер обмена недоступен).",
        "ok",
      );
    });

  const unstash = () => {
    if (!stash || !draftKey) return;
    ds.setText(stash.text);
    removeDraft(`резерв:${draftKey}`);
    setStash(null);
  };

  const dropStash = () => {
    if (!draftKey) return;
    removeDraft(`резерв:${draftKey}`);
    setStash(null);
  };

  /** «Отменить правки»: не подменять текст устаревшей версией — перечитать документ с диска. */
  const revert = () =>
    run(async () => {
      if (!current || !dirty) return;
      if (!(await confirm(`Отменить несохранённые правки в «${current.path}»?`))) return;
      try {
        const d = await apiGet<Doc>(`/api/canon/doc?path=${encodeURIComponent(current.path)}`);
        ds.discard();
        setConflict(null);
        setDiffBase(null);
        if (d.version !== current.version) {
          setCurrent(d);
          notify("Правки отменены; документ на диске изменился с момента открытия — загружена новая версия.", "ok");
        } else {
          notify("Правки отменены.", "ok");
        }
      } catch (e) {
        notify(String(e));
      }
    });

  const applyFix = (f: LintFinding) =>
    run(async () => {
      if (!f.fix) return;
      if (dirty && current && current.path === f.fix.file) {
        return notify("В этом документе есть несохранённые правки — сначала сохраните или отмените их, затем применяйте исправление.");
      }
      const ok = await confirm(`Применить исправление в ${f.fix.file}:${f.fix.line}?\n«${f.fix.old}» → «${f.fix.new}»\n(${f.fix.note || "механическая правка"}; запись в библиотеку канона, Д-8)`);
      if (!ok) return;
      try {
        await apiPost("/api/lint/fix", { fix: f.fix }); // ровно то, что автор подтвердил
        notify("Исправление применено — канон перепроверен.", "ok");
        loadLint();
        if (current && current.path === f.fix.file) {
          const d = await apiGet<Doc>(`/api/canon/doc?path=${encodeURIComponent(current.path)}`);
          setCurrent(d);
        }
      } catch (e) {
        notify(String(e));
      }
    });

  const lintLlm = () =>
    run(async () => {
      const n = current ? 1 : docs.filter((d) => !d.path.startsWith("ИНСТРУМЕНТ_") && !d.path.startsWith("ТЗ_") && !d.path.startsWith("Тест_Писателя/")).length;
      const ok = await confirm(`Проверить моделью ${current ? `документ «${current.path}»` : `все документы (${n} вызовов Anthropic)`} на смысловые противоречия?`);
      if (!ok) return;
      await runCommand("lint-llm", undefined, { files: current ? [current.path] : [] });
    });

  const commit = () =>
    run(async () => {
      const msg = commitMsg.trim();
      if (!msg) return notify("Введите сообщение коммита (при изменении норм — со ссылкой Р-№).");
      if (!(await confirm(`Закоммитить изменения библиотеки: «${msg}»? (Д-8)`))) return;
      await runCommand("canon-commit", undefined, { message: msg });
      setCommitMsg("");
    });

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  };

  const report = lint?.report ?? null;
  const findings = (report?.findings ?? []).map((f, index) => ({ f, index }))
    .filter(({ f }) => !filter || f.file === filter);
  const visibleDocs = docs.filter((d) => d.path.endsWith(".md"));
  const diffRows = useMemo(() => (diffBase ? lineDiff(diffBase.text, draft) : null), [diffBase, draft]);

  return (
    <>
      <h1>Канон — правка и проверка противоречий</h1>
      <p className="muted">
        Документы библиотеки правятся здесь или в любом редакторе на диске; сервер следит за папкой и перепроверяет канон
        при каждом изменении: хронология глав, границы актов, допустимость фокала, эпистемика брифов и прозы, реестр тайн
        против матрицы, диапазоны глав, возраст в досье, круги истории. Механические исправления применяются одной кнопкой,
        остальное — подсветка для вашего решения. Модельный слой ищет смысловые противоречия.
        {report && (
          <>
            {" "}Последняя проверка: {report.ts.slice(0, 19).replace("T", " ")} · документов {report.files_checked} ·{" "}
            <strong className={report.errors ? "bad" : "ok"}>ошибок {report.errors}</strong>, предупреждений {report.warnings}, заметок {report.notes}
            {lint?.running && <> · проверяю…</>}
            {lint?.pending && !lint.running && <> · есть непроверенные изменения (сервер занят)</>}
          </>
        )}
      </p>

      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => run(() => runCommand("lint"))}>Проверить канон</button>
        <button disabled={busy} onClick={lintLlm}>Проверить моделью{current ? " (этот документ)" : " (все)"}</button>
        <input className="search" style={{ minWidth: 260 }} placeholder="сообщение коммита канона (Р-№ при смене норм)"
          value={commitMsg} onChange={(e) => setCommitMsg(e.target.value)} aria-label="Сообщение коммита" />
        <button disabled={busy || !commitMsg.trim()} onClick={commit}>Закоммитить канон</button>
      </div>
      {uncommitted.length > 0 && (
        <p className="muted" title={uncommitted.join("\n")}>
          <span className="bad">Не закоммичено: {uncommitted.length} файл(ов)</span> — {uncommitted.slice(0, 5).join(", ")}
          {uncommitted.length > 5 ? "…" : ""}. Правки сохранены на диске и проверены; закоммитьте их с сообщением выше.
        </p>
      )}

      <div className="canon-layout">
        <div className="canon-docs" role="list" aria-label="Документы канона">
          {visibleDocs.map((d) => {
            const n = (report?.findings ?? []).filter((f) => f.file === d.path).length;
            return (
              <div key={d.path} role="listitem" tabIndex={0}
                className={"qitem" + (current?.path === d.path ? " active" : "")}
                onClick={() => open(d.path)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(d.path); } }}>
                <div className="row"><span>{d.path}</span>{n > 0 && <span className="badge b-FLAG">{n}</span>}</div>
              </div>
            );
          })}
        </div>
        <div className="canon-editor">
          {current ? (
            <>
              <div className="row" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{current.path}{dirty ? " · не сохранено" : ""}</strong>
                <div className="actions" style={{ margin: 0 }}>
                  <button disabled={!dirty || busy} onClick={revert}>Отменить правки</button>
                  <button className="primary" disabled={!dirty || busy} onClick={save}>Сохранить (Ctrl+S)</button>
                </div>
              </div>
              <RestoredNote state={ds} extra={staleDraft ? "документ на диске с тех пор изменился, проверьте различия перед сохранением" : undefined} />
              {staleDraft && !diffBase && (
                <div className="actions" style={{ margin: "4px 0" }}>
                  <button onClick={() => setDiffBase(current)}>Показать различия с диском</button>
                </div>
              )}
              {stash && (
                <div className="draft-note" role="status">
                  резервная копия ваших правок от {fmtTime(stash.ts)} (после конфликта версий){" "}
                  <button type="button" disabled={busy} onClick={unstash}>вернуть в редактор</button>{" "}
                  <button type="button" disabled={busy} onClick={dropStash}>удалить</button>
                </div>
              )}
              {conflict && (
                <div className="card conflict" role="alertdialog" aria-labelledby="conflict-title">
                  <strong id="conflict-title">На диске новая версия «{current.path}»</strong>
                  <p className="muted" style={{ margin: "4px 0 8px" }}>
                    Документ изменён после того, как вы его открыли (другой редактор, исправление линтера или git).
                    Ваш текст не сохранён и не потерян: выберите, что делать.
                  </p>
                  <div className="actions" style={{ margin: 0 }}>
                    <button onClick={() => setDiffBase(diffBase ? null : conflict)}>
                      {diffBase ? "Скрыть различия" : "Показать различия"}
                    </button>
                    <button disabled={busy} onClick={reread}>Перечитать (мои правки — в буфер обмена и в резервную копию)</button>
                    <button className="danger" disabled={busy} onClick={overwrite}>Перезаписать всё равно</button>
                    <button onClick={() => { setConflict(null); setDiffBase(null); }}>Закрыть</button>
                  </div>
                </div>
              )}
              {diffBase && diffRows && (
                <div className="diff-wrap">
                  <div className="row" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <span className="muted">
                      различия: <span className="del">− только на диске</span> · <span className="add">+ только у вас</span>
                      {diffRows.approximate && " · документ большой — показаны только несовпадающие строки, без выравнивания"}
                      {diffRows.lines.every((l) => l.kind === " ") && " · текст совпадает с диском"}
                    </span>
                    <button onClick={() => setDiffBase(null)}>скрыть</button>
                  </div>
                  <div className="diff">
                    {compactDiff(diffRows.lines).map((l, i) =>
                      l.kind === "…" ? (
                        <div key={i} className="muted">… {l.count} стр. без изменений …</div>
                      ) : (
                        <div key={i} className={l.kind === "+" ? "add" : l.kind === "-" ? "del" : ""}>
                          {l.kind}{" "}{l.text}
                        </div>
                      ),
                    )}
                  </div>
                </div>
              )}
              <textarea ref={editor} className="canon-text" value={draft} spellCheck={false}
                onChange={(e) => ds.setText(e.target.value)} onKeyDown={onKey} aria-label={`Документ ${current.path}`} />
            </>
          ) : (
            <p className="muted">Выберите документ слева. Находки справа ведут к нужной строке.</p>
          )}
        </div>
      </div>

      <h2>
        Находки {report ? `(${findings.length}${filter ? ` в ${filter}` : ""})` : ""}
        {filter && <button style={{ marginLeft: 10 }} onClick={() => setFilter("")}>показать все</button>}
        {current && !filter && <button style={{ marginLeft: 10 }} onClick={() => setFilter(current.path)}>только этот документ</button>}
      </h2>
      {!report && <p className="muted">Проверка ещё не выполнялась — нажмите «Проверить канон».</p>}
      {report && findings.length === 0 && <p className="ok">Противоречий не найдено.</p>}
      {findings.map(({ f, index }) => (
        <div className="card" key={index}>
          <span className={"badge " + (SEV_CLASS[f.severity] ?? "")}>{f.severity}</span>{" "}
          <strong>{f.code}</strong>{f.source === "модель" && <span className="muted"> · модель</span>}{" "}
          <a href="#" onClick={(e) => { e.preventDefault(); open(f.file, f.line ?? undefined); }}>
            {f.file}{f.line ? `:${f.line}` : ""}
          </a>
          <div>{f.message}</div>
          {f.quote && <blockquote>{f.quote}</blockquote>}
          {f.fix && (
            <div className="resolvebtns">
              <span className="muted">«{f.fix.old}» → «{f.fix.new}»</span>
              <button disabled={busy} onClick={() => applyFix(f)}>Применить исправление</button>
            </div>
          )}
        </div>
      ))}
    </>
  );
}
