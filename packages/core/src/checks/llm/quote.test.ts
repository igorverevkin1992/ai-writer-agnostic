import { describe, expect, it } from 'vitest';
import { lineHasFragment, locateQuote } from './quote.ts';

const text = `Серия 3. Уля находит шаль Лики в гримёрке. Семён говорит: «Ты не первая, кто это спрашивает».
Серия 4. Регина запирает дверь. Снаружи — меловой крест.`;

describe('locateQuote', () => {
  it('accepts an exact quote whatever the quote marks, case and ё', () => {
    expect(locateQuote(text, 'Семен говорит: "Ты не первая, кто это спрашивает"')).toBe('семен говорит ты не первая кто это спрашивает');
  });
  it('accepts an ellipsis quote when every part is there', () => {
    expect(locateQuote(text, 'Уля находит шаль… Семён говорит')).toBe('уля находит шаль');
    expect(locateQuote(text, 'Уля находит шаль… Семён молчит')).toBeUndefined();
  });
  it('accepts a slightly paraphrased quote and rejects a loose one', () => {
    expect(locateQuote(text, 'Уля находит шаль Лики в гримёрке, Семён говорит ей')).toBeDefined();
    expect(locateQuote(text, 'Регина закрывает дверь, а снаружи крест')).toBeUndefined();
    expect(locateQuote(text, 'Лика исчезает')).toBeUndefined();
    expect(locateQuote(text, '')).toBeUndefined();
  });
  it('accepts a stitched quote with the model\'s own labels and «[...]» when its fragments are there', () => {
    expect(locateQuote(text, 'Уля находит шаль Лики в гримёрке (Серия 3) [...] Снаружи — меловой крест (Серия 4)')).toBe('уля находит шаль лики в гримерке');
    expect(locateQuote(text, 'Героиня: Уля находит шаль Лики / Крючок (Удар (attack)): Регина запирает дверь')).toBeDefined();
    // Two of three fragments real, the third invented: still the majority, accepted; one of three: not.
    expect(locateQuote(text, 'Уля находит шаль Лики в гримёрке... Регина запирает дверь... Лика уезжает в Петербург')).toBeDefined();
    expect(locateQuote(text, 'Лика уезжает в Петербург... Семён платит фонду... Регина запирает дверь')).toBeUndefined();
  });

  it('finds the line of a fragment', () => {
    const fragment = locateQuote(text, 'Снаружи — меловой крест')!;
    expect(lineHasFragment('Серия 4. Регина запирает дверь. Снаружи — меловой крест.', fragment)).toBe(true);
    expect(lineHasFragment('Серия 3. Уля находит шаль Лики в гримёрке.', fragment)).toBe(false);
  });
});
