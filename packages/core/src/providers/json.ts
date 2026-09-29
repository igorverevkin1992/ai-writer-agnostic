/**
 * Extracts a JSON value from model text: tolerates ```json fences
 * and prose around the payload.
 */
export function extractJson(text: string): unknown {
  const trimmed = text.trim();
  const fenced = /```(?:json)?\s*([\s\S]*?)```/u.exec(trimmed);
  const body = fenced?.[1]?.trim() ?? trimmed;
  try {
    return JSON.parse(body);
  } catch {
    const start = body.search(/[[{]/u);
    const end = Math.max(body.lastIndexOf('}'), body.lastIndexOf(']'));
    if (start >= 0 && end > start) return JSON.parse(body.slice(start, end + 1));
    throw new SyntaxError('В ответе нет JSON');
  }
}
