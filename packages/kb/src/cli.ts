import { KbLoadError } from './errors.ts';
import { genreKit, loadKb } from './loader.ts';

try {
  const kb = loadKb();
  console.log('База знаний в порядке.\n');
  for (const id of Object.keys(kb.genres)) {
    const g = genreKit(kb, id);
    const villains = g.frame.villains ? `, злодеев: ${g.frame.villains.count}` : '';
    console.log(`Жанр «${g.genre.title}» (${id})`);
    console.log(
      `  серий: ${g.frame.episodes}, бесплатных: ${g.frame.free}${villains}; ` +
        `правил: ${g.rules.rules.length}, пунктов чек-листа: ${g.checklist.items.length}, персон зрителей: ${g.personas.personas.length}`,
    );
  }
  console.log(`\nОбщее для всех жанров: типов дыр ${kb.holes.holes.length}, кейсов ${kb.cases.length}, промптов ${Object.keys(kb.prompts).length}.`);

  const stubs = [
    ...Object.entries(kb.rules).filter(([, f]) => f.stub).map(([id]) => `rules/${id}`),
    ...Object.entries(kb.checklists).filter(([, c]) => c.stub).map(([id]) => `checklist/${id}`),
    ...Object.entries(kb.personas).filter(([, p]) => p.stub).map(([id]) => `personas/${id}`),
    ...Object.values(kb.methods).filter((m) => m.stub).map((m) => `methods/${m.id}`),
    kb.glossary.stub && 'glossary',
    ...kb.cases.filter((c) => c.stub).map((c) => `cases/${c.id}`),
  ].filter(Boolean);
  if (stubs.length) console.log(`Заглушки, ждут материалов продюсера: ${stubs.join(', ')}.`);
} catch (err) {
  console.error(err instanceof KbLoadError ? err.message : err);
  process.exit(1);
}
