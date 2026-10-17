import { loadKb, genreKit, METHOD_IDS, METHOD_STEPS } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { KNOWLEDGE_CAP, knowledgeBlock, methodsBlock } from './render.ts';

const kb = loadKb();

describe('method principles in the knowledge block', () => {
  it('holds every method with unique principle ids', () => {
    for (const id of METHOD_IDS) {
      const m = kb.methods[id];
      const ids = m.principles.map((p) => p.id);
      expect(new Set(ids).size, id).toBe(ids.length);
      for (const p of m.principles) for (const step of p.for) expect(METHOD_STEPS).toContain(step);
    }
    expect(kb.methods.mowery.stub).toBe(false);
    expect(kb.methods.mowery.principles.length).toBeGreaterThanOrEqual(40);
    expect(kb.methods.rabkin.principles.length).toBeGreaterThanOrEqual(10);
    expect(kb.methods.harmon.principles.length).toBeGreaterThanOrEqual(8);
    expect(kb.methods.weiland.principles.length).toBeGreaterThanOrEqual(10);
  });

  it('gives a step only the principles addressed to it, plus the universal ones', () => {
    const scripts = methodsBlock(kb, 'scripts').join('\n');
    expect(scripts).toContain('M36.'); // scene = want → obstacle → choice → change
    expect(scripts).toContain('M02.'); // universal: three layers of conflict
    expect(scripts).not.toContain('M17.'); // season_plan only
    const plan = methodsBlock(kb, 'season_plan').join('\n');
    expect(plan).toContain('M17.');
    expect(plan).toContain('W4.');
    expect(plan).not.toContain('M36.');
    const all = methodsBlock(kb).join('\n');
    for (const id of METHOD_IDS) for (const p of kb.methods[id].principles) expect(all).toContain(`${p.id}. `);
  });

  it('keeps principles and glossary terms of other genres out of a genre block', () => {
    const thriller = genreKit(kb, 'vertical_thriller');
    const myth = genreKit(kb, 'slavic_myth');
    const plan = methodsBlock(kb, 'season_plan', thriller.genre.id).join('\n');
    expect(plan).not.toContain('W9.'); // the midpoint of the positive arc: not for a genre with a reset
    expect(plan).not.toContain('R06.'); // «a series that lives on its mystery loses viewers»
    expect(methodsBlock(kb, 'season_plan', myth.genre.id).join('\n')).toContain('W9.');
    const block = knowledgeBlock(kb, thriller, { step: 'season_plan' });
    expect(block).toContain('матрёшка —');
    expect(block).not.toContain('лестница злодеев —');
    expect(block).toContain('Возрастной рейтинг 16+');
    const mythBlock = knowledgeBlock(kb, myth, { step: 'season_plan' });
    expect(mythBlock).toContain('лестница злодеев —');
    expect(mythBlock).not.toContain('матрёшка —');
  });

  it('narrows the knowledge block by step and keeps its other sections', () => {
    const kit = genreKit(kb, 'slavic_myth');
    const full = knowledgeBlock(kb, kit);
    const review = knowledgeBlock(kb, kit, { step: 'review' });
    expect(full).toContain('### Практическая драматургия Моуэри (Тайлер Моуэри)');
    expect(full).toContain('### Пилот и франшиза (Уильям Рабкин)');
    expect(review.length).toBeLessThan(full.length);
    expect(review).toContain('M28.'); // ending mistakes: for the auditor
    expect(review).not.toContain('M43.'); // script format: not for the auditor
    expect(review).toContain('## Правила жанра');
    expect(review).toContain('## Кейсы');
  });
});

describe('«Что проверит критик» in the knowledge block', () => {
  const thriller = genreKit(kb, 'vertical_thriller');

  it('gives concept and logline only the categories with their checks', () => {
    const text = knowledgeBlock(kb, thriller, { step: 'concept' });
    expect(text).toContain('## Что проверит критик');
    expect(text).toContain('- А. Мотивы:');
    expect(text).not.toContain('### Вопросы судьи к правилам жанра');
    expect(text).not.toContain('### Каталог дыр');
    // Categories of other genres (the villain ladder) stay out.
    expect(text).not.toMatch(/^- Ж\. /mu);
  });

  it('gives the author of a bible or plan the category rules, the judge questions with fix hints, and the hole questions as the genre asks them', () => {
    const text = knowledgeBlock(kb, thriller, { step: 'bible' });
    expect(text).toContain('  - Злодей не действует против своей выгоды');
    expect(text).toContain('- R01: Стоп-кадр на 3-й секунде понятен случайному зрителю');
    expect(text).toContain('Как исправить: Начать с нарушения нормы или посреди действия');
    // Hole type 10: the thriller guide replaces the catalog's questions with its own.
    expect(text).toContain('Стоит ли на серии барьера поворот вопроса');
    expect(text).not.toContain('Переворачивает ли середина сезона то, во что зритель верил?');
    // Hole type 3: the guide adds to the catalog's.
    expect(text).toContain('Почему героиня не уходит? Почему не идёт в полицию? Почему злодей не действует быстрее? Есть ли у героини «окно спасения»');
  });

  it('stays under the size cap for every genre and step; the methods section gives way first', () => {
    for (const id of Object.keys(kb.genres)) {
      const kit = genreKit(kb, id);
      for (const step of METHOD_STEPS) {
        const text = knowledgeBlock(kb, kit, { step });
        expect(text.length, `${id}/${step}`).toBeLessThanOrEqual(KNOWLEDGE_CAP);
        expect(text, `${id}/${step}`).toContain('## Что проверит критик');
        expect(text, `${id}/${step}`).toContain('## Методики');
      }
    }
    // The thriller season plan is the largest: it keeps the principles addressed to the step and drops the general ones.
    const plan = knowledgeBlock(kb, thriller, { step: 'season_plan' });
    expect(plan).toContain('M17.');
    expect(plan).not.toContain('M02.');
    expect(methodsBlock(kb, 'season_plan', 'vertical_thriller', 'all').join('\n')).not.toContain('M17.');
  });
});
