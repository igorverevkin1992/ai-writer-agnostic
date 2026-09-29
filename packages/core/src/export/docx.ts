import { AlignmentType, BorderStyle, Document, HeadingLevel, Packer, Paragraph, Table, TableCell, TableRow, TextRun, WidthType } from 'docx';
import { bibleText } from '../checks/llm/texts.ts';
import { renderBlock } from '../text/script.ts';
import { anchorLabel, type ProjectBundle } from './bundle.ts';

const FONT = 'Arial';
const p = (text: string, opts: { bold?: boolean; size?: number } = {}) =>
  new Paragraph({ children: [new TextRun({ text, bold: opts.bold, size: opts.size ?? 22, font: FONT })], spacing: { after: 80 } });
const h = (text: string, level: (typeof HeadingLevel)[keyof typeof HeadingLevel]) =>
  new Paragraph({ heading: level, children: [new TextRun({ text, font: FONT })], spacing: { before: 240, after: 120 } });

function cell(text: string, bold = false): TableCell {
  return new TableCell({ children: [new Paragraph({ children: [new TextRun({ text, bold, size: 18, font: FONT })] })] });
}

/** Word document: logline, bible, season table, scripts. */
export async function buildDocx(b: ProjectBundle): Promise<Buffer> {
  const body: (Paragraph | Table)[] = [
    new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: b.project.title, bold: true, size: 40, font: FONT })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: b.kit.genre.title, size: 24, font: FONT })], spacing: { after: 240 } }),
  ];
  if (b.logline) body.push(h('Логлайн', HeadingLevel.HEADING_1), p(b.logline.text_35w), p(`Реклама: ${b.logline.ad_15w}`));
  if (b.bible) {
    body.push(h('Библия', HeadingLevel.HEADING_1));
    for (const line of bibleText(b.bible).split('\n')) {
      if (!line.trim()) continue;
      body.push(/^[А-ЯЁ ]+$/u.test(line) ? h(line.charAt(0) + line.slice(1).toLowerCase(), HeadingLevel.HEADING_2) : p(line));
    }
  }
  if (b.plan) {
    body.push(h('План сезона', HeadingLevel.HEADING_1));
    const head = ['Серия', 'Название', 'Событие', 'Героиня', 'Опорные точки', 'Крючок'];
    body.push(
      new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        borders: { top: { style: BorderStyle.SINGLE, size: 1, color: '999999' }, bottom: { style: BorderStyle.SINGLE, size: 1, color: '999999' }, left: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, right: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: 'DDDDDD' }, insideVertical: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' } },
        rows: [
          new TableRow({ tableHeader: true, children: head.map((t) => cell(t, true)) }),
          ...b.plan.episodes.map(
            (e) => new TableRow({ children: [String(e.ep), e.title, e.event, e.heroine_action, e.anchors.map((a) => anchorLabel(b.kit, a)).join(', '), e.cliffhanger].map((t) => cell(t)) }),
          ),
        ],
      }),
    );
  }
  if (b.scripts.length) {
    body.push(h('Сценарии', HeadingLevel.HEADING_1));
    for (const s of b.scripts) {
      body.push(h(`Серия ${s.ep}. «${s.title}» (${s.duration_s} с)`, HeadingLevel.HEADING_2));
      for (const block of s.blocks) body.push(p(renderBlock(block), { bold: block.kind === 'scene' }));
    }
  }
  const doc = new Document({
    creator: 'Сценарный агент',
    title: b.project.title,
    styles: { default: { document: { run: { font: FONT, size: 22 } } } },
    sections: [{ children: body }],
  });
  return Packer.toBuffer(doc);
}
