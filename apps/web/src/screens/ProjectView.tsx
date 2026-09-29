import { useCallback, useEffect, useState } from 'react';
import { api, STATUS_LABELS, STEP_LABELS, type Overview } from '../api.ts';
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

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Загружаю проект…</p>;
  const spent = data.budget.limitUsd ? data.budget.totalUsd / data.budget.limitUsd : 0;

  return (
    <main>
      <div className="project-head">
        <div>
          <h1>{data.project.title}</h1>
          <p className="muted">
            {data.genre.title} · {data.genre.episodes} серий
          </p>
        </div>
        <a className={`budget ${spent >= 0.8 ? 'budget-warn' : ''}`} href={`#/p/${projectId}/costs`} title="Расходы на модели">
          ${data.budget.totalUsd.toFixed(2)} из ${data.budget.limitUsd.toFixed(0)}
        </a>
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
      {tab === 'bible' && <BibleScreen data={data} />}
      {tab === 'season' && <SeasonScreen data={data} />}
      {tab === 'episode' && <EpisodeScreen data={data} refresh={refresh} />}
      {tab === 'checks' && <ChecksScreen data={data} refresh={refresh} />}
      {tab === 'export' && <ExportScreen data={data} refresh={refresh} />}
      {tab === 'costs' && <CostsScreen data={data} />}
    </main>
  );
}
