import { useState } from 'react';
import { patch, type Doc, type Overview } from '../api.ts';

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

interface Propagation {
  affectedEpisodes: number[];
  staleCards: number[];
  staleScripts: number[];
  findings: unknown[];
}

/** What a hand edit touched, in one line. */
function propagationNote(p: Propagation): string {
  const parts: string[] = [];
  if (p.staleCards.length) parts.push(`устарели карточки серий ${p.staleCards.join(', ')}`);
  if (p.staleScripts.length) parts.push(`устарели сценарии серий ${p.staleScripts.join(', ')}`);
  if (p.findings.length) parts.push(`новых замечаний: ${p.findings.length}`);
  return parts.length ? `Сохранено. ${cap(parts.join('; '))}.` : 'Сохранено. На серии это не повлияло.';
}

/** One fact with an inline editor: text and the episode from which it holds. */
function FactRow({ f, pid, onDone }: { f: Doc; pid: string; onDone: (note: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(f.text as string);
  const [since, setSince] = useState(f.since_ep ? String(f.since_ep) : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await patch<Propagation>(`/projects/${pid}/facts/${encodeURIComponent(f.id)}`, {
        text: text.trim(),
        since_ep: since.trim() ? Number(since) : null,
      });
      setEditing(false);
      onDone(propagationNote(res));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!editing) {
    return (
      <li>
        {f.text}
        {f.since_ep ? <span className="muted"> · с {f.since_ep}-й серии</span> : ''}{' '}
        <button className="ghost small" onClick={() => setEditing(true)}>
          Править
        </button>
      </li>
    );
  }
  return (
    <li className="edit-row">
      <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
      <label className="inline">
        С какой серии верен
        <input type="number" min={1} value={since} onChange={(e) => setSince(e.target.value)} placeholder="до сезона" />
      </label>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={busy || !text.trim()} onClick={() => void save()}>
          Сохранить
        </button>
        <button className="ghost" onClick={() => setEditing(false)}>
          Отмена
        </button>
      </div>
    </li>
  );
}

/** One timeline event with an inline editor: year and text. */
function EventRow({ e, pid, onDone }: { e: Doc; pid: string; onDone: (note: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [year, setYear] = useState(String(e.year));
  const [text, setText] = useState(e.text as string);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await patch<Propagation>(`/projects/${pid}/events/${encodeURIComponent(e.id)}`, { year: Number(year), text: text.trim() });
      setEditing(false);
      onDone(propagationNote(res));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!editing) {
    return (
      <li>
        <b>{e.year}</b> — {e.text}{' '}
        <button className="ghost small" onClick={() => setEditing(true)}>
          Править
        </button>
      </li>
    );
  }
  return (
    <li className="edit-row">
      <label className="inline">
        Год
        <input type="number" value={year} onChange={(ev) => setYear(ev.target.value)} />
      </label>
      <textarea rows={2} value={text} onChange={(ev) => setText(ev.target.value)} />
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={busy || !text.trim() || !year.trim()} onClick={() => void save()}>
          Сохранить
        </button>
        <button className="ghost" onClick={() => setEditing(false)}>
          Отмена
        </button>
      </div>
    </li>
  );
}

export function BibleScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const b = data.bible;
  const [note, setNote] = useState('');
  if (!b) return <p className="muted panel">Библии пока нет: она появится после шага «Библия».</p>;
  const pid = data.project.id;
  const terms = data.genre?.terms ?? { hero: { nom: 'героиня', gen: 'героини', dat: 'героине' }, wound: 'Предательство' };
  const villains = [...((b.villains ?? []) as Doc[])].sort((x, y) => y.rank - x.rank);
  const done = (text: string) => {
    setNote(text);
    void refresh();
  };
  return (
    <section className="bible">
      {data.logline && (
        <article className="panel">
          <h2>Логлайн</h2>
          <p>{data.logline.text_35w}</p>
        </article>
      )}
      <article className="panel">
        <h2>{terms.wound}</h2>
        <p>
          <b>{b.betrayal.who}</b>: {b.betrayal.what}. Почему: {b.betrayal.why}. {cap(terms.hero.nom)} видит это на {b.betrayal.ep1_second}-й секунде 1-й серии.
        </p>
      </article>
      <article className="panel">
        <h2>Персонажи</h2>
        <table>
          <thead>
            <tr>
              <th>Кто</th>
              <th>Год рожд.</th>
              <th>Внешность</th>
              <th>Хочет</th>
              <th>Маска</th>
            </tr>
          </thead>
          <tbody>
            {(b.characters as Doc[]).map((c) => (
              <tr key={c.name}>
                <td>
                  <b>{c.name}</b>
                  {c.regular === false && <span className="muted"> · эпизод</span>}
                </td>
                <td>{c.birth_year}</td>
                <td>{c.look}</td>
                <td>{c.want}</td>
                <td>{c.mask}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </article>
      {villains.length > 0 && (
        <article className="panel">
          <h2>Лестница злодеев</h2>
          <table>
            <thead>
              <tr>
                <th>Ранг</th>
                <th>Кто</th>
                <th>Угроза</th>
                <th>Слабость</th>
                <th>Снят</th>
                <th>Ключ к следующему</th>
              </tr>
            </thead>
            <tbody>
              {villains.map((v) => (
                <tr key={v.name}>
                  <td>{v.rank}</td>
                  <td>
                    <b>{v.name}</b>
                    <div className="muted">{v.mask}</div>
                  </td>
                  <td>{v.threat}</td>
                  <td>{v.weakness}</td>
                  <td>{v.takedown_ep}-я</td>
                  <td>{v.key_to_next}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      )}
      <article className="panel">
        <h2>Правила мира</h2>
        <ul>
          {(b.world_rules as Doc[]).map((r) => (
            <li key={r.rule}>
              <b>{r.rule}.</b> Причина: {r.why}. Цена: {r.cost}.
            </li>
          ))}
        </ul>
      </article>
      <article className="panel">
        <h2>Тайна</h2>
        <ol>
          {((b.secrets ?? []) as Doc[]).map((s) => (
            <li key={s.layer}>
              {s.revealed_ep}-я серия: {s.truth}. <span className="muted">Цель {terms.hero.gen}: {s.goal_from} → {s.goal_to}</span>
            </li>
          ))}
        </ol>
      </article>
      {note && <p className="note">{note}</p>}
      <article className="panel">
        <h2>Хронология</h2>
        <p className="muted">Правка года или события проверяется сразу: возрасты, порядок событий, серии, которые на них опираются.</p>
        <ul className="timeline">
          {[...(b.timeline.events as Doc[])]
            .sort((x, y) => x.year - y.year)
            .map((e) => (
              <EventRow key={e.id} e={e} pid={pid} onDone={done} />
            ))}
        </ul>
      </article>
      <article className="panel">
        <h2>Факты и кто что знает</h2>
        <p className="muted">Изменённый факт делает устаревшими карточки и сценарии серий, которые на него опираются: их нужно будет переписать или проверить.</p>
        <ul>
          {(b.facts as Doc[]).map((f) => (
            <FactRow key={f.id} f={f} pid={pid} onDone={done} />
          ))}
        </ul>
      </article>
    </section>
  );
}
