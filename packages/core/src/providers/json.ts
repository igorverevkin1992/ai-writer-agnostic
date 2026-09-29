/**
 * Extracts a JSON value from model text: tolerates ```json fences
 * and prose around the payload.
 */
export function extractJson(text: string): unknown {
  const trimmed = text.trim();
  const candidates = [trimmed];
  for (const m of trimmed.matchAll(/```(?:json)?\s*([\s\S]*?)```/gu)) candidates.push(m[1]!.trim());
  for (const c of candidates) {
    try {
      return JSON.parse(c);
    } catch {
      // try the next candidate
    }
  }
  // Prose around the payload: try every place where an object or array may start,
  // ending at the matching bracket.
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i];
    if (ch !== '{' && ch !== '[') continue;
    const end = matchingBracket(trimmed, i);
    if (end < 0) continue;
    try {
      return JSON.parse(trimmed.slice(i, end + 1));
    } catch {
      // not JSON here, keep scanning
    }
  }
  throw new SyntaxError('В ответе нет JSON');
}

/** Index of the bracket closing the one at `start`, skipping strings; -1 if none. */
function matchingBracket(text: string, start: number): number {
  const stack: string[] = [];
  let inString = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i]!;
    if (inString) {
      if (ch === '\\') i++;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === '{' || ch === '[') stack.push(ch === '{' ? '}' : ']');
    else if (ch === '}' || ch === ']') {
      if (stack.pop() !== ch) return -1;
      if (stack.length === 0) return i;
    }
  }
  return -1;
}
