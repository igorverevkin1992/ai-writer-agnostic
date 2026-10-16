/**
 * Locating a model's quote in the text under review. Models paraphrase slightly even when
 * asked for a word-for-word quote: they drop a comma, change a quote mark, shorten with an
 * ellipsis. A finding is kept when most of the quote is really there, in order.
 */

const MIN_RUN_WORDS = 5;
const MIN_RUN_SHARE = 0.6;

/** Words only: no punctuation or case, «ё» as «е», so the same phrase compares equal. */
export function wordsOf(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/[ёЁ]/gu, 'е')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
}

function hasRun(text: string[], run: string[]): boolean {
  if (run.length === 0) return false;
  outer: for (let i = 0; i + run.length <= text.length; i++) {
    for (let j = 0; j < run.length; j++) if (text[i + j] !== run[j]) continue outer;
    return true;
  }
  return false;
}

/** The longest run of consecutive quote words that appears in the text, in order. */
function longestRun(text: string[], quote: string[]): string[] {
  const index = new Map<string, number[]>();
  text.forEach((w, i) => index.set(w, [...(index.get(w) ?? []), i]));
  let best: string[] = [];
  for (let q = 0; q < quote.length && quote.length - q > best.length; q++) {
    for (const start of index.get(quote[q]!) ?? []) {
      let n = 0;
      while (q + n < quote.length && start + n < text.length && quote[q + n] === text[start + n]) n++;
      if (n > best.length) best = quote.slice(q, q + n);
    }
  }
  return best;
}

/**
 * Finds the quote in the text. Returns the fragment that is really there (as words joined
 * by spaces), or undefined when too little of the quote matches.
 * Exact match first; then an ellipsis quote part by part; then the longest matching run,
 * accepted when it covers at least 60% of the quote (and at least 5 words, or the whole
 * quote when it is shorter).
 */
export function locateQuote(text: string, quote: string): string | undefined {
  const t = wordsOf(text);
  const q = wordsOf(quote);
  if (q.length === 0) return undefined;
  if (hasRun(t, q)) return q.join(' ');

  const parts = quote
    .split(/…|\.\.\./u)
    .map(wordsOf)
    .filter((p) => p.length > 0);
  if (parts.length > 1 && parts.every((p) => hasRun(t, p))) return parts[0]!.join(' ');

  const run = longestRun(t, q);
  const need = Math.min(q.length, Math.max(MIN_RUN_WORDS, Math.ceil(q.length * MIN_RUN_SHARE)));
  return run.length >= need ? run.join(' ') : undefined;
}

/** Whether a line of the text contains the located fragment. */
export function lineHasFragment(line: string, fragment: string): boolean {
  return hasRun(wordsOf(line), fragment.split(' '));
}
