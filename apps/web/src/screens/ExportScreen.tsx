import { useState } from 'react';
import type { Overview } from '../api.ts';

const FORMATS: [string, string, string][] = [
  ['docx', 'Word', 'Логлайн, библия, таблица сезона и все сценарии'],
  ['review', 'Разбор дыр', 'Word: дыры по уровням, как закрыть, правила для агента, кто что знает, посадки, возрасты'],
  ['xlsx', 'Excel', 'Таблица сезона, карточки, замечания, промпты для видео'],
  ['video', 'Промпты для ИИ-видео', 'По сцене на промпт; внешность героев — только из библии'],
  ['md', 'Markdown', 'Всё то же простым текстом'],
  ['json', 'JSON', 'Полные данные проекта для других программ'],
];

export function ExportScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const pid = data.project.id;
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const download = async (format: string) => {
    setBusy(format);
    setError('');
    try {
      const res = await fetch(`/api/projects/${pid}/export?format=${format}`);
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(body.error ?? `Ошибка сервера (${res.status})`);
      }
      const name = /filename="([^"]+)"/u.exec(res.headers.get('Content-Disposition') ?? '')?.[1] ?? `export.${format}`;
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      await refresh();
    } catch (e) {
      setError(`Файл не получился: ${(e as Error).message}`);
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="export">
      <p className="muted">В файлы попадает всё, что есть в проекте сейчас. Скачать можно в любой момент.</p>
      {error && <p className="error">{error}</p>}
      <div className="export-grid">
        {FORMATS.map(([format, title, text]) => (
          <button key={format} className="panel export-card" disabled={!!busy} onClick={() => void download(format)}>
            <h3>{title}</h3>
            <p className="muted">{text}</p>
            <span className="button">{busy === format ? 'Готовлю…' : 'Скачать'}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
