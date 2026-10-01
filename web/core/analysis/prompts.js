// 「点 → 線 → 面 → 立体」とおすすめの本のためのプロンプトと JSON スキーマ
// 小さなローカルモデルでも崩れにくいよう、1 回の依頼は小さく・出力形式は固定にしている。

import { truncate } from '../text.js';

export const PROMPT_VERSION = 3;

const SYSTEM = `あなたは読書の知識を構造化する編集者です。
ユーザーが本に引いた線（ハイライト）を「点」と呼びます。点どうしの共通項を抽象化して「線（概念）」を作り、線を束ねて「面（テーマ）」を作り、面の関係から知識の全体像「立体」を組み立てます。
- 必ず日本語で書く
- 引用文をそのまま繰り返さず、一段抽象化した言葉で表す
- 指定された JSON だけを出力する（説明文やコードフェンスは付けない）`;

const str = { type: 'string' };
const strArray = { type: 'array', items: str };

export function linePrompt(points) {
  const list = points.map((p, i) => `[${i + 1}]『${truncate(p.book, 40)}』 ${truncate(p.text.replace(/\s+/g, ' '), 280)}${p.note ? `（読者のメモ: ${truncate(p.note, 120)}）` : ''}`).join('\n');
  return {
    system: SYSTEM,
    name: 'line',
    user: `次の点（ハイライト）は、意味が近いものとして集まりました。これらをつなぐ「線」を作ってください。

${list}

書名を並べるのではなく、点に共通する考えの中身を自分の言葉で書いてください。

出力する JSON:
{"name": "線の名前（15字以内の概念名）", "summary": "点に共通する考えを抽象化した説明（2〜3文）", "insight": "この線から生まれる問いや実践への示唆（1文）", "keywords": ["キーワード", "3〜5個"]}`,
    schema: {
      type: 'object',
      properties: { name: str, summary: str, insight: str, keywords: strArray },
      required: ['name', 'summary', 'insight', 'keywords'],
      additionalProperties: false,
    },
  };
}

export function planePrompt(lines) {
  const list = lines.map((l, i) => `[${i + 1}] ${l.name}: ${truncate(l.summary, 200)}`).join('\n');
  return {
    system: SYSTEM,
    name: 'plane',
    user: `次の線（概念）は近いものとして集まりました。これらを束ねる「面（テーマ）」を作ってください。

${list}

線の名前や本の名前を並べるのではなく、線に共通する考えの中身を自分の言葉で書いてください。

出力する JSON:
{"name": "面の名前（15字以内のテーマ名）", "summary": "線どうしがどう関わり合い、全体として何を語っているか（2〜4文）"}`,
    schema: {
      type: 'object',
      properties: { name: str, summary: str },
      required: ['name', 'summary'],
      additionalProperties: false,
    },
  };
}

export const RELATION_TYPES = ['支える', '対立する', '具体化する', '補完する'];

export function solidPrompt(planes) {
  const refs = planes.map((_, i) => `P${i + 1}`);
  const list = planes
    .map((p, i) => `P${i + 1}「${p.name}」: ${truncate(p.summary, 220)}\n  線: ${p.lines.map((l) => l.name).join('、')}`)
    .join('\n');
  return {
    system: SYSTEM,
    name: 'solid',
    user: `読者の知識は次の面（テーマ）でできています。面どうしの関係を見て、知識を「立体」として組み立ててください。

${list}

- relations の type は「支える」「対立する」「具体化する」「補完する」のどれか 1 つ
- principles は「〜する」で終わる、明日から実践できる行動の原則
- questions は「〜か？」で終わる、まだ答えの無い問い

出力する JSON:
{"title": "この知識全体に付ける名前（20字以内）",
 "core": "全ての面を貫く中心的な考え（3〜5文）",
 "relations": [{"from": "P1", "to": "P2", "type": "支える", "description": "関係の説明（1文）"}],
 "principles": ["行動の原則（3〜5個）"],
 "questions": ["まだ答えが無い問い（2〜4個）"]}`,
    schema: {
      type: 'object',
      properties: {
        title: str,
        core: str,
        relations: {
          type: 'array',
          items: {
            type: 'object',
            properties: { from: { type: 'string', enum: refs }, to: { type: 'string', enum: refs }, type: { type: 'string', enum: RELATION_TYPES }, description: str },
            required: ['from', 'to', 'type', 'description'],
            additionalProperties: false,
          },
        },
        principles: strArray,
        questions: strArray,
      },
      required: ['title', 'core', 'relations', 'principles', 'questions'],
      additionalProperties: false,
    },
  };
}

export const RECOMMEND_KINDS = ['deepen', 'broaden', 'challenge'];

/** おすすめへの反応から「好み」を伝える行（無ければ空） */
export function preferenceLines(prefs = {}) {
  const names = (list) => (list || []).slice(0, 20).map((f) => f.title).join('、');
  const lines = [];
  if (prefs.want?.length) lines.push(`- 読みたいと言った本: ${names(prefs.want)}（このような本を好む）`);
  if (prefs.no?.length) lines.push(`- 興味なしとした本: ${names(prefs.no)}（このような方向は避ける）`);
  return lines.join('\n');
}

export function recommendPrompt({ solid, planes, readTitles, count = 6, avoid = [], prefs = {} }) {
  const refs = planes.map((_, i) => `P${i + 1}`);
  const list = planes.map((p, i) => `P${i + 1}「${p.name}」: ${truncate(p.summary, 160)}`).join('\n');
  return {
    system: SYSTEM,
    name: 'recommendations',
    user: `読者の知識の全体像は次のとおりです。

核: ${solid?.core || '(なし)'}
面:
${list}
まだ答えが無い問い: ${(solid?.questions || []).join(' / ') || '(なし)'}

この読者に「次に」読んでほしい本を ${count} 冊選んでください。
- 読者がまだ読んでいない、実在する本だけを挙げる（書名と著者名は正確に。自信が無い本は挙げない）
- 読者が既に読んだ本（挙げてはいけない）: ${readTitles.slice(0, 60).join('、')}${avoid.length ? `\n- 次の本も挙げてはいけない: ${avoid.join('、')}` : ''}${preferenceLines(prefs) ? `\n${preferenceLines(prefs)}` : ''}
- 日本語で読める本を優先する
- kind は「deepen」（既存の面を掘り下げる）、「broaden」（隣の分野へつなぐ）、「challenge」（反対の立場・盲点を突く）をバランスよく

出力する JSON:
{"books": [{"title": "書名", "author": "著者名", "plane": "P1", "kind": "deepen", "reason": "この読者の知識に照らした推薦理由（1〜2文）"}]}`,
    schema: {
      type: 'object',
      properties: {
        books: {
          type: 'array',
          items: {
            type: 'object',
            properties: { title: str, author: str, plane: { type: 'string', enum: refs }, kind: { type: 'string', enum: RECOMMEND_KINDS }, reason: str },
            required: ['title', 'author', 'plane', 'kind', 'reason'],
            additionalProperties: false,
          },
        },
      },
      required: ['books'],
      additionalProperties: false,
    },
  };
}

/** おすすめ（書誌 DB を使う版）1: 本を探すための検索語を決める */
export function searchPrompt({ solid, planes, count = 5, prefs = {} }) {
  const refs = planes.map((_, i) => `P${i + 1}`);
  const list = planes.map((p, i) => `P${i + 1}「${p.name}」: ${truncate(p.summary, 160)}`).join('\n');
  return {
    system: SYSTEM,
    name: 'searches',
    user: `読者の知識の全体像は次のとおりです。

核: ${solid?.core || '(なし)'}
面:
${list}
まだ答えが無い問い: ${(solid?.questions || []).join(' / ') || '(なし)'}

この読者が次に読む本を書店で探すための検索語を ${count} 個考えてください。
- query は本のテーマを表す短い日本語（1〜3 語。例: 「習慣 行動科学」「ストア哲学」）${preferenceLines(prefs) ? `\n${preferenceLines(prefs)}` : ''}
- kind は「deepen」（既存の面を掘り下げる）、「broaden」（隣の分野へつなぐ）、「challenge」（反対の立場・盲点を突く）をバランスよく

出力する JSON:
{"searches": [{"query": "検索語", "plane": "P1", "kind": "deepen"}]}`,
    schema: {
      type: 'object',
      properties: {
        searches: {
          type: 'array',
          items: {
            type: 'object',
            properties: { query: str, plane: { type: 'string', enum: refs }, kind: { type: 'string', enum: RECOMMEND_KINDS } },
            required: ['query', 'plane', 'kind'],
            additionalProperties: false,
          },
        },
      },
      required: ['searches'],
      additionalProperties: false,
    },
  };
}

/** おすすめ（書誌 DB を使う版）2: 実在する候補の中から選ばせる */
export function pickPrompt({ solid, planes, candidates, count = 6, prefs = {} }) {
  const refs = planes.map((_, i) => `P${i + 1}`);
  const list = planes.map((p, i) => `P${i + 1}「${p.name}」: ${truncate(p.summary, 120)}`).join('\n');
  const books = candidates.map((c, i) => `[${i + 1}] 『${truncate(c.title, 60)}』${c.authors ? ` ${truncate(c.authors, 40)}` : ''}${c.publishedDate ? `（${String(c.publishedDate).slice(0, 4)}）` : ''}${c.description ? ` — ${truncate(c.description, 100)}` : ''}`).join('\n');
  return {
    system: SYSTEM,
    name: 'picks',
    user: `読者の知識の全体像:
核: ${solid?.core || '(なし)'}
面:
${list}

書店で見つけた本の候補:
${books}

候補の中から、この読者に次に読んでほしい本を最大 ${count} 冊選び、読者の知識に照らした理由を書いてください。
- candidate は候補の番号${preferenceLines(prefs) ? `\n${preferenceLines(prefs)}` : ''}
- kind は「deepen」（既存の面を掘り下げる）、「broaden」（隣の分野へつなぐ）、「challenge」（反対の立場・盲点を突く）

出力する JSON:
{"picks": [{"candidate": 1, "plane": "P1", "kind": "deepen", "reason": "推薦理由（1〜2文）"}]}`,
    schema: {
      type: 'object',
      properties: {
        picks: {
          type: 'array',
          items: {
            type: 'object',
            properties: { candidate: { type: 'integer' }, plane: { type: 'string', enum: refs }, kind: { type: 'string', enum: RECOMMEND_KINDS }, reason: str },
            required: ['candidate', 'plane', 'kind', 'reason'],
            additionalProperties: false,
          },
        },
      },
      required: ['picks'],
      additionalProperties: false,
    },
  };
}
