import { useEffect, useState, type KeyboardEvent } from 'react';
import { api, post, put, type Doc, type Fact, type Finding, type Overview } from '../api.ts';
import { FindingCard, topThree } from './Findings.tsx';

interface ScriptView {
  script: { ep: number; blocks: Doc[] };
  text: string;
}

function blockLine(b: Doc): string {
  if (b.kind === 'scene') return `[${b.t0}–${b.t1} с] ${b.text}`;
  if (b.kind === 'line') return `${b.speaker}${b.parenthetical ? ` (${b.parenthetical})` : ''}: ${b.text}`;
  const tag: Record<string, string> = { sound: 'ЗВУК', overlay: 'ТЕКСТ НА ЭКРАНЕ', insert: 'ВСТАВКА', silence: 'ТИШИНА' };
  return `[${tag[b.kind]}] ${b.text}`;
}

/**
 * Raw editor of a card or a script: the document as JSON, saved as a new version after the
 * server checks it. For a producer who wants to fix a line by hand without a model call.
 */
function RawEditor({ doc, what, onSave, onClose }: { doc: Doc; what: string; onSave: (body: Doc) => Promise<string>; onClose: () => void }) {
  const [text, setText] = useState(() => JSON.stringify(doc, null, 2));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      let body: Doc;
      try {
        body = JSON.parse(text) as Doc;
      } catch {
        throw new Error('Текст не разбирается как JSON: проверьте кавычки, запятые и скобки');
      }
      const note = await onSave(body);
      onClose();
      window.alert(note);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="raw-editor">
      <p className="muted">{what} в виде JSON. Меняйте тексты в кавычках, структуру не трогайте: сервер проверит документ и сохранит его как новую версию, затем прогонит кодовые проверки без модели.</p>
      <textarea rows={18} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => void save()}>
          Сохранить
        </button>
        <button className="ghost" onClick={onClose}>
          Отмена
        </button>
      </div>
    </div>
  );
}

/** Card, script, up to three findings, and polishing of a selected fragment. */
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function EpisodeScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const pid = data.project.id;
  const [cards, setCards] = useState<Doc[]>([]);
  // Every episode that has something: a plan line, a card or a script.
  const eps = [...new Set([...(data.plan?.episodes.map((e) => e.ep as number) ?? []), ...cards.map((c) => c.ep as number), ...data.scripts])].sort((a, b) => a - b);
  const [ep, setEp] = useState<number>(data.scripts[0] ?? data.plan?.episodes[0]?.ep ?? 1);
  const status = (step: string) => data.steps.find((s) => s.step === step)?.status;
  // The server accepts polishing only after scripts, before polish is approved, while no step runs.
  const polishOpen =
    ['approved', 'skipped'].includes(status('scripts') ?? '') && status('polish') !== 'approved' && !data.steps.some((s) => s.status === 'checking');
  const [script, setScript] = useState<ScriptView | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [note, setNote] = useState('');
  const [variants, setVariants] = useState<Doc[][] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<'' | 'card' | 'script'>('');
  const working = data.steps.some((s) => s.status === 'checking') || !!data.job?.running;
  const terms = data.genre?.terms ?? { hero: { nom: 'героиня', gen: 'героини', dat: 'героине' }, wound: 'Предательство' };
  const saveCard = async (body: Doc) => {
    const res = await put<{ findings: unknown[] }>(`/projects/${pid}/cards/${ep}`, body);
    await refresh();
    return `Карточка ${ep}-й серии сохранена. Замечаний кодовых проверок: ${res.findings.length}. Блок карточек нужно утвердить заново.`;
  };
  const saveScript = async (body: Doc) => {
    const res = await put<{ findings: unknown[] }>(`/projects/${pid}/scripts/${ep}`, body);
    await refresh();
    return `Сценарий ${ep}-й серии сохранён. Замечаний кодовых проверок: ${res.findings.length}. Шаг «Сценарии» нужно утвердить заново.`;
  };

  useEffect(() => {
    let alive = true;
    api<Doc[]>(`/projects/${pid}/cards`)
      .then((x) => alive && setCards(x))
      .catch(() => alive && setCards([]));
    return () => {
      alive = false;
    };
  }, [pid, data]);
  useEffect(() => {
    // Answers for an episode the producer already left must not show up on screen.
    let alive = true;
    api<ScriptView>(`/projects/${pid}/scripts/${ep}`)
      .then((x) => alive && setScript(x))
      .catch(() => alive && setScript(null));
    api<Finding[]>(`/projects/${pid}/findings?status=open`)
      .then((all) => alive && setFindings(all.filter((f) => f.episode === ep)))
      .catch(() => alive && setFindings([]));
    return () => {
      alive = false;
    };
  }, [pid, ep, data]);

  const card = cards.find((c) => c.ep === ep);
  const outline = data.plan?.episodes.find((e) => e.ep === ep);
  // A new selection makes old variants meaningless: they were written for another fragment.
  const pick = (i: number) => {
    if (busy || !polishOpen) return;
    setVariants(null);
    setSel((s) => (!s ? [i, i] : s[0] === s[1] && i > s[0] ? [s[0], i] : [i, i]));
  };

  const propose = async () => {
    if (!sel) return;
    setBusy(true);
    setError('');
    try {
      const res = await post<{ variants: Doc[][] }>(`/projects/${pid}/polish`, { ep, from: sel[0], to: sel[1], note });
      setVariants(res.variants);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const choose = async (i: number) => {
    setBusy(true);
    setError('');
    try {
      await post(`/projects/${pid}/polish/choose`, { variant: i });
      setVariants(null);
      setSel(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="episode">
      <div className="ep-picker">
        {eps.map((n) => (
          <button key={n} className={`ep-btn ${n === ep ? 'ep-active' : ''} ${data.scripts.includes(n) ? 'ep-has-script' : ''}`} disabled={busy} onClick={() => {
              setEp(n);
              setSel(null);
              setVariants(null);
            }}>
            {n}
          </button>
        ))}
      </div>
      <div className="episode-grid">
        <article className="panel">
          <h2>
            {ep}-я серия{outline ? ` «${outline.title}»` : ''}
            {data.stale.cards.includes(ep) && <span className="badge badge-bad"> карточка устарела</span>}
          </h2>
          {card && editing === '' && (
            <button className="ghost small" disabled={busy || working} onClick={() => setEditing('card')}>
              Править карточку
            </button>
          )}
          {card && editing === 'card' && <RawEditor doc={card} what="Карточка серии" onSave={saveCard} onClose={() => setEditing('')} />}
          {card && editing !== 'card' ? (
            <dl className="card">
              <dt>Первые 5 секунд</dt>
              <dd>{card.hook_0_5s}</dd>
              <dt>Событие</dt>
              <dd>{card.event}</dd>
              <dt>{cap(terms.hero.nom)}</dt>
              <dd>{card.heroine_action}</dd>
              <dt>Реплика-удар</dt>
              <dd>{card.punchline}</dd>
              <dt>Клиффхэнгер</dt>
              <dd>{card.cliffhanger}</dd>
              <dt>В кадре · локация</dt>
              <dd>
                {card.cast.join(', ')} · {card.location}
              </dd>
              <dt>Кадр метро</dt>
              <dd>
                {card.metro_frame.face} / {card.metro_frame.action} / {card.metro_frame.object}
              </dd>
            </dl>
          ) : card ? null : (
            <p className="muted">{outline ? `По плану: ${outline.event}` : 'Карточки пока нет.'}</p>
          )}
          {topThree(findings).map((f) => (
            <FindingCard key={f.id} f={f} facts={(data.bible?.facts ?? []) as Fact[]} projectId={pid} canAddFact={!!data.bible} onDone={() => void refresh()} />
          ))}
        </article>
        <article className="panel">
          <h2>
            Сценарий
            {data.stale.scripts.includes(ep) && <span className="badge badge-bad"> устарел</span>}
          </h2>
          {script && editing === '' && (
            <button className="ghost small" disabled={busy || working} onClick={() => setEditing('script')}>
              Править сценарий
            </button>
          )}
          {script && editing === 'script' && <RawEditor doc={script.script} what="Сценарий серии" onSave={saveScript} onClose={() => setEditing('')} />}
          {!script ? (
            <p className="muted">Сценария этой серии пока нет.</p>
          ) : (
            <>
              <p className="muted">
                {polishOpen
                  ? 'Выделите реплики для доработки: щёлкните (или нажмите Enter) по первой и по последней строке фрагмента.'
                  : status('polish') === 'approved'
                    ? 'Доработка утверждена: фрагменты больше не меняются.'
                    : 'Дорабатывать фрагменты можно после того, как сценарии утверждены или пропущены.'}
              </p>
              <ol className="script">
                {script.script.blocks.map((b, i) => (
                  <li
                    key={i}
                    className={`blk blk-${b.kind} ${sel && i >= sel[0] && i <= sel[1] ? 'blk-sel' : ''}`}
                    {...(polishOpen
                      ? {
                          role: 'button',
                          tabIndex: 0,
                          'aria-pressed': !!sel && i >= sel[0] && i <= sel[1],
                          onClick: () => pick(i),
                          onKeyDown: (e: KeyboardEvent) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              pick(i);
                            }
                          },
                        }
                      : {})}
                  >
                    {blockLine(b)}
                  </li>
                ))}
              </ol>
              {sel && polishOpen && (
                <div className="polish">
                  <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Пожелание: жёстче, смешнее, с подтекстом…" />
                  <button className="primary" disabled={busy} onClick={propose}>
                    {busy ? 'Пишу варианты…' : '5 вариантов'}
                  </button>
                </div>
              )}
              {error && <p className="error">{error}</p>}
              {variants && (
                <div className="variants">
                  {variants.map((v, i) => (
                    <div key={i} className="variant">
                      <pre>{v.map(blockLine).join('\n')}</pre>
                      <button disabled={busy} onClick={() => choose(i)}>
                        Взять вариант {i + 1}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </article>
      </div>
    </section>
  );
}
