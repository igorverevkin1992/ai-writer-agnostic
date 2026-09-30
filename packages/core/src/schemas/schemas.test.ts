import { describe, expect, it } from 'vitest';
import {
  Bible,
  Concept,
  ConceptSet,
  EpisodeCard,
  EpisodeOutline,
  Finding,
  Logline,
  Script,
  ScriptBlock,
  SeasonPlan,
  Villain,
  countWords,
  parseFindings,
  topOpenFindings,
} from './index.ts';
import {
  sampleBible,
  sampleCard,
  sampleConcept,
  sampleFinding,
  sampleLogline,
  sampleOutline,
  sampleScript,
  sampleVillains,
} from './samples.ts';

/** Returns the dotted paths of all validation issues. */
function issuePaths(result: { success: boolean; error?: { issues: { path: PropertyKey[] }[] } }): string[] {
  return result.error?.issues.map((i) => i.path.join('.')) ?? [];
}

describe('Concept', () => {
  it('accepts a valid concept', () => {
    expect(Concept.parse(sampleConcept)).toEqual(sampleConcept);
  });
  it('requires exactly three concepts in a set', () => {
    expect(ConceptSet.safeParse([sampleConcept, sampleConcept, sampleConcept]).success).toBe(true);
    expect(ConceptSet.safeParse([sampleConcept, sampleConcept]).success).toBe(false);
  });
  it('rejects empty fields', () => {
    expect(issuePaths(Concept.safeParse({ ...sampleConcept, premise: '  ' }))).toEqual(['premise']);
  });
});

describe('Logline', () => {
  it('accepts a valid logline', () => {
    expect(Logline.safeParse(sampleLogline).success).toBe(true);
  });
  it('requires every field', () => {
    const rest: Partial<typeof sampleLogline> = { ...sampleLogline };
    delete rest.stakes;
    expect(issuePaths(Logline.safeParse(rest))).toEqual(['stakes']);
  });
  it('limits text_35w to 35 words and ad_15w to 15 words', () => {
    const long = Array.from({ length: 36 }, () => 'слово').join(' ');
    const res = Logline.safeParse({ ...sampleLogline, text_35w: long, ad_15w: long });
    expect(issuePaths(res)).toEqual(['text_35w', 'ad_15w']);
    expect(res.error?.issues[0]?.message).toBe('Не больше 35 слов');
  });
  it('counts words by whitespace', () => {
    expect(countWords('  Он женился   ради крови. ')).toBe(4);
  });
});

describe('Bible', () => {
  it('accepts a valid bible', () => {
    expect(Bible.safeParse(sampleBible).success).toBe(true);
  });
  it('does not fix the number of villains: that is a genre rule', () => {
    expect(Bible.safeParse({ ...sampleBible, villains: sampleVillains.slice(0, 3) }).success).toBe(true);
    expect(Bible.safeParse({ ...sampleBible, villains: [] }).success).toBe(true);
  });
  it('marks a takedown through villain infighting', () => {
    const v = Villain.parse({ ...sampleVillains[0], via_infighting: undefined });
    expect(v.via_infighting).toBe(false);
  });
  it('leaves the boss link to the wound to the genre check, not the schema', () => {
    const boss = sampleVillains.find((v) => v.rank === 1)!;
    expect(Villain.safeParse({ ...boss, link_to_ghost: undefined }).success).toBe(true);
  });
  it('rejects an empty role and an unknown punishment type', () => {
    const res = Villain.safeParse({ ...sampleVillains[0], role: '', punishment: { type: 'death', public: true } });
    expect(issuePaths(res)).toEqual(['role', 'punishment.type']);
  });
  it('accepts any positive rank: ladder size comes from the genre', () => {
    expect(Villain.safeParse({ ...sampleVillains[0], rank: 7 }).success).toBe(true);
    expect(Villain.safeParse({ ...sampleVillains[0], rank: 0 }).success).toBe(false);
  });
  it('allows a gun without a firing episode in a draft', () => {
    const guns = [{ ...sampleBible.guns[0]!, fired_ep: null }];
    expect(Bible.safeParse({ ...sampleBible, guns }).success).toBe(true);
  });
  it('rejects a non-integer birth year', () => {
    const characters = [{ ...sampleBible.characters[0]!, birth_year: 1999.5 }];
    expect(issuePaths(Bible.safeParse({ ...sampleBible, characters }))).toEqual(['characters.0.birth_year']);
  });
});

describe('SeasonPlan', () => {
  const plan = (eps: number[]) => ({ episodes: eps.map((ep) => ({ ...sampleOutline, ep })) });

  it('accepts a plan of 60 episode outlines', () => {
    const res = SeasonPlan.safeParse(plan(Array.from({ length: 60 }, (_, i) => i + 1)));
    expect(res.success).toBe(true);
    expect(res.data?.deviations).toEqual([]);
  });
  it('rejects duplicate episode numbers', () => {
    expect(issuePaths(SeasonPlan.safeParse(plan([1, 2, 2])))).toEqual(['episodes.2.ep']);
  });
  it('requires a reason for a frame deviation', () => {
    const res = SeasonPlan.safeParse({ ...plan([1]), deviations: [{ anchor: 'midpoint', ep: 31, reason: '' }] });
    expect(issuePaths(res)).toEqual(['deviations.0.reason']);
  });
  it('rejects an unknown mood', () => {
    expect(EpisodeOutline.safeParse({ ...sampleOutline, mood: 'joy' }).success).toBe(false);
  });
});

describe('EpisodeCard', () => {
  it('accepts a valid card', () => {
    expect(EpisodeCard.safeParse(sampleCard).success).toBe(true);
  });
  it('requires the full metro frame: face, action, object', () => {
    const res = EpisodeCard.safeParse({ ...sampleCard, metro_frame: { face: 'Лиза', action: 'сжимает' } });
    expect(issuePaths(res)).toEqual(['metro_frame.object']);
  });
  it('requires the heroine action', () => {
    expect(issuePaths(EpisodeCard.safeParse({ ...sampleCard, heroine_action: '' }))).toEqual(['heroine_action']);
    expect(issuePaths(EpisodeOutline.safeParse({ ...sampleOutline, heroine_action: undefined }))).toEqual(['heroine_action']);
  });
  it('requires at least one person in the cast', () => {
    expect(issuePaths(EpisodeCard.safeParse({ ...sampleCard, cast: [] }))).toEqual(['cast']);
  });
});

describe('Script', () => {
  it('accepts a valid script', () => {
    expect(Script.safeParse(sampleScript).success).toBe(true);
  });
  it('requires a speaker for a line', () => {
    expect(issuePaths(ScriptBlock.safeParse({ t0: 0, t1: 2, kind: 'line', text: 'Привет' }))).toEqual(['speaker']);
  });
  it('forbids a speaker outside lines', () => {
    expect(ScriptBlock.safeParse({ t0: 0, t1: 2, kind: 'sound', speaker: 'ЛИЗА', text: 'Шаги' }).success).toBe(false);
  });
  it('rejects a block that ends before it starts', () => {
    expect(issuePaths(ScriptBlock.safeParse({ t0: 5, t1: 2, kind: 'scene', text: 'Сцена' }))).toEqual(['t1']);
  });
  it('rejects an unknown block kind', () => {
    expect(ScriptBlock.safeParse({ t0: 0, t1: 1, kind: 'music', text: 'Музыка' }).success).toBe(false);
  });
  it('requires blocks in time order', () => {
    const blocks = [sampleScript.blocks[1]!, sampleScript.blocks[0]!];
    expect(issuePaths(Script.safeParse({ ...sampleScript, blocks }))).toEqual(['blocks.1.t0']);
  });
});

describe('Finding', () => {
  it('accepts a valid finding', () => {
    expect(Finding.safeParse(sampleFinding).success).toBe(true);
  });
  it('requires a quote', () => {
    expect(issuePaths(Finding.safeParse({ ...sampleFinding, quote: '' }))).toEqual(['quote']);
  });
  it('allows one or two fixes', () => {
    expect(Finding.safeParse({ ...sampleFinding, fixes: [] }).success).toBe(false);
    expect(Finding.safeParse({ ...sampleFinding, fixes: ['a', 'b'] }).success).toBe(true);
    expect(Finding.safeParse({ ...sampleFinding, fixes: ['a', 'b', 'c'] }).success).toBe(false);
  });
  it('requires a fact reference to dismiss', () => {
    expect(issuePaths(Finding.safeParse({ ...sampleFinding, status: 'dismissed' }))).toEqual(['resolutionFactId']);
    expect(Finding.safeParse({ ...sampleFinding, status: 'dismissed', resolutionFactId: 'fact-1' }).success).toBe(true);
  });
  it('only knows the eleven hole types and seven controllers', () => {
    expect(Finding.safeParse({ ...sampleFinding, holeType: 12 }).success).toBe(false);
    expect(Finding.safeParse({ ...sampleFinding, controller: 'style' }).success).toBe(false);
  });
  it('drops findings without a quote', () => {
    const { kept, dropped } = parseFindings([sampleFinding, { ...sampleFinding, id: 'f2', quote: undefined }]);
    expect(kept.map((f) => f.id)).toEqual(['f1']);
    expect(dropped).toBe(1);
  });
  it('shows at most three open findings, blockers first', () => {
    const list: Finding[] = [
      { ...sampleFinding, id: 'minor', severity: 'minor' },
      { ...sampleFinding, id: 'major', severity: 'major' },
      { ...sampleFinding, id: 'closed', status: 'resolved' },
      { ...sampleFinding, id: 'minor2', severity: 'minor' },
      { ...sampleFinding, id: 'blocker', severity: 'blocker' },
    ];
    expect(topOpenFindings(list).map((f) => f.id)).toEqual(['blocker', 'major', 'minor']);
  });
});

describe('error messages', () => {
  it('are in Russian', () => {
    const res = Finding.safeParse({ ...sampleFinding, severity: 'critical' });
    expect(res.error?.issues[0]?.message).toMatch(/ожидалось/);
  });
});
