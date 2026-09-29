import type { Script, ScriptBlock } from '../schemas/script.ts';

const TAGS: Partial<Record<ScriptBlock['kind'], string>> = {
  sound: 'ЗВУК',
  overlay: 'ТЕКСТ НА ЭКРАНЕ',
  insert: 'ВСТАВКА',
  silence: 'ТИШИНА',
};

const secs = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

export function renderBlock(b: ScriptBlock): string {
  if (b.kind === 'scene') return `[${secs(b.t0)}–${secs(b.t1)} с] ${b.text}`;
  if (b.kind === 'line') return `${b.speaker}${b.parenthetical ? ` (${b.parenthetical})` : ''}: ${b.text}`;
  return `[${TAGS[b.kind]}] ${b.text}`;
}

/** Human-readable script, built from JSON by code (format from SPEC.md). */
export function renderScript(s: Script): string {
  return [`СЕРИЯ ${s.ep}. «${s.title}» (${s.duration_s} с)`, ...s.blocks.map(renderBlock)].join('\n');
}
