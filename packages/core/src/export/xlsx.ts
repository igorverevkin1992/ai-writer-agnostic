import ExcelJS from 'exceljs';
import { anchorLabel, cleanText, type ProjectBundle } from './bundle.ts';
import { videoPrompts } from './video.ts';

const MAX_CELL = 32_767;

const MOOD: Record<string, string> = { suffering: 'страдание', kaif: 'кайф', neutral: 'нейтрально' };

/** Excel workbook: season table, cards, findings, video prompts. */
export async function buildXlsx(b: ProjectBundle): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  wb.creator = 'Сценарный агент';
  const sheet = (name: string, columns: { header: string; key: string; width: number }[]) => {
    const ws = wb.addWorksheet(name, { views: [{ state: 'frozen', ySplit: 1 }] });
    ws.columns = columns;
    ws.getRow(1).font = { bold: true };
    ws.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFEFEFEA' } };
    return ws;
  };

  const season = sheet('Сезон', [
    { header: 'Серия', key: 'ep', width: 8 },
    { header: 'Название', key: 'title', width: 22 },
    { header: 'Событие', key: 'event', width: 60 },
    { header: 'Вопрос', key: 'question', width: 30 },
    { header: 'Героиня', key: 'action', width: 30 },
    { header: 'Опорные точки', key: 'anchors', width: 18 },
    { header: 'Настроение', key: 'mood', width: 14 },
    { header: 'Удар злодеев', key: 'v', width: 12 },
    { header: 'Удар героини', key: 'h', width: 12 },
    { header: 'Снят злодей', key: 'td', width: 12 },
    { header: 'Крючок', key: 'hook', width: 18 },
    { header: 'Клиффхэнгер', key: 'cliff', width: 40 },
    { header: 'Бесплатная', key: 'free', width: 11 },
  ]);
  for (const e of b.plan?.episodes ?? []) {
    season.addRow({
      ep: e.ep,
      title: e.title,
      event: e.event,
      question: e.question,
      action: e.heroine_action,
      anchors: e.anchors.map((a) => anchorLabel(b.kit, a)).join(', '),
      mood: MOOD[e.mood] ?? e.mood,
      v: e.strike_by_villain ? 'да' : '',
      h: e.strike_by_heroine ? 'да' : '',
      td: e.takedown_rank ?? '',
      hook: e.hook_type,
      cliff: e.cliffhanger,
      free: e.ep <= b.kit.frame.free ? 'да' : '',
    });
  }
  season.getColumn('event').alignment = { wrapText: true, vertical: 'top' };

  const cards = sheet('Карточки', [
    { header: 'Серия', key: 'ep', width: 8 },
    { header: 'Хронометраж, с', key: 'dur', width: 14 },
    { header: 'Крючок 0–5 с', key: 'hook', width: 30 },
    { header: 'Событие', key: 'event', width: 50 },
    { header: 'Поворот', key: 'twist', width: 30 },
    { header: 'Реплика-удар', key: 'punch', width: 30 },
    { header: 'Клиффхэнгер', key: 'cliff', width: 30 },
    { header: 'В кадре', key: 'cast', width: 22 },
    { header: 'Локация', key: 'loc', width: 18 },
    { header: 'Кадр метро', key: 'metro', width: 30 },
  ]);
  for (const c of b.cards) {
    cards.addRow({
      ep: c.ep,
      dur: c.duration_s,
      hook: c.hook_0_5s,
      event: c.event,
      twist: c.twist,
      punch: c.punchline,
      cliff: c.cliffhanger,
      cast: c.cast.join(', '),
      loc: c.location,
      metro: `${c.metro_frame.face} / ${c.metro_frame.action} / ${c.metro_frame.object}`,
    });
  }

  const fs = sheet('Замечания', [
    { header: 'Шаг', key: 'step', width: 14 },
    { header: 'Серия', key: 'ep', width: 8 },
    { header: 'Важность', key: 'sev', width: 11 },
    { header: 'Контролёр', key: 'ctl', width: 13 },
    { header: 'Тип дыры', key: 'type', width: 9 },
    { header: 'Цитата', key: 'quote', width: 40 },
    { header: 'Вопрос зрителя', key: 'q', width: 40 },
    { header: 'Как исправить', key: 'fix', width: 40 },
    { header: 'Статус', key: 'status', width: 11 },
    { header: 'Вердикт', key: 'verdict', width: 30 },
  ]);
  const STATUS: Record<string, string> = { open: 'открыто', resolved: 'закрыто', dismissed: 'отклонено' };
  const SEV: Record<string, string> = { blocker: 'блокирующее', major: 'серьёзное', minor: 'мелкое' };
  for (const f of b.findings) {
    fs.addRow({
      step: f.step ?? '',
      ep: f.episode ?? '',
      sev: SEV[f.severity] ?? f.severity,
      ctl: f.controller,
      type: f.holeType ?? '',
      quote: f.quote,
      q: f.viewerQuestion,
      fix: f.fixes.join(' / '),
      status: STATUS[f.status] ?? f.status,
      verdict: f.verdict ?? '',
    });
  }

  const video = sheet('Промпты для видео', [
    { header: 'Серия', key: 'ep', width: 8 },
    { header: 'Сцена', key: 'scene', width: 8 },
    { header: 'Секунды', key: 't', width: 10 },
    { header: 'Промпт', key: 'prompt', width: 100 },
  ]);
  for (const v of videoPrompts(b)) video.addRow({ ep: v.ep, scene: v.scene, t: `${v.t0}–${v.t1}`, prompt: v.prompt });
  video.getColumn('prompt').alignment = { wrapText: true, vertical: 'top' };

  // Excel refuses a cell over 32 767 characters and XML control characters.
  wb.eachSheet((ws) =>
    ws.eachRow((row) =>
      row.eachCell((c) => {
        if (typeof c.value !== 'string') return;
        const text = cleanText(c.value);
        c.value = text.length > MAX_CELL ? `${text.slice(0, MAX_CELL - 20)}… (обрезано)` : text;
      }),
    ),
  );
  return Buffer.from(await wb.xlsx.writeBuffer());
}
