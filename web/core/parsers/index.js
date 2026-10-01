// ファイルの形式を自動判定してパースする入口（CLI / コンパニオンサーバ / Web アプリで共通）
//
// 対応形式
//   Kindle     : My Clippings.txt（端末） / 「ノートブックをエクスポート」HTML（アプリ） / ブックマークレットの JSON（read.amazon.co.jp/notebook）
//   Play Books : Google ドライブ「Play ブックスのメモ」のドキュメントを .docx / .html / .md で書き出したもの
//   zip        : 上記をまとめた zip（ドライブでフォルダごとダウンロードすると docx の zip になる）
//   JSON       : このアプリのバックアップ（ライブラリ全体）

import { isZip, readZip } from '../zip.js';
import { BACKUP_FORMAT } from '../importing.js';
import { looksLikeClippings, parseKindleClippings } from './kindle-clippings.js';
import { isNotebookJson, looksLikeKindleExport, parseKindleExportHtml, parseNotebookJson } from './kindle-notebook.js';
import { looksLikePlayBooksMarkdown, parsePlayBooksDocx, parsePlayBooksHtml, parsePlayBooksMarkdown, titleFromFileName } from './playbooks.js';

export const FORMAT_LABELS = {
  'kindle-clippings': 'Kindle（My Clippings.txt）',
  'kindle-export': 'Kindle（ノートブックのエクスポート HTML）',
  'kindle-notebook': 'Kindle（ノートブックのブックマークレット）',
  playbooks: 'Play Books（ドライブのメモ）',
  library: 'このアプリのバックアップ',
  parsed: '汎用 JSON',
};

export const ACCEPT = '.txt,.html,.htm,.docx,.md,.markdown,.json,.zip';

export function decodeText(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(b).replace(/^﻿/, '');
  } catch {
    // Windows で保存された日本語テキスト向け
    try {
      return new TextDecoder('shift_jis').decode(b);
    } catch {
      return new TextDecoder('utf-8').decode(b);
    }
  }
}

function ext(name) {
  return (String(name).match(/\.([a-z0-9]+)$/i)?.[1] || '').toLowerCase();
}

/** バックアップ（新形式: { format, library, analysis } / 旧形式: ライブラリに analysis を足したもの）→ { library, analysis } */
function readBackup(data) {
  if (data?.format === BACKUP_FORMAT && isLibraryBackup(data.library)) return { library: data.library, analysis: data.analysis || null };
  if (isLibraryBackup(data)) {
    const { analysis, ...library } = data;
    return { library, analysis: analysis || null };
  }
  return null;
}

function isLibraryBackup(data) {
  return data && typeof data === 'object' && data.books && data.highlights && !Array.isArray(data.books) && typeof data.highlights === 'object';
}

/** 1 ファイル → { format, books?, library?, error? } */
async function parseOne(name, bytes) {
  const e = ext(name);
  const base = String(name).split('/').pop();
  if (isZip(bytes)) {
    const entries = await readZip(bytes);
    if (e === 'docx' || entries.some((x) => x.name === 'word/document.xml')) {
      return { format: 'playbooks', books: await parsePlayBooksDocx(entries, titleFromFileName(base)) };
    }
    return { format: 'zip', entries };
  }
  const text = decodeText(bytes);
  if (e === 'json' || /^\s*[{[]/.test(text)) {
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      if (e === 'json') return { error: 'JSON として読めませんでした' };
    }
    if (data !== undefined) {
      if (isNotebookJson(data)) return { format: 'kindle-notebook', books: parseNotebookJson(data) };
      const backup = readBackup(data);
      if (backup) return { format: 'library', backup };
      const arr = Array.isArray(data) ? data : data.books;
      if (Array.isArray(arr) && arr.every((b) => b && b.title && Array.isArray(b.highlights))) {
        return { format: 'parsed', books: arr.map((b) => ({ ...b, source: b.source === 'playbooks' ? 'playbooks' : b.source === 'kindle' ? 'kindle' : 'manual' })) };
      }
      return { error: '対応していない JSON です' };
    }
  }
  if (/<html|<body|<div|<table|<p[\s>]/i.test(text)) {
    if (looksLikeKindleExport(text)) return { format: 'kindle-export', books: parseKindleExportHtml(text) };
    const books = parsePlayBooksHtml(text, titleFromFileName(base));
    if (books.length) return { format: 'playbooks', books };
    return { error: 'ハイライトが見つからない HTML です（Kindle のエクスポートか Play ブックスのメモを選んでください）' };
  }
  if (looksLikeClippings(text)) return { format: 'kindle-clippings', books: parseKindleClippings(text) };
  if (looksLikePlayBooksMarkdown(text)) return { format: 'playbooks', books: parsePlayBooksMarkdown(text, titleFromFileName(base)) };
  if (e === 'txt') return { error: 'My Clippings.txt の形式ではありません（Play ブックスのメモは .docx か .html で書き出してください）' };
  return { error: '対応していない形式です' };
}

/**
 * @param {{ name: string, bytes: Uint8Array }[]} files
 * @returns {{ books: object[], backups: { library, analysis }[], results: { name, format, formatLabel, books, highlights, error }[] }}
 */
export async function parseFiles(files) {
  const books = [];
  const backups = [];
  const results = [];
  const queue = [...files];
  while (queue.length) {
    const f = queue.shift();
    if (/(^|\/)(__MACOSX|\.DS_Store)|(^|\/)\._/.test(f.name)) continue;
    let r;
    try {
      r = await parseOne(f.name, f.bytes);
    } catch (e) {
      r = { error: e.message };
    }
    if (r.format === 'zip') {
      for (const entry of r.entries) queue.push({ name: `${f.name}/${entry.name}`, bytes: entry.bytes });
      continue;
    }
    if (r.backup) backups.push(r.backup);
    if (r.books) books.push(...r.books);
    const lib = r.backup?.library;
    const hl = r.books ? r.books.reduce((s, b) => s + b.highlights.length, 0) : lib ? Object.keys(lib.highlights).length : 0;
    if (!r.error && !lib && hl === 0) r.error = 'ハイライトが見つかりませんでした';
    results.push({ name: f.name, format: r.format || '', formatLabel: FORMAT_LABELS[r.format] || '', books: r.books?.length ?? (lib ? Object.keys(lib.books).length : 0), highlights: hl, error: r.error || '' });
  }
  return { books, backups, results };
}
