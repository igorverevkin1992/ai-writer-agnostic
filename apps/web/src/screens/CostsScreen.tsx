import { STEP_LABELS, type Doc, type Overview } from '../api.ts';

const ROLE: Record<string, string> = {
  architect: 'Архитектор',
  architect_heavy: 'Архитектор, план сезона',
  writer: 'Писатель',
  critic_of_architect: 'Критик архитектора',
  critic_of_writer: 'Критик писателя',
  helper: 'Помощник',
};

function Rows({ rows, label }: { rows: Doc[]; label: (r: Doc) => string }) {
  const max = Math.max(0.0001, ...rows.map((r) => r.costUsd as number));
  return (
    <table className="costs">
      <tbody>
        {[...rows]
          .sort((a, b) => b.costUsd - a.costUsd)
          .map((r) => (
            <tr key={label(r)}>
              <td>{label(r)}</td>
              <td className="num">{r.calls} выз.</td>
              <td className="bar-cell">
                <span className="cost-bar" style={{ width: `${(r.costUsd / max) * 100}%` }} />
              </td>
              <td className="num">${(r.costUsd as number).toFixed(2)}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}

export function CostsScreen({ data }: { data: Overview }) {
  const b = data.budget;
  const share = b.limitUsd ? Math.min(1, b.totalUsd / b.limitUsd) : 0;
  return (
    <section>
      <div className="panel">
        <h2>
          Потрачено ${b.totalUsd.toFixed(2)} из ${b.limitUsd.toFixed(0)}
        </h2>
        <div className="budget-track">
          <span className={`budget-fill ${share >= 0.8 ? 'budget-fill-warn' : ''}`} style={{ width: `${share * 100}%` }} />
        </div>
        <p className="muted">
          {share >= 1 ? 'Лимит исчерпан: агент остановлен до вашего решения.' : share >= 0.8 ? 'Потрачено больше 80% лимита.' : `Вызовов моделей: ${b.calls}.`}
          {data.demo && ' В демо-режиме модели не вызываются и расходов нет.'}
          {b.unknownPriceCalls > 0 && ` У ${b.unknownPriceCalls} вызовов резервного поставщика нет цены в config/models.yaml.`}
        </p>
      </div>
      <div className="panel">
        <h3>По шагам</h3>
        {b.byStep.length ? <Rows rows={b.byStep} label={(r) => STEP_LABELS[r.step] ?? r.step} /> : <p className="muted">Пока ничего.</p>}
      </div>
      <div className="panel">
        <h3>По ролям</h3>
        {b.byRole.length ? <Rows rows={b.byRole} label={(r) => `${ROLE[r.role] ?? r.role}${data.models[r.role] ? ` · ${data.models[r.role]}` : ''}`} /> : <p className="muted">Пока ничего.</p>}
      </div>
    </section>
  );
}
