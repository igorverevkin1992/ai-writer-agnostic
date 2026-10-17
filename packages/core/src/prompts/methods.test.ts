import { loadKb, genreKit, METHOD_IDS, METHOD_STEPS } from '@aiw/kb';
import { describe, expect, it } from 'vitest';
import { knowledgeBlock, methodsBlock } from './render.ts';

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
