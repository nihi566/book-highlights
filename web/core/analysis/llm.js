// ローカル LLM クライアント（OpenAI 互換 API）
// Ollama / LM Studio / llama.cpp server / vLLM などに共通で使える。ブラウザと Node の両方で動く。
//
// baseUrl の例:
//   http://localhost:11434          … PC のブラウザから Ollama を直接使う
//   http://localhost:1234           … LM Studio
//   https://my-pc.tailXXXX.ts.net/llm … スマホから、PC のコンパニオンサーバ経由で使う

import { l2normalize } from './vectors.js';

export function normalizeBaseUrl(url) {
  return String(url || '')
    .trim()
    .replace(/\/+$/, '')
    .replace(/\/v1$/, '');
}

export class LlmError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = 'LlmError';
    this.status = status;
    this.body = body;
  }
}

export function createLlmClient({ baseUrl, chatModel, embedModel = '', apiKey = '', token = '', fetchImpl, timeoutMs = 300000, temperature = 0.3 } = {}) {
  const base = normalizeBaseUrl(baseUrl);
  const doFetch = fetchImpl || globalThis.fetch.bind(globalThis);
  // response_format の対応状況はサーバごとに違うので、失敗したら段階的に緩める
  // （LM Studio は json_object 不可、古いサーバは json_schema 不可）
  let jsonMode = 'json_schema';
  // 思考モデル（qwen3 など）は思考を切った方が速く、JSON も崩れにくい。Ollama の /v1 は reasoning_effort:"none" で切れる
  let sendReasoningOff = true;

  async function request(path, body, { method = body ? 'POST' : 'GET', signal } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
    if (token) headers['X-BH-Token'] = token;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('timeout')), timeoutMs);
    const onAbort = () => ctrl.abort(signal.reason);
    signal?.addEventListener('abort', onAbort);
    let res;
    try {
      res = await doFetch(base + path, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: ctrl.signal });
    } catch (e) {
      if (signal?.aborted) throw new LlmError('中止しました');
      throw new LlmError(`LLM サーバに接続できません (${base}): ${e.message}。サーバの起動と CORS 設定を確認してください。`);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    }
    const text = await res.text();
    if (!res.ok) throw new LlmError(`LLM サーバがエラーを返しました (HTTP ${res.status}): ${text.slice(0, 300)}`, { status: res.status, body: text });
    try {
      return JSON.parse(text);
    } catch {
      throw new LlmError(`LLM サーバの応答が JSON ではありません: ${text.slice(0, 200)}`);
    }
  }

  async function listModels() {
    try {
      const r = await request('/v1/models');
      return (r.data || []).map((m) => m.id).sort();
    } catch (e) {
      // 古い Ollama 向けのフォールバック
      try {
        const r = await request('/api/tags');
        return (r.models || []).map((m) => m.name).sort();
      } catch {
        throw e;
      }
    }
  }

  /** JSON を返させるチャット。schema を渡すと構造化出力を要求する */
  async function chatJson({ system, user, schema, name = 'result', signal, temperature: temp = temperature }) {
    const messages = [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ];
    for (let attempt = 0; attempt < 6; attempt++) {
      const body = { model: chatModel, messages, temperature: temp, stream: false };
      if (sendReasoningOff) body.reasoning_effort = 'none';
      if (jsonMode === 'json_schema' && schema) body.response_format = { type: 'json_schema', json_schema: { name, schema, strict: true } };
      else if (jsonMode === 'json_object') body.response_format = { type: 'json_object' };
      let r;
      try {
        r = await request('/v1/chat/completions', body, { signal });
      } catch (e) {
        if (e instanceof LlmError && e.status >= 400 && e.status < 500 && e.status !== 404) {
          if (sendReasoningOff && /reason|think/i.test(e.body || '')) {
            sendReasoningOff = false;
            continue;
          }
          if (jsonMode !== 'none') {
            jsonMode = jsonMode === 'json_schema' ? 'json_object' : 'none';
            continue;
          }
        }
        throw e;
      }
      const msg = r.choices?.[0]?.message || {};
      const content = msg.content || '';
      if (!content.trim() && jsonMode !== 'none') {
        // 構造化出力が思考側にだけ適用されて本文が空になるサーバへの対策
        jsonMode = jsonMode === 'json_schema' ? 'json_object' : 'none';
        continue;
      }
      const parsed = extractJson(content);
      if (parsed !== undefined) return parsed;
      // 壊れた JSON が返ったら 1 回だけ言い直させる
      messages.push({ role: 'assistant', content }, { role: 'user', content: '出力が JSON として読めませんでした。説明文を付けず、指定の形式の JSON だけを出力し直してください。' });
    }
    throw new LlmError('LLM から JSON 形式の応答を得られませんでした。より大きなモデルを試してください。');
  }

  /** テキスト配列 → 正規化済みベクトル配列 */
  async function embed(texts, { batchSize = 32, signal, onProgress } = {}) {
    if (!embedModel) throw new LlmError('埋め込みモデルが設定されていません');
    const out = [];
    for (let i = 0; i < texts.length; i += batchSize) {
      const batch = texts.slice(i, i + batchSize);
      const r = await request('/v1/embeddings', { model: embedModel, input: batch }, { signal });
      const data = (r.data || []).slice().sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
      if (data.length !== batch.length) throw new LlmError('埋め込みの件数が一致しません');
      for (const d of data) out.push(l2normalize(Float32Array.from(d.embedding)));
      onProgress?.(Math.min(texts.length, i + batch.length), texts.length);
    }
    return out;
  }

  return { baseUrl: base, chatModel, embedModel, listModels, chatJson, embed };
}

/** LLM の出力から JSON を取り出す（<think> タグやコードフェンス、前後の説明文に強い） */
export function extractJson(text) {
  let s = String(text ?? '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/^[\s\S]*?<\/think>/i, '')
    .trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) s = fence[1].trim();
  try {
    return JSON.parse(s);
  } catch {
    /* 下で部分抽出を試す */
  }
  const start = s.search(/[[{]/);
  if (start < 0) return undefined;
  const open = s[start];
  const close = open === '{' ? '}' : ']';
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === '\\') esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === open) depth++;
    else if (c === close && --depth === 0) {
      try {
        return JSON.parse(s.slice(start, i + 1));
      } catch {
        return undefined;
      }
    }
  }
  return undefined;
}
