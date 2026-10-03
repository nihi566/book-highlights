"""
test_recommender.py
--------------------
src/recommender.py（「見た」作品と★評価からローカル LLM におすすめを出してもらう）の単体テスト。

実際の LLM には接続せず、HTTP は FakeHttp で差し替える。

実行:
    python -m unittest test.test_recommender -v
"""

import json
import os
import random
import sys
import unittest
from unittest.mock import patch

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src import recommender
from src.recommender import LocalLlmError


def _book(asin, title, is_purchased=0):
    return {"asin": asin, "title": title, "is_wanted": 1, "is_purchased": is_purchased}


def _mark(tag="", rating=None, kind=None, title=None, updated_at="2026-09-01T00:00:00"):
    return {"tag": tag, "rating": rating, "kind": kind, "title": title, "updated_at": updated_at}


class _FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text or (json.dumps(data, ensure_ascii=False) if data is not None else "")

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class _FakeHttp:
    """requests の get/post の代わり。呼び出しを記録し、用意した応答か例外を返す。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def _respond(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def get(self, url, **kwargs):
        return self._respond("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._respond("POST", url, **kwargs)


class CollectInputsTest(unittest.TestCase):
    def setUp(self):
        self.books = [
            _book("B0MANGA001", "マイペースと歩く 1巻 (バンチコミックス)"),
            _book("B0MANGA002", "本なら売るほど 1 (ハルタコミックス)"),
            _book("B0MANGA003", "戦争は女の顔をしていない 6"),
            _book("B0BOOK0001", "現代思想入門 (講談社現代新書)"),
            _book("B0BOOK0002", "独ソ戦 (岩波新書)"),
            _book("B0BOOK0003", "ハッキング・ラボのつくりかた"),
            _book("B0BOOK0004", "ブルシット・ジョブ"),
            _book("B0BOOK0005", "ブルシット・ジョブ"),  # 同じタイトルの別 ASIN
            _book("B0BOOK0006", "積読の本", is_purchased=1),
        ]
        self.marks = {
            "B0MANGA001": _mark("seen", 5),
            "B0MANGA003": _mark("", kind="manga"),  # レーベル無しをページでマンガに切り替えた
            "B0BOOK0001": _mark("seen", 4),
            "B0BOOK0003": _mark("unwanted"),
            "B0GONE0001": _mark("seen", 2, title="一覧から消えた本"),  # book_mappings に無い読書記録
        }

    def test_manga_and_book_are_separated(self):
        manga = recommender.collect_inputs(self.books, self.marks, "manga", rng=random.Random(0))
        self.assertEqual([item["asin"] for item in manga["seen"]], ["B0MANGA001"])
        self.assertEqual({item["asin"] for item in manga["candidates"]}, {"B0MANGA002", "B0MANGA003"})

        book = recommender.collect_inputs(self.books, self.marks, "book", rng=random.Random(0))
        self.assertEqual({item["asin"] for item in book["seen"]}, {"B0BOOK0001", "B0GONE0001"})
        self.assertNotIn("B0MANGA002", {item["asin"] for item in book["candidates"]})

    def test_seen_and_unwanted_are_not_candidates(self):
        inputs = recommender.collect_inputs(self.books, self.marks, "book", rng=random.Random(0))
        asins = {item["asin"] for item in inputs["candidates"]}
        self.assertNotIn("B0BOOK0001", asins)
        self.assertNotIn("B0BOOK0003", asins)
        self.assertEqual(inputs["unwanted"], ["ハッキング・ラボのつくりかた"])

    def test_same_title_is_listed_once(self):
        inputs = recommender.collect_inputs(self.books, self.marks, "book", rng=random.Random(0))
        titles = [item["title"] for item in inputs["candidates"]]
        self.assertEqual(titles.count("ブルシット・ジョブ"), 1)

    def test_seen_is_sorted_by_rating(self):
        inputs = recommender.collect_inputs(self.books, self.marks, "book", rng=random.Random(0))
        self.assertEqual([item["rating"] for item in inputs["seen"]], [4, 2])

    def test_purchased_or_wanted_candidates_come_first_and_limit_applies(self):
        marks = dict(self.marks, B0BOOK0002=_mark("wanted"))
        inputs = recommender.collect_inputs(self.books, marks, "book", max_candidates=2, rng=random.Random(0))
        self.assertEqual({item["asin"] for item in inputs["candidates"]}, {"B0BOOK0002", "B0BOOK0006"})

    def test_recent_seen_are_kept_when_over_limit(self):
        marks = {
            "B0BOOK0001": _mark("seen", 5, updated_at="2026-01-01T00:00:00"),
            "B0BOOK0002": _mark("seen", 1, updated_at="2026-09-01T00:00:00"),
        }
        with patch.object(recommender, "MAX_SEEN_IN_PROMPT", 1):
            inputs = recommender.collect_inputs(self.books, marks, "book", rng=random.Random(0))
        self.assertEqual([item["asin"] for item in inputs["seen"]], ["B0BOOK0002"])

    def test_summarize_seen_counts_by_kind(self):
        summary = recommender.summarize_seen(self.books, self.marks)
        self.assertEqual(summary, {"manga": {"seen": 1, "rated": 1}, "book": {"seen": 2, "rated": 2}})


class BuildMessagesTest(unittest.TestCase):
    def _inputs(self, candidates=True):
        return {
            "seen": [{"asin": "B0SEEN0001", "title": "好きな本", "rating": 5}, {"asin": "B0SEEN0002", "title": "未評価の本", "rating": None}],
            "unwanted": ["嫌いな本"],
            "candidates": [{"asin": "B0CAND0001", "title": "候補の本"}] if candidates else [],
        }

    def test_prompt_lists_ratings_unwanted_and_candidates(self):
        messages = recommender.build_messages("book", self._inputs(), count=3, new_count=2)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        prompt = messages[1]["content"]
        self.assertIn("ユーザーが読んだ本と★評価", prompt)
        self.assertIn("- 好きな本（★5）", prompt)
        self.assertIn("- 未評価の本（評価なし）", prompt)
        self.assertIn("- 嫌いな本", prompt)
        self.assertIn("- [B0CAND0001] 候補の本", prompt)
        self.assertIn("最大3件", prompt)
        self.assertIn("最大2件", prompt)

    def test_manga_prompt_says_manga(self):
        prompt = recommender.build_messages("manga", self._inputs(), count=3, new_count=2)[1]["content"]
        self.assertIn("ユーザーが読んだマンガ", prompt)
        self.assertIn("実在のマンガ", prompt)

    def test_without_candidates_or_new_titles_asks_for_empty_arrays(self):
        prompt = recommender.build_messages("book", self._inputs(candidates=False), count=3, new_count=0)[1]["content"]
        self.assertIn("from_list は空の配列にする。", prompt)
        self.assertIn("new_titles は空の配列にする。", prompt)
        self.assertNotIn("おすすめ候補", prompt)


class LoadLlmSettingsTest(unittest.TestCase):
    def test_defaults_to_local_ollama(self):
        settings = recommender.load_llm_settings({})
        self.assertEqual(settings, {"api": "ollama", "url": "http://localhost:11434", "model": None, "timeout": 600})

    def test_openai_compatible_url_drops_v1_suffix(self):
        settings = recommender.load_llm_settings({"LOCAL_LLM_API": "openai", "LOCAL_LLM_URL": "http://localhost:1234/v1/"})
        self.assertEqual(settings["url"], "http://localhost:1234")

    def test_model_argument_wins_over_env(self):
        settings = recommender.load_llm_settings({"LOCAL_LLM_MODEL": "env-model"}, model="arg-model", timeout=30)
        self.assertEqual(settings["model"], "arg-model")
        self.assertEqual(settings["timeout"], 30)
        self.assertEqual(recommender.load_llm_settings({"LOCAL_LLM_MODEL": "env-model"})["model"], "env-model")

    def test_unknown_api_is_rejected(self):
        with self.assertRaises(LocalLlmError):
            recommender.load_llm_settings({"LOCAL_LLM_API": "cloud"})


class ResolveModelTest(unittest.TestCase):
    def test_uses_configured_model_without_http(self):
        http = _FakeHttp()
        settings = recommender.load_llm_settings({}, model="qwen2.5:7b")
        self.assertEqual(recommender.resolve_model(settings, http=http), "qwen2.5:7b")
        self.assertEqual(http.calls, [])

    def test_picks_first_chat_model_from_ollama(self):
        http = _FakeHttp(_FakeResponse(data={"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen2.5:7b"}]}))
        model = recommender.resolve_model(recommender.load_llm_settings({}), http=http)
        self.assertEqual(model, "qwen2.5:7b")
        self.assertEqual(http.calls[0][1], "http://localhost:11434/api/tags")

    def test_picks_first_model_from_openai_compatible_server(self):
        http = _FakeHttp(_FakeResponse(data={"data": [{"id": "local-model"}]}))
        settings = recommender.load_llm_settings({"LOCAL_LLM_API": "openai", "LOCAL_LLM_URL": "http://localhost:1234"})
        self.assertEqual(recommender.resolve_model(settings, http=http), "local-model")
        self.assertEqual(http.calls[0][1], "http://localhost:1234/v1/models")

    def test_no_model_installed_raises(self):
        http = _FakeHttp(_FakeResponse(data={"models": []}))
        with self.assertRaises(LocalLlmError) as cm:
            recommender.resolve_model(recommender.load_llm_settings({}), http=http)
        self.assertIn("ollama pull", str(cm.exception))


class RequestRecommendationsTest(unittest.TestCase):
    MESSAGES = [{"role": "user", "content": "hi"}]

    def test_ollama_request_uses_schema_and_returns_content(self):
        http = _FakeHttp(_FakeResponse(data={"message": {"role": "assistant", "content": '{"taste": "x"}'}}))
        settings = recommender.load_llm_settings({})
        content = recommender.request_recommendations(self.MESSAGES, settings, "qwen2.5:7b", http=http)
        self.assertEqual(content, '{"taste": "x"}')
        method, url, kwargs = http.calls[0]
        self.assertEqual((method, url), ("POST", "http://localhost:11434/api/chat"))
        self.assertEqual(kwargs["json"]["model"], "qwen2.5:7b")
        self.assertFalse(kwargs["json"]["stream"])
        self.assertEqual(kwargs["json"]["format"], recommender.RESPONSE_SCHEMA)
        self.assertEqual(kwargs["timeout"], 600)

    def test_openai_compatible_request_returns_first_choice(self):
        http = _FakeHttp(_FakeResponse(data={"choices": [{"message": {"content": "{}"}}]}))
        settings = recommender.load_llm_settings({"LOCAL_LLM_API": "openai", "LOCAL_LLM_URL": "http://localhost:1234"})
        self.assertEqual(recommender.request_recommendations(self.MESSAGES, settings, "m", http=http), "{}")
        self.assertEqual(http.calls[0][1], "http://localhost:1234/v1/chat/completions")

    def test_connection_error_explains_how_to_start_ollama(self):
        http = _FakeHttp(error=requests.exceptions.ConnectionError("refused"))
        with self.assertRaises(LocalLlmError) as cm:
            recommender.request_recommendations(self.MESSAGES, recommender.load_llm_settings({}), "m", http=http)
        self.assertIn("ollama serve", str(cm.exception))

    def test_read_timeout_suggests_longer_timeout(self):
        http = _FakeHttp(error=requests.exceptions.ReadTimeout("slow"))
        with self.assertRaises(LocalLlmError) as cm:
            recommender.request_recommendations(self.MESSAGES, recommender.load_llm_settings({}), "m", http=http)
        self.assertIn("--timeout", str(cm.exception))

    def test_missing_ollama_model_suggests_pull(self):
        http = _FakeHttp(_FakeResponse(status_code=404, data={"error": "model not found"}))
        with self.assertRaises(LocalLlmError) as cm:
            recommender.request_recommendations(self.MESSAGES, recommender.load_llm_settings({}), "llama9", http=http)
        self.assertIn("ollama pull llama9", str(cm.exception))

    def test_url_without_scheme_is_explained(self):
        http = _FakeHttp(error=requests.exceptions.MissingSchema("no scheme"))
        settings = recommender.load_llm_settings({"LOCAL_LLM_URL": "localhost:11434"})
        with self.assertRaises(LocalLlmError) as cm:
            recommender.request_recommendations(self.MESSAGES, settings, "m", http=http)
        self.assertIn("LOCAL_LLM_URL の形式", str(cm.exception))

    def test_other_request_errors_become_local_llm_error(self):
        http = _FakeHttp(error=requests.exceptions.ChunkedEncodingError("cut"))
        with self.assertRaises(LocalLlmError):
            recommender.request_recommendations(self.MESSAGES, recommender.load_llm_settings({}), "m", http=http)

    def test_unexpected_response_shape_raises_local_llm_error(self):
        for data in ({"message": "text"}, {"choices": {"message": {}}}):
            with self.subTest(data=data):
                http = _FakeHttp(_FakeResponse(data=data))
                api = "openai" if "choices" in data else "ollama"
                settings = recommender.load_llm_settings({"LOCAL_LLM_API": api})
                with self.assertRaises(LocalLlmError):
                    recommender.request_recommendations(self.MESSAGES, settings, "m", http=http)

    def test_empty_content_raises(self):
        http = _FakeHttp(_FakeResponse(data={"message": {"content": "  "}}))
        with self.assertRaises(LocalLlmError):
            recommender.request_recommendations(self.MESSAGES, recommender.load_llm_settings({}), "m", http=http)


class ParseRecommendationsTest(unittest.TestCase):
    INPUTS = {
        "seen": [{"asin": "B0SEEN0001", "title": "進撃の巨人（１） (週刊少年マガジンコミックス)", "rating": 5}],
        "unwanted": [],
        "candidates": [
            {"asin": "B0CAND0001", "title": "候補A"},
            {"asin": "B0CAND0002", "title": "候補B"},
            {"asin": "B0CAND0003", "title": "候補C"},
        ],
    }

    def _parse(self, data, count=5, new_count=5):
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return recommender.parse_recommendations(text, self.INPUTS, count=count, new_count=new_count)

    def test_only_candidate_asins_are_kept_with_titles_from_db(self):
        result = self._parse(
            {
                "taste": " 重めの話が好き ",
                "from_list": [
                    {"asin": "B0CAND0002", "reason": "理由B"},
                    {"asin": "B0NOTACAND", "reason": "候補外"},
                    {"asin": "[b0cand0001]", "reason": "理由A"},
                    {"asin": "B0CAND0002", "reason": "重複"},
                ],
                "new_titles": [],
            }
        )
        self.assertEqual(result["taste"], "重めの話が好き")
        self.assertEqual(
            result["from_list"],
            [
                {"asin": "B0CAND0002", "title": "候補B", "reason": "理由B"},
                {"asin": "B0CAND0001", "title": "候補A", "reason": "理由A"},
            ],
        )

    def test_candidate_can_be_matched_by_exact_title(self):
        result = self._parse({"taste": "", "from_list": [{"title": "候補C", "reason": "r"}], "new_titles": []})
        self.assertEqual(result["from_list"][0]["asin"], "B0CAND0003")

    def test_count_limits_are_applied(self):
        result = self._parse(
            {
                "taste": "",
                "from_list": [{"asin": "B0CAND0001", "reason": ""}, {"asin": "B0CAND0002", "reason": ""}],
                "new_titles": [{"title": "新作1", "reason": ""}, {"title": "新作2", "reason": ""}],
            },
            count=1,
            new_count=1,
        )
        self.assertEqual(len(result["from_list"]), 1)
        self.assertEqual(len(result["new_titles"]), 1)

    def test_new_titles_skip_already_read_or_listed_works(self):
        result = self._parse(
            {
                "taste": "",
                "from_list": [],
                "new_titles": [
                    {"title": "進撃の巨人", "author": "諫山創", "reason": "既読"},
                    {"title": "候補A", "reason": "候補にある"},
                    {"title": "ヴィンランド・サガ", "author": "幸村誠", "reason": "歴史もの"},
                ],
            }
        )
        self.assertEqual(
            result["new_titles"], [{"title": "ヴィンランド・サガ", "author": "幸村誠", "reason": "歴史もの"}]
        )

    def test_json_wrapped_in_code_fence_is_read(self):
        text = '以下です。\n```json\n{"taste": "t", "from_list": [{"asin": "B0CAND0001", "reason": "r"}], "new_titles": []}\n```'
        self.assertEqual(self._parse(text)["from_list"][0]["asin"], "B0CAND0001")

    def test_non_json_output_is_returned_raw(self):
        result = self._parse("おすすめは候補Aです。")
        self.assertEqual(result["raw"], "おすすめは候補Aです。")
        self.assertEqual(result["from_list"], [])


class FormatRecommendationsTest(unittest.TestCase):
    INPUTS = {"seen": [{"asin": "B0SEEN0001", "title": "t", "rating": 4}], "unwanted": [], "candidates": [{"asin": "B0CAND0001", "title": "候補A"}]}

    def test_shows_sections_with_amazon_link(self):
        result = {
            "taste": "歴史が好き",
            "from_list": [{"asin": "B0CAND0001", "title": "候補A", "reason": "理由"}],
            "new_titles": [{"title": "新作", "author": "著者", "reason": "理由2"}],
            "raw": None,
        }
        text = recommender.format_recommendations("manga", result, model="qwen2.5:7b", inputs=self.INPUTS)
        self.assertIn("■ マンガのおすすめ（見た 1件・うち★評価 1件から / 候補 1件 / モデル: qwen2.5:7b）", text)
        self.assertIn("好みの傾向: 歴史が好き", text)
        self.assertIn("https://www.amazon.co.jp/dp/B0CAND0001", text)
        self.assertIn("1. 新作 / 著者", text)

    def test_raw_output_is_shown_as_is(self):
        result = {"taste": "", "from_list": [], "new_titles": [], "raw": "自由な文章"}
        text = recommender.format_recommendations("book", result, model="m", inputs=self.INPUTS)
        self.assertIn("自由な文章", text)
        self.assertIn("JSON として読めなかった", text)

    def test_empty_result_suggests_retry(self):
        result = {"taste": "", "from_list": [], "new_titles": [], "raw": None}
        text = recommender.format_recommendations("book", result, model="m", inputs=self.INPUTS)
        self.assertIn("おすすめを取り出せませんでした", text)


if __name__ == "__main__":
    unittest.main()
