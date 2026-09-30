import { useEffect, useState } from 'react';
import { api, STEP_LABELS, type Fact, type Finding, type Overview } from '../api.ts';
import { FindingCard } from './Findings.tsx';

const ORDER = { blocker: 0, major: 1, minor: 2 } as const;

export function ChecksScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const pid = data.project.id;
  const [all, setAll] = useState<Finding[]>([]);
  const [status, setStatus] = useState<'open' | 'resolved' | 'dismissed'>('open');
  const [step, setStep] = useState('');

  useEffect(() => {
    api<Finding[]>(`/projects/${pid}/findings`).then(setAll).catch(() => setAll([]));
  }, [pid, data]);

  const shown = all
    .filter((f) => f.status === status && (!step || f.step === step))
    .sort((a, b) => ORDER[a.severity] - ORDER[b.severity] || (a.episode ?? 0) - (b.episode ?? 0));
  const steps = [...new Set(all.map((f) => f.step).filter((s): s is string => !!s))];
  const count = (s: Finding['status'], sev?: Finding['severity']) => all.filter((f) => f.status === s && (!sev || f.severity === sev)).length;

  return (
    <section>
      <div className="panel stats">
        <div>
          <b>{count('open', 'blocker')}</b> блокирующих открыто
        </div>
        <div>
          <b>{count('open')}</b> всего открыто
        </div>
        <div>
          <b>{count('resolved')}</b> закрыто
        </div>
        <div>
          <b>{count('dismissed')}</b> отклонено
        </div>
      </div>
      <div className="filters">
        <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          <option value="open">Открытые</option>
          <option value="resolved">Закрытые</option>
          <option value="dismissed">Отклонённые</option>
        </select>
        <select value={step} onChange={(e) => setStep(e.target.value)}>
          <option value="">Все шаги</option>
          {steps.map((s) => (
            <option key={s} value={s}>
              {STEP_LABELS[s] ?? s}
            </option>
          ))}
        </select>
      </div>
      {shown.length === 0 ? (
        <p className="muted panel">Здесь пусто.</p>
      ) : (
        shown.map((f) => <FindingCard key={f.id} f={f} facts={(data.bible?.facts ?? []) as Fact[]} projectId={pid} canAddFact={!!data.bible} onDone={() => void refresh()} />)
      )}
    </section>
  );
}
