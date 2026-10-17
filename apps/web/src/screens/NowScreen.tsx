import { useEffect, useState } from 'react';
import { api, ApiError, formatEpisodes, parseEpisodes, patch, post, progressLine, type Doc, type Fact, type Finding, type Overview } from '../api.ts';
import { FindingCard, topThree } from './Findings.tsx';

/** Raise the budget right where the run stopped for it. */
export function RaiseLimit({ data, refresh, onError }: { data: Overview; refresh: () => Promise<void>; onError: (m: string) => void }) {
  const [value, setValue] = useState(String(Math.ceil(data.budget.limitUsd * 1.5)));
  const [busy, setBusy] = useState(false);
  const raise = async () => {
    setBusy(true);
    try {
      const n = Number(value);
      if (!Number.isFinite(n) || n <= data.budget.totalUsd) throw new Error(`Новый лимит должен быть больше потраченного ($${data.budget.totalUsd.toFixed(2)})`);
      await patch(`/projects/${data.project.id}`, { budgetLimitUsd: n });
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="raise-limit">
      <label className="inline">
        Новый лимит, $
        <input type="number" min={1} step={10} value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <button disabled={busy} onClick={() => void raise()}>
        Поднять лимит
      </button>
    </div>
  );
}

/** One task at a time: what to do now and the criterion of done, in one line. */
export function NowScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const { next } = data;
  const pid = data.project.id;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [overBudget, setOverBudget] = useState(false);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [pick, setPick] = useState<string | null>(null);
  const facts = (data.bible?.facts ?? []) as Fact[];
  const total = data.genre?.episodes ?? 0;

  useEffect(() => {
    api<Finding[]>(`/projects/${pid}/findings?status=open&step=${next.step}`)
      .then(setFindings)
      .catch(() => setFindings([]));
  }, [pid, next.step, data]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    setOverBudget(false);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.budgetExceeded) setOverBudget(true);
    } finally {
      setBusy(false);
    }
  };
  const run = (body: object = {}) => act(() => post(`/projects/${pid}/steps/${next.step}/run`, body));
  const approve = (body: object = {}) => act(() => post(`/projects/${pid}/steps/${next.step}/approve`, body));
  const skip = () => act(() => post(`/projects/${pid}/steps/${next.step}/skip`));
  const stop = () => act(() => post(`/projects/${pid}/steps/${data.job!.step}/cancel`));
  const top = topThree(findings);
  const job = data.job;
  const working = next.action === 'wait' || !!job?.running;
  // Cards and scripts are written per episode: the producer chooses which ones, instead of the whole season by default.
  const perEpisode = next.step === 'episode_cards' || next.step === 'scripts';
  const suggested = next.episodes ?? (next.step === 'scripts' ? data.cards.filter((ep) => !data.scripts.includes(ep)) : []);
  const openPicker = (initial: number[]) => setPick(formatEpisodes(initial.length ? initial : [1]));
  const runPicked = (fix = false) => {
    try {
      const episodes = parseEpisodes(pick ?? '', total || 9999);
      if (!episodes.length) throw new Error('Укажите хотя бы одну серию');
      setPick(null);
      void run({ episodes, ...(fix ? { fix: true } : {}) });
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const lastRunNote = !working && job && job.step === next.step && (job.stopped || job.error);

  return (
    <section className="now">
      <div className="task">
        <p className="task-step">Шаг: {next.stepLabel}</p>
        <h2>{working ? `Агент работает: «${next.stepLabel}». Это может занять несколько минут — экран обновится сам` : next.task}</h2>
        {working && job?.running && (
          <p className="progress">
            {progressLine(job.progress, job.step)}
            {job.cancelRequested && ' · останавливаюсь после текущего вызова модели…'}
          </p>
        )}
        <p className="criterion">Готово, когда: {next.criterion}</p>
        {error && <p className="error">{error}</p>}
        {lastRunNote &&
          (job.stopped ? (
            <p className="muted">{job.error ?? 'Остановлено продюсером'}. Сделанное сохранено — запустите шаг ещё раз для оставшихся серий.</p>
          ) : (
            <p className="error">Последний запуск не удался: {job.error}</p>
          ))}
        {(overBudget || (lastRunNote && job.budgetExceeded)) && (
          <div className="over-budget">
            <p className="muted">Лимит расходов исчерпан: ${data.budget.totalUsd.toFixed(2)} из ${data.budget.limitUsd.toFixed(0)}. Поднимите лимит, чтобы продолжить.</p>
            <RaiseLimit data={data} refresh={refresh} onError={setError} />
          </div>
        )}
        <div className="actions">
          {working && job?.running && !job.cancelRequested && (
            <button className="danger" disabled={busy} onClick={() => void stop()} title="Текущий вызов модели завершится, следующая серия не начнётся">
              Остановить
            </button>
          )}
          {next.action === 'run' && !perEpisode && (
            <button className="primary" disabled={busy || working} onClick={() => run()}>
              Запустить
            </button>
          )}
          {next.action === 'run' && perEpisode && pick === null && (
            <>
              <button className="primary" disabled={busy || working} onClick={() => openPicker(suggested)}>
                {suggested.length ? `Написать: ${suggested.length} ${suggested.length === 1 ? 'серия' : 'серий'}…` : 'Выбрать серии…'}
              </button>
            </>
          )}
          {next.action === 'approve' && (
            <>
              <button className="primary" disabled={busy || working} onClick={() => approve()}>
                Утвердить
              </button>
              <button
                disabled={busy || working}
                onClick={() => {
                  if (perEpisode) openPicker(data.genre ? Array.from({ length: total }, (_, i) => i + 1) : []);
                  else void run();
                }}
              >
                Переделать
              </button>
            </>
          )}
          {next.action === 'approve_block' && (
            <>
              <button className="primary" disabled={busy || working} onClick={() => approve({ block: next.block })}>
                Утвердить блок
              </button>
              <button disabled={busy || working} onClick={() => run({ episodes: [next.block] })}>
                Переписать блок
              </button>
            </>
          )}
          {next.action === 'resolve' && (
            <button disabled={busy || working} onClick={() => (perEpisode ? openPicker(next.episodes ?? []) : void run())}>
              {next.episodes ? `Переписать серии с замечаниями: ${formatEpisodes(next.episodes)}` : 'Переделать шаг'}
            </button>
          )}
          {next.action === 'polish' && (
            <>
              <a className="button" href={`#/p/${pid}/episode`}>
                Открыть серию
              </a>
              {(data.steps.find((s) => s.step === 'polish')?.version ?? 0) > 0 ? (
                <button className="primary" disabled={busy || working} onClick={() => approve()}>
                  Утвердить доработку
                </button>
              ) : (
                <button className="primary" disabled={busy || working} onClick={skip}>
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
          {!['export', 'polish', 'wait'].includes(next.action) && !working && (
            <button className="ghost" disabled={busy} onClick={skip} title="Шаг будет пропущен по вашему решению">
              Пропустить шаг
            </button>
          )}
        </div>
        {pick !== null && perEpisode && (
          <div className="episode-pick">
            <label>
              Какие серии писать (номера через запятую, диапазон через дефис)
              <input value={pick} onChange={(e) => setPick(e.target.value)} placeholder="1-10, 12" />
            </label>
            <p className="muted">
              {next.step === 'episode_cards' ? 'Карточки пишутся блоками по 10 серий: будет написан каждый блок, в который попала хотя бы одна серия.' : 'Каждая серия — отдельный вызов писателя и проверка критиком; по времени и деньгам это самый дорогой шаг.'}
              {next.step === 'scripts' && data.scripts.length > 0 && ` Сценарии уже есть у серий: ${formatEpisodes(data.scripts)}.`}
            </p>
            <div className="actions">
              <button className="primary" disabled={busy} onClick={() => runPicked(next.action === 'resolve' && next.step === 'scripts')}>
                Запустить
              </button>
              <button className="ghost" onClick={() => setPick(null)}>
                Отмена
              </button>
            </div>
          </div>
        )}
      </div>

      {next.action === 'choose_concept' && (
        <div className="concepts">
          {(data.concepts ?? []).map((c: Doc, i: number) => (
            <article key={c.id ?? i} className="panel concept">
              <h3>{c.title}</h3>
              <p>{c.premise}</p>
              <p className="muted">Крючок: {c.hook}</p>
              <p className="muted">Формула: {c.genre_formula}</p>
              <button className="primary" disabled={busy || working} onClick={() => approve({ choice: i })}>
                Выбрать эту
              </button>
            </article>
          ))}
          <button disabled={busy || working} onClick={() => run()}>
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
            <FindingCard key={f.id} f={f} facts={facts} projectId={pid} canAddFact={!!data.bible} onDone={() => void refresh()} />
          ))}
        </div>
      )}
    </section>
  );
}
