// ライブラリと分析結果を Obsidian の Vault 用 Markdown / Canvas に変換する
//
// 生成するノート（root は既定で "Highlights"）
//   {root}/Index.md                 … 本の一覧（入口）
//   {root}/Books/{本}.md            … 点：ハイライト。各ハイライトにブロック ID (^hxxxx) を付ける
//   {root}/Lines/{線}.md            … 線：複数の点をつなぐ概念。点をブロック埋め込みで引用
//   {root}/Planes/{面}.md           … 面：線を束ねたテーマ
//   {root}/Knowledge Map.md         … 立体：全体の構造・原則・問い
//   {root}/Knowledge Map.canvas     … 立体を Obsidian Canvas で俯瞰
//   {root}/Recommendations.md       … おすすめの本
//
// 自動生成部分は <!-- bh:start --> 〜 <!-- bh:end --> で囲み、その外側に書いた自分のメモは再出力しても残る。

import { FEEDBACK_LABELS, SOURCES, bookHighlights, feedbackByStatus, feedbackFor, listBooks } from './model.js';
import { isoDate, safeFileName, yamlString } from './text.js';

export const START = '<!-- bh:start 自動生成（この区間は再出力で上書きされます。自分のメモは bh:end の下へ） -->';
export const END = '<!-- bh:end -->';
const START_RE = /<!-- bh:start[^>]*-->/;
const MANIFEST = '.bh-manifest.json';

/**
 * 各ノートのファイル名を決める。owners（前回の書き出しで「どのファイルがどの本・線・面のものか」）があれば
 * 同じファイルを使い続けるので、書名の先頭が同じ本が後から増えても、ノートと自分のメモが別の本に付け替わらない。
 */
export function vaultPaths(library, analysis, root = 'Highlights', owners = {}) {
  const stem = (p) => p.replace(/\.(md|canvas)$/, '');
  const reservedBy = new Map();
  const previousOf = new Map();
  for (const [path, id] of Object.entries(owners || {})) {
    reservedBy.set(stem(path).toLowerCase(), id);
    if (!previousOf.has(id)) previousOf.set(id, []);
    previousOf.get(id).push(stem(path));
  }
  const used = new Set();
  const take = (p) => used.add(p.toLowerCase()) && p;
  // 本の ID は変わらないので、前回の持ち主の予約は（削除した本のメモ付きノートでも）守る。
  // 線・面の ID は分析し直すと変わるので、予約は持ち主が今も存在するときだけ有効（同じ名前なら同じノートを使い続ける）
  const books = listBooks(library);
  const current = new Set([...books, ...(analysis?.lines || []), ...(analysis?.planes || [])].map((x) => x.id));
  const blocks = (p, id, dir) => {
    const owner = reservedBy.get(p.toLowerCase());
    if (!owner || owner === id) return false;
    return dir === 'Books' || current.has(owner);
  };
  const assign = (dir, items, nameOf) => {
    const out = {};
    const sorted = [...items].sort((a, b) => a.id.localeCompare(b.id));
    const baseOf = (it) => `${root}/${dir}/${safeFileName(nameOf(it))}`;
    // 1) 前回と同じファイル（今の名前、または今の名前 + 連番）を使い続ける
    for (const it of sorted) {
      const base = baseOf(it);
      const prev = (previousOf.get(it.id) || []).find((p) => (p === base || (p.startsWith(base + ' (') && /^ \(\d+\)$/.test(p.slice(base.length)))) && !used.has(p.toLowerCase()));
      if (prev) out[it.id] = take(prev);
    }
    // 2) 新しいものは、使われていない・他の本などの予約が無い名前にする
    for (const it of sorted) {
      if (out[it.id]) continue;
      const base = baseOf(it);
      let p = base;
      for (let i = 2; used.has(p.toLowerCase()) || blocks(p, it.id, dir); i++) p = `${base} (${i})`;
      out[it.id] = take(p);
    }
    return out;
  };
  return {
    root,
    index: `${root}/Index`,
    map: `${root}/Knowledge Map`,
    recommendations: `${root}/Recommendations`,
    books: assign('Books', books, (b) => b.title),
    lines: assign('Lines', analysis?.lines || [], (l) => l.name),
    planes: assign('Planes', analysis?.planes || [], (p) => p.name),
  };
}

/** 前回の書き出しのマニフェストから「ファイル → 本・線・面の ID」を読む */
export async function loadVaultOwners(readExisting, root = 'Highlights') {
  try {
    return JSON.parse((await readExisting(`${root}/${MANIFEST}`)) || '{}').owners || {};
  } catch {
    return {};
  }
}

const link = (path, label) => `[[${path}|${String(label).replace(/[[\]|]/g, ' ')}]]`;
const tagify = (t) => '#' + String(t).trim().replace(/\s+/g, '_').replace(/[^\p{L}\p{N}_/-]/gu, '');

function frontmatter(obj) {
  const lines = ['---'];
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) lines.push(`${k}: [${v.map(yamlString).join(', ')}]`);
    else if (typeof v === 'number' || typeof v === 'boolean') lines.push(`${k}: ${v}`);
    else lines.push(`${k}: ${yamlString(v)}`);
  }
  lines.push('---', '');
  return lines.join('\n');
}

function managed(body, tail = '## 自分のメモ\n\n') {
  return `${START}\n${body.trim()}\n${END}\n\n${tail}`;
}

function locationLabel(h) {
  const parts = [];
  if (h.location != null) parts.push(`位置 ${h.location}${h.locationEnd && h.locationEnd !== h.location ? '-' + h.locationEnd : ''}`);
  if (h.page) parts.push(`p.${h.page}`);
  const d = isoDate(h.createdAt);
  if (d) parts.push(d);
  return parts.join(' · ');
}

function quoteLines(text) {
  // 引用文中のコードフェンス（```dataviewjs など）が Obsidian で実行されないよう無害化する
  return String(text)
    .replace(/^(\s*)(`{3,}|~{3,})/gm, (m, sp, fence) => sp + fence[0] + '\u200b' + fence.slice(1))
    .split('\n')
    .map((l) => (l.trim() ? `> ${l}` : '>'))
    .join('\n');
}

export function renderHighlight(h) {
  const title = [h.favorite ? '★' : '', locationLabel(h) || SOURCES[h.source] || ''].filter(Boolean).join(' ');
  const out = [`> [!quote]${h.kind === 'note' ? '-' : ''} ${title}`.trimEnd(), quoteLines(h.text)];
  if (h.note) out.push('>', quoteLines(`**メモ:** ${h.note}`));
  if (h.userNote) out.push('>', quoteLines(`**自分のメモ:** ${h.userNote}`));
  if (h.tags?.length) out.push('>', `> ${h.tags.map(tagify).join(' ')}`);
  out.push('', `^${h.id}`);
  return out.join('\n');
}

export function renderBookNote(library, book, paths, analysis) {
  const hs = bookHighlights(library, book.id);
  const last = hs.map((h) => h.createdAt || h.importedAt).filter(Boolean).sort().pop();
  const fm = frontmatter({
    title: book.title,
    author: book.author,
    sources: book.sources.map((s) => SOURCES[s] || s),
    highlights: hs.length,
    last_highlighted: isoDate(last),
    asin: book.asin,
    bh_id: book.id,
    tags: ['book-highlights/book'],
  });
  const body = [];
  const meta = [book.author && `著者: ${book.author}`, `ソース: ${book.sources.map((s) => SOURCES[s] || s).join(' / ')}`, `ハイライト ${hs.length} 件`].filter(Boolean);
  body.push(meta.join(' ｜ '), '');
  // この本の点がつながっている線
  const lineIds = (analysis?.lines || []).filter((l) => l.highlightIds.some((id) => hs.some((h) => h.id === id)));
  if (lineIds.length) {
    body.push('**この本から伸びる線:** ' + lineIds.map((l) => link(paths.lines[l.id], l.name)).join('、'), '');
  }
  let chapter = null;
  for (const h of hs) {
    if (h.chapter && h.chapter !== chapter) {
      chapter = h.chapter;
      body.push(`## ${chapter.replace(/\n/g, ' ')}`, '');
    }
    body.push(renderHighlight(h), '');
  }
  return `${fm}# ${book.title}\n\n${managed(body.join('\n'))}`;
}

export function renderIndex(library, paths, analysis) {
  const books = listBooks(library);
  const total = books.reduce((s, b) => s + b.count, 0);
  const body = [
    `本 ${books.length} 冊 ｜ 点（ハイライト） ${total} 件`,
    '',
    analysis ? `- 立体: ${link(paths.map, '知識マップ')}（${link(paths.map + '.canvas', 'Canvas')}）\n- 面 ${analysis.planes.length} ｜ 線 ${analysis.lines.length}\n- ${link(paths.recommendations, 'おすすめの本')}\n` : '> AI 分析はまだです。`bh analyze` か Web アプリの「AI」タブで実行できます。\n',
    '## 本',
    '',
    '| 本 | 著者 | ソース | 点 | 最終 |',
    '| --- | --- | --- | ---: | --- |',
    ...books.map((b) => `| ${link(paths.books[b.id], b.title)} | ${b.author.replace(/\|/g, '/')} | ${b.sources.map((s) => SOURCES[s] || s).join(', ')} | ${b.count} | ${isoDate(b.lastHighlightedAt)} |`),
  ];
  return `${frontmatter({ tags: ['book-highlights/index'] })}# ハイライト索引\n\n${managed(body.join('\n'), '')}`;
}

export function renderLineNote(library, analysis, line, paths) {
  const plane = analysis.planes.find((p) => p.lineIds.includes(line.id));
  const hs = line.highlightIds.map((id) => library.highlights[id]).filter((h) => h && !h.deleted);
  const byBook = new Map();
  for (const h of hs) {
    if (!byBook.has(h.bookId)) byBook.set(h.bookId, []);
    byBook.get(h.bookId).push(h);
  }
  const fm = frontmatter({ bh_layer: '線', plane: plane?.name, points: hs.length, books: byBook.size, keywords: line.keywords, tags: ['book-highlights/line'] });
  const body = [
    `> [!abstract] 抽象化（線）`,
    quoteLines(line.summary || ''),
    '',
  ];
  if (line.insight) body.push(`**問い・示唆:** ${line.insight}`, '');
  if (plane) body.push(`**面:** ${link(paths.planes[plane.id], plane.name)}`, '');
  body.push(`## つながっている点（${hs.length}）`, '');
  for (const [bookId, list] of byBook) {
    const book = library.books[bookId];
    if (!book || !paths.books[bookId]) continue;
    body.push(`### ${link(paths.books[bookId], book.title)}`, '');
    for (const h of list) body.push(`![[${paths.books[bookId]}#^${h.id}]]`, '');
  }
  const siblings = plane ? plane.lineIds.filter((id) => id !== line.id) : [];
  if (siblings.length) {
    body.push('## 同じ面の線', '');
    for (const id of siblings) {
      const s = analysis.lines.find((l) => l.id === id);
      if (s) body.push(`- ${link(paths.lines[id], s.name)}`);
    }
  }
  return `${fm}# ${line.name}\n\n${managed(body.join('\n'))}`;
}

export function renderPlaneNote(library, analysis, plane, paths) {
  const lines = plane.lineIds.map((id) => analysis.lines.find((l) => l.id === id)).filter(Boolean);
  const bookIds = new Set(lines.flatMap((l) => l.highlightIds.map((id) => library.highlights[id]?.bookId)).filter(Boolean));
  const fm = frontmatter({ bh_layer: '面', lines: lines.length, books: bookIds.size, tags: ['book-highlights/plane'] });
  const body = [`> [!summary] テーマ（面）`, quoteLines(plane.summary || ''), ''];
  body.push(`## 線（${lines.length}）`, '');
  for (const l of lines) body.push(`- ${link(paths.lines[l.id], l.name)} — ${l.summary.split('\n')[0]}`);
  const rels = (analysis.solid?.relations || []).filter((r) => r.from === plane.id || r.to === plane.id);
  if (rels.length) {
    body.push('', '## 他の面との関係', '');
    for (const r of rels) {
      const other = analysis.planes.find((p) => p.id === (r.from === plane.id ? r.to : r.from));
      if (other) body.push(`- ${link(paths.planes[other.id], other.name)}: ${r.description}`);
    }
  }
  body.push('', '## 関わる本', '');
  for (const id of bookIds) if (paths.books[id]) body.push(`- ${link(paths.books[id], library.books[id].title)}`);
  body.push('', `立体: ${link(paths.map, '知識マップ')}`);
  return `${fm}# ${plane.name}\n\n${managed(body.join('\n'))}`;
}

export function renderMapNote(library, analysis, paths) {
  const s = analysis.solid || {};
  const fm = frontmatter({ bh_layer: '立体', analyzed_at: isoDate(analysis.createdAt), model: analysis.model?.chat, planes: analysis.planes.length, lines: analysis.lines.length, points: analysis.stats?.points, tags: ['book-highlights/map'] });
  const body = [];
  body.push(`> [!tip] ${s.title || '知識の核'}`, quoteLines(s.core || ''), '');
  body.push(`Canvas で見る: ${link(paths.map + '.canvas', '知識マップ.canvas')}`, '');
  body.push('## 面（テーマ）', '');
  for (const p of analysis.planes) {
    body.push(`### ${link(paths.planes[p.id], p.name)}`, '', p.summary || '', '');
    for (const id of p.lineIds) {
      const l = analysis.lines.find((x) => x.id === id);
      if (l) body.push(`- ${link(paths.lines[l.id], l.name)}（点 ${l.highlightIds.length}）`);
    }
    body.push('');
  }
  if (s.relations?.length) {
    body.push('## 面と面の関係', '');
    for (const r of s.relations) {
      const a = analysis.planes.find((p) => p.id === r.from);
      const b = analysis.planes.find((p) => p.id === r.to);
      if (a && b) body.push(`- ${link(paths.planes[a.id], a.name)} → ${link(paths.planes[b.id], b.name)}: ${r.description}`);
    }
    body.push('');
  }
  if (s.principles?.length) body.push('## 行動の原則', '', ...s.principles.map((p) => `- ${p}`), '');
  if (s.questions?.length) body.push('## これからの問い（知識の空白）', '', ...s.questions.map((q) => `- ${q}`), '');
  if (analysis.isolated?.length) body.push(`## まだつながっていない点`, '', `${analysis.isolated.length} 件の点は、まだどの線にもつながっていません。読書を重ねると線になるかもしれません。`, '');
  return `${fm}# 知識マップ（立体）\n\n${managed(body.join('\n'))}`;
}

export function renderRecommendations(analysis, paths, library = null) {
  const recs = analysis.recommendations || [];
  const kinds = { deepen: '深める', broaden: '広げる', challenge: '揺さぶる' };
  const fm = frontmatter({ generated_at: isoDate(analysis.recommendedAt || analysis.createdAt), tags: ['book-highlights/recommendations'] });
  const body = [`${link(paths.map, '知識マップ')}をもとに AI が選んだ本です。書誌データベースで見つけた実在の本から選び、見つけられなかったときは AI が挙げた書名を確認しています（「確認済み」が実在を確かめたもの）。`, ''];
  if (analysis.recommendationNote) body.push(`> [!warning] ${analysis.recommendationNote}`, '');
  for (const r of recs) {
    const plane = analysis.planes.find((p) => p.id === r.planeId);
    const v = r.verified;
    body.push(`## ${r.title}${r.author ? ` — ${r.author}` : ''}`, '');
    body.push(`- 種類: ${kinds[r.kind] || r.kind || '-'}${plane ? ` ｜ 面: ${link(paths.planes[plane.id], plane.name)}` : ''}`);
    body.push(`- 理由: ${r.reason}`);
    if (r.query) body.push(`- 探した言葉: ${r.query}`);
    const reaction = library && feedbackFor(library, r.title);
    if (reaction) body.push(`- あなたの反応: ${FEEDBACK_LABELS[reaction.status]}`);
    const url = /^https:\/\/[^\s()<>]+$/.test(v?.link || '') ? v.link : '';
    if (v) body.push(`- 確認済み（${v.source || '書誌データベース'}）: ${url ? `[${v.title.replace(/[[\]]/g, '')}](${url})` : v.title}${v.authors ? ' / ' + v.authors : ''}${v.publishedDate ? `（${v.publishedDate}）` : ''}${v.isbn ? ` ISBN ${v.isbn}` : ''}`);
    else if (r.verified === false) body.push('- ⚠ 書誌データベースで見つかりませんでした（実在を確認してください）');
    if (r.wishlist) {
      const w = r.wishlist;
      const asin = /^[A-Z0-9]{10}$/.test(w.asin || '') ? w.asin : '';
      body.push(`- 欲しい本に登録済み: ${w.ku ? 'Kindle Unlimited 対象' : Number.isInteger(w.price) ? `¥${w.price.toLocaleString('ja-JP')}` : '価格情報なし'}${asin ? `（[Amazon](https://www.amazon.co.jp/dp/${asin})）` : ''}`);
    }
    body.push('');
  }
  const want = library ? feedbackByStatus(library).want : [];
  if (want.length) body.push('## 読みたい本', '', ...want.map((f) => `- ${f.title}${f.author ? ` — ${f.author}` : ''}`), '');
  return `${fm}# おすすめの本\n\n${managed(body.join('\n'))}`;
}

/** 立体の配置（中心=核、周囲=面、その外側=線）。Canvas と Web アプリの図で共用 */
export function layoutKnowledgeMap(analysis) {
  const nodes = [];
  const edges = [];
  const planes = analysis.planes || [];
  nodes.push({ id: 'core', kind: 'core', x: 0, y: 0, label: analysis.solid?.title || '知識の核' });
  const R1 = 520;
  const R2 = 980;
  const total = Math.max(1, planes.reduce((s, p) => s + Math.max(1, p.lineIds.length), 0));
  let angle = -Math.PI / 2;
  for (const p of planes) {
    const span = (2 * Math.PI * Math.max(1, p.lineIds.length)) / total;
    const mid = angle + span / 2;
    nodes.push({ id: p.id, kind: 'plane', x: Math.cos(mid) * R1, y: Math.sin(mid) * R1, label: p.name, ref: p.id });
    edges.push({ from: 'core', to: p.id, kind: 'core' });
    p.lineIds.forEach((lid, i) => {
      const l = analysis.lines.find((x) => x.id === lid);
      if (!l) return;
      const a = angle + (span * (i + 0.5)) / p.lineIds.length;
      nodes.push({ id: l.id, kind: 'line', x: Math.cos(a) * R2, y: Math.sin(a) * R2, label: l.name, ref: l.id, weight: l.highlightIds.length });
      edges.push({ from: p.id, to: l.id, kind: 'plane' });
    });
    angle += span;
  }
  for (const r of analysis.solid?.relations || []) {
    if (planes.some((p) => p.id === r.from) && planes.some((p) => p.id === r.to)) edges.push({ from: r.from, to: r.to, kind: 'relation', label: r.type || '' });
  }
  return { nodes, edges };
}

export function renderCanvas(analysis, paths) {
  const { nodes, edges } = layoutKnowledgeMap(analysis);
  const size = { core: [460, 260], plane: [360, 140], line: [300, 90] };
  const color = { core: '6', plane: '5', line: '4' };
  const out = { nodes: [], edges: [] };
  for (const n of nodes) {
    const [w, h] = size[n.kind];
    const base = { id: n.id, x: Math.round(n.x - w / 2), y: Math.round(n.y - h / 2), width: w, height: h, color: color[n.kind] };
    if (n.kind === 'core') out.nodes.push({ ...base, type: 'text', text: `## ${analysis.solid?.title || '知識の核'}\n\n${analysis.solid?.core || ''}` });
    else if (n.kind === 'plane') out.nodes.push({ ...base, type: 'file', file: paths.planes[n.ref] + '.md' });
    else out.nodes.push({ ...base, type: 'file', file: paths.lines[n.ref] + '.md' });
  }
  edges.forEach((e, i) => {
    const edge = { id: `e${i}`, fromNode: e.from, toNode: e.to };
    if (e.kind === 'relation') Object.assign(edge, { label: e.label, color: '1', toEnd: 'arrow' });
    else edge.toEnd = 'none';
    out.edges.push(edge);
  });
  return JSON.stringify(out, null, 2);
}

/** Vault に書き出す全ファイル [{ path, content }]（path は .md/.canvas を含む Vault 相対パス） */
export function renderVault(library, analysis, { root = 'Highlights', owners = {} } = {}) {
  const hasAnalysis = analysis && analysis.lines?.length;
  const a = hasAnalysis ? analysis : null;
  const paths = vaultPaths(library, a, root, owners);
  const files = [{ path: paths.index + '.md', content: renderIndex(library, paths, a) }];
  for (const b of listBooks(library)) files.push({ path: paths.books[b.id] + '.md', content: renderBookNote(library, b, paths, a), id: b.id });
  if (a) {
    for (const l of a.lines) files.push({ path: paths.lines[l.id] + '.md', content: renderLineNote(library, a, l, paths), id: l.id });
    for (const p of a.planes) files.push({ path: paths.planes[p.id] + '.md', content: renderPlaneNote(library, a, p, paths), id: p.id });
    files.push({ path: paths.map + '.md', content: renderMapNote(library, a, paths) });
    files.push({ path: paths.map + '.canvas', content: renderCanvas(a, paths) });
    if (a.recommendations?.length || a.recommendationNote || feedbackByStatus(library).want.length) files.push({ path: paths.recommendations + '.md', content: renderRecommendations(a, paths, library) });
  }
  return files;
}

/**
 * 既存ノートに生成内容をマージする。
 * - bh:start〜bh:end の中身だけ差し替え、外側（自分のメモ）は残す
 * - frontmatter は生成したキーだけ更新し、ユーザーが足したキーは残す
 * - マーカーが無い既存ファイル（ユーザーが作った同名ノート）は null を返して上書きしない
 */
export function mergeManaged(existing, generated) {
  if (existing == null) return generated;
  const { text, restore } = normalizeNote(existing);
  if (!text.includes(END) || !START_RE.test(text)) return null;
  const gen = splitNote(generated);
  const cur = splitNote(text);
  const gStart = gen.body.search(START_RE);
  const gEnd = gen.body.indexOf(END) + END.length;
  const cStart = cur.body.search(START_RE);
  const cEnd = cur.body.indexOf(END) + END.length;
  const body = cur.body.slice(0, cStart) + gen.body.slice(gStart, gEnd) + cur.body.slice(cEnd);
  const fm = mergeFrontmatter(cur.fm, gen.fm);
  return restore((fm ? `---\n${fm}\n---\n` : '') + body);
}

/** 改行コード（CRLF）と BOM を外して読み、書き戻すときに元の形へ戻す */
function normalizeNote(text) {
  const bom = text.startsWith('\uFEFF') ? '\uFEFF' : '';
  const raw = bom ? text.slice(1) : text;
  const crlf = raw.includes('\r\n');
  return { text: raw.replace(/\r\n?/g, '\n'), restore: (s) => bom + (crlf ? s.replace(/\n/g, '\r\n') : s) };
}

function splitNote(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
  if (!m) return { fm: '', body: text };
  return { fm: m[1], body: text.slice(m[0].length) };
}

/**
 * frontmatter の最上位のキーを取り出す。Obsidian のプロパティ名は空白や記号を含められる
 * （例: `date read: 2024-05-01`、`"my rating": 5`）。字下げ・リスト・コメント・空行はキーではない
 */
function topLevelKey(line) {
  if (!line.trim() || /^[\s#-]/.test(line)) return null;
  const quoted = line.match(/^"((?:[^"\\]|\\.)*)"\s*:(?:\s|$)/) || line.match(/^'((?:[^']|'')*)'\s*:(?:\s|$)/);
  if (quoted) return quoted[1];
  const plain = line.match(/^(.+?)\s*:(?:\s|$)/);
  return plain ? plain[1] : null;
}

/** frontmatter を「キー 1 つ分の行のまとまり」に分ける。コメント行は独立したまとまりにして残す */
function fmEntries(fm) {
  const entries = [];
  for (const line of fm.split('\n')) {
    const key = topLevelKey(line);
    const comment = /^#/.test(line);
    if (key !== null || comment || !entries.length) entries.push({ key: key ?? '', comment, lines: [line] });
    else entries[entries.length - 1].lines.push(line);
  }
  return entries;
}

/** `key: [a, "b"]` / `key: a` / 次の行からの `- a` のどれでも値の配列にする */
function yamlList(entry) {
  const unquote = (v) => {
    const t = v.trim();
    if (/^".*"$/.test(t)) {
      try {
        return JSON.parse(t);
      } catch {
        return t.slice(1, -1);
      }
    }
    if (/^'.*'$/.test(t)) return t.slice(1, -1).replace(/''/g, "'");
    return t;
  };
  const line0 = entry.lines[0];
  const first = line0.slice(line0.search(/:(?:\s|$)/) + 1).trim();
  const values = [];
  const items = (str) => [...str.matchAll(/\s*("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^,]+)/g)].map((m) => unquote(m[1]));
  if (first.startsWith('[')) values.push(...items(first.replace(/^\[|\]$/g, '')));
  else if (first) values.push(...items(first));
  for (const line of entry.lines.slice(1)) {
    const item = line.match(/^\s*-\s+(.*)$/);
    if (item) values.push(unquote(item[1]));
  }
  return values.filter(Boolean);
}

function mergeFrontmatter(current, generated) {
  if (!current) return generated;
  const gen = fmEntries(generated);
  const cur = fmEntries(current);
  const genKeys = new Set(gen.map((e) => e.key).filter(Boolean));
  const curTags = cur.find((e) => e.key === 'tags');
  // 生成キーは生成順で先頭に置き、ユーザーのキー・コメントは後ろに残す。tags は自分で足したタグと合わせる
  const head = gen.map((e) => {
    if (e.key !== 'tags' || !curTags) return e.lines.join('\n');
    const tags = [...new Set([...yamlList(e), ...yamlList(curTags)])];
    return `tags: [${tags.map(yamlString).join(', ')}]`;
  });
  const rest = cur.filter((e) => !e.key || !genKeys.has(e.key)).map((e) => e.lines.join('\n'));
  return [...head, ...rest].join('\n');
}

/**
 * Vault への書き込み計画を立てる（CLI と File System Access API の両方で使う）
 * readExisting(path) は既存内容（無ければ null）を返す非同期関数。
 * 前回のマニフェストにあって今回生成されないファイルは、自分のメモが無ければ削除候補にする。
 */
export async function planVaultWrite(files, readExisting, root = 'Highlights') {
  const manifestPath = `${root}/${MANIFEST}`;
  let previous = [];
  let previousOwners = {};
  try {
    const m = JSON.parse((await readExisting(manifestPath)) || '{}');
    previous = m.files || [];
    previousOwners = m.owners || {};
  } catch {
    previous = [];
  }
  const writes = [];
  const skipped = [];
  let unchanged = 0;
  for (const f of files) {
    const existing = await readExisting(f.path);
    if (f.path.endsWith('.canvas')) {
      if (existing !== f.content) writes.push(f);
      else unchanged++;
      continue;
    }
    const merged = mergeManaged(existing, f.content);
    if (merged == null) skipped.push(f.path);
    else if (merged !== existing) writes.push({ path: f.path, content: merged });
    else unchanged++;
  }
  const current = new Set(files.map((f) => f.path));
  const deletes = [];
  const orphaned = [];
  for (const p of previous) {
    if (current.has(p)) continue;
    const existing = await readExisting(p);
    if (existing == null) continue;
    if (p.endsWith('.canvas') || !hasUserContent(existing)) deletes.push(p);
    else orphaned.push(p);
  }
  // どのファイルがどの本・線・面のものかも残す（次回も同じファイルを使うため。残したノートの持ち主も覚えておく）
  const owners = {};
  for (const p of orphaned) if (previousOwners[p]) owners[p] = previousOwners[p];
  for (const f of files) if (f.id) owners[f.path] = f.id;
  const sortedOwners = Object.fromEntries(Object.entries(owners).sort(([a], [b]) => a.localeCompare(b)));
  const manifest = { path: manifestPath, content: JSON.stringify({ generator: 'book-highlights', files: [...current, ...orphaned].sort(), owners: sortedOwners }, null, 2) };
  return { writes: [...writes, manifest], deletes, skipped, orphaned, unchanged, owners: sortedOwners };
}

// このツールがノートの種類ごとに frontmatter に書くキー（これ以外のキーがあればユーザーが足したもの）
const GENERATED_KEYS = {
  book: new Set(['title', 'author', 'sources', 'highlights', 'last_highlighted', 'asin', 'bh_id', 'tags']),
  line: new Set(['bh_layer', 'plane', 'points', 'books', 'keywords', 'tags']),
  plane: new Set(['bh_layer', 'lines', 'books', 'tags']),
  map: new Set(['bh_layer', 'analyzed_at', 'model', 'planes', 'lines', 'points', 'tags']),
  other: new Set(['generated_at', 'tags']),
};

function generatedKeysFor(entries) {
  const value = (key) => {
    const e = entries.find((x) => x.key === key);
    return e ? yamlList(e)[0] || '' : '';
  };
  if (entries.some((e) => e.key === 'bh_id')) return GENERATED_KEYS.book;
  return { 線: GENERATED_KEYS.line, 面: GENERATED_KEYS.plane, 立体: GENERATED_KEYS.map }[value('bh_layer')] || GENERATED_KEYS.other;
}

/** 自動生成部分の外に、ユーザーが書いたものが残っているか（あれば削除しない） */
function hasUserContent(raw) {
  const { fm, body } = splitNote(normalizeNote(raw).text);
  const start = body.search(START_RE);
  const end = body.indexOf(END);
  if (start < 0 || end < 0) return true;
  const entries = fmEntries(fm);
  const generated = generatedKeysFor(entries);
  for (const e of entries) {
    if (e.comment || (e.key && !generated.has(e.key))) return true;
    if (e.key === 'tags' && yamlList(e).some((t) => !String(t).startsWith('book-highlights/'))) return true;
  }
  const before = body.slice(0, start).replace(/^# .*$/m, '').trim();
  const after = body.slice(end + END.length).replace('## 自分のメモ', '').trim();
  return before.length > 0 || after.length > 0;
}
