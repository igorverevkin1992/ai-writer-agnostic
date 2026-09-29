/** Minimal JSON Patch (RFC 6902: add, replace, remove) on plain JSON data. */
export type PatchOp =
  | { op: 'replace' | 'add'; path: string; value: unknown }
  | { op: 'remove'; path: string };

function parts(path: string): string[] {
  return path
    .split('/')
    .slice(1)
    .map((p) => p.replace(/~1/gu, '/').replace(/~0/gu, '~'));
}

export function applyPatch<T>(doc: T, ops: PatchOp[]): T {
  const out = structuredClone(doc) as unknown;
  for (const op of ops) {
    const keys = parts(op.path);
    const last = keys.pop();
    if (last === undefined) throw new Error(`Пустой путь: ${op.path}`);
    let node = out as Record<string, unknown>;
    for (const k of keys) {
      const next = (node as Record<string, unknown>)[k];
      if (next === undefined || next === null || typeof next !== 'object') throw new Error(`Нет пути ${op.path}`);
      node = next as Record<string, unknown>;
    }
    if (Array.isArray(node)) {
      const idx = last === '-' ? node.length : Number(last);
      if (op.op === 'add') node.splice(idx, 0, op.value);
      else if (op.op === 'replace') node[idx] = op.value;
      else node.splice(idx, 1);
    } else if (op.op === 'remove') delete node[last];
    else node[last] = op.value;
  }
  return out as T;
}
