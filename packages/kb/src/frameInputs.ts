/**
 * Frame inputs: a season frame may name its own parameters (`inputs: {paywall: 10}`) and write
 * anchors, blocks and `free` as expressions over them — `pw-2`, `pw+1..pw+2`, `0.45*L`, `L-4..L`.
 * `L` is always the number of episodes; the other names come from `inputs`. The study the
 * producer brought asks for exactly this: the paywall episode is a generation input, not a constant.
 * Expressions are resolved before validation, so the rest of the code sees plain numbers.
 */

export type FrameInputs = Record<string, number>;

const TOKEN = /\s*(\d+(?:\.\d+)?|[A-Za-z_]\w*|[-+*/()])\s*/gu;

/** Evaluates `a*b + c - d` (also `/`) with names from `vars`; the result is rounded to a whole number. */
export function evalEpisodeExpr(expr: string, vars: FrameInputs): number {
  const tokens: string[] = [];
  let rest = expr;
  while (rest.trim()) {
    TOKEN.lastIndex = 0;
    const m = TOKEN.exec(rest);
    if (!m || m.index !== 0) throw new Error(`Непонятное выражение «${expr}»`);
    tokens.push(m[1]!);
    rest = rest.slice(m[0].length);
  }
  let i = 0;
  const peek = () => tokens[i];
  const atom = (): number => {
    const t = tokens[i++];
    if (t === undefined) throw new Error(`Обрыв выражения «${expr}»`);
    if (t === '(') {
      const v = sum();
      if (tokens[i++] !== ')') throw new Error(`Нет закрывающей скобки в «${expr}»`);
      return v;
    }
    if (t === '-') return -atom();
    if (/^\d/u.test(t)) return Number(t);
    const v = vars[t];
    if (v === undefined) throw new Error(`Неизвестное имя «${t}» в «${expr}»: есть ${Object.keys(vars).join(', ')}`);
    return v;
  };
  const product = (): number => {
    let v = atom();
    while (peek() === '*' || peek() === '/') {
      v = tokens[i++] === '*' ? v * atom() : v / atom();
    }
    return v;
  };
  const sum = (): number => {
    let v = product();
    while (peek() === '+' || peek() === '-') {
      v = tokens[i++] === '+' ? v + product() : v - product();
    }
    return v;
  };
  const value = sum();
  if (i !== tokens.length) throw new Error(`Лишние символы в «${expr}»`);
  return Math.round(value);
}

const isExpr = (s: string) => /[A-Za-z_]/u.test(s) && !/^<=/u.test(s);

/** An episode spec with expressions → a number or a [min, max] range. */
function resolveSpec(spec: unknown, vars: FrameInputs): unknown {
  if (typeof spec === 'string' && isExpr(spec)) {
    const parts = spec.split('..');
    if (parts.length === 1) return evalEpisodeExpr(spec, vars);
    if (parts.length === 2) return [evalEpisodeExpr(parts[0]!, vars), evalEpisodeExpr(parts[1]!, vars)];
    throw new Error(`Диапазон «${spec}» — это «a..b»`);
  }
  if (Array.isArray(spec)) return spec.map((x) => (typeof x === 'string' ? evalEpisodeExpr(x, vars) : x));
  return spec;
}

/**
 * Replaces expressions in a raw frame (before Zod) with numbers, using the frame's own
 * `inputs` overridden by `overrides`. Frames without `inputs` pass through untouched.
 */
export function resolveFrameInputs(raw: unknown, overrides: FrameInputs = {}): unknown {
  if (!raw || typeof raw !== 'object' || !('season_frame' in raw)) return raw;
  const sf = (raw as { season_frame: unknown }).season_frame;
  if (!sf || typeof sf !== 'object') return raw;
  const f = { ...(sf as Record<string, unknown>) };
  const declared = (f.inputs && typeof f.inputs === 'object' ? f.inputs : {}) as FrameInputs;
  const inputs: FrameInputs = { ...declared, ...overrides };
  if (typeof f.episodes === 'string') f.episodes = evalEpisodeExpr(f.episodes, inputs);
  const vars: FrameInputs = { ...inputs, L: typeof f.episodes === 'number' ? f.episodes : inputs.L ?? 0 };
  if (typeof f.free === 'string') f.free = evalEpisodeExpr(f.free, vars);
  if (f.anchors && typeof f.anchors === 'object') {
    f.anchors = Object.fromEntries(Object.entries(f.anchors as Record<string, unknown>).map(([k, v]) => [k, resolveSpec(v, vars)]));
  }
  if (Array.isArray(f.blocks)) f.blocks = f.blocks.map((b) => resolveSpec(b, vars));
  f.inputs = inputs;
  return { ...(raw as object), season_frame: f };
}
