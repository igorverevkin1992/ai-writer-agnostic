import type { Doc, Overview } from '../api.ts';

export function BibleScreen({ data }: { data: Overview }) {
  const b = data.bible;
  if (!b) return <p className="muted panel">Библии пока нет: она появится после шага «Библия».</p>;
  const villains = [...(b.villains as Doc[])].sort((x, y) => y.rank - x.rank);
  return (
    <section className="bible">
      {data.logline && (
        <article className="panel">
          <h2>Логлайн</h2>
          <p>{data.logline.text_35w}</p>
        </article>
      )}
      <article className="panel">
        <h2>Предательство</h2>
        <p>
          <b>{b.betrayal.who}</b>: {b.betrayal.what}. Почему: {b.betrayal.why}. Героиня видит это на {b.betrayal.ep1_second}-й секунде 1-й серии.
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
          {(b.secrets as Doc[]).map((s) => (
            <li key={s.layer}>
              {s.revealed_ep}-я серия: {s.truth}. <span className="muted">Цель мести: {s.goal_from} → {s.goal_to}</span>
            </li>
          ))}
        </ol>
      </article>
      <article className="panel">
        <h2>Хронология</h2>
        <ul className="timeline">
          {[...(b.timeline.events as Doc[])]
            .sort((x, y) => x.year - y.year)
            .map((e) => (
              <li key={e.id}>
                <b>{e.year}</b> — {e.text}
              </li>
            ))}
        </ul>
      </article>
      <article className="panel">
        <h2>Факты и кто что знает</h2>
        <ul>
          {(b.facts as Doc[]).map((f) => (
            <li key={f.id}>
              {f.text}
              {f.since_ep ? <span className="muted"> · с {f.since_ep}-й серии</span> : ''}
            </li>
          ))}
        </ul>
      </article>
    </section>
  );
}
