import { genreKit, loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { projects } from '../db/schema.ts';
import { confirmChecklist } from '../demo.ts';
import { loadGolden } from '../fixtures.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { ProducerHolesFile, renderEvalReport, runAuditEval } from './auditEval.ts';
import { seedHoles, withHole } from './seed.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const golden = loadGolden();

let db: Db;
let tasks: string[];
let matchCode = false;

const hole = {
  level: 'critical',
  quote: 'Лиза сама возвращается в семью, чтобы спасти сестру',
  viewerQuestion: 'Зритель спросит: почему не в полицию?',
  whyNoticed: 'Полиция — первое, о чём подумает зритель',
  fixes: ['Показать, почему полиция не поможет'],
  agentRule: 'Каждое «почему не в полицию» закрывать в кадре',
};

function reply(req: LlmRequest): string {
  const task = req.task ?? '';
  tasks.push(task);
  if (task.startsWith('devil_advocate:А:')) return JSON.stringify([hole]);
  if (task.startsWith('devil_advocate:') || task.startsWith('persona:') || task.startsWith('realism:')) return '[]';
  if (task.startsWith('eval_match')) {
    const id = /(review\.А\S+) \| тип 3/u.exec(req.system ?? '')?.[1];
    // A code finding is offered to the judge too and counts towards the producer's holes.
    const codeId = /^(\S+) \| тип \d+ \| серия [^|]+\| «[^»]*» \| Зритель спросит: [^\n]*/mu;
    const codeLine = (req.system ?? '').split('\n').find((l) => codeId.test(l) && !l.startsWith('review.') && !l.startsWith('persona.'));
    const matches = id ? [{ finding_id: id, hole_id: 'p1', real: true, disputed: false, reason: 'Та же дыра' }] : [];
    if (codeLine && matchCode) matches.push({ finding_id: codeLine.split(' | ')[0]!, hole_id: 'p2', real: true, disputed: false, reason: 'Код нашёл' });
    return JSON.stringify({ matches });
  }
  if (task.startsWith('respond:')) return JSON.stringify({ action: 'cite', fact_id: 'f_dasha_target', explanation: 'Даша в опасности' });
  if (task.startsWith('judge:')) return JSON.stringify({ closed: true, reason: 'Закрыто' });
  if (task === 'checklist_judge') return JSON.stringify(confirmChecklist(req.system ?? ''));
  throw new Error(task);
}

function client() {
  return new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply]) } });
}

beforeEach(() => {
  db = openDb(':memory:');
  tasks = [];
  matchCode = false;
  db.insert(projects).values({ id: 'ev', title: 'eval', genreId: 'revenge_thriller', budgetLimitUsd: 100 }).run();
});

describe('auditor evaluation', () => {
  it('measures producer recall, real findings, seeded holes and author replies', async () => {
    const seeded = seedHoles(golden, kit, 1).slice(0, 6);
    const report = await runAuditEval({
      llm: client(),
      kb,
      kit,
      golden,
      projectId: 'ev',
      seeded,
      producer: ProducerHolesFile.parse({ holes: [{ id: 'p1', holeType: 3, episode: 42, description: 'Почему не в полицию' }, { id: 'p2', holeType: 7, description: 'Возраст Лизы' }], checklist_score: 20 }),
    });
    expect(report.findings).toHaveLength(1);
    expect(report.producerFound).toBe(1);
    expect(report.producerHoles).toBe(2);
    expect(report.realFindings).toBe(1);
    expect(report.seeded).toHaveLength(6);
    expect(report.seeded.every((s) => s.code)).toBe(true);
    expect(report.resolved).toMatchObject({ tried: 1, closed: 1, architect: 'architect' });
    expect(report.anchors.onPlace).toBe(report.anchors.total);

    const md = renderEvalReport(report, { date: '2026-09-29', project: 'muzh_krov', costUsd: 1.5, architectModel: 'a-big' });
    expect(md).toContain('| Найдено дыр продюсера | 1 из 2 (50%); без спорных 1 (50%) | ≥70% ✗ | ≥85% ✗ |');
    expect(md).toContain('| Доля реальных замечаний | 1 из 1 (100%); без спорных 1 (100%) | ≥50% ✓ | ≥65% ✓ |');
    expect(md).toContain('| Опорные точки на своих номерах | 12 из 12 (100%) | 100% ✓ | 100% |');
  });

  it('a code finding matched to a producer hole counts as found, but not as a model finding', async () => {
    matchCode = true;
    // A seeded hole the code catches, so the golden project yields a code finding to match.
    const holed = withHole(golden, seedHoles(golden, kit, 1)[0]!);
    const report = await runAuditEval({
      llm: client(),
      kb,
      kit,
      golden: holed,
      projectId: 'ev',
      seeded: [],
      producer: ProducerHolesFile.parse({ holes: [{ id: 'p1', holeType: 3, episode: 42, description: 'Почему не в полицию' }, { id: 'p2', holeType: 7, description: 'Возраст Лизы' }] }),
    });
    expect(report.producerFound).toBe(2);
    expect(report.realFindings).toBe(1);
    expect(report.findings).toHaveLength(1);
    expect(report.producerMissed).toEqual([]);
  });

  it('audits seeded holes with a targeted pass: only the categories of the hole type', async () => {
    const [h] = seedHoles(golden, kit, 1).filter((x) => x.holeType === 11 && x.episode);
    await runAuditEval({ llm: client(), kb, kit, golden, projectId: 'ev', seeded: [h!] });
    const after = tasks.slice(tasks.findLastIndex((t) => t.startsWith('eval_match')) + 1).filter((t) => t.startsWith('devil_advocate:'));
    // Hole type 11 (character knowledge) is covered by category Д only, over the block of the episode.
    expect(after).toHaveLength(1);
    expect(after[0]).toMatch(/^devil_advocate:Д:план, серии \d+–\d+$/u);
  });

  it('stops on the budget limit and still returns a report', async () => {
    db.insert(projects).values({ id: 'tiny', title: 'eval', genreId: 'revenge_thriller', budgetLimitUsd: 0.05 }).run();
    const report = await runAuditEval({ llm: client(), kb, kit, golden, projectId: 'tiny', seeded: [] });
    expect(report.stopped).toMatch(/Бюджет проекта исчерпан/);
    expect(renderEvalReport(report, { date: 'd', project: 'p', costUsd: 0.05, architectModel: 'm' })).toContain('Отчёт неполный');
  });

  it('a seeded hole counts only for a finding the golden project did not already raise', async () => {
    const same = { id: 's1', holeType: 3, episode: 42, description: 'Та же дыра, что и в эталоне', patch: [] };
    const report = await runAuditEval({ llm: client(), kb, kit, golden, projectId: 'ev', seeded: [same] });
    expect(report.seeded).toMatchObject([{ code: false, model: false }]);
  });

  it('the share of real findings counts every finding, not only matched ones', () => {
    const f = { controller: 'logic', severity: 'major', viewerQuestion: 'Зритель спросит: ?', fixes: ['a'], status: 'open' } as const;
    const md = renderEvalReport(
      {
        findings: [1, 2, 3, 4].map((n) => ({ ...f, id: `f${n}`, quote: `q${n}`, fixes: ['a'] })),
        dropped: 0,
        droppedSamples: [],
        producerMissed: [],
        matches: [{ finding_id: 'f1', hole_id: null, real: true, disputed: false, reason: '' }],
        producerHoles: 0,
        producerFound: 0,
        producerFoundStrict: 0,
        realFindings: 1,
        realFindingsStrict: 1,
        seeded: [],
        checklist: { code: 0, unknown: 0 },
        anchors: { total: 1, onPlace: 1 },
        resolved: { tried: 0, closed: 0, architect: 'architect' },
      },
      { date: 'd', project: 'p', costUsd: 0, architectModel: 'm' },
    );
    expect(md).toContain('| Доля реальных замечаний | 1 из 4 (25%)');
  });
});
