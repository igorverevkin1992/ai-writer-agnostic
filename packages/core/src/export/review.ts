import type { Kb } from '@aiw/kb';
import { AlignmentType, Document, HeadingLevel, Packer, PageOrientation, Paragraph, Table, TableCell, TableRow, TextRun, WidthType } from 'docx';
import { reviewTables, type ReviewSummary } from '../checks/llm/reviewSummary.ts';
import type { Finding, ReviewLevel } from '../schemas/finding.ts';
import { cleanText, type ProjectBundle } from './bundle.ts';

type Severity = Finding['severity'];

const FONT = 'Arial';
const p = (text: string, bold = false) =>
  new Paragraph({ children: [new TextRun({ text: cleanText(text), bold, size: 22, font: FONT })], spacing: { after: 80 } });
const h = (text: string, level: (typeof HeadingLevel)[keyof typeof HeadingLevel] = HeadingLevel.HEADING_1) =>
  new Paragraph({ heading: level, children: [new TextRun({ text: cleanText(text), font: FONT })], spacing: { before: 240, after: 120 } });
const cell = (text: string, bold = false) =>
  new TableCell({ children: [new Paragraph({ children: [new TextRun({ text: cleanText(text), bold, size: 18, font: FONT })] })] });

function table(head: string[], rows: string[][]): Table {
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    rows: [new TableRow({ tableHeader: true, children: head.map((t) => cell(t, true)) }), ...rows.map((r) => new TableRow({ children: r.map((t) => cell(t)) }))],
  });
}

const LEVELS: { level: ReviewLevel; title: string; name: string }[] = [
  { level: 'critical', title: 'Критические дыры', name: 'критическая' },
  { level: 'high', title: 'Высокие', name: 'высокая' },
  { level: 'medium', title: 'Средние', name: 'средняя' },
  { level: 'low', title: 'Низкие', name: 'низкая' },
];
/** Code checks have no level: it follows from the severity. */
const SEVERITY_LEVEL: Record<Severity, ReviewLevel> = { blocker: 'critical', major: 'high', minor: 'low' };

type Row = ProjectBundle['findings'][number];

/** The review saved after the latest audit: the plan's if there is one, else the bible's. */
export function latestReview(b: ProjectBundle): ReviewSummary | undefined {
  return b.reviews.season_plan ?? b.reviews.bible;
}

/**
 * «Разбор дыр» as a Word document, in the layout of the producer's reference review:
 * verdict, the most dangerous places, a table of holes per level, then who-knows-what,
 * setups and payoffs, ages, questions for a lawyer.
 */
export async function buildReviewDocx(b: ProjectBundle, kb: Kb): Promise<Buffer> {
  const review = latestReview(b);
  const tables = review?.tables ?? (b.bible ? reviewTables(b.bible) : undefined);
  const open = b.findings.filter((f) => f.status === 'open');
  const closed = b.findings.length - open.length;
  const levelOf = (f: Row): ReviewLevel => (f.level as ReviewLevel | null) ?? SEVERITY_LEVEL[f.severity as Severity] ?? 'low';
  const categoryOf = (f: Row) =>
    f.category?.length ? f.category.join(', ') : (kb.review.categories.find((c) => f.holeType && c.hole_types.includes(f.holeType))?.id ?? '—');

  const ruleText = (id: string | null) => {
    const r = id ? b.kit.rules.rules.find((x) => x.id === id) : undefined;
    return r ? `${r.id}. ${r.text}` : (id ?? '—');
  };

  const body: (Paragraph | Table)[] = [
    new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: cleanText(`Разбор дыр: ${b.project.title}`), bold: true, size: 36, font: FONT })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: cleanText(b.kit.genre.title), size: 24, font: FONT })], spacing: { after: 240 } }),
  ];

  body.push(h('Итог'));
  const counts = LEVELS.map((l) => `${l.name} — ${open.filter((f) => levelOf(f) === l.level).length}`).join(', ');
  body.push(p(`Открытых дыр: ${open.length} (${counts}). Закрыто по ходу работы: ${closed}.`));
  if (review?.summary) {
    body.push(p(review.summary.verdict));
    body.push(h('Самые опасные места', HeadingLevel.HEADING_2));
    review.summary.dangers.forEach((d, i) => body.push(p(`${i + 1}. ${d.where}`, true), p(d.why)));
  } else {
    body.push(p('Заключения модели нет: разбор ещё не запускали или вызов не удался. Таблицы ниже собраны кодом.'));
  }

  const head = ['№', 'Серии', 'Категория', 'Серьёзность', 'Что не сходится', 'Почему заметят', 'Как закрыть', 'Правило для агента'];
  let n = 0;
  for (const l of LEVELS) {
    const rows = open.filter((f) => levelOf(f) === l.level);
    if (!rows.length) continue;
    body.push(h(`${l.title} (${rows.length})`));
    body.push(
      table(
        head,
        rows.map((f) => [
          String(++n),
          f.episodes ?? (f.episode ? String(f.episode) : 'библия'),
          categoryOf(f),
          l.name + (f.doubt ? ', сомневаюсь' : ''),
          `${f.viewerQuestion}\nЦитата: «${f.quote}»`,
          f.whyNoticed ?? '—',
          f.fixes.map((x, i) => `${i + 1}) ${x}`).join('\n'),
          f.agentRule ?? ruleText(f.rule),
        ]),
      ),
    );
  }

  if (tables) {
    body.push(h('Кто что знает и с какой серии'));
    body.push(
      tables.knowledge.length
        ? table(['Кто', 'Что знает', 'С какой серии', 'Откуда'], tables.knowledge.map((k) => [k.who, k.fact, k.since ? String(k.since) : 'до сезона', k.how ?? '—']))
        : p('В библии не записано, кто что знает.'),
    );
    body.push(h('Посадки и окупаемости'));
    body.push(
      tables.guns.length
        ? table(['Что', 'Посажено', 'Выстрелило', 'Понятно без звука'], tables.guns.map((g) => [g.object, String(g.planted), g.fired ? String(g.fired) : 'не выстрелило', g.metroVisible ? 'да' : 'нет']))
        : p('Посадок в библии нет.'),
    );
    body.push(h('Возрасты по событиям'));
    body.push(
      tables.ages.length
        ? table(
            ['Год', 'Событие', 'Возраст'],
            tables.ages.map((r) => [
              String(r.year),
              r.event,
              r.ages
                .map((a) => {
                  const said = a.stated !== undefined ? `${a.stated}` : '';
                  const real = a.byBirthYear !== undefined ? `${a.byBirthYear}` : '';
                  const age = said && real && said !== real ? `${said} по сюжету, ${real} по году рождения` : said || real || '?';
                  return `${a.who} — ${age}`;
                })
                .join('; '),
            ]),
          )
        : p('Событий с участниками в хронологии нет.'),
    );
  }

  body.push(h('Вопросы для юриста'));
  const legal = review?.summary?.legal ?? [];
  if (legal.length) legal.forEach((q, i) => body.push(p(`${i + 1}. ${q}`)));
  else body.push(p(review?.summary ? 'Вопросов нет.' : 'Появятся после разбора.'));

  const doc = new Document({
    creator: 'Сценарный агент',
    title: `Разбор дыр: ${b.project.title}`,
    styles: { default: { document: { run: { font: FONT, size: 22 } } } },
    sections: [{ properties: { page: { size: { orientation: PageOrientation.LANDSCAPE } } }, children: body }],
  });
  return Packer.toBuffer(doc);
}
