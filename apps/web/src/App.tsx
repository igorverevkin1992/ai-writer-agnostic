import { useEffect, useState } from 'react';
import { STEPS } from './steps.ts';

type ServerState = 'checking' | 'online' | 'offline';

export function App() {
  const [server, setServer] = useState<ServerState>('checking');

  useEffect(() => {
    fetch('/api/health')
      .then((res) => setServer(res.ok ? 'online' : 'offline'))
      .catch(() => setServer('offline'));
  }, []);

  return (
    <div className="layout">
      <header className="header">
        <h1>Сценарный агент</h1>
        <span className={`status status-${server}`}>
          {server === 'checking' && 'Проверяю сервер…'}
          {server === 'online' && 'Сервер работает'}
          {server === 'offline' && 'Сервер не отвечает'}
        </span>
      </header>
      <main className="main">
        <ol className="steps">
          {STEPS.map((step) => (
            <li key={step.id}>{step.label}</li>
          ))}
        </ol>
        <section className="now">
          <h2>Сейчас</h2>
          <p>Приложение пока пустое. Шаги появятся на следующих вехах.</p>
        </section>
      </main>
    </div>
  );
}
