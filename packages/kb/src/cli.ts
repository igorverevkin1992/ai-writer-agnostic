import { KbLoadError } from './errors.ts';
import { loadKb } from './loader.ts';

try {
  const kb = loadKb();
  const ruleCount = kb.rules.reduce((n, f) => n + f.rules.length, 0);
  const stubs = [
    ...kb.rules.filter((f) => f.stub).map((f) => `rules/${f.module}`),
    kb.checklist.stub && 'checklist',
    ...Object.values(kb.methods).filter((m) => m.stub).map((m) => `methods/${m.id}`),
    kb.glossary.stub && 'glossary',
    kb.personas.stub && 'personas',
    ...kb.cases.filter((c) => c.stub).map((c) => `cases/${c.id}`),
  ].filter(Boolean);
  console.log('База знаний в порядке.');
  console.log(`Правил: ${ruleCount}, пунктов чек-листа: ${kb.checklist.items.length}, типов дыр: ${kb.holes.holes.length}, кейсов: ${kb.cases.length}, промптов: ${Object.keys(kb.prompts).length}.`);
  if (stubs.length) console.log(`Заглушки, ждут материалов продюсера: ${stubs.join(', ')}.`);
} catch (err) {
  console.error(err instanceof KbLoadError ? err.message : err);
  process.exit(1);
}
