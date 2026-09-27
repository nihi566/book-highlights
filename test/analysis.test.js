import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyLibrary, mergeParsed } from '../web/core/model.js';
import { SAMPLE_BOOKS } from '../web/core/sample.js';
import { createLlmClient, extractJson } from '../web/core/analysis/llm.js';
import { analyzeLibrary, deserializeCache, emptyCache, serializeCache } from '../web/core/analysis/pipeline.js';
import { dot, groupPoints, kmeans, tfidfEmbed } from '../web/core/analysis/vectors.js';
import { matchVolume, parseNdlRss, verifyBooks } from '../web/core/analysis/recommend.js';
import { recommendBooks } from '../web/core/analysis/pipeline.js';
import { renderVault } from '../web/core/obsidian.js';
import { startFakeLlm } from './helpers/fake-llm.js';

function sampleLibrary() {
  const lib = emptyLibrary();
  mergeParsed(lib, SAMPLE_BOOKS);
  return lib;
}

test('tfidfEmbed: 似た文は近く、無関係な文は遠い', () => {
  const [a, b, c] = tfidfEmbed(['注意は最も希少な資源である', '注意という資源を守る', '今日の夕飯はカレーだった', '別の文章', '資源の話']);
  assert.ok(dot(a, b) > dot(a, c));
  assert.ok(Math.abs(dot(a, a) - 1) < 1e-5);
});

test('kmeans / groupPoints: シード固定で再現可能、全ての点がどこかに属する', () => {
  const texts = SAMPLE_BOOKS.flatMap((b) => b.highlights.map((h) => h.text));
  const vecs = tfidfEmbed(texts);
  const r1 = kmeans(vecs, 6);
  const r2 = kmeans(vecs, 6);
  assert.deepEqual(r1.assign, r2.assign);
  const { groups, isolated } = groupPoints(vecs, { targetSize: 5 });
  const all = [...groups.flat(), ...isolated].sort((x, y) => x - y);
  assert.deepEqual(all, [...texts.keys()]);
  assert.ok(groups.every((g) => g.length >= 2));
});

test('extractJson: 思考タグ・コードフェンス・前置きの文章に強い', () => {
  assert.deepEqual(extractJson('{"a":1}'), { a: 1 });
  assert.deepEqual(extractJson('<think>{"x":0}</think>\n```json\n{"a":"b}"}\n```'), { a: 'b}' });
  assert.deepEqual(extractJson('はい、こちらです: {"a":[1,2,{"b":"}"}]} 以上です'), { a: [1, 2, { b: '}' }] });
  assert.equal(extractJson('JSON はありません'), undefined);
});

test('LLM クライアント: json_schema を拒否するサーバでは json_object に切り替える', async () => {
  const fake = await startFakeLlm({ rejectJsonSchema: true, wrapInThink: true });
  try {
    const llm = createLlmClient({ baseUrl: fake.url + '/v1/', chatModel: 'fake-chat', embedModel: 'fake-embed' });
    assert.deepEqual(await llm.listModels(), ['fake-chat', 'fake-embed']);
    const r = await llm.chatJson({ system: 's', user: '面の名前', schema: { type: 'object' }, name: 'plane' });
    assert.match(r.name, /^テーマ/);
    assert.equal(fake.calls.bodies[0].response_format.type, 'json_schema');
    assert.equal(fake.calls.bodies[1].response_format.type, 'json_object');
    assert.equal(fake.calls.bodies[1].reasoning_effort, 'none');
    const v = await llm.embed(['a', 'b', 'c'], { batchSize: 2 });
    assert.equal(v.length, 3);
    assert.equal(fake.calls.embed, 2);
  } finally {
    await fake.close();
  }
});

test('LLM クライアント: 接続できないときは分かりやすいエラー', async () => {
  const llm = createLlmClient({ baseUrl: 'http://127.0.0.1:9', chatModel: 'x' });
  await assert.rejects(llm.chatJson({ system: 's', user: 'u' }), /LLM サーバに接続できません/);
});

test('analyzeLibrary: 点→線→面→立体→おすすめ。キャッシュで 2 回目は LLM を呼ばない', async () => {
  const fake = await startFakeLlm();
  try {
    const lib = sampleLibrary();
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat', embedModel: 'fake-embed' });
    // Google Books の検索: 「習慣 科学」では既読の本（除かれる）と実在する候補を返す
    const verifyFetch = async (url) => {
      const q = decodeURIComponent(new URL(url).searchParams.get('q') || '');
      const vol = (id, title, authors) => ({ id, volumeInfo: { title, authors, publishedDate: '2020', infoLink: `https://books.google.com/?id=${id}`, description: '説明' } });
      const items = q.includes('習慣') ? [vol('r', '小さな習慣の力', ['山田 太郎']), vol('a', '習慣の科学', ['著者 A']), vol('b', '行動を変える技術', ['著者 B'])] : q.includes('哲学') ? [vol('c', '哲学の入口', ['著者 C'])] : [];
      return new Response(JSON.stringify({ items }));
    };
    const stages = new Set();
    const cache = emptyCache();
    const { analysis } = await analyzeLibrary({ library: lib, llm, cache, onProgress: (p) => stages.add(p.stage), options: { fetchImpl: verifyFetch } });
    assert.deepEqual([...stages], ['embed', 'lines', 'planes', 'solid', 'recommend']);
    const lineIds = new Set(analysis.lines.map((l) => l.id));
    // 全ての点は、いずれかの線か「まだつながらない点」に入る
    const covered = new Set([...analysis.lines.flatMap((l) => l.highlightIds), ...analysis.isolated]);
    assert.equal(covered.size, 48);
    assert.ok(analysis.lines.length >= 3);
    // 全ての線はちょうど 1 つの面に属する
    const inPlanes = analysis.planes.flatMap((p) => p.lineIds);
    assert.equal(inPlanes.length, lineIds.size);
    assert.deepEqual(new Set(inPlanes), lineIds);
    // 立体: 存在しない面 (P9) への関係は捨てる
    assert.ok(analysis.solid.relations.every((r) => analysis.planes.some((p) => p.id === r.from) && analysis.planes.some((p) => p.id === r.to)));
    assert.equal(analysis.solid.principles.length, 2);
    // おすすめ: 書誌 DB で見つけた実在の候補から選ぶ（既読の本は候補から除く・存在しない番号は捨てる）
    assert.deepEqual(analysis.recommendations.map((r) => [r.title, r.kind, r.query]), [
      ['行動を変える技術', 'deepen', '習慣 科学'],
      ['哲学の入口', 'challenge', '哲学 入門'],
    ]);
    assert.equal(analysis.recommendations[0].verified.source, 'Google Books');
    assert.equal(analysis.recommendations[0].verified.link, 'https://books.google.com/?id=b');
    const picks = fake.calls.bodies.find((b) => b.response_format?.json_schema?.name === 'picks');
    assert.match(picks.messages[1].content, /\[1\] 『習慣の科学』/);
    assert.doesNotMatch(picks.messages[1].content, /小さな習慣の力』/);
    assert.equal(analysis.recommendationNote, '');

    // キャッシュの保存と復元 → 2 回目は線・面・立体の LLM 呼び出しも埋め込みも無し（おすすめだけ）
    const restored = deserializeCache(JSON.parse(JSON.stringify(serializeCache(cache))));
    const before = { ...fake.calls };
    const again = await analyzeLibrary({ library: lib, llm, cache: restored, options: { recommend: false } });
    assert.equal(fake.calls.chat, before.chat);
    assert.equal(fake.calls.embed, before.embed + 1, '線の説明の埋め込みだけ');
    assert.deepEqual(again.analysis.lines.map((l) => l.name), analysis.lines.map((l) => l.name));

    // Vault 出力まで通る
    const files = renderVault(lib, analysis);
    assert.ok(files.some((f) => f.path === 'Highlights/Knowledge Map.md' && f.content.includes('知識の核')));
  } finally {
    await fake.close();
  }
});

test('analyzeLibrary: 埋め込みモデル無し（文字 n-gram）でも動く・点が少なすぎるとエラー', async () => {
  const fake = await startFakeLlm();
  try {
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const { analysis } = await analyzeLibrary({ library: sampleLibrary(), llm, options: { recommend: false } });
    assert.equal(analysis.model.embed, 'tfidf');
    assert.ok(analysis.lines.length > 0);
    const tiny = emptyLibrary();
    mergeParsed(tiny, [{ title: 'x', source: 'manual', highlights: [{ text: 'a' }] }]);
    await assert.rejects(analyzeLibrary({ library: tiny, llm }), /4 件以上/);
  } finally {
    await fake.close();
  }
});

test('おすすめ: 書誌 DB で検索できないときは LLM の書名を Google Books → 国立国会図書館サーチで確認する', async () => {
  const fake = await startFakeLlm();
  try {
    const lib = sampleLibrary();
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const analysis = { planes: [{ id: 'p1', name: '面1', summary: 's' }, { id: 'p2', name: '面2', summary: 's' }], solid: { core: 'c', questions: [] } };
    const ndl = `<rss><channel><item><title>実在する本</title><link>https://ndlsearch.ndl.go.jp/books/R1</link><category>図書</category><dc:title>実在する本</dc:title><dc:creator>著者, A, 1970-</dc:creator><dc:date xsi:type="dcterms:W3CDTF">2019</dc:date><dc:identifier xsi:type="dcndl:ISBN">978-4-00-000000-0</dc:identifier></item></channel></rss>`;
    const fetchImpl = async (url) => {
      if (url.startsWith('https://www.googleapis.com')) return new Response('rate limited', { status: 429 });
      const title = new URL(url).searchParams.get('title');
      return new Response(title === '実在する本' ? ndl : '<rss><channel></channel></rss>');
    };
    const recs = await recommendBooks({ library: lib, analysis, llm, fetchImpl });
    assert.ok(!recs.some((r) => r.title === '小さな習慣の力'), '既読は除く');
    assert.deepEqual(recs.map((r) => r.title), ['実在する本', '架空の本'], '確認できた本が先');
    assert.equal(recs[0].verified.source, '国立国会図書館サーチ');
    assert.equal(recs[0].verified.isbn, '9784000000000');
    assert.equal(recs[1].verified, false);
    // 既読の本を挙げたら、それを伝えてもう一度だけ頼む
    const recCalls = fake.calls.bodies.filter((b) => b.response_format?.json_schema?.name === 'recommendations');
    assert.equal(recCalls.length, 2);
    assert.match(recCalls[1].messages[1].content, /次の本も挙げてはいけない: 小さな習慣の力/);
  } finally {
    await fake.close();
  }
});

test('おすすめの失敗で分析全体は失われない', async () => {
  const fake = await startFakeLlm();
  try {
    const llm = createLlmClient({ baseUrl: fake.url, chatModel: 'fake-chat' });
    const broken = { ...llm, chatJson: async (p) => (p.name === 'searches' || p.name === 'recommendations' ? Promise.reject(new Error('JSON 形式の応答を得られませんでした')) : llm.chatJson(p)) };
    const { analysis } = await analyzeLibrary({ library: sampleLibrary(), llm: broken, options: { fetchImpl: async () => new Response('{}') } });
    assert.ok(analysis.lines.length > 0);
    assert.deepEqual(analysis.recommendations, []);
    assert.match(analysis.recommendationNote, /JSON 形式の応答を得られませんでした/);
  } finally {
    await fake.close();
  }
});

test('おすすめの実在確認: 書名・著者の照合、NDL の RSS、通信エラーは未確認のまま', async () => {
  const items = [{ title: '別の本', authors: 'X' }, { title: '深い集中 新版', authors: '佐藤 花子', link: 'https://x' }];
  assert.equal(matchVolume(items, { title: '深い集中', author: '佐藤花子' }).title, '深い集中 新版');
  assert.equal(matchVolume(items, { title: '深い集中', author: '別人' }), null);
  const rss = '<rss><item><category>記事</category><dc:title>記事は除く</dc:title></item><item><category>図書</category><dc:title>本 &amp; 本</dc:title><link>javascript:alert(1)</link><dc:creator>渡部, 昇一, 1930-2017</dc:creator></item></rss>';
  const parsed = parseNdlRss(rss);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].title, '本 & 本');
  assert.equal(parsed[0].authors, '渡部 昇一');
  assert.equal(parsed[0].link, '', 'https 以外のリンクは捨てる');
  const recs = await verifyBooks([{ title: 'a', author: 'b' }], { fetchImpl: async () => { throw new Error('offline'); } });
  assert.equal(recs[0].verified, undefined);
});

test('本の検索: Google Books が使えないときは国立国会図書館サーチで探す（ISBN あり・新しい順）', async () => {
  const { searchBooks } = await import('../web/core/analysis/recommend.js');
  const item = (title, date, isbn) => `<item><category>図書</category><dc:title>${title}</dc:title><link>https://ndlsearch.ndl.go.jp/books/${title}</link><dc:date>${date}</dc:date>${isbn ? `<dc:identifier xsi:type="dcndl:ISBN">${isbn}</dc:identifier>` : ''}</item>`;
  const urls = [];
  const fetchImpl = async (url) => {
    urls.push(url);
    if (url.startsWith('https://www.googleapis.com')) return new Response('', { status: 429 });
    const title = new URL(url).searchParams.get('title');
    if (title === '集中力 科学') return new Response('<rss></rss>');
    return new Response(`<rss>${item('古い本', '1999', '4-00-000000-0')}${item('ISBN の無い資料', '2024', '')}${item('新しい本', '2024', '978-4-00-000000-1')}</rss>`);
  };
  const books = await searchBooks('集中力 科学', { fetchImpl });
  assert.deepEqual(books.map((b) => b.title), ['新しい本', '古い本']);
  assert.equal(books[0].source, '国立国会図書館サーチ');
  assert.equal(urls.length, 3, 'Google → NDL（全ての語）→ NDL（最初の語）');
});
