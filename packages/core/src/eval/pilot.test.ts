import { genreKit, loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { runCodeChecks } from '../checks/code/runner.ts';
import { scoreChecklist } from '../checks/code/checklist.ts';
import { judgeChecklist } from '../checks/llm/checklistJudge.ts';
import { openDb, type Db } from '../db/client.ts';
import { confirmChecklist } from '../demo.ts';
import { loadGolden } from '../fixtures.ts';
import { LlmClient } from '../providers/llm.ts';
import { FakeProvider, testConfig } from '../providers/testing.ts';
import type { LlmRequest } from '../providers/types.ts';
import { renderPilotReport, runPilot } from './pilot.ts';

const kb = loadKb();
const kit = genreKit(kb, 'revenge_thriller');
const golden = loadGolden();
let db: Db;
let judge: unknown;

function reply(req: LlmRequest): string {
  const t = req.task ?? '';
  const [kind, a = ''] = t.split(':');
  if (kind === 'episode_cards') {
    const [from, to] = a.split('-').map(Number);
    return JSON.stringify(
      Array.from({ length: to! - from! + 1 }, (_, i) => {
        const o = golden.plan.episodes[from! - 1 + i]!;
        return {
          ep: o.ep, duration_s: 90, hook_0_5s: 'Крючок', event: o.event, twist: o.cliffhanger, emotions: o.emotions,
          heroine_action: o.heroine_action, punchline: 'Я всё помню.', cliffhanger: o.cliffhanger, cast: ['Лиза'],
          location: 'архив', sound: 'Тишина', metro_frame: { face: 'Лиза', action: 'смотрит', object: 'фото' },
          knowledge: { viewer: 'а', heroine: 'б', villain: 'в' }, acts_on: o.acts_on,
        };
      }),
    );
  }
  if (kind === 'script') {
    const ep = Number(a);
    const lines = Array.from({ length: 8 }, (_, i) => ({ t0: 5 + i * 10, t1: 15 + i * 10, kind: 'line', speaker: i % 2 ? 'ЛИЗА' : 'ВЕРА', text: 'Я знаю, что ты сделала с моей семьёй, Вера.' }));
    return JSON.stringify({ ep, title: `Серия ${ep}`, duration_s: 90, blocks: [{ t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. АРХИВ — НОЧЬ.' }, ...lines, { t0: 85, t1: 90, kind: 'scene', text: 'Темнота.' }] });
  }
  if (kind === 'controller') return '[]';
  if (kind === 'checklist_judge') return JSON.stringify(judge ?? confirmChecklist(req.system ?? ''));
  if (kind === 'shootable') return JSON.stringify({ verdict: Number(a) % 3 === 0 ? 'no' : 'light_edit', reason: 'Реплики однообразные' });
  throw new Error(t);
}

const client = () =>
  new LlmClient({ config: testConfig(), db, env: {}, providers: { anthropic: new FakeProvider('anthropic', [reply]), google: new FakeProvider('google', [reply], () => 10) } });

beforeEach(() => {
  db = openDb(':memory:');
  judge = undefined;
});

describe('checklist judge', () => {
  const base = scoreChecklist(kit.checklist, kit.rules, runCodeChecks({ kit, ...golden }));

  it('scores items code cannot count, backed by a real quote', async () => {
    const s = await judgeChecklist({ llm: client(), kb, kit }, base, { ...golden, authorProvider: 'anthropic' });
    expect(s.items.find((i) => i.id === 'C09')?.status).toBe('ok');
    expect(s.score).toBe(20);
    expect(s.unknownPoints).toBe(0);
  });

  it('ignores a verdict without a real quote', async () => {
    judge = { items: [{ id: 'C09', ok: true, quote: 'выдуманная цитата', reason: '—' }] };
    const s = await judgeChecklist({ llm: client(), kb, kit }, base, { ...golden, authorProvider: 'anthropic' });
    expect(s.items.find((i) => i.id === 'C09')?.status).toBe('unknown');
  });
});

describe('pilot', () => {
  it('runs cards and scripts for the first episodes and reports the M5 criterion', async () => {
    const r = await runPilot({ db, llm: client(), kb }, golden, { genreId: 'revenge_thriller', episodes: 15 });
    expect(r.stopped).toBeUndefined();
    expect(r.episodes).toHaveLength(15);
    expect(r.metricsFailed.map((m) => m.findings.map((f) => f.quote))).toEqual([]);
    expect(r.metricsPassed).toHaveLength(15);
    expect(r.checklist.score).toBe(20);
    expect(r.shootable.filter((s) => s.verdict === 'no')).toHaveLength(5);
    const md = renderPilotReport(r, { date: '2026-09-29', project: 'muzh_krov', costUsd: 3.2 });
    expect(md).toContain('| Чек-лист | 20 из 20 | ≥15 ✓ |');
    expect(md).toContain('| Метрики сценария пройдены | 15 из 15 серий | все ✓ |');
    expect(md).toContain('| Сцены «можно снимать после лёгкой правки» | 10 из 15 (67%) | ≥40% ✓ |');
  });
});
