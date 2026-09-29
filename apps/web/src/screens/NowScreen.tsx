import { useEffect, useState } from 'react';
import { api, post, type Doc, type Fact, type Finding, type Overview } from '../api.ts';
import { FindingCard, topThree } from './Findings.tsx';

/** One task at a time: what to do now and the criterion of done, in one line. */
export function NowScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const { next } = data;
  const pid = data.project.id;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [findings, setFindings] = useState<Finding[]>([]);
  const facts = (data.bible?.facts ?? []) as Fact[];

  useEffect(() => {
    api<Finding[]>(`/projects/${pid}/findings?status=open&step=${next.step}`)
      .then(setFindings)
      .catch(() => setFindings([]));
  }, [pid, next.step, data]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const run = (body: object = {}) => act(() => post(`/projects/${pid}/steps/${next.step}/run`, body));
  const approve = (body: object = {}) => act(() => post(`/projects/${pid}/steps/${next.step}/approve`, body));
  const skip = () => act(() => post(`/projects/${pid}/steps/${next.step}/skip`));
  const top = topThree(findings);

  return (
    <section className="now">
      <div className="task">
        <p className="task-step">Шаг: {next.stepLabel}</p>
        <h2>{busy ? 'Агент работает… это может занять несколько минут' : next.task}</h2>
        <p className="criterion">Готово, когда: {next.criterion}</p>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {next.action === 'run' && (
            <button className="primary" disabled={busy} onClick={() => run(next.episodes ? { episodes: next.episodes } : {})}>
              Запустить
            </button>
          )}
          {next.action === 'approve' && (
            <>
              <button className="primary" disabled={busy} onClick={() => approve()}>
                Утвердить
              </button>
              <button disabled={busy} onClick={() => run()}>
                Переделать
              </button>
            </>
          )}
          {next.action === 'approve_block' && (
            <>
              <button className="primary" disabled={busy} onClick={() => approve({ block: next.block })}>
                Утвердить блок
              </button>
              <button disabled={busy} onClick={() => run({ episodes: [next.block] })}>
                Переписать блок
              </button>
            </>
          )}
          {next.action === 'resolve' && (
            <button disabled={busy} onClick={() => run(next.step === 'scripts' ? { episodes: [...new Set(findings.map((f) => f.episode).filter(Boolean))], fix: true } : {})}>
              {next.step === 'scripts' ? 'Переписать серии с замечаниями' : 'Переделать шаг'}
            </button>
          )}
          {next.action === 'polish' && (
            <>
              <a className="button" href={`#/p/${pid}/episode`}>
                Открыть серию
              </a>
              {(data.steps.find((s) => s.step === 'polish')?.version ?? 0) > 0 ? (
                <button className="primary" disabled={busy} onClick={() => approve()}>
                  Утвердить доработку
                </button>
              ) : (
                <button className="primary" disabled={busy} onClick={skip}>
                  Доработка не нужна
                </button>
              )}
            </>
          )}
          {next.action === 'export' && (
            <a className="button primary" href={`#/p/${pid}/export`}>
              К экспорту
            </a>
          )}
          {!['export', 'polish', 'wait'].includes(next.action) && (
            <button className="ghost" disabled={busy} onClick={skip} title="Шаг будет пропущен по вашему решению">
              Пропустить шаг
            </button>
          )}
        </div>
      </div>

      {next.action === 'choose_concept' && (
        <div className="concepts">
          {(data.concepts ?? []).map((c: Doc, i: number) => (
            <article key={c.id ?? i} className="panel concept">
              <h3>{c.title}</h3>
              <p>{c.premise}</p>
              <p className="muted">Крючок: {c.hook}</p>
              <p className="muted">Формула: {c.genre_formula}</p>
              <button className="primary" disabled={busy} onClick={() => approve({ choice: i })}>
                Выбрать эту
              </button>
            </article>
          ))}
          <button disabled={busy} onClick={() => run()}>
            Другие три концепции
          </button>
        </div>
      )}

      {next.step === 'logline' && data.logline && next.action === 'approve' && (
        <article className="panel">
          <h3>Логлайн</h3>
          <p>{data.logline.text_35w}</p>
          <p className="muted">Реклама: {data.logline.ad_15w}</p>
        </article>
      )}

      {top.length > 0 && (
        <div className="findings">
          <h3>Замечания {findings.filter((f) => f.status === 'open').length > 3 ? `(показаны 3 из ${findings.filter((f) => f.status === 'open').length})` : ''}</h3>
          {top.map((f) => (
            <FindingCard key={f.id} f={f} facts={facts} projectId={pid} onDone={() => void refresh()} />
          ))}
        </div>
      )}
    </section>
  );
}
