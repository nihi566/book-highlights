// 「点 → 線 → 面 → 立体」の分析パイプライン
//
// 1. 点: ハイライトを埋め込みベクトルにする（埋め込みモデルが無ければ文字 n-gram の TF-IDF）
// 2. 線: 似た点をクラスタリングし、LLM が共通する考えを抽象化して名前と説明を付ける
// 3. 面: 線をクラスタリングし、LLM がテーマとしてまとめる
// 4. 立体: LLM が面どうしの関係・核となる考え・原則・問いを組み立てる
// 5. おすすめ: 立体と「問い」をもとに LLM が次の本を選び、書誌 DB で実在を確認する
//
// LLM の結果はメンバー構成のハッシュでキャッシュするので、再分析は変わった部分だけで済む。

import { liveHighlights } from '../model.js';
import { bookKey, hash } from '../text.js';
import { PROMPT_VERSION, RECOMMEND_KINDS, RELATION_TYPES, linePrompt, planePrompt, recommendPrompt, solidPrompt } from './prompts.js';
import { centroid, dot, groupLines, groupPoints, l2normalize, tfidfEmbed } from './vectors.js';
import { verifyBooks } from './recommend.js';

export const ANALYSIS_VERSION = 1;

export function emptyCache() {
  return { embeddings: { model: '', vectors: {} }, llm: {} };
}

/**
 * @param {object} p
 * @param {object} p.library
 * @param {object} p.llm        createLlmClient() の戻り値
 * @param {object} [p.cache]    emptyCache() 形式。呼び出し側で保存すると次回が速い
 * @param {function} [p.onProgress] ({ stage, done, total, message }) => void
 * @param {AbortSignal} [p.signal]
 * @param {object} [p.options]  { granularity: 点いくつで線 1 本か (既定 5), maxLines, recommend: bool, verify: bool, fetchImpl }
 */
export async function analyzeLibrary({ library, llm, cache = emptyCache(), onProgress = () => {}, signal, options = {} }) {
  const { granularity = 5, maxLines = 40, recommend = true, verify = true, recommendCount = 6, fetchImpl } = options;
  const points = liveHighlights(library).sort((a, b) => a.id.localeCompare(b.id));
  if (points.length < 4) throw new Error(`点（ハイライト）が ${points.length} 件しかありません。4 件以上取り込んでから分析してください。`);
  const check = () => {
    if (signal?.aborted) throw new Error('分析を中止しました');
  };

  // 1. 点 → ベクトル
  const texts = points.map((h) => (h.note ? `${h.text}\n${h.note}` : h.text));
  let vectors;
  let embedMethod;
  if (llm.embedModel) {
    if (cache.embeddings.model !== llm.embedModel) cache.embeddings = { model: llm.embedModel, vectors: {} };
    const missing = points.filter((h) => !cache.embeddings.vectors[h.id]);
    onProgress({ stage: 'embed', done: 0, total: missing.length, message: `点をベクトル化しています（${llm.embedModel}）` });
    if (missing.length) {
      const vecs = await llm.embed(
        missing.map((h) => texts[points.indexOf(h)]),
        { signal, onProgress: (done, total) => onProgress({ stage: 'embed', done, total, message: '点をベクトル化しています' }) },
      );
      missing.forEach((h, i) => (cache.embeddings.vectors[h.id] = vecs[i]));
    }
    vectors = points.map((h) => cache.embeddings.vectors[h.id]);
    embedMethod = llm.embedModel;
  } else {
    onProgress({ stage: 'embed', done: 0, total: 1, message: '点をベクトル化しています（文字 n-gram）' });
    vectors = tfidfEmbed(texts);
    embedMethod = 'tfidf';
  }
  check();

  // 2. 点 → 線
  const { groups, isolated } = groupPoints(vectors, { targetSize: granularity, maxGroups: maxLines });
  const lines = [];
  for (let gi = 0; gi < groups.length; gi++) {
    check();
    const members = groups[gi];
    const c = centroid(members.map((i) => vectors[i]));
    // プロンプトには中心に近い点から最大 12 件
    const ordered = [...members].sort((a, b) => dot(vectors[b], c) - dot(vectors[a], c));
    const sample = ordered.slice(0, 12).map((i) => ({ text: points[i].text, note: points[i].note, book: library.books[points[i].bookId]?.title || '' }));
    const ids = ordered.map((i) => points[i].id);
    const key = 'line:' + hash([PROMPT_VERSION, llm.chatModel, ...[...ids].sort()].join('|'));
    onProgress({ stage: 'lines', done: gi, total: groups.length, message: `点をつないで線を引いています（${gi + 1}/${groups.length}）` });
    let r = cache.llm[key];
    if (!r) {
      const p = linePrompt(sample);
      r = await llm.chatJson({ ...p, signal });
      cache.llm[key] = r;
    }
    lines.push({
      id: 'l' + hash([...ids].sort().join('|')),
      name: clean(r.name, 40) || `線 ${gi + 1}`,
      summary: clean(r.summary, 600),
      insight: clean(r.insight, 300),
      keywords: (Array.isArray(r.keywords) ? r.keywords : []).map((k) => clean(k, 30)).filter(Boolean).slice(0, 6),
      highlightIds: ids,
      bookIds: [...new Set(ids.map((id) => library.highlights[id].bookId))],
      vector: c,
    });
  }
  onProgress({ stage: 'lines', done: groups.length, total: groups.length, message: `線を ${lines.length} 本引きました` });
  if (!lines.length) throw new Error('点どうしのつながりが見つかりませんでした。ハイライトを増やしてから試してください。');
  dedupeNames(lines);

  // 3. 線 → 面（線の説明文の埋め込みがあればそれも使う）
  if (llm.embedModel) {
    const summaryVecs = await llm.embed(lines.map((l) => `${l.name}\n${l.summary}`), { signal });
    lines.forEach((l, i) => (l.vector = l2normalize(l.vector.map((x, j) => x + summaryVecs[i][j]))));
  }
  const planeGroups = groupLines(lines.map((l) => l.vector));
  const planes = [];
  for (let pi = 0; pi < planeGroups.length; pi++) {
    check();
    const ls = planeGroups[pi].map((i) => lines[i]);
    const key = 'plane:' + hash([PROMPT_VERSION, llm.chatModel, ...ls.map((l) => l.id + l.name).sort()].join('|'));
    onProgress({ stage: 'planes', done: pi, total: planeGroups.length, message: `線を束ねて面を作っています（${pi + 1}/${planeGroups.length}）` });
    let r = cache.llm[key];
    if (!r) {
      r = await llm.chatJson({ ...planePrompt(ls), signal });
      cache.llm[key] = r;
    }
    planes.push({ id: 'p' + hash(ls.map((l) => l.id).sort().join('|')), name: clean(r.name, 40) || `面 ${pi + 1}`, summary: clean(r.summary, 800), lineIds: ls.map((l) => l.id) });
  }
  dedupeNames(planes);

  // 4. 面 → 立体
  check();
  onProgress({ stage: 'solid', done: 0, total: 1, message: '面の関係から立体を組み立てています' });
  const planeInput = planes.map((p) => ({ ...p, lines: p.lineIds.map((id) => lines.find((l) => l.id === id)) }));
  const solidKey = 'solid:' + hash([PROMPT_VERSION, llm.chatModel, ...planes.map((p) => p.id + p.name)].join('|'));
  let s = cache.llm[solidKey];
  if (!s) {
    s = await llm.chatJson({ ...solidPrompt(planeInput), signal });
    cache.llm[solidKey] = s;
  }
  const planeRef = (ref) => planes[parseInt(String(ref).replace(/[^\d]/g, ''), 10) - 1]?.id;
  const solid = {
    title: clean(s.title, 60) || '知識の核',
    core: clean(s.core, 1200),
    relations: (Array.isArray(s.relations) ? s.relations : [])
      .map((r) => ({ from: planeRef(r.from), to: planeRef(r.to), type: RELATION_TYPES.find((t) => String(r.type).includes(t)) || '関連する', description: clean(r.description, 300) }))
      .filter((r) => r.from && r.to && r.from !== r.to),
    principles: strList(s.principles, 8, 300),
    questions: strList(s.questions, 6, 300),
  };
  onProgress({ stage: 'solid', done: 1, total: 1, message: '立体ができました' });

  const analysis = {
    version: ANALYSIS_VERSION,
    createdAt: new Date().toISOString(),
    model: { chat: llm.chatModel, embed: embedMethod },
    stats: { points: points.length, lines: lines.length, planes: planes.length, isolated: isolated.length },
    lines: lines.map(({ vector, ...l }) => l),
    planes,
    solid,
    isolated: isolated.map((i) => points[i].id),
    recommendations: [],
  };

  // 5. おすすめの本
  if (recommend) {
    analysis.recommendations = await recommendBooks({ library, analysis, llm, signal, onProgress, verify, count: recommendCount, fetchImpl });
    analysis.recommendedAt = new Date().toISOString();
    analysis.recommendationNote = recommendationNote(analysis.recommendations);
  }
  return { analysis, cache };
}

/** 分析済みの立体をもとにおすすめの本を選ぶ（単独でも再実行できる） */
export async function recommendBooks({ library, analysis, llm, signal, onProgress = () => {}, verify = true, count = 6, fetchImpl }) {
  onProgress({ stage: 'recommend', done: 0, total: 1, message: 'おすすめの本を選んでいます' });
  const readTitles = Object.values(library.books)
    .filter((b) => !b.deleted)
    .map((b) => b.title);
  const readKeys = new Set(readTitles.map(bookKey));
  const planeRef = (ref) => analysis.planes[parseInt(String(ref).replace(/[^\d]/g, ''), 10) - 1]?.id || null;
  const seen = new Set();
  const rejected = [];
  let recs = [];
  // 小さなモデルは既読の本を挙げがちなので、多めに頼み、足りなければ却下した本を伝えてもう一度だけ頼む
  for (let round = 0; round < 2 && recs.length < Math.ceil(count / 2); round++) {
    const p = recommendPrompt({ solid: analysis.solid, planes: analysis.planes, readTitles, count: count + 2, avoid: rejected });
    const r = await llm.chatJson({ ...p, signal, temperature: 0.5 + round * 0.2 });
    for (const b of Array.isArray(r?.books) ? r.books : []) {
      const rec = { title: clean(b.title, 120), author: clean(b.author, 80), planeId: planeRef(b.plane), kind: RECOMMEND_KINDS.includes(b.kind) ? b.kind : 'deepen', reason: clean(b.reason, 400) };
      const key = bookKey(rec.title);
      if (!rec.title || seen.has(key)) continue;
      seen.add(key);
      if (readKeys.has(key)) rejected.push(rec.title);
      else recs.push(rec);
    }
  }
  recs = recs.slice(0, count);
  if (verify) {
    onProgress({ stage: 'recommend', done: 0, total: 1, message: '書誌データベースで実在を確認しています' });
    recs = await verifyBooks(recs, { fetchImpl, signal });
  }
  onProgress({ stage: 'recommend', done: 1, total: 1, message: `おすすめの本を ${recs.length} 冊選びました` });
  return recs;
}

/** おすすめが選べなかったときの説明（画面と Obsidian に出す） */
export function recommendationNote(recs) {
  if (!recs.length) return 'おすすめを選べませんでした。モデルが既に読んだ本しか挙げなかった可能性があります。より大きなモデル（7B 以上）で「おすすめを選び直す」を試してください。';
  if (recs.every((r) => r.verified === false)) return '挙がった本はどれも書誌データベースで見つかりませんでした。実在しない本の可能性が高いので、より大きなモデルで選び直してください。';
  return '';
}

function clean(v, max) {
  const s = String(v ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

function strList(v, n, max) {
  return (Array.isArray(v) ? v : [])
    .map((x) => clean(typeof x === 'string' ? x : x?.text ?? JSON.stringify(x), max))
    .filter(Boolean)
    .slice(0, n);
}

function dedupeNames(items) {
  const seen = new Map();
  for (const it of items) {
    const k = it.name;
    const n = (seen.get(k) || 0) + 1;
    seen.set(k, n);
    if (n > 1) it.name = `${k} (${n})`;
  }
}

// キャッシュの保存用（Float32Array ↔ base64）
export function serializeCache(cache) {
  const vectors = {};
  for (const [id, v] of Object.entries(cache.embeddings?.vectors || {})) vectors[id] = toBase64(new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
  return { embeddings: { model: cache.embeddings?.model || '', vectors }, llm: cache.llm || {} };
}

export function deserializeCache(data) {
  if (!data) return emptyCache();
  const vectors = {};
  for (const [id, b64] of Object.entries(data.embeddings?.vectors || {})) {
    const bytes = fromBase64(b64);
    vectors[id] = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
  }
  return { embeddings: { model: data.embeddings?.model || '', vectors }, llm: data.llm || {} };
}

function toBase64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function fromBase64(b64) {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}
