// Kindle 端末の "My Clippings.txt" パーサ（英語・日本語の両形式）
//
// 1 件の書式:
//   本のタイトル (著者名)
//   - 位置No. 170-172のハイライト |作成日: 2023年1月1日 日曜日 12:34:56
//   (空行)
//   本文
//   ==========
// 英語:
//   - Your Highlight on page 12 | Location 170-172 | Added on Sunday, January 1, 2023 12:34:56 PM

import { parseLooseDate } from '../text.js';

const SEPARATOR = /^={5,}\s*$/m;

export function looksLikeClippings(text) {
  return /={10}/.test(text) && /(Your Highlight|Your Note|のハイライト|のメモ|Highlight Loc|Added on|作成日)/.test(text);
}

export function parseClippingMeta(line) {
  const s = line.normalize('NFKC').replace(/^\s*-\s*/, '');
  let kind = null;
  if (/ハイライト|Highlight|Surlignement|Markierung|Subrayado|evidenziazione|destaque|标注/i.test(s)) kind = 'highlight';
  else if (/メモ|Note|Notiz|Nota|notitie|笔记/i.test(s)) kind = 'note';
  else if (/ブックマーク|Bookmark|Lesezeichen|Marcador|Signet|segnalibro|bladwijzer|书签/i.test(s)) kind = 'bookmark';
  const loc = s.match(/(?:位置No\.|位置|Location|Loc\.|Pos\.|Position|Emplacement|posición|posizione|posição|locatie)\s*#?\s*(\d+)(?:\s*(?:-|t\/m)\s*(\d+))?/i);
  const page = s.match(/(?:page|Seite|página|pagina|p\.)\s*(\d+|[ivxlcdm]+)(?:\s*-\s*\d+)?/i) || s.match(/(\d+|[ivxlcdm]+)\s*ページ/i) || s.match(/ページ\s*(\d+|[ivxlcdm]+)/i) || s.match(/第\s*(\d+)\s*页/);
  const date = s.match(/(?:Added on|作成日[:：]?|追加日[:：]?|Hinzugefügt am|Añadido el|Ajouté le|Aggiunto in data|Toegevoegd op|Adicionado:?|添加于)\s*(.+)$/i);
  let location = null;
  let locationEnd = null;
  if (loc) {
    location = Number(loc[1]);
    if (loc[2]) {
      // 英語の古い形式 "Loc. 1234-56" は終端が省略される
      let end = loc[2];
      if (end.length < loc[1].length) end = loc[1].slice(0, loc[1].length - end.length) + end;
      locationEnd = Number(end);
    }
  }
  return {
    kind,
    location,
    locationEnd,
    page: page ? page[1] : '',
    createdAt: date ? parseLooseDate(date[1]) : null,
  };
}

/** "タイトル (著者)" を分ける。括弧が入れ子のタイトルにも対応するため末尾の括弧を探す */
export function splitTitleAuthor(line) {
  const s = line.replace(/^﻿/, '').trim();
  if (s.endsWith(')') || s.endsWith('）')) {
    let depth = 0;
    for (let i = s.length - 1; i >= 0; i--) {
      const c = s[i];
      if (c === ')' || c === '）') depth++;
      else if (c === '(' || c === '（') {
        depth--;
        if (depth === 0) {
          const title = s.slice(0, i).trim();
          const author = s.slice(i + 1, -1).trim();
          if (title) return { title, author };
          break;
        }
      }
    }
  }
  return { title: s, author: '' };
}

export function parseKindleClippings(text) {
  const books = new Map();
  const chunks = String(text).replace(/\r\n?/g, '\n').split(SEPARATOR);
  for (const chunk of chunks) {
    const lines = chunk.replace(/﻿/g, '').split('\n');
    while (lines.length && !lines[0].trim()) lines.shift();
    if (lines.length < 2) continue;
    const { title, author } = splitTitleAuthor(lines[0]);
    const meta = parseClippingMeta(lines[1]);
    const body = lines.slice(2).join('\n').trim();
    if (!title || !meta.kind || meta.kind === 'bookmark') continue;
    if (!books.has(title)) books.set(title, { title, author, source: 'kindle', entries: [] });
    const book = books.get(title);
    if (!book.author && author) book.author = author;
    if (body) book.entries.push({ ...meta, text: body });
  }
  return [...books.values()].map((b) => ({ title: b.title, author: b.author, source: 'kindle', highlights: attachNotes(b.entries) }));
}

/** メモは同じ位置（範囲の終端）のハイライトにくっつける。対応が無いメモは単独の点にする */
function attachNotes(entries) {
  const highlights = entries.filter((e) => e.kind === 'highlight').map((e) => ({ ...e, note: '' }));
  const orphans = [];
  for (const n of entries.filter((e) => e.kind === 'note')) {
    const target =
      highlights.find((h) => h.location != null && n.location != null && (h.locationEnd ?? h.location) === n.location) ||
      highlights.find((h) => h.location != null && n.location != null && h.location <= n.location && n.location <= (h.locationEnd ?? h.location)) ||
      highlights.find((h) => h.location == null && n.location == null && h.page && h.page === n.page);
    if (target) target.note = target.note ? `${target.note}\n${n.text}` : n.text;
    else orphans.push({ ...n, kind: 'note' });
  }
  return [...highlights, ...orphans].map(({ kind, ...h }) => ({ ...h, kind: kind === 'note' ? 'note' : 'highlight' }));
}
