"""
test_title_resolver.py
-----------------------
src/title_resolver.py の検索URL組み立て・検索結果パース処理を検証する単体テスト。
静的HTMLフィクスチャを使用し、実ブラウザ起動は行わない。
"""

import asyncio
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.title_resolver import (
    build_kindle_search_url,
    parse_kindle_search_results,
    resolve_title_to_paid_asin,
    _pick_asin_from_search,
)

# ─── フィクスチャ ──────────────────────────────────────────────────────────────

# 紙版とKindle版が混在する検索結果ページ（Kindle版が2件、紙版が1件）
HTML_MIXED = """
<div class="s-main-slot">
  <div data-asin="4041033839" class="s-result-item">
    <span>単行本</span>
  </div>
  <div data-asin="B0C1H2K3L4" class="s-result-item">
    <span>Kindle版</span>
  </div>
  <div data-asin="B0D9Z8Y7X6" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
"""

# 紙版のみで Kindle版が存在しない検索結果ページ
HTML_PAPER_ONLY = """
<div class="s-main-slot">
  <div data-asin="4041033839" class="s-result-item">
    <span>単行本</span>
  </div>
  <div data-asin="4198765432" class="s-result-item">
    <span>文庫</span>
  </div>
</div>
"""

# 検索結果が0件のページ
HTML_NO_RESULTS = """
<div class="s-main-slot">
  <span>該当する商品はありませんでした。</span>
</div>
"""

# 検索結果本体の先頭にスポンサー枠（無関係な本のKindle版）が入っているページ。
# スポンサー枠もKindle形式ASINを持つため、is_kindle_asinの検証だけでは除外できない。
HTML_WITH_SPONSORED = """
<div class="s-main-slot">
  <div data-asin="B0SPONSR01" class="s-result-item AdHolder">
    <span>スポンサー</span>
    <span>Kindle版</span>
  </div>
  <div data-asin="B0GENUIN01" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
"""

# 検索結果本体（s-main-slot）より前にヘッダー・カルーセル等の無関係なdata-asinがあるページ
HTML_WITH_PRE_CONTAINER_CAROUSEL = """
<div class="nav-carousel">
  <div data-asin="B0UNRELTD1"></div>
</div>
<div class="s-main-slot">
  <div data-asin="B0GENUIN02" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
"""

# 検索結果本体（s-main-slot）が閉じた後、フッター等の関連商品カルーセルに
# 無関係なdata-asinが続くページ。文字列位置ヒューリスティック（次のdata-asinまで／
# ページ末尾まで）ではこの終端を検出できず取り込んでしまう。
HTML_WITH_TRAILING_CAROUSEL_AFTER_CONTAINER = """
<div class="s-main-slot">
  <div data-asin="B0GENUIN03" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
<div class="s-result-list-placeholder">
  <div data-asin="B0FOOTER001"></div>
</div>
"""

# head の <style> に s-main-slot という文字列が含まれるページ。
# class属性ではなくページ全体の文字列一致で判定すると、ここを検索結果本体の
# 開始位置と誤認し、それより前のヘッダー・カルーセルまで走査対象になる。
HTML_WITH_CSS_TEXT_FALSE_MARKER = """
<head><style>.s-main-slot{color:red}</style></head>
<body>
<div class="nav-carousel">
  <div data-asin="B0UNRELTD2"></div>
</div>
<div class="s-main-slot">
  <div data-asin="B0GENUIN04" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
</body>
"""

# スポンサー枠自身が内部に同一ASINの入れ子data-asinを持つページ
# （インプレッション計測用等）。「次のdata-asinまで」という窓の切り方だと、
# 入れ子のdata-asinがスポンサー表示より手前で窓が終わり除外をすり抜けていた。
HTML_WITH_NESTED_SPONSOR_ASIN = """
<div class="s-main-slot">
  <div data-asin="B0SPONSR02" class="AdHolder">
    <div data-asin="B0SPONSR02"></div>
    <span>スポンサー</span>
    <span>Kindle版</span>
  </div>
  <div data-asin="B0GENUIN05" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
"""

# スポンサー印（AdHolder）がdata-asinを持つ要素自身ではなく、それを包む
# 外側のラッパー要素に付いているページ。カード自身の属性だけを見る判定では
# 祖先のスポンサー印を見落とす（レビューで発見された未収束ケース）。
HTML_WITH_SPONSOR_MARKER_ON_OUTER_WRAPPER = """
<div class="s-main-slot">
  <div class="AdHolder">
    <div data-asin="B0SPONSR03" class="s-result-item">
      <span>Kindle版</span>
    </div>
  </div>
  <div data-asin="B0GENUIN06" class="s-result-item">
    <span>Kindle版</span>
  </div>
</div>
"""

# 検索結果本体（s-main-slot）が見つからないページ（レイアウト変更等を想定）
HTML_WITHOUT_RESULT_CONTAINER = """
<div class="some-other-layout">
  <div data-asin="B0WHATEVER1"></div>
</div>
"""

# 検索結果本体内に <noscript> があり、その中身（ブラウザが生テキストとして
# 再出力する）に閉じられていないタグが含まれるページ。HTMLParserがscript/style
# しかCDATA扱いしないと、この不整合でタグスタックがずれてコンテナの終端を
# 見失い、閉じた後のフッターまで走査対象になってしまう。
HTML_WITH_UNBALANCED_NOSCRIPT = """
<div class="s-main-slot">
  <div data-asin="B0GENUIN07" class="s-result-item">
    <noscript><div class="pixel"><img src="p.gif"></noscript>
    <span>Kindle版</span>
  </div>
</div>
<div class="s-result-list-placeholder">
  <div data-asin="B0FOOTER002"></div>
</div>
"""

# カード内に <script> タグがあり、その中身（インラインJSON等）に
# 「スポンサー」という文字列を含むページ。scriptの中身は表示されないため、
# スポンサー判定の対象に含めると正規のKindle版まで誤って除外してしまう。
HTML_WITH_SPONSOR_WORD_INSIDE_SCRIPT = """
<div class="s-main-slot">
  <div data-asin="B0GENUIN08" class="s-result-item">
    <span>Kindle版</span>
    <script type="application/json">{"label":"スポンサー"}</script>
  </div>
</div>
"""


class TestBuildKindleSearchUrl(unittest.TestCase):
    def test_includes_kindle_store_filter(self):
        url = build_kindle_search_url("吾輩は猫である", "夏目漱石")
        self.assertIn("i=digital-text", url)

    def test_encodes_title_and_author(self):
        url = build_kindle_search_url("A/B?", "C&D")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        # URLを壊す記号（?, &, スペース）がクエリ値として正しく復元できること
        self.assertEqual(query["k"], ["A/B? C&D"])
        self.assertEqual(query["i"], ["digital-text"])

    def test_author_omitted(self):
        url = build_kindle_search_url("タイトルのみ")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["k"], ["タイトルのみ"])


class TestParseKindleSearchResults(unittest.TestCase):
    def test_extracts_only_kindle_asins_from_mixed_results(self):
        candidates = parse_kindle_search_results(HTML_MIXED)
        self.assertEqual(candidates, ["B0C1H2K3L4", "B0D9Z8Y7X6"])
        self.assertNotIn("4041033839", candidates)

    def test_paper_only_results_yield_empty_list(self):
        candidates = parse_kindle_search_results(HTML_PAPER_ONLY)
        self.assertEqual(candidates, [])

    def test_no_results_yield_empty_list(self):
        candidates = parse_kindle_search_results(HTML_NO_RESULTS)
        self.assertEqual(candidates, [])

    def test_deduplicates_repeated_asin(self):
        html = (
            '<div class="s-main-slot">'
            '<div data-asin="B0C1H2K3L4"></div><div data-asin="B0C1H2K3L4"></div>'
            "</div>"
        )
        candidates = parse_kindle_search_results(html)
        self.assertEqual(candidates, ["B0C1H2K3L4"])

    def test_excludes_sponsored_card_even_if_kindle_format(self):
        # スポンサー枠は無関係な本でもKindle形式ASINを持ちうるため、
        # is_kindle_asinの検証だけでは通過してしまう。属性・表示テキストを見て除外する。
        candidates = parse_kindle_search_results(HTML_WITH_SPONSORED)
        self.assertEqual(candidates, ["B0GENUIN01"])
        self.assertNotIn("B0SPONSR01", candidates)

    def test_excludes_asin_appearing_before_result_container(self):
        # s-main-slot（検索結果本体）より前のヘッダー・カルーセルは検索結果ではない
        candidates = parse_kindle_search_results(HTML_WITH_PRE_CONTAINER_CAROUSEL)
        self.assertEqual(candidates, ["B0GENUIN02"])
        self.assertNotIn("B0UNRELTD1", candidates)

    def test_excludes_asin_appearing_after_container_closes(self):
        # s-main-slotが閉じた後のフッター・関連商品カルーセルは検索結果ではない
        candidates = parse_kindle_search_results(HTML_WITH_TRAILING_CAROUSEL_AFTER_CONTAINER)
        self.assertEqual(candidates, ["B0GENUIN03"])
        self.assertNotIn("B0FOOTER001", candidates)

    def test_css_text_does_not_trigger_false_container_start(self):
        # <style>内の文字列一致では検索結果本体と誤認しない（class属性でのみ判定する）
        candidates = parse_kindle_search_results(HTML_WITH_CSS_TEXT_FALSE_MARKER)
        self.assertEqual(candidates, ["B0GENUIN04"])
        self.assertNotIn("B0UNRELTD2", candidates)

    def test_excludes_sponsored_card_with_nested_duplicate_asin(self):
        # スポンサー枠内部に同一ASINの入れ子data-asinがあっても除外できる
        candidates = parse_kindle_search_results(HTML_WITH_NESTED_SPONSOR_ASIN)
        self.assertEqual(candidates, ["B0GENUIN05"])
        self.assertNotIn("B0SPONSR02", candidates)

    def test_excludes_sponsored_card_when_marker_is_on_outer_wrapper(self):
        # スポンサー印がdata-asin要素自身ではなく外側のラッパーに付いていても除外できる
        candidates = parse_kindle_search_results(HTML_WITH_SPONSOR_MARKER_ON_OUTER_WRAPPER)
        self.assertEqual(candidates, ["B0GENUIN06"])
        self.assertNotIn("B0SPONSR03", candidates)

    def test_returns_empty_list_when_result_container_not_found(self):
        # 検索結果本体を特定できない場合は候補を1件も返さない（fail-closed）
        candidates = parse_kindle_search_results(HTML_WITHOUT_RESULT_CONTAINER)
        self.assertEqual(candidates, [])

    def test_noscript_unbalanced_markup_does_not_leak_container_boundary(self):
        # noscript内の未閉じタグでタグスタックがずれても、本体外のフッターを候補にしない
        candidates = parse_kindle_search_results(HTML_WITH_UNBALANCED_NOSCRIPT)
        self.assertEqual(candidates, ["B0GENUIN07"])
        self.assertNotIn("B0FOOTER002", candidates)

    def test_script_content_is_not_treated_as_sponsor_text(self):
        # script内のテキスト（表示されない）はスポンサー判定に使わない
        candidates = parse_kindle_search_results(HTML_WITH_SPONSOR_WORD_INSIDE_SCRIPT)
        self.assertEqual(candidates, ["B0GENUIN08"])


class TestPickAsinFromSearch(unittest.TestCase):
    """resolve_title_to_paid_asin が最終的にどの値を返すかを決める判定ロジック。

    実ブラウザを起動せずに「検索結果0件」「候補が紙の本のみ」「BAN検知」の
    3ケースいずれも None を返すことを確認する。
    """

    def test_returns_first_candidate_when_ok(self):
        self.assertEqual(_pick_asin_from_search(HTML_MIXED, "ok"), "B0C1H2K3L4")

    def test_returns_none_when_no_results(self):
        self.assertIsNone(_pick_asin_from_search(HTML_NO_RESULTS, "ok"))

    def test_returns_none_when_paper_only(self):
        self.assertIsNone(_pick_asin_from_search(HTML_PAPER_ONLY, "ok"))

    def test_returns_none_when_ban_detected(self):
        # BAN検知シグナルがあれば、Kindle版の候補があっても必ずNoneを返す
        # （ページ内容の信頼性が確認できない状態では採用しない、という別種の防御）
        self.assertIsNone(_pick_asin_from_search(HTML_MIXED, "captcha"))
        self.assertIsNone(_pick_asin_from_search(HTML_MIXED, "robot_check"))
        self.assertIsNone(_pick_asin_from_search(HTML_MIXED, "access_denied"))
        self.assertIsNone(_pick_asin_from_search(HTML_MIXED, "suspicious"))


class TestResolveTitleToPaidAsinGuard(unittest.TestCase):
    """resolve_title_to_paid_asin の入力ガードのみを検証する。

    playwrightは本実行環境に未導入のため、実ブラウザ起動を伴う正常系パスは
    ここでは検証できない。空タイトルのガードはplaywrightのimportより前に
    early returnするため、未導入環境でもテスト可能。
    """

    def test_empty_title_returns_none_without_launching_browser(self):
        result = asyncio.run(resolve_title_to_paid_asin(""))
        self.assertIsNone(result)

    def test_whitespace_only_title_returns_none(self):
        result = asyncio.run(resolve_title_to_paid_asin("   "))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
