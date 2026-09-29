import type { Overview } from '../api.ts';

const FORMATS: [string, string, string][] = [
  ['docx', 'Word', 'Логлайн, библия, таблица сезона и все сценарии'],
  ['xlsx', 'Excel', 'Таблица сезона, карточки, замечания, промпты для видео'],
  ['video', 'Промпты для ИИ-видео', 'По сцене на промпт; внешность героев — только из библии'],
  ['md', 'Markdown', 'Всё то же простым текстом'],
  ['json', 'JSON', 'Полные данные проекта для других программ'],
];

export function ExportScreen({ data, refresh }: { data: Overview; refresh: () => Promise<void> }) {
  const pid = data.project.id;
  return (
    <section className="export">
      <p className="muted">В файлы попадает всё, что есть в проекте сейчас. Скачать можно в любой момент.</p>
      <div className="export-grid">
        {FORMATS.map(([format, title, text]) => (
          <a key={format} className="panel export-card" href={`/api/projects/${pid}/export?format=${format}`} download onClick={() => setTimeout(() => void refresh(), 1500)}>
            <h3>{title}</h3>
            <p className="muted">{text}</p>
            <span className="button">Скачать</span>
          </a>
        ))}
      </div>
    </section>
  );
}
