import { useCallback, useEffect, useState } from 'react';
import { api, del, patch, progressLine, STATUS_LABELS, STEP_LABELS, type Overview } from '../api.ts';
import { BibleScreen } from './BibleScreen.tsx';
import { ChecksScreen } from './ChecksScreen.tsx';
import { CostsScreen } from './CostsScreen.tsx';
import { EpisodeScreen } from './EpisodeScreen.tsx';
import { ExportScreen } from './ExportScreen.tsx';
import { NowScreen } from './NowScreen.tsx';
import { SeasonScreen } from './SeasonScreen.tsx';

const TABS: [string, string][] = [
  ['now', 'Сейчас'],
  ['bible', 'Библия'],
  ['season', 'Сезон'],
  ['episode', 'Серия'],
  ['checks', 'Проверки'],
  ['export', 'Экспорт'],
  ['costs', 'Расходы'],
];

export function ProjectView({ projectId, tab }: { projectId: string; tab: string }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [renaming, setRenaming] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api<Overview>(`/projects/${projectId}/overview`));
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);
  // Reload on every tab switch: steps may have changed on another screen.
  useEffect(() => {
    let alive = true;
    api<Overview>(`/projects/${projectId}/overview`)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError('');
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [projectId, tab]);

  // While the agent works in the background, the screens follow it.
  const working = !!data && (data.job?.running || data.steps.some((s) => s.status === 'checking'));
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [working, refresh]);

  const rename = async () => {
    if (renaming === null) return;
    try {
      await patch(`/projects/${projectId}`, { title: renaming });
      setRenaming(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!data) return;
    if (!window.confirm(`Удалить сериал «${data.project.title}» со всеми текстами и замечаниями? Это нельзя отменить.`)) return;
    try {
      await del(`/projects/${projectId}`);
      window.location.hash = '#/';
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Загружаю проект…</p>;
  const spent = data.budget.limitUsd ? data.budget.totalUsd / data.budget.limitUsd : 0;
  const job = data.job;

  return (
    <main>
      <div className="project-head">
        <div>
          {renaming === null ? (
            <h1>
              {data.project.title}{' '}
              <button className="ghost small" title="Переименовать" onClick={() => setRenaming(data.project.title)}>
                Переименовать
              </button>
            </h1>
          ) : (
            <div className="rename">
              <input value={renaming} onChange={(e) => setRenaming(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void rename()} autoFocus />
              <button className="primary" disabled={!renaming.trim()} onClick={() => void rename()}>
                Сохранить
              </button>
              <button className="ghost" onClick={() => setRenaming(null)}>
                Отмена
              </button>
            </div>
          )}
          <p className="muted">
            {data.genre ? `${data.genre.title} · ${data.genre.episodes} серий` : 'Жанр не выбран'}
            {data.genre && Object.keys(data.genre.inputs).length > 0 && ` · ${Object.entries(data.genre.inputs).map(([k, v]) => `${k === 'paywall' ? 'пэйволл' : k}: ${v}`).join(', ')}`}
          </p>
        </div>
        <div className="head-right">
          <a className={`budget ${spent >= 0.8 ? 'budget-warn' : ''}`} href={`#/p/${projectId}/costs`} title="Расходы на модели">
            ${data.budget.totalUsd.toFixed(2)} из ${data.budget.limitUsd.toFixed(0)}
          </a>
          {job?.running && (
            <span className="badge badge-demo" title="Агент работает">
              {progressLine(job.progress, job.step)}
            </span>
          )}
          <button className="ghost small" disabled={!!job?.running} title="Удалить сериал" onClick={() => void remove()}>
            Удалить
          </button>
        </div>
      </div>
      <ol className="steps-line">
        {data.steps.map((s) => (
          <li key={s.step} className={`step step-${s.status} ${data.next.step === s.step ? 'step-current' : ''}`} title={STATUS_LABELS[s.status]}>
            {STEP_LABELS[s.step]}
          </li>
        ))}
      </ol>
      <nav className="tabs">
        {TABS.map(([id, label]) => (
          <a key={id} href={`#/p/${projectId}/${id}`} className={tab === id ? 'tab-active' : ''}>
            {label}
          </a>
        ))}
      </nav>
      {error && <p className="error">{error}</p>}
      {tab === 'now' && <NowScreen data={data} refresh={refresh} />}
      {tab === 'bible' && <BibleScreen data={data} refresh={refresh} />}
      {tab === 'season' && <SeasonScreen data={data} />}
      {tab === 'episode' && <EpisodeScreen data={data} refresh={refresh} />}
      {tab === 'checks' && <ChecksScreen data={data} refresh={refresh} />}
      {tab === 'export' && <ExportScreen data={data} refresh={refresh} />}
      {tab === 'costs' && <CostsScreen data={data} refresh={refresh} />}
    </main>
  );
}
