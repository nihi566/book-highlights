// 文書（docx / HTML）を「段落」と「表」の並びに変換する。DOM を使わないので Node でも動く。
// Block = { type: 'p', text, style, fill } | { type: 'table', rows: Cell[][] }
// Cell  = { text, blocks }
// 段落の fill は「背景色つきの文字（=ハイライト部分）」の色。Play Books では
// ハイライト本文にだけ背景色が付き、読者のメモや日付には付かないので、この区別に使う。

import { decodeEntities } from '../text.js';

const TOKEN = /<!--[\s\S]*?-->|<(\/?)([A-Za-z][\w:.-]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>|([^<]+)/g;

function attr(attrs, name) {
  const m = attrs.match(new RegExp(`(?:^|\\s)${name.replace(/[:.]/g, '\\$&')}\\s*=\\s*("([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i'));
  return m ? decodeEntities(m[2] ?? m[3] ?? m[4] ?? '') : '';
}

/** 白・透明・auto は「色なし」とみなす */
function realFill(fill) {
  const f = String(fill || '').replace('#', '').toLowerCase();
  if (!f || f === 'auto' || f === 'transparent' || f === 'none' || f === 'inherit') return '';
  if (/^f{3}$|^f{6}$|^white$/.test(f)) return '';
  return f;
}

class Builder {
  constructor() {
    this.root = { blocks: [] };
    this.containers = [this.root];
    this.tables = [];
    this.para = null;
  }
  get container() {
    return this.containers[this.containers.length - 1];
  }
  startPara(style = '') {
    this.flush();
    this.para = { type: 'p', text: '', style, fill: '' };
  }
  text(s, fill = '') {
    if (!this.para) this.para = { type: 'p', text: '', style: '', fill: '' };
    this.para.text += s;
    if (fill && s.trim() && !this.para.fill) this.para.fill = fill;
  }
  flush() {
    if (this.para) {
      const text = this.para.text.replace(/[ \t ]+/g, ' ').replace(/ *\n */g, '\n').trim();
      if (text) this.container.blocks.push({ ...this.para, text });
      this.para = null;
    }
  }
  startTable() {
    this.flush();
    const t = { type: 'table', rows: [] };
    this.container.blocks.push(t);
    this.tables.push(t);
  }
  endTable() {
    this.flush();
    this.tables.pop();
  }
  startRow() {
    this.tables[this.tables.length - 1]?.rows.push([]);
  }
  startCell() {
    this.flush();
    const t = this.tables[this.tables.length - 1];
    if (!t) return;
    if (!t.rows.length) t.rows.push([]);
    const cell = { text: '', blocks: [] };
    t.rows[t.rows.length - 1].push(cell);
    this.containers.push(cell);
  }
  endCell() {
    this.flush();
    if (this.containers.length > 1) {
      const cell = this.containers.pop();
      cell.text = cell.blocks.map((b) => (b.type === 'p' ? b.text : '')).filter(Boolean).join('\n');
    }
  }
}

/** word/document.xml → blocks */
export function docxXmlToBlocks(xml) {
  const b = new Builder();
  let inText = false;
  let inRun = false;
  let inRunProps = false;
  let runFill = '';
  let m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(xml))) {
    const [, close, rawTag, attrs, selfClose, text] = m;
    if (text !== undefined) {
      if (inText) b.text(decodeEntities(text), runFill);
      continue;
    }
    if (!rawTag) continue;
    const tag = rawTag.toLowerCase();
    if (close) {
      if (tag === 'w:t') inText = false;
      else if (tag === 'w:r') inRun = false;
      else if (tag === 'w:rpr') inRunProps = false;
      else if (tag === 'w:p') b.flush();
      else if (tag === 'w:tc') b.endCell();
      else if (tag === 'w:tbl') b.endTable();
      continue;
    }
    switch (tag) {
      case 'w:p':
        b.startPara();
        if (selfClose) b.flush();
        break;
      case 'w:pstyle':
        if (!b.para) b.startPara();
        b.para.style = attr(attrs, 'w:val');
        break;
      case 'w:r':
        inRun = !selfClose;
        runFill = '';
        break;
      case 'w:rpr':
        // 段落記号の書式（w:pPr 内の w:rPr）は無視し、文字列（w:r）の書式だけ見る
        inRunProps = inRun && !selfClose;
        break;
      case 'w:shd':
        if (inRunProps) runFill = realFill(attr(attrs, 'w:fill'));
        break;
      case 'w:highlight':
        if (inRunProps) runFill = realFill(attr(attrs, 'w:val'));
        break;
      case 'w:t':
        if (!selfClose) inText = true;
        break;
      case 'w:tab':
        if (inRun) b.text('\t');
        break;
      case 'w:br':
      case 'w:cr':
        b.text('\n');
        break;
      case 'w:tbl':
        b.startTable();
        break;
      case 'w:tr':
        b.startRow();
        break;
      case 'w:tc':
        b.startCell();
        break;
      default:
        break;
    }
  }
  b.flush();
  return b.root.blocks;
}

const BLOCK_TAGS = new Set(['p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'section', 'article', 'header', 'footer', 'title']);

/** <style> 内の「.c3{background-color:#fde096}」を集めて クラス名 → 色 の表にする */
function classFills(html) {
  const map = new Map();
  for (const style of html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)) {
    for (const rule of style[1].matchAll(/([^{}]+)\{([^}]*)\}/g)) {
      const bg = rule[2].match(/background(?:-color)?\s*:\s*(#[0-9a-f]{3,6}|[a-z]+)/i);
      if (!bg) continue;
      for (const sel of rule[1].split(',')) {
        const cls = sel.trim().match(/^\.([\w-]+)$/);
        if (cls) map.set(cls[1], realFill(bg[1]));
      }
    }
  }
  return map;
}

/** HTML → blocks（Google ドキュメントの HTML 書き出しなど） */
export function htmlToBlocks(html) {
  const src = String(html);
  const fills = classFills(src);
  const body = src.replace(/<(script|style)[\s\S]*?<\/\1>/gi, '').replace(/<head[\s\S]*?<\/head>/i, (h) => h.match(/<title[\s\S]*?<\/title>/i)?.[0] ?? '');
  const b = new Builder();
  const spans = [];
  const currentFill = () => {
    for (let i = spans.length - 1; i >= 0; i--) if (spans[i]) return spans[i];
    return '';
  };
  let m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(body))) {
    const [, close, rawTag, attrs, selfClose, text] = m;
    if (text !== undefined) {
      const t = decodeEntities(text.replace(/\s+/g, ' '));
      if (t.trim() || (b.para && b.para.text)) b.text(t, currentFill());
      continue;
    }
    if (!rawTag) continue;
    const tag = rawTag.toLowerCase();
    if (close) {
      if (tag === 'span' || tag === 'mark') spans.pop();
      else if (BLOCK_TAGS.has(tag)) b.flush();
      else if (tag === 'td' || tag === 'th') b.endCell();
      else if (tag === 'table') b.endTable();
      continue;
    }
    if (tag === 'span' || tag === 'mark') {
      if (selfClose) continue;
      const style = attr(attrs, 'style');
      const inline = style.match(/background(?:-color)?\s*:\s*(#[0-9a-f]{3,6}|[a-z]+)/i);
      let fill = inline ? realFill(inline[1]) : '';
      if (!fill) for (const c of attr(attrs, 'class').split(/\s+/)) fill = fill || fills.get(c) || '';
      if (!fill && tag === 'mark') fill = 'ffff00';
      spans.push(fill);
    } else if (BLOCK_TAGS.has(tag)) b.startPara(/^h\d$/.test(tag) ? tag : tag === 'title' ? 'doc-title' : attr(attrs, 'class'));
    else if (tag === 'br') b.text('\n');
    else if (tag === 'table') b.startTable();
    else if (tag === 'tr') b.startRow();
    else if (tag === 'td' || tag === 'th') b.startCell();
  }
  b.flush();
  return b.root.blocks;
}

/** blocks を平らなテキスト行にする（表のセルは " | " でつなぐ）。判定・デバッグ用 */
export function blocksToText(blocks) {
  const out = [];
  for (const bl of blocks) {
    if (bl.type === 'p') out.push(bl.text);
    else for (const row of bl.rows) out.push(row.map((c) => c.text.replace(/\n/g, ' / ')).join(' | '));
  }
  return out.join('\n');
}

/** 16 進の色 → 色名（色相で判定） */
export function colorName(hex) {
  const h = String(hex || '').replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(h)) return /^[a-z]+$/i.test(h) ? h.toLowerCase() : '';
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d < 0.08) return '';
  let hue;
  if (max === r) hue = 60 * (((g - b) / d) % 6);
  else if (max === g) hue = 60 * ((b - r) / d + 2);
  else hue = 60 * ((r - g) / d + 4);
  if (hue < 0) hue += 360;
  if (hue < 20 || hue >= 345) return 'red';
  if (hue < 40) return 'orange';
  if (hue < 70) return 'yellow';
  if (hue < 170) return 'green';
  if (hue < 260) return 'blue';
  if (hue < 300) return 'purple';
  return 'pink';
}
