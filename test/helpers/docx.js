// テスト用: document.xml から deflate 圧縮の docx(zip) を作る
import { deflateRawSync } from 'node:zlib';
import { crc32 } from '../../web/core/zip.js';

export function makeDeflateZip(files) {
  const enc = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const raw = typeof f.content === 'string' ? enc.encode(f.content) : f.content;
    const data = new Uint8Array(deflateRawSync(raw));
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(crc32(raw), 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(name.length, 26);
    const c = Buffer.alloc(46);
    c.writeUInt32LE(0x02014b50, 0);
    c.writeUInt16LE(20, 4);
    c.writeUInt16LE(20, 6);
    c.writeUInt16LE(8, 10);
    c.writeUInt32LE(crc32(raw), 16);
    c.writeUInt32LE(data.length, 20);
    c.writeUInt32LE(raw.length, 24);
    c.writeUInt16LE(name.length, 28);
    c.writeUInt32LE(offset, 42);
    parts.push(local, name, data);
    central.push(c, name);
    offset += 30 + name.length + data.length;
  }
  const cd = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(offset, 16);
  return new Uint8Array(Buffer.concat([...parts, cd, end]));
}

const run = (text, fill) => `<w:r><w:rPr><w:i w:val="1"/>${fill ? `<w:shd w:fill="${fill}" w:val="clear"/>` : '<w:shd w:fill="auto" w:val="clear"/>'}</w:rPr><w:t xml:space="preserve">${text}</w:t></w:r>`;
const para = (text, fill, style) => `<w:p><w:pPr>${style ? `<w:pStyle w:val="${style}"/>` : ''}<w:rPr><w:shd w:fill="fde096" w:val="clear"/></w:rPr></w:pPr>${text ? run(text, fill) : ''}</w:p>`;
const cell = (inner) => `<w:tc><w:tcPr><w:shd w:fill="auto" w:val="clear"/></w:tcPr>${inner}</w:tc>`;

/** Play ブックスのメモの docx（document.xml）を組み立てる */
export function playBooksDocumentXml({ title, author, annotations }) {
  const body = [];
  body.push(`<w:tbl><w:tr>${cell(para(''))}${cell(para(title, '', 'Heading1') + para(author) + para('架空出版'))}</w:tr></w:tbl>`);
  body.push(`<w:tbl><w:tr>${cell(para('このドキュメントは、Play ブックスで変更を加えると上書きされます。'))}</w:tr></w:tbl>`);
  body.push(para(`${annotations.length} 件のメモまたはハイライト表示`, '', 'Heading1'));
  body.push(para('作成者: テスト – 前回の同期: 2024年5月1日'));
  let chapter = null;
  for (const a of annotations) {
    if (a.chapter !== chapter) {
      chapter = a.chapter;
      body.push(para(chapter, '', 'Heading2'));
    }
    const main = para(a.text, a.fill) + (a.note ? para(a.note) : '') + para('') + para(a.date);
    body.push(`<w:tbl><w:tr>${cell(`<w:tbl><w:tr>${cell(para(''))}${cell(main)}${cell(para(a.page))}</w:tr></w:tbl>`)}</w:tr></w:tbl>`);
  }
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body.join('')}</w:body></w:document>`;
}
