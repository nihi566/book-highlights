// 「点 → 線 → 面 → 立体」の分析パイプライン
//
// 1. 点: ハイライトを埋め込みベクトルにする（埋め込みモデルが無ければ文字 n-gram の TF-IDF）
// 2. 線: 似た点をクラスタリングし、LLM が共通する考えを抽象化して名前と説明を付ける
// 3. 面: 線をクラスタリングし、LLM がテーマとしてまとめる
// 4. 立体: LLM が面どうしの関係・核となる考え・原則・問いを組み立てる
// 5. おすすめ: 立体と「問い」をもとに LLM が次の本を選び、書誌 DB で実在を確認する
//
// LLM の結果はメンバー構成のハッシュでキャッシュするので、再分析は変わった部分だけで済む。

import { feedbackByStatus, liveHighlights } from '../model.js';
import { bookKey, hash } from '../text.js';
import { PROMPT_VERSION, RECOMMEND_KINDS, RELATION_TYPES, linePrompt, pickPrompt, planePrompt, recommendPrompt, searchPrompt, solidPrompt } from './prompts.js';
import { centroid, dot, groupLines, groupPoints, l2normalize, tfidfEmbed } from './vectors.js';
import { searchBooks, verifyBooks } from './recommend.js';
import { titleKey, wishlistForRecommend } from '../wishlist.js';

// おすすめの候補に混ぜる欲しい本の冊数（多すぎると小さなモデルが選びきれない）
const WISHLIST_CANDIDATES = 8;

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
 * @param {object} [p.options]  { granularity: 点いくつで線 1 本か (既定 5), maxLines, recommend: bool, verify: bool, fetchImpl, wishlist: toRecommendWishlist() の結果 }
 */
export async function analyzeLibrary({ library, llm, cache = emptyCache(), onProgress = () => {}, signal, options = {} }) {
  const { granularity = 5, maxLines = 40, recommend = true, verify = true, recommendCount = 6, fetchImpl, wishlist } = options;
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
    // おすすめで失敗しても、ここまでの分析（線・面・立体）は捨てない
    try {
      analysis.recommendations = await recommendBooks({ library, analysis, llm, signal, onProgress, verify, count: recommendCount, fetchImpl, wishlist });
      analysis.recommendationNote = recommendationNote(analysis.recommendations);
    } catch (e) {
      if (signal?.aborted) throw e;
      analysis.recommendations = [];
      analysis.recommendationNote = `おすすめを選べませんでした（${e.message}）。「おすすめを選び直す」で再実行できます。`;
    }
    analysis.recommendedAt = new Date().toISOString();
  }
  return { analysis, cache };
}

/**
 * 分析済みの立体をもとにおすすめの本を選ぶ（単独でも再実行できる）
 * 1. 書誌 DB を使う版: LLM が検索語を決め → Google Books で実在する候補を集め → LLM が選んで理由を書く
 * 2. 書誌 DB で検索できないとき: LLM が書名を挙げ → Google Books / 国立国会図書館サーチで実在を確認
 */
export async function recommendBooks({ library, analysis, llm, signal, onProgress = () => {}, verify = true, count = 6, fetchImpl, wishlist = [] }) {
  // おすすめへの反応: 読んだ本は既読として扱い、反応済みの本はもう挙げない。読みたい／興味なしは好みとして伝える
  const prefs = feedbackByStatus(library);
  const readTitles = [...Object.values(library.books).filter((b) => !b.deleted).map((b) => b.title), ...prefs.read.map((f) => f.title)];
  const readKeys = new Set([...readTitles, ...prefs.want.map((f) => f.title), ...prefs.no.map((f) => f.title)].map(bookKey));
  // 欲しい本: 購入済み・読んだ本は書誌 DB で見つかっても出さない。残りは候補に混ぜる（書名はレーベル表記を落として照合）
  const wish = wishlistForRecommend(wishlist);
  const skipKeys = new Set(wish.filter((w) => w.skip).map((w) => titleKey(w.title)).filter(Boolean));
  const excluded = (title) => readKeys.has(bookKey(title)) || skipKeys.has(titleKey(title));
  const wishByKey = new Map(wish.filter((w) => !w.skip && titleKey(w.title)).map((w) => [titleKey(w.title), w]));
  const wishInfo = (w) => ({ asin: w.asin, price: w.price, ku: w.ku });
  const planeRef = (ref) => analysis.planes[parseInt(String(ref).replace(/[^\d]/g, ''), 10) - 1]?.id || null;
  const kindOf = (k) => (RECOMMEND_KINDS.includes(k) ? k : 'deepen');

  if (verify) {
    onProgress({ stage: 'recommend', done: 0, total: 3, message: '本を探す方向を考えています' });
    const s = await llm.chatJson({ ...searchPrompt({ solid: analysis.solid, planes: analysis.planes, count: Math.min(6, count), prefs }), signal });
    const searches = (Array.isArray(s?.searches) ? s.searches : []).map((x) => ({ query: clean(x.query, 40), plane: x.plane, kind: kindOf(x.kind) })).filter((x) => x.query).slice(0, 6);
    const candidates = [];
    const seen = new Set(readKeys);
    // 欲しい本のうち、知識の全体像に近い本を先に候補にする
    for (const w of closestWishlist([...wishByKey.values()].filter((w) => !excluded(w.title)), analysis, WISHLIST_CANDIDATES)) {
      seen.add(bookKey(w.title));
      seen.add(titleKey(w.title));
      candidates.push({ title: w.title, authors: '', description: `欲しい本に登録済み（${w.ku ? 'Kindle Unlimited 対象' : w.price === null ? '価格情報なし' : `¥${w.price}`}）`, wishlist: wishInfo(w) });
    }
    for (const [i, q] of searches.entries()) {
      onProgress({ stage: 'recommend', done: 1, total: 3, message: `書誌データベースで探しています（${i + 1}/${searches.length}: ${q.query}）` });
      try {
        const found = await searchBooks(q.query, { fetchImpl, signal });
        for (const b of found.slice(0, 5)) {
          const key = bookKey(b.title);
          if (!key || seen.has(key) || seen.has(titleKey(b.title)) || skipKeys.has(titleKey(b.title))) continue;
          seen.add(key);
          const w = wishByKey.get(titleKey(b.title));
          candidates.push({ ...b, search: q, ...(w ? { wishlist: wishInfo(w) } : {}) });
        }
      } catch (e) {
        if (signal?.aborted) throw e;
      }
    }
    if (candidates.length) {
      onProgress({ stage: 'recommend', done: 2, total: 3, message: `見つかった ${candidates.length} 冊から選んでいます` });
      const r = await llm.chatJson({ ...pickPrompt({ solid: analysis.solid, planes: analysis.planes, candidates, count, prefs }), signal, temperature: 0.3 });
      const picked = new Set();
      const recs = [];
      for (const p of Array.isArray(r?.picks) ? r.picks : []) {
        const c = candidates[Number(p.candidate) - 1];
        if (!c || picked.has(c)) continue;
        picked.add(c);
        // 欲しい本だけから来た候補は書誌 DB で確かめていない（search が無い）
        const { search, description, wishlist: wished, ...verified } = c;
        recs.push({ title: c.title, author: c.authors, planeId: planeRef(p.plane) || planeRef(search?.plane), kind: kindOf(p.kind || search?.kind), reason: clean(p.reason, 400), ...(search ? { query: search.query, verified } : {}), ...(wished ? { wishlist: wished } : {}) });
      }
      onProgress({ stage: 'recommend', done: 3, total: 3, message: `おすすめの本を ${recs.length} 冊選びました` });
      if (recs.length) return recs.slice(0, count);
    }
  }

  // 書誌 DB で候補を集められなかったとき: LLM に書名を挙げさせ、あとで実在を確認する
  onProgress({ stage: 'recommend', done: 0, total: 1, message: 'おすすめの本を選んでいます' });
  const seen = new Set();
  const rejected = [];
  let recs = [];
  // 小さなモデルは既読の本を挙げがちなので、多めに頼み、足りなければ却下した本を伝えてもう一度だけ頼む
  for (let round = 0; round < 2 && recs.length < Math.ceil(count / 2); round++) {
    const p = recommendPrompt({ solid: analysis.solid, planes: analysis.planes, readTitles, count: count + 2, avoid: rejected, prefs });
    const r = await llm.chatJson({ ...p, signal, temperature: 0.5 + round * 0.2 });
    for (const b of Array.isArray(r?.books) ? r.books : []) {
      const rec = { title: clean(b.title, 120), author: clean(b.author, 80), planeId: planeRef(b.plane), kind: kindOf(b.kind), reason: clean(b.reason, 400) };
      const key = bookKey(rec.title);
      if (!rec.title || seen.has(key)) continue;
      seen.add(key);
      const w = wishByKey.get(titleKey(rec.title));
      if (excluded(rec.title)) rejected.push(rec.title);
      else recs.push(w ? { ...rec, wishlist: wishInfo(w) } : rec);
    }
  }
  recs = recs.slice(0, count);
  if (verify) {
    onProgress({ stage: 'recommend', done: 0, total: 1, message: '書誌データベースで実在を確認しています' });
    recs = await verifyBooks(recs, { fetchImpl, signal });
    // 確認できた本を先に
    recs.sort((a, b) => (b.verified ? 1 : 0) - (a.verified ? 1 : 0));
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

/** 欲しい本のうち、知識の全体像（核・面）に書名が近い順に limit 冊（文字 n-gram の TF-IDF。LLM は使わない） */
function closestWishlist(books, analysis, limit) {
  if (books.length <= limit) return books;
  const profile = [analysis.solid?.core, ...analysis.planes.map((p) => `${p.name} ${p.summary}`)].filter(Boolean).join(' ');
  const [target, ...vectors] = tfidfEmbed([profile, ...books.map((b) => b.title)]);
  return books
    .map((b, i) => ({ b, score: dot(target, vectors[i]), i }))
    .sort((x, y) => y.score - x.score || x.i - y.i)
    .slice(0, limit)
    .map((x) => x.b);
}
