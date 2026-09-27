// Google Play ブックスのメモ（Google ドライブの「Play ブックスのメモ」フォルダに自動で作られる Google ドキュメント）のパーサ
//
// ドキュメントの構造（docx / HTML 書き出しで共通）:
//   表[表紙画像 | 見出し1=書名, 著者, 出版社]
//   表[「このドキュメントは Play ブックスで変更を加えると上書きされます…」]
//   (見出し1「Annotations by color」→ 見出し2=色 → 注釈…)   ← 新しい形式では同じ注釈が色別にも一度出る
//   見出し1「All your annotations」/「59 notes/highlights」
//   見出し2=章 → 注釈の表…
// 注釈 1 件 = 表の 1 行 [色見本の画像 | ハイライト本文・メモ・日付 | ページ番号]
// ハイライト本文にだけ背景色が付くので、背景色の有無で「本文」と「読者のメモ」を分ける。

import { cleanText, parseLooseDate } from '../text.js';
import { colorName, docxXmlToBlocks, htmlToBlocks } from './blocks.js';

const UNAVAILABLE = /ハイライト表示したテキストを表示できません|can.?t (be )?display|cannot be displayed|non può essere visualizzato/i;
const COLOR_WORDS = {
  yellow: /^(yellow|黄色?|イエロー|giall[oe])$/i,
  green: /^(green|緑色?|グリーン|verd[ei])$/i,
  blue: /^(blue|青色?|ブルー|blu)$/i,
  red: /^(red|赤色?|レッド|ross[oi])$/i,
  orange: /^(orange|オレンジ色?|arancione)$/i,
};

function isDateLike(s) {
  const t = String(s || '').trim();
  return t.length <= 40 && /(19|20)\d{2}/.test(t) && !/[。.!?]$/.test(t.replace(/\d{4}\.?$/, ''));
}

function isHeading(b, level) {
  return new RegExp(`^(h${level}|heading ?${level}${level === 1 ? '|title' : ''})$`, 'i').test(b.style || '');
}

function colorFromWord(w) {
  const s = String(w || '').replace(/[*_]/g, '').trim();
  for (const [name, re] of Object.entries(COLOR_WORDS)) if (re.test(s)) return name;
  return '';
}

/** 表の行が「注釈」かどうか */
function annotationFromRow(row) {
  if (row.length < 3) return null;
  const main = row[row.length - 2];
  const pageCell = row[row.length - 1];
  const paras = main.blocks.filter((b) => b.type === 'p');
  if (!paras.length || pageCell.text.length > 12) return null;
  const last = paras[paras.length - 1];
  if (!isDateLike(last.text)) return null;
  const content = paras.slice(0, -1);
  const highlighted = content.filter((p) => p.fill);
  let text;
  let note;
  if (highlighted.length) {
    text = highlighted.map((p) => p.text).join('\n');
    note = content.filter((p) => !p.fill).map((p) => p.text).join('\n');
  } else {
    text = content[0]?.text || '';
    note = content.slice(1).map((p) => p.text).join('\n');
  }
  return { text: cleanText(text), note: cleanText(note), date: last.text.trim(), page: pageCell.text.trim(), color: colorName(highlighted[0]?.fill) };
}

/** blocks を順に歩き、段落と注釈の並びにする */
function* walk(blocks) {
  for (const b of blocks) {
    if (b.type === 'p') {
      yield { kind: 'p', block: b };
      continue;
    }
    for (const row of b.rows) {
      const a = annotationFromRow(row);
      if (a) yield { kind: 'annotation', a };
      else for (const cell of row) yield* walk(cell.blocks);
    }
  }
}

export function parsePlayBooksBlocks(blocks, fallbackTitle = '') {
  let title = '';
  let author = '';
  let expectAuthor = false;
  let h2 = '';
  const occurrences = new Map();
  let order = 0;
  for (const item of walk(blocks)) {
    if (item.kind === 'p') {
      const b = item.block;
      if (b.style === 'doc-title') {
        // HTML の <title>（ドキュメント名「「書名」のメモ」）は本文に書名が無いときだけ使う
        fallbackTitle = fallbackTitle || titleFromFileName(b.text + '.html');
        continue;
      }
      if (!title && isHeading(b, 1)) {
        title = b.text;
        expectAuthor = true;
        continue;
      }
      if (expectAuthor) {
        expectAuthor = false;
        if (!isHeading(b, 1) && !isHeading(b, 2) && b.text.length < 200) author = b.text;
        continue;
      }
      if (isHeading(b, 1)) h2 = '';
      else if (isHeading(b, 2)) h2 = b.text.replace(/[*_]/g, '').trim();
      continue;
    }
    const a = item.a;
    if (!a.text || UNAVAILABLE.test(a.text)) continue;
    const key = `${a.text}|${a.date}|${a.page}`;
    if (!occurrences.has(key)) occurrences.set(key, { a, order: order++, headings: [] });
    occurrences.get(key).headings.push(h2);
  }
  const highlights = [...occurrences.values()]
    .sort((x, y) => x.order - y.order)
    .map(({ a, headings }) => {
      // 同じ注釈が 2 回出る形式では、最初の見出しが色、最後の見出しが章
      const chapterHeading = headings[headings.length - 1];
      const color = a.color || (headings.length > 1 ? colorFromWord(headings[0]) : '');
      const chapter = headings.length > 1 || !colorFromWord(chapterHeading) ? chapterHeading : '';
      return { text: a.text, note: a.note, page: a.page, color, chapter, createdAt: parseLooseDate(a.date) };
    });
  if (!highlights.length) return [];
  title = cleanText(title) || fallbackTitle;
  return [{ title, author: cleanText(author), source: 'playbooks', highlights }];
}

export async function parsePlayBooksDocx(entries, fallbackTitle) {
  const doc = entries.find((e) => e.name === 'word/document.xml');
  if (!doc) return [];
  return parsePlayBooksBlocks(docxXmlToBlocks(new TextDecoder().decode(doc.bytes)), fallbackTitle);
}

export function parsePlayBooksHtml(html, fallbackTitle) {
  return parsePlayBooksBlocks(htmlToBlocks(html), fallbackTitle);
}

export function looksLikePlayBooksMarkdown(text) {
  return /play\.google\.com\/books\/reader/.test(text) || /^\|\s*!\[Cover Image\]/m.test(text);
}

/** Google ドキュメントの Markdown 書き出し（表が 1 行ずつの Markdown 表になる） */
export function parsePlayBooksMarkdown(md, fallbackTitle = '') {
  const unescape = (s) => s.replace(/\\([\\`*_{}[\]()#+\-.!|>~])/g, '$1');
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  let title = '';
  let h2 = '';
  const occurrences = new Map();
  let order = 0;
  const DATE = /\s((?:[A-Z][a-z]+\.? \d{1,2}, \d{4})|(?:\d{4}年\s?\d{1,2}月\s?\d{1,2}日)|(?:\d{1,2} [A-Za-zà-ü]+ \d{4})|(?:\d{4}[/-]\d{1,2}[/-]\d{1,2}))\s*$/;
  for (const line of lines) {
    if (!title) {
      const cover = line.match(/^\|\s*!\[Cover Image\][^|]*\|\s*\*([^*]+)\*/);
      if (cover) {
        title = unescape(cover[1]).trim();
        continue;
      }
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      if (h[1].length === 1) {
        if (!title && !/notes|annotations|highlights|件/.test(h[2])) title = unescape(h[2].replace(/[*_]/g, '')).trim();
        h2 = '';
      } else h2 = unescape(h[2].replace(/[*_]/g, '')).trim();
      continue;
    }
    if (!line.startsWith('|') || /^\|\s*:?-{3,}/.test(line)) continue;
    const cell = line.replace(/^\|\s*/, '').replace(/\s*\|\s*$/, '');
    const pageLink = cell.match(/\[([0-9ivxlcdmIVXLCDM]{1,8})\]\((https?:\/\/[^)]*)\)\s*$/);
    if (!pageLink) continue;
    let rest = cell.slice(0, pageLink.index).replace(/!\[[^\]]*\]\[[^\]]*\]|!\[[^\]]*\]\([^)]*\)/g, '').trim();
    const dm = rest.match(DATE);
    if (!dm) continue;
    rest = rest.slice(0, dm.index).trim();
    let text = '';
    let note = '';
    const italic = rest.match(/^\*+\s*([\s\S]*?)\s*\*+(?:\s+([\s\S]*))?$/);
    if (italic) {
      text = italic[1];
      note = italic[2] || '';
    } else text = rest;
    text = cleanText(unescape(text.replace(/\*/g, '')));
    note = cleanText(unescape(note));
    if (!text || UNAVAILABLE.test(text)) continue;
    const key = `${text}|${dm[1]}|${pageLink[1]}`;
    if (!occurrences.has(key)) occurrences.set(key, { order: order++, a: { text, note, date: dm[1], page: pageLink[1] }, headings: [] });
    occurrences.get(key).headings.push(h2);
  }
  const highlights = [...occurrences.values()]
    .sort((x, y) => x.order - y.order)
    .map(({ a, headings }) => {
      const chapterHeading = headings[headings.length - 1];
      return {
        text: a.text,
        note: a.note,
        page: a.page,
        color: headings.length > 1 ? colorFromWord(headings[0]) : '',
        chapter: headings.length > 1 || !colorFromWord(chapterHeading) ? chapterHeading : '',
        createdAt: parseLooseDate(a.date),
      };
    });
  if (!highlights.length) return [];
  return [{ title: title || fallbackTitle, author: '', source: 'playbooks', highlights }];
}

/** ファイル名「Notes from "Drive".docx」「「書名」のメモ.docx」から書名を推測（本文に書名が無いとき用） */
export function titleFromFileName(name) {
  const base = String(name || '').replace(/\.[^.]+$/, '');
  const m = base.match(/^Notes from [_"“「]?(.*?)[_"”」]?$/) || base.match(/^[「『_"](.*)[」』_"]のメモ$/);
  return (m ? m[1] : base).replace(/_/g, ' ').trim();
}
