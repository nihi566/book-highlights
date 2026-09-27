import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyLibrary, mergeParsed } from '../web/core/model.js';
import { SAMPLE_BOOKS } from '../web/core/sample.js';
import { createLlmClient, extractJson } from '../web/core/analysis/llm.js';
import { analyzeLibrary, deserializeCache, emptyCache, serializeCache } from '../web/core/analysis/pipeline.js';
import { dot, groupPoints, kmeans, tfidfEmbed } from '../web/core/analysis/vectors.js';
import { matchVolume, verifyBooks } from '../web/core/analysis/recommend.js';
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
    const verifyFetch = async (url) => {
      const q = decodeURIComponent(new URL(url).searchParams.get('q'));
      const items = q.includes('実在する本') ? [{ id: 'v1', volumeInfo: { title: '実在する本', authors: ['著者 A'], publishedDate: '2020', infoLink: 'https://books.google.com/?id=v1' } }] : [];
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
    // おすすめ: 既読の本は除き、実在確認の結果を付ける
    const titles = analysis.recommendations.map((r) => r.title);
    assert.ok(!titles.includes('小さな習慣の力'));
    assert.equal(analysis.recommendations.find((r) => r.title === '実在する本').verified.title, '実在する本');
    assert.equal(analysis.recommendations.find((r) => r.title === '架空の本').verified, false);
    // 既読の本を挙げたら、それを伝えてもう一度だけ頼む
    const recCalls = fake.calls.bodies.filter((b) => b.response_format?.json_schema?.name === 'recommendations');
    assert.equal(recCalls.length, 2);
    assert.match(recCalls[1].messages[1].content, /次の本も挙げてはいけない: 小さな習慣の力/);
    assert.deepEqual(recCalls[0].response_format.json_schema.schema.properties.books.items.properties.kind.enum, ['deepen', 'broaden', 'challenge']);
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

test('おすすめの実在確認: 書名・著者の照合、通信エラーは未確認のまま', async () => {
  const items = [{ id: '1', volumeInfo: { title: '別の本', authors: ['X'] } }, { id: '2', volumeInfo: { title: '深い集中 新版', authors: ['佐藤 花子'], infoLink: 'https://x' } }];
  assert.equal(matchVolume(items, { title: '深い集中', author: '佐藤花子' }).title, '深い集中 新版');
  assert.equal(matchVolume(items, { title: '深い集中', author: '別人' }), null);
  const recs = await verifyBooks([{ title: 'a', author: 'b' }], { fetchImpl: async () => { throw new Error('offline'); } });
  assert.equal(recs[0].verified, undefined);
});
