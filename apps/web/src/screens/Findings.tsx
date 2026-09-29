import { useState } from 'react';
import { post, STEP_LABELS, type Fact, type Finding } from '../api.ts';

const SEVERITY: Record<Finding['severity'], string> = { blocker: 'блокирующее', major: 'серьёзное', minor: 'мелкое' };
const CONTROLLER: Record<string, string> = {
  logic: 'логика',
  genre: 'жанр',
  structure: 'структура',
  consistency: 'согласованность',
  production: 'производство',
  metro: 'проверка метро',
  legal: 'право',
};

/** One finding in the viewer's language, with the producer's actions. */
export function FindingCard({ f, facts, projectId, onDone }: { f: Finding; facts: Fact[]; projectId: string; onDone: () => void }) {
  const [mode, setMode] = useState<'' | 'resolve' | 'dismiss'>('');
  const [factId, setFactId] = useState('');
  const [newText, setNewText] = useState('');
  const [since, setSince] = useState('');
  const [error, setError] = useState('');

  const submit = async () => {
    setError('');
    try {
      if (mode === 'dismiss') {
        await post(`/findings/${encodeURIComponent(f.id)}/dismiss`, { projectId, factId });
      } else if (factId === '__new') {
        const id = `f_p${Date.now().toString(36)}`;
        await post(`/findings/${encodeURIComponent(f.id)}/resolve`, {
          projectId,
          fact: { id, text: newText.trim(), ...(since ? { since_ep: Number(since) } : {}) },
        });
      } else {
        await post(`/findings/${encodeURIComponent(f.id)}/resolve`, { projectId, factId });
      }
      setMode('');
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <article className={`finding finding-${f.severity}`}>
      <header>
        <span className={`sev sev-${f.severity}`}>{SEVERITY[f.severity]}</span>
        <span className="muted">
          {f.episode ? `${f.episode}-я серия · ` : ''}
          {CONTROLLER[f.controller] ?? f.controller}
          {f.step ? ` · ${STEP_LABELS[f.step] ?? f.step}` : ''}
        </span>
      </header>
      <p className="viewer-question">{f.viewerQuestion}</p>
      <blockquote>{f.quote}</blockquote>
      <p className="fixes">
        Как исправить: {f.fixes.join(' / ')}
        {f.verdict && <span className="muted"> · Судья: {f.verdict}</span>}
      </p>
      {f.status === 'open' ? (
        mode === '' ? (
          <div className="actions">
            <button onClick={() => setMode('resolve')}>Закрыть фактом</button>
            <button className="ghost" onClick={() => setMode('dismiss')}>
              Это не дыра
            </button>
          </div>
        ) : (
          <div className="resolve-form">
            <label>
              {mode === 'dismiss' ? 'Какой факт уже отвечает на вопрос зрителя' : 'Факт, который закрывает дыру'}
              <select value={factId} onChange={(e) => setFactId(e.target.value)}>
                <option value="">— выберите —</option>
                {facts.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.text}
                  </option>
                ))}
                {mode === 'resolve' && <option value="__new">+ Новый факт…</option>}
              </select>
            </label>
            {factId === '__new' && (
              <>
                <textarea rows={2} placeholder="Например: следователь без доказательств не может защитить Дашу" value={newText} onChange={(e) => setNewText(e.target.value)} />
                <label className="inline">
                  С какой серии верен
                  <input type="number" min={1} value={since} onChange={(e) => setSince(e.target.value)} placeholder="до сезона" />
                </label>
              </>
            )}
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <button className="primary" disabled={!factId || (factId === '__new' && !newText.trim())} onClick={submit}>
                Готово
              </button>
              <button className="ghost" onClick={() => setMode('')}>
                Отмена
              </button>
            </div>
          </div>
        )
      ) : (
        <p className="muted">{f.status === 'resolved' ? 'Закрыто' : 'Отклонено'}</p>
      )}
    </article>
  );
}

export { topThree } from '../findings.ts';
