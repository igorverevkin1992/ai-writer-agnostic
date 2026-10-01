import { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.ts';
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
}

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

function DemoBadge() {
  const [demo, setDemo] = useState(false);
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    api<{ demo: boolean }>('/health')
      .then((h) => setDemo(h.demo))
      .catch(() => setOffline(true));
  }, []);
  if (offline) return <span className="badge badge-bad">Сервер не отвечает</span>;
  return demo ? <span className="badge badge-demo" title="Модели отвечают заготовками по эталону">Демо-режим</span> : null;
}

function Home() {
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [genres, setGenres] = useState<Genre[]>([]);
  const [title, setTitle] = useState('');
  const [genreId, setGenreId] = useState('');
  const [idea, setIdea] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ProjectRow[]>('/projects').then(setProjects).catch((e: Error) => setError(e.message));
    api<Genre[]>('/genres')
      .then((g) => {
        setGenres(g);
        setGenreId((cur) => cur || g[0]?.id || '');
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const create = async () => {
    setBusy(true);
    setError('');
    try {
      const { id } = await post<{ id: string }>('/projects', { title, genreId, idea });
      window.location.hash = `#/p/${id}/now`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

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
          <select value={genreId} onChange={(e) => setGenreId(e.target.value)}>
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
