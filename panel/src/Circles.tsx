import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, errText } from "./api";
import type { Notify, RunCommand } from "./App";
import type { Confirm } from "./Confirm";
import { RestoredNote, useDraft } from "./drafts";
import { usePending } from "./hooks";

interface Step { n: number; name: string; text: string; chapters?: string }
interface Circle { scope: string; key: number | null; title: string; steps: Step[]; weak_spot?: string; summary?: string; generated?: string }
interface Part { part: number; title: string; from_chapter: number; to_chapter: number }
interface Act { act: number; title: string; from_chapter: number; to_chapter: number; parts: string; steps: string }
interface CirclesData {
  circles: Circle[];
  parts: Part[];
  acts: Act[];
  prompts: string[];
  canon_status: Record<string, string>;
  in_canon: number;
  /** документ канона, в который вносятся каркасы текущего тома (имя — из каталога типов, П-1) */
  canon_doc: string;
  volume: number;
}

const SCOPE_LABEL: Record<string, string> = { книга: "Книга", акт: "Акты", глава: "Главы" };

function stemOf(c: Circle): string {
  if (c.scope === "книга") return "книга";
  if (c.scope === "акт") return `акт_${c.key}`;
  return `глава_${String(c.key ?? 0).padStart(2, "0")}`;
}

export function Circles(props: {
  busy: boolean;
  runCommand: RunCommand;
  notify: Notify;
  confirm: Confirm;
  refreshTick: number;
  chapterCount: number; // число глав из реестра (state.briefs), а не константа
}) {
  const { busy: jobBusy, runCommand, notify, confirm, refreshTick, chapterCount } = props;
  const [data, setData] = useState<CirclesData | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [manualStem, setManualStem] = useState<string>("");
  // предпросмотр внесения в канон (FR-DR-4): дифф документа каркасов до подтверждения
  const [preview, setPreview] = useState<{ doc: string; lines: string[]; changed: boolean } | null>(null);
  // вставленный ответ модели (ручной режим) — в localStorage и в реестре «не сохранено» (аудит 5.3)
  const ds = useDraft("круги:ручной", "", "в ручном режиме «Кругов истории»");
  const pasted = ds.text;
  const setPasted = ds.setText;
  const [pending, run] = usePending();
  const busy = jobBusy || pending;

  const load = useCallback(() => {
    apiGet<CirclesData>("/api/circles").then(setData).catch((e) => notify(errText(e)));
  }, [notify]);
  useEffect(load, [load, refreshTick]);

  if (!data) return <p>Загрузка…</p>;

  const nActs = data.acts.length;
  const generate = (scope: string, redo = false) =>
    run(async () => {
      const count = scope === "книга" ? 1 : scope === "акты" ? nActs : scope === "главы" ? chapterCount : 1 + nActs + chapterCount;
      if (!(await confirm(`Построить круги истории: ${scope} (${count} вызов(ов) модели${redo ? ", с пересчётом" : ""})?`))) return;
      await runCommand("story-circles", undefined, { scope, redo });
    });

  const pendingCanon = Object.values(data.canon_status).filter((s) => s !== "в каноне").length;

  const showPreview = () =>
    run(async () => {
      if (preview) return setPreview(null);
      try {
        setPreview(await apiGet<{ doc: string; lines: string[]; changed: boolean }>("/api/circles/preview"));
      } catch (e) {
        notify(errText(e));
      }
    });

  const toCanon = () =>
    run(async () => {
      const ok = await confirm(
        `Внести ${data.circles.length} каркас(ов) в документ «${data.canon_doc}» библиотеки (том ${data.volume}) и закоммитить канон? ` +
        "После этого окна глав получат секцию «Драматургия», а Э2 — проверку драматургии. (Д-8)",
      );
      if (!ok) return;
      await runCommand("circles-canon");
    });

  const copyPrompt = (stem: string) =>
    run(async () => {
      try {
        const r = await apiGet<{ text: string }>(`/api/circles/prompt/${encodeURIComponent(stem)}`);
        await navigator.clipboard.writeText(r.text);
        notify(`Промпт «${stem}» скопирован — вставьте ответ модели ниже.`, "ok");
        setManualStem(stem);
      } catch (e) {
        notify(errText(e));
      }
    });

  const acceptManual = () =>
    run(async () => {
      const m = manualStem.match(/^(книга|акт|глава)_?(\d+)?$/);
      if (!m) return notify("Выберите промпт (книга / акт_N / глава_NN).");
      try {
        await apiPost("/api/circles/manual", { scope: m[1], key: m[2] ? +m[2] : null, text: pasted });
        notify("Круг принят.", "ok");
        ds.discard(); // принято сервером — черновик больше не нужен
        load();
      } catch (e) {
        notify(errText(e));
      }
    });

  const groups = ["книга", "акт", "глава"].map((s) => ({ scope: s, items: data.circles.filter((c) => c.scope === s) }));

  return (
    <>
      <h1>Круги истории — каркас драматургии</h1>
      <p className="muted">
        Каркас драматургии по методике проекта: каркас тома → каркасы {nActs} актов → каркасы глав; каждый уровень
        строится внутри шага уровня выше. Черновики лежат в <code>драматургия/</code>; после внесения в канон
        (документ «{data.canon_doc}») они попадают в окно Писателя («Драматургия главы») и в проверку Э2.
        {" "}В каноне сейчас: <strong>{data.in_canon}</strong> круг(ов)
        {pendingCanon > 0 && <>, не внесено или изменено: <strong>{pendingCanon}</strong></>}.
      </p>
      {nActs > 0 && (
        <table>
          <thead><tr><th>Акт</th><th>Название</th><th>Главы</th><th>Части</th><th>Шаги круга тома</th></tr></thead>
          <tbody>
            {data.acts.map((a) => (
              <tr key={a.act}>
                <td>{a.act}</td><td>«{a.title}»</td><td>{a.from_chapter}–{a.to_chapter}</td><td>{a.parts}</td><td>{a.steps}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => generate("всё")}>Построить все круги</button>
        <button disabled={busy} onClick={() => generate("книга")}>Книга</button>
        <button disabled={busy} onClick={() => generate("акты")}>Акты</button>
        <button disabled={busy} onClick={() => generate("главы")}>Главы</button>
        <button disabled={busy} onClick={() => generate("всё", true)}>Пересчитать всё</button>
        <button disabled={busy || data.circles.length === 0} onClick={showPreview}>
          {preview ? "Скрыть изменения" : "Что изменится в каноне"}
        </button>
        <button className={pendingCanon > 0 ? "primary" : ""} disabled={busy || data.circles.length === 0} onClick={toCanon}>
          Внести в канон{pendingCanon > 0 ? ` (${pendingCanon})` : ""}
        </button>
      </div>
      {preview && (
        <div className="card" data-testid="circles-preview">
          <strong>{preview.doc}</strong>{" "}
          <span className="muted">{preview.changed ? "— изменения при внесении в канон:" : "— уже совпадает с черновиками"}</span>
          {preview.changed && (
            <div className="diff">
              {preview.lines.map((l, i) => (
                <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>{l}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {data.prompts.length > 0 && (
        <details className="card">
          <summary>Ручной режим: промпты без ответа ({data.prompts.length})</summary>
          <div className="actions">
            {data.prompts.map((p) => {
              const stem = p.replace(/\.md$/, "");
              return <button key={p} disabled={pending} onClick={() => copyPrompt(stem)}>{stem}</button>;
            })}
          </div>
          {manualStem && (
            <>
              <p className="muted">Ответ модели для «{manualStem}»:</p>
              <RestoredNote state={ds} />
              <textarea aria-label="Ответ модели" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="Вставьте JSON-ответ модели" />
              <div className="actions">
                <button className="primary" disabled={busy || !pasted.trim()} onClick={acceptManual}>Принять круг</button>
              </div>
            </>
          )}
        </details>
      )}

      {data.circles.length === 0 && <p className="muted">Кругов ещё нет — нажмите «Построить все круги» (сначала строится том, затем акты внутри тома, затем главы внутри актов).</p>}

      {groups.map(({ scope, items }) => items.length > 0 && (
        <div key={scope}>
          <h2>{SCOPE_LABEL[scope]}{scope !== "книга" ? ` (${items.length})` : ""}</h2>
          {items.map((c) => {
            const id = `${c.scope}_${c.key ?? ""}`;
            const act = c.scope === "акт" ? data.acts.find((a) => a.act === c.key) : undefined;
            const actTitle = act ? ` · «${act.title}» (гл. ${act.from_chapter}–${act.to_chapter})` : "";
            const status = data.canon_status[stemOf(c)] ?? "не в каноне";
            const isOpen = open === id;
            return (
              <div className="card" key={id}>
                <div
                  className="row circle-head"
                  role="button"
                  tabIndex={0}
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setOpen(isOpen ? null : id);
                    }
                  }}
                >
                  <strong>
                    {c.title}{c.title.includes("«") ? "" : actTitle}{" "}
                    <span className={"badge" + (status === "в каноне" ? " b-зафиксировано" : "")}>{status}</span>
                  </strong>
                  <span className="muted">{c.summary}</span>
                </div>
                {isOpen && (
                  <ol className="circle">
                    {c.steps.map((s) => (
                      <li key={s.n}>
                        <strong>{s.name}</strong>{s.chapters ? <span className="muted"> · {s.chapters}</span> : null}
                        <div>{s.text}</div>
                      </li>
                    ))}
                  </ol>
                )}
                {isOpen && c.weak_spot && (
                  <div className="bad" style={{ marginTop: 6 }}><strong>Слабое место:</strong> {c.weak_spot}</div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </>
  );
}
