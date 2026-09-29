import type { GenreKit } from '@aiw/kb';
import { runCodeChecks } from '../checks/code/runner.ts';
import type { ProjectFixture } from '../fixtures.ts';
import { Bible } from '../schemas/bible.ts';
import type { Finding } from '../schemas/finding.ts';
import { SeasonPlan } from '../schemas/season.ts';
import { applyPatch, type PatchOp } from './patch.ts';

/** One hole planted into a correct project. */
export interface SeededHole {
  id: string;
  holeType: number;
  episode?: number;
  description: string;
  /** Applied to {bible, plan} of the golden project. */
  patch: PatchOp[];
}

/** Deterministic PRNG (mulberry32) so the seeded set is reproducible. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Maker = (p: ProjectFixture, rand: () => number) => Omit<SeededHole, 'id'>[];

const int = (rand: () => number, lo: number, hi: number) => lo + Math.floor(rand() * (hi - lo + 1));

// ---- type 7: chronology ----

const bornAfter: Maker = ({ bible }, rand) =>
  bible.timeline.events.flatMap((e) =>
    e.participants
      .map((who) => ({ who, idx: bible.characters.findIndex((c) => c.name === who) }))
      .filter(({ idx }) => idx >= 0)
      .filter(({ who }) => !(e.kind === 'birth' && e.participants[0] === who))
      .map(({ who, idx }) => {
        const year = e.year + int(rand, 1, 8);
        return {
          holeType: 7,
          description: `${who} родился в ${year}, но участвует в «${e.text}» (${e.year})`,
          patch: [{ op: 'replace' as const, path: `/bible/characters/${idx}/birth_year`, value: year }],
        };
      }),
  );

const wrongAge: Maker = ({ bible }, rand) =>
  bible.timeline.events.flatMap((e, i) =>
    Object.entries(e.ages).map(([who, age]) => {
      const value = Math.max(0, age + (rand() < 0.5 ? -1 : 1) * int(rand, 3, 9));
      return {
        holeType: 7,
        description: `В «${e.text}» (${e.year}) ${who} ${value} лет вместо ${age}`,
        patch: [{ op: 'replace' as const, path: `/bible/timeline/events/${i}/ages/${who}`, value }],
      };
    }),
  );

const brokenOrder: Maker = ({ bible }, rand) =>
  bible.timeline.events.flatMap((e, i) =>
    e.after.map((id) => {
      const prev = bible.timeline.events.find((x) => x.id === id)!;
      const year = Math.max(1900, prev.year - int(rand, 1, 15));
      return {
        holeType: 7,
        description: `«${e.text}» перенесено в ${year}, раньше «${prev.text}» (${prev.year})`,
        patch: [{ op: 'replace' as const, path: `/bible/timeline/events/${i}/year`, value: year }],
      };
    }),
  );

const earlyDeath: Maker = ({ bible }, rand) =>
  bible.timeline.events
    .filter((e) => e.kind !== 'birth')
    .flatMap((e) =>
      e.participants.flatMap((who) => {
        const born = bible.characters.find((c) => c.name === who)?.birth_year;
        if (born === undefined) return [];
        const year = e.year - int(rand, 1, 10);
        if (year <= born) return [];
        return [
          {
            holeType: 7,
            description: `${who} умирает в ${year}, но участвует в «${e.text}» (${e.year})`,
            patch: [
              {
                op: 'add' as const,
                path: '/bible/timeline/events/-',
                value: { id: `e_seed_death_${who}`, year, kind: 'death', text: `Смерть: ${who}`, participants: [who], ages: {}, after: [] },
              },
            ],
          },
        ];
      }),
    );

// ---- type 11: character knowledge ----

const learnsTooLate: Maker = ({ bible, plan }, rand) =>
  plan.episodes.flatMap((e) =>
    e.acts_on.flatMap((a) => {
      const i = bible.knowledge.findIndex((k) => k.who === a.who && k.fact === a.fact);
      const otherSource =
        bible.characters.some((c) => c.name === a.who && c.knows_at_start.includes(a.fact)) ||
        bible.villains.some((v) => v.name === a.who && v.knows.some((k) => k.fact === a.fact));
      if (i < 0 || otherSource) return [];
      const since = e.ep + int(rand, 1, 6);
      return [
        {
          holeType: 11,
          episode: e.ep,
          description: `${a.who} узнаёт «${a.fact}» только в ${since}-й серии, а действует по нему в ${e.ep}-й`,
          patch: [{ op: 'replace' as const, path: `/bible/knowledge/${i}/since_ep`, value: since }],
        },
      ];
    }),
  );

const actsTooEarly: Maker = ({ bible }, rand) =>
  bible.knowledge
    .filter((k) => k.since_ep > 1)
    .map((k) => {
      const ep = int(rand, 1, k.since_ep - 1);
      return {
        holeType: 11,
        episode: ep,
        description: `${k.who} действует по «${k.fact}» в ${ep}-й серии, а узнаёт его в ${k.since_ep}-й`,
        patch: [{ op: 'add' as const, path: `/plan/episodes/${ep - 1}/acts_on/-`, value: { who: k.who, fact: k.fact } }],
      };
    });

const knowsBeforeFact: Maker = ({ bible }, rand) => {
  const since = new Map(bible.facts.filter((f) => f.since_ep !== undefined && f.since_ep > 1).map((f) => [f.id, f.since_ep!]));
  const fromKnowledge = bible.knowledge.flatMap((k, i) => {
    const s = since.get(k.fact);
    if (s === undefined) return [];
    const value = int(rand, 0, s - 1);
    return [
      {
        holeType: 11,
        description: `${k.who} знает «${k.fact}» с ${value}-й серии, а это случается в ${s}-й`,
        patch: [{ op: 'replace' as const, path: `/bible/knowledge/${i}/since_ep`, value }],
      },
    ];
  });
  const fromVillains = bible.villains.flatMap((v, vi) =>
    v.knows.flatMap((k, ki) => {
      const s = since.get(k.fact);
      if (s === undefined) return [];
      const value = int(rand, 1, s - 1);
      return [
        {
          holeType: 11,
          description: `${v.name} знает «${k.fact}» с ${value}-й серии, а это случается в ${s}-й`,
          patch: [{ op: 'replace' as const, path: `/bible/villains/${vi}/knows/${ki}/since_ep`, value }],
        },
      ];
    }),
  );
  return [...fromKnowledge, ...fromVillains];
};

const neverLearns: Maker = ({ bible }, rand) => {
  const people = bible.characters.map((c) => c.name);
  return bible.facts.flatMap((f) =>
    people
      .filter(
        (who) =>
          !bible.knowledge.some((k) => k.who === who && k.fact === f.id) &&
          !bible.characters.some((c) => c.name === who && c.knows_at_start.includes(f.id)) &&
          !bible.villains.some((v) => v.name === who && v.knows.some((k) => k.fact === f.id)),
      )
      .map((who) => {
        const ep = int(rand, Math.max(1, f.since_ep ?? 1), 60);
        return {
          holeType: 11,
          episode: ep,
          description: `${who} действует по «${f.id}» в ${ep}-й серии, но нигде об этом не узнаёт`,
          patch: [{ op: 'add' as const, path: `/plan/episodes/${ep - 1}/acts_on/-`, value: { who, fact: f.id } }],
        };
      }),
  );
};

// ---- other types: code catches some, the model-judge the rest ----

const ruleWithoutReason: Maker = ({ bible }) =>
  bible.world_rules.map((r, i) => ({
    holeType: 1,
    description: `У правила мира «${r.rule}» нет причины`,
    patch: [{ op: 'replace' as const, path: `/bible/world_rules/${i}/why`, value: 'Так работает' }],
  }));

const anchorMoved: Maker = ({ plan }) =>
  plan.episodes.flatMap((e, i) =>
    e.anchors
      .filter((a) => !['fall', 'reveal', 'boss_takedown', 'finale', 'mask', 'first_strike'].includes(a))
      .filter(() => e.ep + 3 <= plan.episodes.length)
      .map((a) => ({
        holeType: 10,
        episode: e.ep + 3,
        description: `«${a}» сдвинута с ${e.ep}-й на ${e.ep + 3}-ю серию`,
        patch: [
          { op: 'replace' as const, path: `/plan/episodes/${i}/anchors`, value: e.anchors.filter((x) => x !== a) },
          { op: 'add' as const, path: `/plan/episodes/${i + 3}/anchors/-`, value: a },
        ],
      })),
  );

const heroineSilent: Maker = ({ plan }) =>
  plan.episodes
    .filter((e) => e.strike_by_villain && e.ep < plan.episodes.length)
    .map((e) => {
      const window = plan.episodes.filter((x) => x.ep > e.ep && x.ep <= e.ep + 3 && x.strike_by_heroine);
      return {
        holeType: 3,
        episode: e.ep,
        description: `После удара злодеев в ${e.ep}-й серии героиня не отвечает`,
        patch: window.map((x) => ({ op: 'replace' as const, path: `/plan/episodes/${x.ep - 1}/strike_by_heroine`, value: false })),
      };
    })
    .filter((h) => h.patch.length > 0);

const tooManyCharacters: Maker = ({ bible }) => [
  {
    holeType: 8,
    description: 'Добавлены три постоянных героя сверх лимита',
    patch: [0, 1, 2].map((n) => ({
      op: 'add' as const,
      path: '/bible/characters/-',
      value: { ...bible.characters[0], name: `Лишний герой ${n + 1}`, regular: true },
    })),
  },
];

/** How many holes of each kind a default seeded set contains (40 in total). */
export const DEFAULT_MIX: [Maker, number][] = [
  [bornAfter, 3],
  [wrongAge, 3],
  [brokenOrder, 3],
  [earlyDeath, 3],
  [learnsTooLate, 3],
  [actsTooEarly, 3],
  [knowsBeforeFact, 3],
  [neverLearns, 3],
  [ruleWithoutReason, 4],
  [anchorMoved, 5],
  [heroineSilent, 4],
  [tooManyCharacters, 3],
];

/** Plants holes into a correct project. Deterministic for a given seed. */
export function seedHoles(project: ProjectFixture, seed = 1, mix: [Maker, number][] = DEFAULT_MIX): SeededHole[] {
  const rand = rng(seed);
  const holes: SeededHole[] = [];
  for (const [make, count] of mix) {
    const pool = make(project, rand);
    for (let n = 0; n < count && pool.length > 0; n++) {
      const pick = pool.splice(Math.floor(rand() * pool.length), 1)[0]!;
      holes.push({ id: `h${String(holes.length + 1).padStart(2, '0')}`, ...pick });
    }
  }
  return holes;
}

/** The golden project with one hole planted. */
export function withHole(project: ProjectFixture, hole: SeededHole): ProjectFixture {
  const raw = applyPatch(project, hole.patch);
  return { bible: Bible.parse(raw.bible), plan: SeasonPlan.parse(raw.plan) };
}

export interface HoleDetection {
  hole: SeededHole;
  found: boolean;
  /** Code findings of the same hole type. */
  matches: Finding[];
}

export interface DetectionReport {
  results: HoleDetection[];
  byType: Record<number, { planted: number; found: number }>;
}

/**
 * Plants each hole alone into the project and runs the code checks.
 * A hole counts as found when a finding of the same hole type appears.
 * The project must be clean, so any such finding is caused by the hole.
 */
export function detectSeeded(kit: GenreKit, project: ProjectFixture, holes: SeededHole[]): DetectionReport {
  const baseline = runCodeChecks({ kit, ...project }).findings;
  if (baseline.length > 0) {
    throw new Error(`Эталонный проект должен проходить проверки, а замечаний: ${baseline.length}`);
  }
  const results = holes.map((hole) => {
    const matches = runCodeChecks({ kit, ...withHole(project, hole) }).findings.filter((f) => f.holeType === hole.holeType);
    return { hole, found: matches.length > 0, matches };
  });
  const byType: DetectionReport['byType'] = {};
  for (const r of results) {
    const t = (byType[r.hole.holeType] ??= { planted: 0, found: 0 });
    t.planted++;
    if (r.found) t.found++;
  }
  return { results, byType };
}
