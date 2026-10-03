import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { runCodeChecks } from './checks/code/runner.ts';
import { scoreChecklist } from './checks/code/checklist.ts';
import { openDb, type Db } from './db/client.ts';
import { demoProviders } from './demo.ts';
import { loadGolden } from './fixtures.ts';
import { ProjectMemory } from './memory/store.ts';
import { approveStep, runStep, skipStep } from './pipeline/runners.ts';
import { createProject } from './pipeline/project.ts';
import { LlmClient } from './providers/llm.ts';
import { testConfig } from './providers/testing.ts';
import { genreKit } from '@aiw/kb';

const kb = loadKb();
let db: Db;

beforeEach(() => {
  db = openDb(':memory:');
});

/** Steps 1–4 in demo mode for a project of the given genre. */
async function throughPlan(genreId: string) {
  const llm = new LlmClient({ config: testConfig(), db, env: {}, providers: demoProviders(kb) });
  const projectId = createProject(db, kb, { title: 'Демо', genreId, idea: 'идея' });
  const deps = { db, llm, kb, projectId };
  await runStep(deps, 'concept');
  approveStep(deps, 'concept', { choice: 0 });
  await runStep(deps, 'logline');
  approveStep(deps, 'logline');
  await runStep(deps, 'bible');
  skipStep(deps, 'bible');
  const plan = await runStep(deps, 'season_plan');
  return { memory: new ProjectMemory(db, projectId), plan };
}

describe('golden project «Наследница Вороновых» (romantasy)', () => {
  it('passes every code check of its genre', () => {
    const kit = genreKit(kb, 'romantasy_revenge');
    const golden = loadGolden('naslednitsa');
    const res = runCodeChecks({ kit, ...golden });
    expect(res.findings).toEqual([]);
    const score = scoreChecklist(kit.checklist, kit.rules, res);
    expect(score.items.filter((i) => i.status === 'fail')).toEqual([]);
    expect(score.score + score.unknownPoints).toBe(20);
  });
});

describe('demo mode answers with the golden project of the genre', () => {
  it('romantasy: its own concepts, bible and plan; only the planted hole is open', async () => {
    const { memory, plan } = await throughPlan('romantasy_revenge');
    expect((memory.latestArtifact('concept') as { title: string }[])[0]?.title).toBe('Наследница Вороновых');
    expect(memory.currentBible()?.characters[0]?.name).toBe('Вера');
    expect(memory.findingsOf('season_plan', 'open').map((f) => f.quote)).toEqual(['Веру выгоняют из отеля']);
    expect(plan.checklist?.passed).toBe(true);
  });

  it('thriller: still «Муж женился на мне ради крови»', async () => {
    const { memory } = await throughPlan('revenge_thriller');
    expect(memory.currentBible()?.characters[0]?.name).toBe('Лиза');
    expect(memory.findingsOf('season_plan', 'open').map((f) => f.quote)).toEqual(['Лиза сама возвращается в семью, чтобы спасти сестру']);
  });

  it('myth: «Три луны» with its hero, and the planted hole among the code findings', async () => {
    const { memory } = await throughPlan('slavic_myth');
    expect((memory.latestArtifact('concept') as { title: string }[])[0]?.title).toBe('Три луны');
    expect(memory.currentBible()?.characters[0]?.name).toBe('Гордей');
    const open = memory.findingsOf('season_plan', 'open');
    expect(open.map((f) => f.quote)).toContain('Твой староста сам просил тебя назад не пускать');
    // The genre follows the reference: its paywall after episode 8 is no hole.
    expect(open.map((f) => f.check)).not.toContain('season_frame.anchor.paywall_hook');
    expect(memory.latestArtifact('review:season_plan')).toMatchObject({ summary: { dangers: [{ where: expect.stringContaining('Василисе семь') }, {}] } });
  });
});
