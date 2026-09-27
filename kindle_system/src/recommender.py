"""
src/recommender.py
-------------------
公開ページで付けた「見た」タグと★評価（run.py import-marks で book_marks に取り込んだもの）から、
手元で動く LLM にマンガ・本それぞれのおすすめを出してもらう（run.py recommend）。

- 問い合わせ先は LOCAL_LLM_URL だけ（既定は Ollama の http://localhost:11434）。
  LOCAL_LLM_API=openai にすると LM Studio / llama.cpp server 等の OpenAI 互換 API
  （/v1/chat/completions）を使う。読書記録を外部の有料 API へは送らない。
- マンガと本は別々に頼む（好みの傾向が違うため、互いの記録を混ぜない）。
- 候補は登録済み（book_mappings）のうち「見た」「読みたくない」以外の同じ種別の本。
  LLM が候補に無い ASIN を返しても採用しない（実在しない本・別の本の混入を防ぐ）。
"""

import json
import random
import re
from typing import Optional

import requests

from src.book_kind import KIND_BOOK, KIND_MANGA, KINDS, classify_kind

KIND_LABELS = {KIND_MANGA: "マンガ", KIND_BOOK: "本"}

DEFAULT_LLM_URL = "http://localhost:11434"
LLM_APIS = ("ollama", "openai")
DEFAULT_TIMEOUT_SECONDS = 600
_MODEL_LIST_TIMEOUT_SECONDS = 30

# 小さなローカルモデルの文脈長に収まるよう、プロンプトに載せる件数に上限を設ける
MAX_SEEN_IN_PROMPT = 60
MAX_UNWANTED_IN_PROMPT = 20
DEFAULT_MAX_CANDIDATES = 40
_OLLAMA_NUM_CTX = 8192
_TEMPERATURE = 0.7

# Ollama の構造化出力（format に JSON Schema を渡す）で使う応答の形
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "taste": {"type": "string"},
        "from_list": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"asin": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["asin", "reason"],
            },
        },
        "new_titles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["title", "reason"],
            },
        },
    },
    "required": ["taste", "from_list", "new_titles"],
}


class LocalLlmError(Exception):
    """ローカル LLM を使えないときのエラー。メッセージはそのまま利用者に表示する。"""


def _normalize_title(title) -> str:
    """重複・既読判定用に空白（全角含む）を除いたタイトル。"""
    return re.sub(r"\s+", "", title or "")


def _clean_text(value) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _kind_of(mark: dict, title: str) -> str:
    kind = (mark or {}).get("kind")
    return kind if kind in KINDS else classify_kind(title or "")


def summarize_seen(books: list, marks: dict) -> dict:
    """種別ごとの「見た」件数と★評価ありの件数を {kind: {"seen": n, "rated": n}} で返す。"""
    titles_by_asin = {book["asin"]: book.get("title") for book in books if book.get("asin")}
    summary = {kind: {"seen": 0, "rated": 0} for kind in KINDS}
    for asin, mark in marks.items():
        if mark.get("tag") != "seen":
            continue
        kind = _kind_of(mark, titles_by_asin.get(asin) or mark.get("title"))
        summary[kind]["seen"] += 1
        if mark.get("rating"):
            summary[kind]["rated"] += 1
    return summary


def collect_inputs(
    books: list,
    marks: dict,
    kind: str,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    rng: Optional[random.Random] = None,
) -> dict:
    """
    プロンプトに載せる材料を種別ごとに集める。

    books は repository.get_books(filter="all")、marks は repository.get_book_marks() の返り値。
    戻り値:
      seen:       [{"asin", "title", "rating"}]  … 「見た」作品（★の高い順）
      unwanted:   [title]                        … 「読みたくない」と付けた作品
      candidates: [{"asin", "title"}]            … おすすめ候補（登録済み・未読・同じ種別）

    「見た」が上限を超えるときは最近付けたものを優先する。候補は「読みたい」「購入済み」
    （積読）を先に、残りは rng で並べ替えてから上限まで載せる（候補が多い蔵書でも、実行の
    たびに違う一部を LLM に見せられるように）。同じタイトルの別 ASIN は1件にまとめる。
    """
    titles_by_asin = {book["asin"]: book.get("title") for book in books if book.get("asin")}

    seen, unwanted = [], []
    for asin, mark in marks.items():
        title = titles_by_asin.get(asin) or mark.get("title")
        if not title or _kind_of(mark, title) != kind:
            continue
        if mark.get("tag") == "seen":
            seen.append(
                {"asin": asin, "title": title, "rating": mark.get("rating"), "updated_at": mark.get("updated_at") or ""}
            )
        elif mark.get("tag") == "unwanted":
            unwanted.append(title)
    seen.sort(key=lambda item: item["updated_at"], reverse=True)
    seen = [
        {"asin": item["asin"], "title": item["title"], "rating": item["rating"]}
        for item in sorted(seen[:MAX_SEEN_IN_PROMPT], key=lambda item: (-(item["rating"] or 0), item["title"]))
    ]
    unwanted = sorted(unwanted)[:MAX_UNWANTED_IN_PROMPT]

    excluded_asins = {asin for asin, mark in marks.items() if mark.get("tag") in ("seen", "unwanted")}
    known_titles = {_normalize_title(item["title"]) for item in seen} | {_normalize_title(t) for t in unwanted}
    preferred, others = [], []
    for book in books:
        asin, title = book.get("asin"), book.get("title")
        if not asin or not title or asin in excluded_asins:
            continue
        mark = marks.get(asin) or {}
        key = _normalize_title(title)
        if key in known_titles or _kind_of(mark, title) != kind:
            continue
        known_titles.add(key)
        candidate = {"asin": asin, "title": title}
        if mark.get("tag") in ("wanted", "purchased") or book.get("is_purchased"):
            preferred.append(candidate)
        else:
            others.append(candidate)
    (rng or random.Random()).shuffle(others)
    candidates = (preferred + others)[: max(0, max_candidates)]

    return {"seen": seen, "unwanted": unwanted, "candidates": candidates}


def build_messages(kind: str, inputs: dict, *, count: int, new_count: int) -> list:
    """collect_inputs() の材料からチャット形式のプロンプト（system / user）を組み立てる。"""
    label = KIND_LABELS[kind]
    lines = [f"ユーザーが読んだ{label}と★評価（5段階、★5が最高）:"]
    for item in inputs["seen"]:
        stars = f"★{item['rating']}" if item["rating"] else "評価なし"
        lines.append(f"- {item['title']}（{stars}）")
    if inputs["unwanted"]:
        lines += ["", f"ユーザーが「読みたくない」と付けた{label}:"]
        lines += [f"- {title}" for title in inputs["unwanted"]]

    tasks = []
    if inputs["candidates"] and count > 0:
        lines += ["", f"おすすめ候補（ユーザーが登録済みで、まだ読んでいない{label}。[ ] 内は ASIN）:"]
        lines += [f"- [{item['asin']}] {item['title']}" for item in inputs["candidates"]]
        tasks.append(
            f"候補の中から好みに合う順に最大{count}件を選び、from_list に ASIN と理由（1〜2文）を入れる。"
            "候補に無い ASIN は使わない。"
        )
    else:
        tasks.append("from_list は空の配列にする。")
    if new_count > 0:
        tasks.append(
            f"候補以外で好みに合いそうな実在の{label}を最大{new_count}件、new_titles にタイトル・著者・理由（1〜2文）で入れる。"
            "読んだ作品と同じ作品は入れない。実在に自信が無い作品は入れない。"
        )
    else:
        tasks.append("new_titles は空の配列にする。")
    tasks.append(
        "taste に、評価から読み取れる好みの傾向を1〜2文で書く。"
        "★が高い作品に近いものを優先し、★が低い作品や読みたくない作品に近いものは避ける。"
    )
    lines += ["", "やること:"]
    lines += [f"{i}. {task}" for i, task in enumerate(tasks, 1)]
    lines += [
        "",
        "出力は次の形の JSON だけにする:",
        '{"taste": "...", "from_list": [{"asin": "...", "reason": "..."}], '
        '"new_titles": [{"title": "...", "author": "...", "reason": "..."}]}',
    ]

    system = (
        "あなたは日本語で答える読書アドバイザーです。ユーザーの読書記録と★評価から好みを読み取り、"
        "次に読む作品を提案します。理由は日本語で簡潔に書き、出力は指定された JSON だけにします。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def load_llm_settings(env, *, model: Optional[str] = None, timeout: Optional[int] = None) -> dict:
    """環境変数（LOCAL_LLM_API / LOCAL_LLM_URL / LOCAL_LLM_MODEL）と引数から接続設定を作る。"""
    api = (env.get("LOCAL_LLM_API") or "ollama").strip().lower()
    if api not in LLM_APIS:
        raise LocalLlmError(f"LOCAL_LLM_API は ollama / openai のどちらかにしてください（現在: {api!r}）。")
    url = (env.get("LOCAL_LLM_URL") or DEFAULT_LLM_URL).strip().rstrip("/")
    if api == "openai" and url.endswith("/v1"):
        url = url[: -len("/v1")]
    return {
        "api": api,
        "url": url,
        "model": (model or env.get("LOCAL_LLM_MODEL") or "").strip() or None,
        "timeout": timeout or DEFAULT_TIMEOUT_SECONDS,
    }


def _call(http, settings: dict, method: str, path: str, payload: Optional[dict] = None) -> dict:
    url = settings["url"] + path
    try:
        if method == "GET":
            response = http.get(url, timeout=_MODEL_LIST_TIMEOUT_SECONDS)
        else:
            response = http.post(url, json=payload, timeout=settings["timeout"])
    except requests.exceptions.ReadTimeout:
        raise LocalLlmError(
            f"ローカル LLM の応答が {settings['timeout']} 秒以内に返りませんでした。"
            "--timeout を延ばすか、--model で小さいモデルを指定してください。"
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        raise LocalLlmError(
            f"ローカル LLM（{settings['url']}）に接続できません。"
            "Ollama なら `ollama serve` で起動しているか、LOCAL_LLM_URL が正しいか確認してください。"
        )
    except (requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema, requests.exceptions.InvalidURL):
        raise LocalLlmError(
            f"LOCAL_LLM_URL の形式が正しくありません（{settings['url']}）。例: http://localhost:11434"
        )
    except requests.exceptions.RequestException as e:
        raise LocalLlmError(f"ローカル LLM（{settings['url']}）への問い合わせに失敗しました: {e}")
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:300]
        if response.status_code == 404 and payload and settings["api"] == "ollama":
            model = payload.get("model")
            raise LocalLlmError(
                f"モデル {model} がローカル LLM にありません。`ollama pull {model}` で取得するか、"
                f"`ollama list` に出るモデル名を --model で指定してください（{detail}）。"
            )
        raise LocalLlmError(f"ローカル LLM がエラーを返しました（HTTP {response.status_code}）: {detail}")
    try:
        data = response.json()
    except ValueError:
        raise LocalLlmError("ローカル LLM の応答を JSON として読めませんでした。LOCAL_LLM_API の設定を確認してください。")
    if not isinstance(data, dict):
        raise LocalLlmError("ローカル LLM の応答の形式が想定と違います。LOCAL_LLM_API の設定を確認してください。")
    return data


def resolve_model(settings: dict, http=requests) -> str:
    """使うモデル名を決める。指定が無ければローカル LLM に入っているチャット用の最初のモデルを使う。"""
    if settings["model"]:
        return settings["model"]
    if settings["api"] == "ollama":
        data = _call(http, settings, "GET", "/api/tags")
        names = [item.get("name") or item.get("model") for item in data.get("models") or [] if isinstance(item, dict)]
    else:
        data = _call(http, settings, "GET", "/v1/models")
        names = [item.get("id") for item in data.get("data") or [] if isinstance(item, dict)]
    # 埋め込み専用モデル（nomic-embed-text 等）はチャットに使えないので除く
    names = [name for name in names if isinstance(name, str) and name and "embed" not in name.lower()]
    if not names:
        raise LocalLlmError(
            "ローカル LLM にチャット用のモデルが入っていません。"
            "例: `ollama pull qwen2.5:7b` で取得するか、--model / LOCAL_LLM_MODEL でモデル名を指定してください。"
        )
    return names[0]


def request_recommendations(messages: list, settings: dict, model: str, http=requests) -> str:
    """ローカル LLM にプロンプトを送り、応答の本文（JSON のはずの文字列）を返す。"""
    if settings["api"] == "ollama":
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": _TEMPERATURE, "num_ctx": _OLLAMA_NUM_CTX},
        }
        data = _call(http, settings, "POST", "/api/chat", payload)
        message = data.get("message")
    else:
        payload = {"model": model, "messages": messages, "temperature": _TEMPERATURE, "stream": False}
        data = _call(http, settings, "POST", "/v1/chat/completions", payload)
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise LocalLlmError("ローカル LLM の応答が空でした。もう一度実行するか、--model で別のモデルを試してください。")
    return content


def _extract_json_object(text: str) -> Optional[dict]:
    """応答から JSON オブジェクトを取り出す（```json で囲まれていたり前置きが付いていても読む）。"""
    text = text.strip()
    attempts = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        attempts.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        attempts.append(text[start : end + 1])
    for attempt in attempts:
        try:
            data = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _is_known_title(title: str, known_titles: set) -> bool:
    """読んだ・候補・読みたくない作品と同じ（片方がもう片方を含む場合も）なら True。"""
    key = _normalize_title(title)
    for known in known_titles:
        if key == known:
            return True
        shorter, longer = sorted((key, known), key=len)
        if len(shorter) >= 4 and shorter in longer:
            return True
    return False


def parse_recommendations(text: str, inputs: dict, *, count: int, new_count: int) -> dict:
    """
    LLM の応答を検証して取り出す。

    from_list は候補の ASIN（無ければ候補と完全一致するタイトル）に当たるものだけを採り、
    タイトルは DB のものに差し替える。new_titles は読んだ作品・候補・読みたくない作品と
    重なるものを除く。JSON として読めなければ raw に応答をそのまま入れて返す。
    """
    data = _extract_json_object(text)
    if data is None:
        return {"taste": "", "from_list": [], "new_titles": [], "raw": text.strip()}

    candidates_by_asin = {item["asin"]: item for item in inputs["candidates"]}
    candidates_by_title = {_normalize_title(item["title"]): item for item in inputs["candidates"]}
    from_list, picked = [], set()
    for item in data.get("from_list") or []:
        if not isinstance(item, dict) or len(from_list) >= count:
            continue
        asin = str(item.get("asin") or "").strip().strip("[]").upper()
        candidate = candidates_by_asin.get(asin) or candidates_by_title.get(_normalize_title(_clean_text(item.get("title"))))
        if candidate is None or candidate["asin"] in picked:
            continue
        picked.add(candidate["asin"])
        from_list.append({"asin": candidate["asin"], "title": candidate["title"], "reason": _clean_text(item.get("reason"))})

    known_titles = (
        {_normalize_title(item["title"]) for item in inputs["seen"]}
        | set(candidates_by_title)
        | {_normalize_title(title) for title in inputs["unwanted"]}
    )
    new_titles = []
    for item in data.get("new_titles") or []:
        if not isinstance(item, dict) or len(new_titles) >= new_count:
            continue
        title = _clean_text(item.get("title"))
        if not title or _is_known_title(title, known_titles):
            continue
        known_titles.add(_normalize_title(title))
        new_titles.append({"title": title, "author": _clean_text(item.get("author")), "reason": _clean_text(item.get("reason"))})

    return {"taste": _clean_text(data.get("taste")), "from_list": from_list, "new_titles": new_titles, "raw": None}


def format_recommendations(kind: str, result: dict, *, model: str, inputs: dict) -> str:
    """parse_recommendations() の結果をコンソール表示用の文字列にする。"""
    label = KIND_LABELS[kind]
    rated = sum(1 for item in inputs["seen"] if item["rating"])
    lines = [
        f"■ {label}のおすすめ（見た {len(inputs['seen'])}件・うち★評価 {rated}件から / "
        f"候補 {len(inputs['candidates'])}件 / モデル: {model}）"
    ]
    if result.get("raw") is not None:
        lines.append("（ローカル LLM の出力を JSON として読めなかったため、そのまま表示します）")
        lines.append(result["raw"])
        return "\n".join(lines)
    if not (result["taste"] or result["from_list"] or result["new_titles"]):
        lines.append("おすすめを取り出せませんでした。もう一度実行するか、--model で別のモデルを試してください。")
        return "\n".join(lines)

    if result["taste"]:
        lines.append(f"好みの傾向: {result['taste']}")
    if result["from_list"]:
        lines += ["", "【登録済みの中から】"]
        for i, item in enumerate(result["from_list"], 1):
            lines.append(f"  {i}. {item['title']}")
            lines.append(f"     https://www.amazon.co.jp/dp/{item['asin']}")
            if item["reason"]:
                lines.append(f"     理由: {item['reason']}")
    if result["new_titles"]:
        lines += ["", "【ほかに読むなら】（ローカル LLM の知識からの提案。実在・巻数は確認してください）"]
        for i, item in enumerate(result["new_titles"], 1):
            lines.append(f"  {i}. {item['title']}" + (f" / {item['author']}" if item["author"] else ""))
            if item["reason"]:
                lines.append(f"     理由: {item['reason']}")
    return "\n".join(lines)
