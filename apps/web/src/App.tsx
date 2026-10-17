import { useCallback, useEffect, useState } from 'react';
import { api, post, type GenreTerms } from './api.ts';
import { ProjectView } from './screens/ProjectView.tsx';

interface ProjectRow {
  id: string;
  title: string;
  genreId: string | null;
  createdAt: string;
}

interface Genre {
  id: string;
  title: string;
  episodes: number;
  free: number;
  /** Frame inputs with their defaults, e.g. {paywall: 10}. A project may change them. */
  inputs: Record<string, number>;
  terms: GenreTerms;
  /** Default budget limit from config/models.yaml. */
  budgetLimitUsd: number;
}

/** Human names of frame inputs the screens know; an unknown input is shown by its id. */
const INPUT_LABELS: Record<string, string> = {
  paywall: 'Серия пэйволла (последняя бесплатная)',
  length: 'Число серий',
};

/** Route in the URL hash: #/p/<projectId>/<tab>. */
function readHash(): { projectId?: string; tab?: string } {
  const [, p, id, tab] = window.location.hash.split('/');
  return p === 'p' ? { projectId: id, tab } : {};
}

export function App() {
  const [route, setRoute] = useState(readHash);
  useEffect(() => {
    const on = () => setRoute(readHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  return (
    <div className="layout">
      <header className="header">
        <a className="brand" href="#/">
          Сценарный агент
        </a>
        <DemoBadge />
      </header>
      {route.projectId ? <ProjectView key={route.projectId} projectId={route.projectId} tab={route.tab ?? 'now'} /> : <Home />}
    </div>
  );
}

const PROBES = 5;
const PROBE_MS = 1500;

/** Asks the server /health; while it is starting, the badge waits a few probes before saying it is down, and keeps trying. */
function DemoBadge() {
  const [state, setState] = useState<'probing' | 'ok' | 'demo' | 'offline'>('probing');
  useEffect(() => {
    let alive = true;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const probe = () => {
      api<{ demo: boolean }>('/health')
        .then((h) => {
          if (!alive) return;
          failures = 0;
          setState(h.demo ? 'demo' : 'ok');
        })
        .catch(() => {
          if (!alive) return;
          failures += 1;
          if (failures >= PROBES) setState('offline');
          // Slow re-probing once the server is down, quick while it may still be starting.
          timer = setTimeout(probe, failures >= PROBES ? PROBE_MS * 4 : PROBE_MS);
        });
    };
    probe();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);
  if (state === 'offline') return <span className="badge badge-bad">Сервер не отвечает</span>;
  if (state === 'demo') return <span className="badge badge-demo" title="Модели отвечают заготовками по эталону">Демо-режим</span>;
  return null;
}

function Home() {
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [genres, setGenres] = useState<Genre[]>([]);
  const [title, setTitle] = useState('');
  const [genreId, setGenreId] = useState('');
  const [idea, setIdea] = useState('');
  const [budget, setBudget] = useState('');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [terms, setTerms] = useState<{ nom: string; gen: string; dat: string; wound: string } | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // A genre brings its own defaults for the budget, the frame inputs and the hero words.
  const applyGenre = (g: Genre | undefined) => {
    if (!g) return;
    setGenreId(g.id);
    setBudget(String(g.budgetLimitUsd));
    setInputs(Object.fromEntries(Object.entries(g.inputs).map(([k, v]) => [k, String(v)])));
    setTerms({ ...g.terms.hero, wound: g.terms.wound });
  };

  const load = useCallback(() => {
    api<ProjectRow[]>('/projects').then(setProjects).catch((e: Error) => setError(e.message));
    api<Genre[]>('/genres')
      .then((g) => {
        setGenres(g);
        applyGenre(g[0]);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const genre = genres.find((g) => g.id === genreId);

  const create = async () => {
    setBusy(true);
    setError('');
    try {
      const budgetLimitUsd = Number(budget);
      if (!Number.isFinite(budgetLimitUsd) || budgetLimitUsd <= 0) throw new Error('Лимит расходов — число больше нуля');
      const frameInputs: Record<string, number> = {};
      for (const [k, v] of Object.entries(inputs)) {
        const n = Number(v);
        if (!Number.isFinite(n)) throw new Error(`«${INPUT_LABELS[k] ?? k}» — нужно число`);
        if (genre && n !== genre.inputs[k]) frameInputs[k] = n;
      }
      const changedTerms =
        genre && terms && (terms.nom !== genre.terms.hero.nom || terms.gen !== genre.terms.hero.gen || terms.dat !== genre.terms.hero.dat || terms.wound !== genre.terms.wound)
          ? { hero: { nom: terms.nom.trim(), gen: terms.gen.trim(), dat: terms.dat.trim() }, wound: terms.wound.trim() }
          : undefined;
      const { id } = await post<{ id: string }>('/projects', {
        title,
        genreId,
        idea,
        budgetLimitUsd,
        ...(Object.keys(frameInputs).length ? { frameInputs } : {}),
        ...(changedTerms ? { terms: changedTerms } : {}),
      });
      window.location.hash = `#/p/${id}/now`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const inputKeys = Object.keys(genre?.inputs ?? {});

  return (
    <main className="home">
      <section className="panel">
        <h2>Новый сериал</h2>
        <label>
          Название
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Муж женился на мне ради крови" />
        </label>
        <label>
          Жанр
          <select value={genreId} onChange={(e) => applyGenre(genres.find((g) => g.id === e.target.value))}>
            {genres.map((g) => (
              <option key={g.id} value={g.id}>
                {g.title} — {g.episodes} серий
              </option>
            ))}
          </select>
        </label>
        <label>
          Идея
          <textarea rows={5} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="Опишите идею своими словами: главный герой или героиня, рана, тайна" />
        </label>
        <label>
          Лимит расходов, $
          <input type="number" min={1} step={10} value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        <p className="muted">Когда расходы на модели дойдут до лимита, агент остановится и подождёт вашего решения. Лимит можно поднять позже на экране «Расходы».</p>
        {genre && (inputKeys.length > 0 || terms) && (
          <button type="button" className="ghost" onClick={() => setMore((m) => !m)}>
            {more ? 'Скрыть параметры жанра' : 'Параметры жанра…'}
          </button>
        )}
        {more && genre && (
          <div className="genre-params">
            {inputKeys.map((k) => (
              <label key={k}>
                {INPUT_LABELS[k] ?? k}
                <input type="number" value={inputs[k] ?? ''} onChange={(e) => setInputs((cur) => ({ ...cur, [k]: e.target.value }))} />
                <span className="muted"> по умолчанию {genre.inputs[k]}</span>
              </label>
            ))}
            {terms && (
              <>
                <p className="muted">Как называть главного героя в документах и замечаниях: кто? кого? кому?</p>
                <div className="terms-row">
                  <input value={terms.nom} onChange={(e) => setTerms({ ...terms, nom: e.target.value })} placeholder="героиня" />
                  <input value={terms.gen} onChange={(e) => setTerms({ ...terms, gen: e.target.value })} placeholder="героини" />
                  <input value={terms.dat} onChange={(e) => setTerms({ ...terms, dat: e.target.value })} placeholder="героине" />
                </div>
                <label>
                  Как называть исходную рану (раздел библии)
                  <input value={terms.wound} onChange={(e) => setTerms({ ...terms, wound: e.target.value })} placeholder="Предательство" />
                </label>
              </>
            )}
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !title.trim() || !idea.trim()} onClick={create}>
          Начать
        </button>
      </section>
      <section className="panel">
        <h2>Мои сериалы</h2>
        {projects.length === 0 ? (
          <p className="muted">Пока ни одного. Начните с идеи слева.</p>
        ) : (
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.id}>
                <a href={`#/p/${p.id}/now`}>{p.title}</a>
                <span className="muted"> · {new Date(p.createdAt).toLocaleDateString('ru-RU')}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
