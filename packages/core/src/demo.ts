/**
 * Demo mode: a provider that answers from a golden project instead of real models,
 * so the producer can walk the whole path in the browser without API keys.
 * Each genre with a golden project (fixtures/golden/<name>/demo.yaml) gets its own answers;
 * nothing about a series lives in this code. Never used in real runs: only with --demo.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Kb } from '@aiw/kb';
import { parse } from 'yaml';
import { z } from 'zod';
import { FIXTURES_DIR, loadGolden, type ProjectFixture } from './fixtures.ts';
import type { ProviderName } from './providers/config.ts';
import type { CallTarget, LlmRequest, Provider, ProviderResult } from './providers/types.ts';

/** fixtures/golden/<name>/demo.yaml: what the demo answers for projects of one genre. */
const DemoScript = z.object({
  genre: z.string(),
  heroine: z.string(),
  partner: z.string(),
  first_hook: z.string(),
  punchline: z.string(),
  sound: z.string(),
  metro_object: z.string(),
  villain_knows: z.string(),
  dialogue: z.array(z.object({ who: z.enum(['heroine', 'partner']), text: z.string(), fixed: z.string().optional() })).length(8),
  metro_hint: z.object({ every: z.int().min(1), line: z.int().min(0), fixes: z.array(z.string()).min(1) }),
  polish_line: z.string(),
  concepts: z.array(z.unknown()).length(3),
  logline: z.record(z.string(), z.unknown()),
  audit: z.object({ hole_type: z.int(), block: z.string(), finding: z.unknown() }),
  respond: z.unknown(),
  judge: z.unknown(),
});
type DemoScript = z.infer<typeof DemoScript>;

interface Golden {
  name: string;
  script: DemoScript;
  project: ProjectFixture;
}

/** Every golden project that has a demo.yaml. */
function loadDemoGoldens(): Golden[] {
  const dir = join(FIXTURES_DIR, 'golden');
  return readdirSync(dir)
    .filter((name) => existsSync(join(dir, name, 'demo.yaml')))
    .sort()
    .map((name) => ({
      name,
      script: DemoScript.parse(parse(readFileSync(join(dir, name, 'demo.yaml'), 'utf8'))),
      project: loadGolden(name),
    }));
}

/**
 * Picks the golden project of the request's genre: the knowledge block starts with the genre
 * title. Requests without it (answers, judges) go on with the last project picked.
 */
class DemoRegistry {
  private goldens: Golden[] | undefined;
  private current: Golden | undefined;

  constructor(private readonly kb?: Kb) {}

  pick(req: LlmRequest): Golden {
    this.goldens ??= loadDemoGoldens();
    const head = req.cacheablePrefix?.[0]?.split('\n')[0] ?? '';
    const genre = this.kb && Object.values(this.kb.genres).find((g) => head.startsWith(`# База знаний: ${g.title}`));
    const found = genre && this.goldens.find((g) => g.script.genre === genre.id);
    if (found) this.current = found;
    this.current ??= this.goldens.find((g) => g.name === 'muzh_krov') ?? this.goldens[0];
    if (!this.current) throw new Error('Нет эталона для демо-режима (fixtures/golden/*/demo.yaml)');
    return this.current;
  }
}

function card(g: Golden, ep: number) {
  const { project: G, script: d } = g;
  const o = G.plan.episodes.find((e) => e.ep === ep)!;
  const cast = [d.heroine, ...G.bible.characters.map((c) => c.name).filter((n) => n !== d.heroine && o.event.includes(n.slice(0, -1)))].slice(0, 3);
  return {
    ep,
    duration_s: 90,
    hook_0_5s: ep === 1 ? d.first_hook : `Продолжение: ${G.plan.episodes[ep - 2]?.cliffhanger ?? ''}`,
    event: o.event,
    twist: o.question,
    emotions: o.emotions,
    heroine_action: o.heroine_action,
    punchline: d.punchline,
    cliffhanger: o.cliffhanger,
    cast: cast.length > 1 ? cast : [d.heroine, d.partner],
    location: G.bible.locations[ep % G.bible.locations.length]!,
    sound: d.sound,
    metro_frame: { face: d.heroine, action: o.heroine_action.toLowerCase(), object: d.metro_object },
    knowledge: { viewer: o.question, heroine: o.event, villain: d.villain_knows },
    acts_on: o.acts_on,
  };
}

function script(g: Golden, ep: number, fixed: boolean) {
  const c = card(g, ep);
  const d = g.script;
  const speaker = { heroine: d.heroine.toUpperCase(), partner: (c.cast[1] ?? d.partner).toUpperCase() };
  return {
    ep,
    title: g.project.plan.episodes[ep - 1]!.title,
    duration_s: 90,
    blocks: [
      { t0: 0, t1: 5, kind: 'scene', text: `ИНТ. ${c.location.toUpperCase()} — НОЧЬ. ${c.hook_0_5s}.` },
      ...d.dialogue.map((l, i) => ({ t0: 5 + i * 10, t1: 15 + i * 10, kind: 'line', speaker: speaker[l.who], text: fixed && l.fixed ? l.fixed : l.text })),
      { t0: 85, t1: 90, kind: 'scene', text: `${c.cliffhanger}.` },
    ],
  };
}

/**
 * A judge that confirms every checklist item it is asked about, quoting the first line
 * of the judged text. For the demo and for tests: never used with real models.
 */
export function confirmChecklist(system: string): { items: { id: string; ok: boolean; quote: string; reason: string }[] } {
  const items = /Пункты:\n([\s\S]*?)\n\n/u.exec(system)?.[1] ?? '';
  const text = /Библия и план сезона:\n([\s\S]*)/u.exec(system)?.[1] ?? '';
  const quote = text.split('\n').find((l) => l.trim().length > 0)?.trim() ?? '';
  return {
    items: items
      .split('\n')
      .map((l) => /^([^:\s]+):/u.exec(l)?.[1])
      .filter((id): id is string => !!id)
      .map((id) => ({ id, ok: true, quote, reason: 'Выполнено в эталоне' })),
  };
}

function answer(g: Golden, task: string, req: LlmRequest): unknown {
  const [kind, a = '', b = ''] = task.split(':');
  const G = g.project;
  const d = g.script;
  switch (kind) {
    case 'concept':
      return d.concepts;
    case 'logline':
      return d.logline;
    case 'bible':
      return G.bible;
    case 'season_plan': {
      const [from = 1, to = Infinity] = a.split('-').map(Number);
      const inPart = (ep: number) => ep >= from && ep <= to;
      return { episodes: G.plan.episodes.filter((e) => inPart(e.ep)), deviations: G.plan.deviations.filter((x) => inPart(x.ep)) };
    }
    case 'extract_facts':
      return { facts: G.bible.facts, knowledge: [] };
    case 'devil_advocate':
      return a === String(d.audit.hole_type) && b.includes(d.audit.block) ? [d.audit.finding] : [];
    case 'persona':
      return [];
    case 'respond':
      return d.respond;
    case 'judge':
      return d.judge;
    case 'episode_cards': {
      const [from, to] = a.split('-').map(Number);
      return Array.from({ length: to! - from! + 1 }, (_, i) => card(g, from! + i));
    }
    case 'script':
      return script(g, Number(a), req.system?.includes('Исправь замечания') ?? false);
    case 'controller':
      if (a === 'metro' && Number(b) % d.metro_hint.every === 1) {
        const line = d.dialogue[d.metro_hint.line]!.text;
        return [{ holeType: 9, severity: 'minor', quote: line, viewerQuestion: 'Зритель спросит: что тут важного без звука?', fixes: d.metro_hint.fixes }];
      }
      return [];
    case 'polish': {
      const tone = ['холоднее', 'с угрозой', 'с подтекстом', 'с усмешкой', 'шёпотом'];
      const t0 = Number(/с (\d+(?:\.\d+)?)-й по/u.exec(req.system ?? '')?.[1] ?? 5);
      const t1 = Number(/по (\d+(?:\.\d+)?)-ю секунду/u.exec(req.system ?? '')?.[1] ?? 15);
      return { variants: tone.map((p) => [{ t0, t1, kind: 'line', speaker: d.heroine.toUpperCase(), parenthetical: p, text: d.polish_line }]) };
    }
    case 'checklist_judge':
      return confirmChecklist(req.system ?? '');
    case 'shootable':
      return { verdict: 'light_edit', reason: 'Реплики стоит сделать живее' };
    case 'eval_match':
      return { matches: [] };
    default:
      return [];
  }
}

export class DemoProvider implements Provider {
  constructor(
    readonly name: ProviderName,
    private readonly registry = new DemoRegistry(),
  ) {}

  async complete(req: LlmRequest, target: CallTarget): Promise<ProviderResult> {
    return {
      text: JSON.stringify(answer(this.registry.pick(req), req.task ?? '', req)),
      model: `${target.model} (демо)`,
      usage: { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 },
    };
  }

  async countTokens(): Promise<number> {
    return 1;
  }
}

/** Demo providers share one registry: a genre picked by one call holds for the next ones. */
export const demoProviders = (kb?: Kb): Record<ProviderName, Provider> => {
  const registry = new DemoRegistry(kb);
  return {
    anthropic: new DemoProvider('anthropic', registry),
    google: new DemoProvider('google', registry),
    openai_compatible: new DemoProvider('openai_compatible', registry),
  };
};
