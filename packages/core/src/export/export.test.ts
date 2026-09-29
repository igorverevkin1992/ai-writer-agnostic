import ExcelJS from 'exceljs';
import { loadKb } from '@aiw/kb';
import { beforeEach, describe, expect, it } from 'vitest';
import { openDb, type Db } from '../db/client.ts';
import { loadGolden } from '../fixtures.ts';
import { ProjectMemory } from '../memory/store.ts';
import { nextTask } from '../pipeline/next.ts';
import { createProject } from '../pipeline/project.ts';
import { approveStep, skipStep } from '../pipeline/runners.ts';
import { EpisodeCard } from '../schemas/episodeCard.ts';
import { sampleCard } from '../schemas/samples.ts';
import { Script } from '../schemas/script.ts';
import { buildXlsx, cleanText, exportProject, loadBundle, videoPrompts } from './index.ts';

const kb = loadKb();
const golden = loadGolden();
let db: Db;
let pid: string;

const script = (ep: number) =>
  Script.parse({
    ep,
    title: `Серия ${ep}`,
    duration_s: 90,
    blocks: [
      { t0: 0, t1: 5, kind: 'scene', text: 'ИНТ. АРХИВ — НОЧЬ. ЛИЗА держит фото.' },
      { t0: 5, t1: 40, kind: 'line', speaker: 'ВЕРА', text: 'Положи на место.' },
      { t0: 40, t1: 45, kind: 'scene', text: 'ИНТ. ФОЙЕ — НОЧЬ. Незнакомец в тени.' },
      { t0: 45, t1: 90, kind: 'line', speaker: 'ЛИЗА', text: 'Кто вы?' },
    ],
  });

beforeEach(() => {
  db = openDb(':memory:');
  pid = createProject(db, kb, { title: 'Муж женился на мне ради крови', genreId: 'revenge_thriller', idea: 'идея' });
  const m = new ProjectMemory(db, pid);
  m.importBible(golden.bible);
  m.importPlan(golden.plan);
  m.saveCard(EpisodeCard.parse({ ...sampleCard, ep: 1 }));
  m.saveScript(script(1));
});

describe('export', () => {
  it('Word: title, bible, season table and scripts', async () => {
    const f = await exportProject(db, kb, pid, 'docx');
    expect(f.filename).toBe('muzh-zhenilsya-na-mne-radi-krovi.docx');
    expect(f.body.subarray(0, 2).toString()).toBe('PK');
    const JSZip = (await import('jszip')).default;
    const xml = await (await JSZip.loadAsync(f.body)).file('word/document.xml')!.async('string');
    expect(xml).toContain('Муж женился на мне ради крови');
    expect(xml).toContain('План сезона');
    expect(xml).toContain('Точка оплаты');
    expect(xml).toContain('ЛИЗА: Кто вы?');
  });

  it('Excel: season, cards, findings and video prompts sheets', async () => {
    const f = await exportProject(db, kb, pid, 'xlsx');
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(f.body as unknown as ArrayBuffer);
    expect(wb.worksheets.map((w) => w.name)).toEqual(['Сезон', 'Карточки', 'Замечания', 'Промпты для видео']);
    const season = wb.getWorksheet('Сезон')!;
    expect(season.rowCount).toBe(61);
    expect(season.getRow(9).getCell(6).value).toBe('Точка оплаты');
    expect(season.getRow(2).getCell(13).value).toBe('да');
  });

  it('JSON and Markdown carry the whole project', async () => {
    const json = JSON.parse((await exportProject(db, kb, pid, 'json')).body.toString());
    expect(json.plan.episodes).toHaveLength(60);
    expect(json.scripts[0].ep).toBe(1);
    expect(json.videoPrompts.length).toBeGreaterThan(0);
    const md = (await exportProject(db, kb, pid, 'md')).body.toString();
    expect(md).toContain('| 8 | Зеркало |');
    expect(md).toContain('СЕРИЯ 1. «Серия 1» (90 с)');
  });

  it('video prompts take appearance only from the bible', () => {
    const prompts = videoPrompts(loadBundle(db, kb, pid));
    expect(prompts).toHaveLength(2);
    const lisa = golden.bible.characters.find((c) => c.name === 'Лиза')!;
    expect(prompts[0]!.characters).toEqual(expect.arrayContaining([{ name: 'Лиза', look: lisa.look }]));
    expect(prompts[0]!.prompt).toContain(`Лиза — ${lisa.look}`);
    // Someone not in the bible gets no invented look.
    expect(prompts[1]!.prompt).not.toMatch(/Незнакомец —/u);
    expect(prompts[0]!.prompt).toContain('Только практические эффекты');
  });
});

describe('next task (one task at a time)', () => {
  it('leads the producer from the idea to the export', () => {
    const fresh = createProject(db, kb, { title: 'Новый', genreId: 'revenge_thriller', idea: 'идея' });
    expect(nextTask(db, kb, fresh)).toMatchObject({ step: 'concept', action: 'run', task: 'Запустите шаг «Концепции»' });
    new ProjectMemory(db, fresh).saveArtifact('concept', [{}, {}, {}]);
    expect(nextTask(db, kb, fresh)).toMatchObject({ action: 'choose_concept' });

    for (const s of ['concept', 'logline', 'bible', 'season_plan'] as const) skipStep({ db, kb, projectId: pid }, s);
    expect(nextTask(db, kb, pid)).toMatchObject({ step: 'episode_cards', action: 'run', task: 'Запустите шаг «Карточки серий»' });
    const m = new ProjectMemory(db, pid);
    m.saveArtifact('episode_cards', {});
    expect(nextTask(db, kb, pid)).toMatchObject({ action: 'run', task: 'Допишите карточки: не хватает 59' });
    for (let ep = 2; ep <= 60; ep++) m.saveCard(EpisodeCard.parse({ ...sampleCard, ep }));
    expect(nextTask(db, kb, pid)).toMatchObject({ action: 'approve_block', block: 1, task: 'Утвердите карточки серий 1–10' });
    approveStep({ db, kb, projectId: pid }, 'episode_cards', { block: 1 });
    expect(nextTask(db, kb, pid)).toMatchObject({ block: 11 });
    skipStep({ db, kb, projectId: pid }, 'episode_cards');
    m.saveArtifact('scripts', {});
    expect(nextTask(db, kb, pid)).toMatchObject({ step: 'scripts', action: 'run', task: 'Напишите сценарии: осталось 59' });
    skipStep({ db, kb, projectId: pid }, 'scripts');
    expect(nextTask(db, kb, pid)).toMatchObject({ step: 'polish', action: 'polish' });
    skipStep({ db, kb, projectId: pid }, 'polish');
    expect(nextTask(db, kb, pid)).toMatchObject({ step: 'export', action: 'export' });
  });
});

describe('review fixes: export', () => {
  it('finds characters by whole names in any case form, not inside other words', () => {
    const b = loadBundle(db, kb, pid);
    const scene = (text: string) =>
      videoPrompts({ ...b, scripts: [Script.parse({ ep: 1, title: 'С', duration_s: 90, blocks: [{ t0: 0, t1: 90, kind: 'scene', text }] })] })[0]!
        .characters.map((c) => c.name);
    expect(scene('ИНТ. ВЕРАНДА — ДЕНЬ. Лиза одна, проверка почты.')).toEqual(['Лиза']);
    expect(scene('ИНТ. ЗАЛ — ДЕНЬ. Лиза ждёт Веры.')).toEqual(expect.arrayContaining(['Лиза', 'Вера']));
  });

  it('Word and Excel files survive control characters and very long text', async () => {
    expect(cleanText('Сцена\u0007 один\nдва\tтри')).toBe('Сцена один\nдва\tтри');
    const b = loadBundle(db, kb, pid);
    const long = 'А'.repeat(40_000);
    const plan = { ...b.plan!, episodes: b.plan!.episodes.map((e, i) => (i === 0 ? { ...e, event: `${long}\u0001` } : e)) };
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load((await buildXlsx({ ...b, plan })) as unknown as ArrayBuffer);
    const cell = String(wb.getWorksheet('Сезон')!.getRow(2).getCell('C').value);
    expect(cell.length).toBeLessThanOrEqual(32_767);
    expect(cell).not.toContain('\u0001');
    await expect(exportProject(db, kb, pid, 'docx')).resolves.toBeTruthy();
  });
});
