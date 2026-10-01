import { useState } from 'react';
import type { Doc, Overview } from '../api.ts';

const MOOD: Record<string, string> = { suffering: 'страдание', kaif: 'кайф', neutral: 'нейтрально' };

/**
 * Suffering and kaif across the season: one bar per episode, up for kaif, down for suffering
 * (direction and color both encode the mood). Anchors are ticked under the strip.
 */
function MoodStrip({ episodes, free, label }: { episodes: Doc[]; free: number; label: (id: string) => string }) {
  const [hover, setHover] = useState<Doc | null>(null);
  const w = 14;
  const gap = 2;
  const h = 28;
  const width = episodes.length * (w + gap);
  return (
    <figure className="mood">
      <figcaption>
        Кривая страдания и кайфа
        <span className="legend">
          <i className="sw sw-kaif" /> кайф <i className="sw sw-suffering" /> страдание <i className="sw sw-neutral" /> нейтрально
        </span>
      </figcaption>
      <div className="mood-scroll">
        <svg width={width} height={h * 2 + 36} role="img" aria-label="Настроение по сериям">
          <line x1={0} x2={width} y1={h} y2={h} className="baseline" />
          <rect x={0} y={0} width={free * (w + gap) - gap / 2} height={h * 2} className="free-zone" />
          {episodes.map((e, i) => {
            const x = i * (w + gap);
            const y = e.mood === 'kaif' ? 4 : h;
            const bh = e.mood === 'neutral' ? 4 : h - 4;
            const yy = e.mood === 'neutral' ? h - 2 : y;
            return (
              <g key={e.ep} onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)}>
                <rect x={x} y={0} width={w + gap} height={h * 2 + 32} fill="transparent" />
                <rect x={x} y={yy} width={w} height={bh} rx={2} className={`bar bar-${e.mood}`} />
                {e.anchors.length > 0 && <circle cx={x + w / 2} cy={h * 2 + 8} r={3} className="anchor-dot" />}
                {(e.ep === 1 || e.ep % 10 === 0) && (
                  <text x={x + w / 2} y={h * 2 + 26} className="tick" textAnchor="middle">
                    {e.ep}
                  </text>
                )}
                <title>{`${e.ep}. ${e.title} — ${MOOD[e.mood]}${e.anchors.length ? ` · ${e.anchors.map(label).join(', ')}` : ''}`}</title>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="muted mood-hover">
        {hover ? `${hover.ep}-я серия «${hover.title}»: ${MOOD[hover.mood]}${hover.anchors.length ? `, опорные точки: ${hover.anchors.map(label).join(', ')}` : ''}` : 'Наведите на серию. Точки внизу — опорные точки каркаса, светлый фон — бесплатные серии.'}
      </p>
    </figure>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function SeasonScreen({ data }: { data: Overview }) {
  const plan = data.plan;
  if (!plan) return <p className="muted panel">Плана сезона пока нет: он появится после шага «План сезона».</p>;
  const label = (id: string) => data.genre.anchorLabels[id] ?? id;
  // Genres with several story lines (e.g. revenge and love) show which of them pay off.
  const lines = Object.entries(data.genre.lines ?? {});
  return (
    <section>
      <div className="panel">
        <MoodStrip episodes={plan.episodes} free={data.genre.free} label={label} />
      </div>
      <div className="panel">
        <table className="season">
          <thead>
            <tr>
              <th>Серия</th>
              <th>Событие</th>
              <th>{cap(data.genre.terms.hero.nom)}</th>
              <th>Опорная точка</th>
              <th>Настроение</th>
              {lines.length > 0 && <th>Кайф линий</th>}
              <th>Крючок</th>
            </tr>
          </thead>
          <tbody>
            {plan.episodes.map((e) => (
              <tr key={e.ep} className={e.anchors.length ? 'row-anchor' : ''}>
                <td>
                  <b>{e.ep}</b>
                  {e.ep <= data.genre.free && <div className="muted">бесплатно</div>}
                </td>
                <td>
                  <b>{e.title}.</b> {e.event}
                </td>
                <td>{e.heroine_action}</td>
                <td>{e.anchors.map(label).join(', ')}</td>
                <td>
                  <span className={`mood-tag mood-${e.mood}`}>{MOOD[e.mood]}</span>
                </td>
                {lines.length > 0 && (
                  <td>
                    {lines
                      .filter(([id]) => ((e.kaif_lines as string[] | undefined) ?? []).includes(id))
                      .map(([, name]) => name)
                      .join(', ') || '—'}
                  </td>
                )}
                <td>{e.hook_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
