// テスト用の OpenAI 互換 LLM サーバ（決定的な応答を返す）
import { createServer } from 'node:http';
import { hash } from '../../web/core/text.js';

function embedText(t, dims = 256) {
  const s = String(t).normalize('NFKC').replace(/[\s\p{P}]/gu, '');
  const v = new Array(dims).fill(0);
  for (let i = 0; i + 2 <= s.length; i++) v[parseInt(hash(s.slice(i, i + 2)).slice(-5), 36) % dims] += 1;
  const n = Math.sqrt(v.reduce((a, x) => a + x * x, 0)) || 1;
  return v.map((x) => x / n);
}

function answer(name, prompt) {
  const tag = hash(prompt).slice(0, 4);
  switch (name) {
    case 'line':
      return { name: `概念${tag}`, summary: `点に共通する考え（${tag}）。`, insight: '明日から試せる問い。', keywords: ['習慣', '集中', '学び'] };
    case 'plane':
      return { name: `テーマ${tag}`, summary: `線が束になったテーマ（${tag}）。` };
    case 'solid':
      return { title: '知識の核', core: '小さな仕組みが大きな変化を生む。', relations: [{ from: 'P1', to: 'P2', type: '支える', description: 'P1 が P2 を支える' }, { from: 'P1', to: 'P9', type: '対立する', description: '存在しない面' }], principles: ['仕組みを先に作る', '注意を守る'], questions: ['どうすれば続くのか'] };
    case 'recommendations':
      return { books: [{ title: '実在する本', author: '著者 A', plane: 'P1', kind: 'deepen', reason: '核を深める' }, { title: '小さな習慣の力', author: '山田 太郎', plane: 'P1', kind: 'deepen', reason: '既読なので除かれるべき' }, { title: '架空の本', author: '誰か', plane: 'P2', kind: 'broaden', reason: '広げる' }] };
    default:
      return {};
  }
}

/** options.rejectJsonSchema: json_schema を 400 で拒否（LM Studio 以外の古いサーバの再現） */
export async function startFakeLlm({ rejectJsonSchema = false, wrapInThink = false } = {}) {
  const calls = { chat: 0, embed: 0, bodies: [] };
  const server = createServer(async (req, res) => {
    let body = '';
    for await (const c of req) body += c;
    const json = body ? JSON.parse(body) : {};
    const send = (status, data) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(data));
    };
    if (req.url === '/v1/models') return send(200, { data: [{ id: 'fake-chat' }, { id: 'fake-embed' }] });
    if (req.url === '/v1/embeddings') {
      calls.embed++;
      return send(200, { data: json.input.map((t, index) => ({ index, embedding: embedText(t) })) });
    }
    if (req.url === '/v1/chat/completions') {
      calls.chat++;
      calls.bodies.push(json);
      if (rejectJsonSchema && json.response_format?.type === 'json_schema') return send(400, { error: 'unsupported response_format' });
      const prompt = json.messages.map((m) => m.content).join('\n');
      let name = json.response_format?.json_schema?.name;
      if (!name) name = /立体/.test(prompt) && /relations/.test(prompt) ? 'solid' : /"books"/.test(prompt) ? 'recommendations' : /面の名前/.test(prompt) ? 'plane' : 'line';
      let content = JSON.stringify(answer(name, prompt));
      if (wrapInThink) content = `<think>考え中</think>\n\`\`\`json\n${content}\n\`\`\``;
      return send(200, { choices: [{ message: { role: 'assistant', content } }] });
    }
    send(404, { error: 'not found' });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${server.address().port}`;
  return { url, calls, close: () => new Promise((r) => server.close(r)) };
}
