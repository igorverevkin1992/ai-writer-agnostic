import { describe, expect, it } from 'vitest';
import { sampleScript } from '../schemas/samples.ts';
import { Script } from '../schemas/script.ts';
import { renderScript } from './script.ts';

describe('renderScript', () => {
  it('builds the human-readable format from SPEC.md', () => {
    expect(renderScript(Script.parse(sampleScript))).toBe(
      [
        'СЕРИЯ 12. «Ключ» (90 с)',
        '[0–5 с] ИНТ. ГРИМЁРКА — НОЧЬ. Крупно: рука ЛИЗЫ сжимает ключ.',
        '[ЗВУК] Шаги за дверью.',
        'ЛИЗА (шёпотом): Он знал. С самого начала.',
        '[ТЕКСТ НА ЭКРАНЕ] «3 дня до премьеры»',
        '[ТИШИНА] 2 с.',
        '[85–90 с] Дверь открывается. В проёме — свекровь с тем же ключом.',
      ].join('\n'),
    );
  });
});
