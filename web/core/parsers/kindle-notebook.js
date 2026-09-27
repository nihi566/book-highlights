// Kindle アプリの「ノートブックをエクスポート」HTML と、ブックマークレット（read.amazon.co.jp/notebook）の JSON のパーサ
//
// エクスポート HTML の例（新旧どちらも。旧形式は <h3> と </div> が食い違う壊れた HTML）:
//   <div class="bookTitle">書名</div><div class="authors">著者</div>
//   <div class="sectionHeading">章</div>
//   <div class="noteHeading">ハイライト(<span class="highlight_yellow">イエロー</span>) - 章 > ページ20 ·位置177</div>
//   <div class="noteText">本文</div>
//   <div class="noteHeading">メモ - 章 > ページ20 ·位置178</div><div class="noteText">メモ</div>

import { cleanText, htmlToText } from '../text.js';

const CLASSES = ['bookTitle', 'authors', 'sectionHeading', 'noteHeading', 'noteText'];
const MARKER = new RegExp(`<[a-z0-9]+[^>]*class\\s*=\\s*["'](${CLASSES.join('|')})["'][^>]*>`, 'gi');

export function looksLikeKindleExport(html) {
  return /class\s*=\s*["']noteHeading["']/i.test(html) && /class\s*=\s*["']noteText["']/i.test(html);
}

export function parseNoteHeading(headingHtml) {
  const colorClass = headingHtml.match(/highlight_(\w+)/i);
  const text = htmlToText(headingHtml).normalize('NFKC').replace(/\s+/g, ' ').trim();
  let kind = 'highlight';
  if (/^(メモ|Note|Notiz|Nota)\b/i.test(text) || /^メモ/.test(text)) kind = 'note';
  else if (/^(ブックマーク|Bookmark|Lesezeichen|Marcador|Signet)/i.test(text)) kind = 'bookmark';
  const loc = text.match(/(?:位置(?:No\.)?|Location|Position|Emplacement|Posición|Loc\.)\s*:?\s*([\d,]+)/i);
  const page = text.match(/(?:ページ|Page|Seite|Página|Pagina)\s*:?\s*([\divxlcdm]+)/i);
  // 「種類 - 章 > ページ ...」の章の部分
  const chapter = text.match(/^[^-]*?\s-\s(.*?)\s>\s(?:ページ|Page|Seite|Página|Pagina|位置|Location|Position)/i);
  return {
    kind,
    color: colorClass ? colorClass[1].toLowerCase() : '',
    location: loc ? Number(loc[1].replace(/,/g, '')) : null,
    page: page ? page[1] : '',
    chapter: chapter ? chapter[1].trim() : '',
  };
}

export function parseKindleExportHtml(html) {
  const src = String(html);
  const marks = [...src.matchAll(MARKER)].map((m) => ({ cls: m[1], start: m.index, contentStart: m.index + m[0].length }));
  const parts = marks.map((m, i) => ({ cls: m.cls, html: src.slice(m.contentStart, i + 1 < marks.length ? marks[i + 1].start : src.length) }));
  let title = '';
  let author = '';
  let section = '';
  const highlights = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.cls === 'bookTitle') title = title || htmlToText(p.html);
    else if (p.cls === 'authors') author = author || htmlToText(p.html);
    else if (p.cls === 'sectionHeading') section = htmlToText(p.html);
    else if (p.cls === 'noteHeading') {
      const meta = parseNoteHeading(p.html);
      const next = parts[i + 1];
      const body = next?.cls === 'noteText' ? cleanText(htmlToText(next.html)) : '';
      if (next?.cls === 'noteText') i++;
      if (meta.kind === 'bookmark' || !body) continue;
      if (meta.kind === 'note') {
        const prev = highlights[highlights.length - 1];
        if (prev && prev.kind === 'highlight' && (meta.location == null || prev.location == null || Math.abs(prev.location - meta.location) <= 2)) {
          prev.note = prev.note ? `${prev.note}\n${body}` : body;
          continue;
        }
      }
      highlights.push({ kind: meta.kind, text: body, note: '', color: meta.color, location: meta.location, page: meta.page, chapter: section || meta.chapter });
    }
  }
  if (!title || !highlights.length) return [];
  return [{ title: fixSpacing(title), author: cleanText(author), source: 'kindle', highlights: highlights.map((h) => ({ ...h, text: fixSpacing(h.text) })) }];
}

/** 旧形式のエクスポートに多い「句読点の前の空白」を詰める（英文のみ） */
function fixSpacing(s) {
  return s.replace(/ +([,.;:!?)])/g, '$1').replace(/\( +/g, '(');
}

/** ブックマークレットが書き出す JSON（format: "book-highlights/kindle-notebook"） */
export function isNotebookJson(data) {
  return data && typeof data === 'object' && data.format === 'book-highlights/kindle-notebook' && Array.isArray(data.books);
}

export function parseNotebookJson(data) {
  return data.books
    .map((b) => ({
      title: cleanText(b.title),
      author: cleanText(b.author),
      asin: b.asin || undefined,
      source: 'kindle',
      highlights: (b.highlights || [])
        .map((h) => ({
          kind: h.text ? 'highlight' : 'note',
          text: cleanText(h.text || h.note),
          note: h.text ? cleanText(h.note) : '',
          color: h.color || '',
          location: h.location != null && h.location !== '' ? Number(String(h.location).replace(/,/g, '')) : null,
          page: h.page || '',
          createdAt: h.createdAt || null,
        }))
        .filter((h) => h.text),
    }))
    .filter((b) => b.title && b.highlights.length);
}
