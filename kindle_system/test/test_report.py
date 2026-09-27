"""
test_report.py
---------------
report.py の HTML 生成関数（build_html）の単体テスト。

実行:
    python -m unittest test.test_report -v
"""

import os
import re
import sys
import shutil
import tempfile
import unittest
import unittest.mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import report


def _extract_section_html(html: str, section_id: str) -> str:
    """指定id の <section> 内側のHTMLだけを取り出す（他セクションとの取り違え防止用）。"""
    match = re.search(rf'<section id="{re.escape(section_id)}"[^>]*>(.*?)</section>', html, re.S)
    assert match is not None, f"section {section_id!r} が見つかりません"
    return match.group(1)


class BuildHtmlTest(unittest.TestCase):
    def test_includes_title_and_price_for_each_book(self):
        books = [
            {
                "title": "読みたい本1",
                "asin": "B0WANT001",
                "sell_price": 1000,
                "point_value": 100,
                "actual_price": 900,
                "campaign_text": "",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 0,
                "is_wanted": 1,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertIn("読みたい本1", html)
        self.assertIn("900", html)

    def test_escapes_html_special_characters_in_title(self):
        """R2: タイトルに <script> 等が含まれていても HTML インジェクションにならないこと。"""
        books = [
            {
                "title": "<script>alert('xss')</script>",
                "asin": "B0XSS001",
                "sell_price": None,
                "point_value": 0,
                "actual_price": None,
                "campaign_text": "",
                "timestamp": None,
                "is_unlimited": 0,
                "is_wanted": 0,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertNotIn("<script>alert('xss')</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_shows_placeholder_when_price_is_missing(self):
        books = [
            {
                "title": "価格未取得本",
                "asin": "B0NOPRICE001",
                "sell_price": None,
                "point_value": 0,
                "actual_price": None,
                "campaign_text": "",
                "timestamp": None,
                "is_unlimited": 0,
                "is_wanted": 1,
                "is_purchased": 0,
            }
        ]
        html = report.build_html(books)
        self.assertIn("価格未取得本", html)
        self.assertIn("価格情報なし", html)

    def test_shows_ku_label_instead_of_zero_yen_for_unlimited_books(self):
        """
        src/crawler.pyのKU安全弁はis_unlimited本のsell_price/actual_priceを常に0で
        保存する（本来の価格はcampaign_textへ退避）。¥0とそのまま表示すると
        「無料で買える本」という誤解を生むため、専用ラベルで表示すること。
        """
        books = [
            {
                "title": "Unlimited対象本",
                "asin": "B0KU001",
                "sell_price": 0,
                "point_value": 0,
                "actual_price": 0,
                "campaign_text": "通常価格: ¥1,200",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 1,
                "is_wanted": 1,
                "is_purchased": 1,
            }
        ]
        html = report.build_html(books)
        self.assertIn("Unlimited対象本", html)
        self.assertNotIn("¥0", html)
        self.assertIn("Kindle Unlimited 対象", html)

    def test_renders_valid_html_when_no_books(self):
        html = report.build_html([])
        self.assertIn("<html", html)
        self.assertIn("</html>", html)
        # 0件時、初期表示される「全部」セクションに空状態メッセージが実際に出ること
        # （section-wanted 等 hidden 側の文言では代用しない。section-all に限定して検証する）
        all_section = _extract_section_html(html, "section-all")
        self.assertIn("本はまだ登録されていません。", all_section)

    def test_escapes_asin(self):
        books = [
            {
                "title": "普通の本",
                "asin": "B0<script>",
                "sell_price": 500,
                "point_value": 0,
                "actual_price": 500,
                "campaign_text": "",
                "timestamp": "2026-01-01T00:00:00",
                "is_unlimited": 0,
                "is_wanted": 0,
                "is_purchased": 1,
            }
        ]
        html = report.build_html(books)
        # フィルタUI用の信頼済み<script>タグ自体は許容しつつ、asin由来の
        # 未エスケープペイロード("B0<script>")のみが混入していないことを確認する。
        self.assertNotIn("B0<script>", html)
        # is_purchased=1のため「全部」「購入済み」の2セクションに表示され、各セクション内で
        # asinは表示用セル（col-asin）の1箇所だけに出る（2セクション×1箇所=2。Amazon への
        # 遷移はページ側の JS が col-asin を読んで行うため、行に href は出さない）
        # （カウントを固定することでエスケープ漏れの検出力低下を防ぐ）。
        self.assertEqual(html.count("&lt;script&gt;"), 2)


class LastScrapedTest(unittest.TestCase):
    """report.build_html() の「価格の最終取得」日時表示のテスト。"""

    def _book(self, asin, timestamp):
        return {
            "title": f"本{asin}",
            "asin": asin,
            "sell_price": None,
            "point_value": 0,
            "actual_price": None,
            "campaign_text": "",
            "timestamp": timestamp,
            "is_unlimited": 0,
            "is_wanted": 0,
            "is_purchased": 0,
        }

    def _last_scraped(self, html):
        match = re.search(r'<p class="last-scraped">(.*?)</p>', html)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_shows_latest_timestamp_among_books(self):
        books = [
            self._book("B0A", "2026-09-20T08:00:00.123456"),
            self._book("B0B", "2026-09-27T14:03:59.000001"),
            self._book("B0C", None),
        ]
        line = self._last_scraped(report.build_html(books))
        self.assertIn('<time datetime="2026-09-27T14:03:59.000001">2026-09-27 14:03</time>', line)

    def test_shows_not_yet_when_no_price_was_fetched(self):
        books = [self._book("B0A", None)]
        self.assertEqual(self._last_scraped(report.build_html(books)), "価格の最終取得: 未取得")
        self.assertEqual(self._last_scraped(report.build_html([])), "価格の最終取得: 未取得")

    def test_escapes_unparsable_timestamp(self):
        books = [self._book("B0A", "<b>broken</b>")]
        line = self._last_scraped(report.build_html(books))
        self.assertNotIn("<b>", line)
        self.assertIn("&lt;b&gt;broken&lt;/b&gt;", line)

    def test_is_placed_right_after_title(self):
        html = report.build_html([])
        self.assertRegex(html, r'<h1>蔵書リスト</h1>\n<p class="last-scraped">')


class CategorySectionsTest(unittest.TestCase):
    """report.build_html() の want済み/購入済み/全部 3カテゴリ分けのテスト。"""

    def _book(self, title, asin, is_wanted, is_purchased):
        return {
            "title": title,
            "asin": asin,
            "sell_price": None,
            "point_value": 0,
            "actual_price": None,
            "campaign_text": "",
            "timestamp": None,
            "is_unlimited": 0,
            "is_wanted": is_wanted,
            "is_purchased": is_purchased,
        }

    def test_three_sections_present_with_expected_ids(self):
        html = report.build_html([])
        self.assertIn('id="section-all"', html)
        self.assertIn('id="section-wanted"', html)
        self.assertIn('id="section-purchased"', html)

    def test_wanted_only_book_appears_in_all_and_wanted_sections_only(self):
        books = [self._book("欲しい本X", "B0CATW1", is_wanted=1, is_purchased=0)]
        html = report.build_html(books)
        self.assertIn("欲しい本X", _extract_section_html(html, "section-all"))
        self.assertIn("欲しい本X", _extract_section_html(html, "section-wanted"))
        self.assertNotIn("欲しい本X", _extract_section_html(html, "section-purchased"))

    def test_purchased_only_book_appears_in_all_and_purchased_sections_only(self):
        books = [self._book("買った本Y", "B0CATP1", is_wanted=0, is_purchased=1)]
        html = report.build_html(books)
        self.assertIn("買った本Y", _extract_section_html(html, "section-all"))
        self.assertNotIn("買った本Y", _extract_section_html(html, "section-wanted"))
        self.assertIn("買った本Y", _extract_section_html(html, "section-purchased"))

    def test_book_without_flags_appears_only_in_all_section(self):
        books = [self._book("フラグなし本Z", "B0CATN1", is_wanted=0, is_purchased=0)]
        html = report.build_html(books)
        self.assertIn("フラグなし本Z", _extract_section_html(html, "section-all"))
        self.assertNotIn("フラグなし本Z", _extract_section_html(html, "section-wanted"))
        self.assertNotIn("フラグなし本Z", _extract_section_html(html, "section-purchased"))

    def test_price_history_key_on_book_is_rendered_as_polyline(self):
        """book['price_history'] が build_html まで正しく伝播し、<polyline> として
        描画されること（main() が付与するキー名との契約を固定する）。"""
        book = self._book("価格履歴あり本", "B0CATH1", is_wanted=0, is_purchased=0)
        book["price_history"] = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        html = report.build_html([book])
        self.assertIn("<polyline", _extract_section_html(html, "section-all"))

    def test_missing_price_history_key_shows_placeholder(self):
        """price_history キーが無い本(未取得データ等)ではプレースホルダーになること。"""
        book = self._book("価格履歴なし本", "B0CATH2", is_wanted=0, is_purchased=0)
        html = report.build_html([book])
        self.assertIn('<td class="col-history">データなし</td>', _extract_section_html(html, "section-all"))


class FilterUiTest(unittest.TestCase):
    """report.build_html() のフィルタ切り替えUI（3カテゴリボタン + 初期表示）のテスト。"""

    def test_filter_buttons_exist_for_each_category(self):
        html = report.build_html([])
        self.assertIn('data-target="section-all"', html)
        self.assertIn('data-target="section-wanted"', html)
        self.assertIn('data-target="section-purchased"', html)

    def test_initial_display_shows_only_all_section(self):
        """初期表示は「全部」セクションのみで、他の2セクションはhiddenクラスを持つこと。"""
        html = report.build_html([])
        self.assertIn('id="section-all" class="book-section"', html)
        self.assertIn('id="section-wanted" class="book-section hidden"', html)
        self.assertIn('id="section-purchased" class="book-section hidden"', html)

    def test_every_filter_button_target_has_matching_section_id(self):
        """フィルタボタンのdata-targetが実在するセクションidを指していること
        （ID不一致はJS実行時に全セクション非表示のまま復旧不能になるため）。"""
        import re

        html = report.build_html([])
        targets = re.findall(r'data-target="([^"]+)"', html)
        self.assertTrue(targets)
        for target in targets:
            self.assertIn(f'id="{target}"', html)

    def test_initial_state_only_all_button_is_pressed(self):
        """初期表示では「全部」ボタンだけaria-pressed="true"で、他はfalseであること。"""
        html = report.build_html([])
        self.assertIn(
            'data-target="section-all" aria-pressed="true"', html
        )
        self.assertIn(
            'data-target="section-wanted" aria-pressed="false"', html
        )
        self.assertIn(
            'data-target="section-purchased" aria-pressed="false"', html
        )

    def test_empty_section_renders_no_table(self):
        """本が0件のセクションには<table>を出さず、空状態メッセージだけを出すこと
        （0件セクションに空のtableを出すと、JS側のcount/no-results処理が
        意味のない空表を対象に動いてしまうため）。"""
        html = report.build_html([])
        all_section = _extract_section_html(html, "section-all")
        self.assertNotIn("<table", all_section)
        self.assertIn("本はまだ登録されていません。", all_section)

    def test_includes_toggle_script_without_page_navigation(self):
        """ページ遷移(XHR/location遷移)を発生させない素のJSでトグルすること。"""
        html = report.build_html([])
        self.assertIn("<script>", html)
        self.assertIn("classList", html)
        self.assertNotIn("location.href", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)


class ControlsUiTest(unittest.TestCase):
    """report.build_html() の検索・並べ替え・詳細フィルタ（KU対象のみ/価格帯）UIのテスト。"""

    def _book(self, title, asin, actual_price, is_unlimited=0):
        return {
            "title": title,
            "asin": asin,
            "sell_price": actual_price,
            "point_value": 0,
            "actual_price": actual_price,
            "campaign_text": "",
            "timestamp": None,
            "is_unlimited": is_unlimited,
            "is_wanted": 0,
            "is_purchased": 0,
        }

    def test_includes_search_sort_ku_and_price_controls(self):
        html = report.build_html([])
        self.assertIn('id="search-input"', html)
        self.assertIn('id="sort-select"', html)
        self.assertIn('id="ku-only-checkbox"', html)
        self.assertIn('id="price-min"', html)
        self.assertIn('id="price-max"', html)

    def test_sort_options_cover_price_and_title(self):
        html = report.build_html([])
        self.assertIn('value="price-asc"', html)
        self.assertIn('value="price-desc"', html)
        self.assertIn('value="title-asc"', html)

    def test_book_row_has_numeric_price_attribute(self):
        books = [self._book("価格属性本", "B0ATTR001", 1234)]
        html = report.build_html(books)
        self.assertIn('data-price="1234"', html)
        self.assertIn('data-ku="0"', html)

    def test_ku_book_has_empty_price_attribute_and_ku_flag(self):
        books = [self._book("KU属性本", "B0ATTR002", 0, is_unlimited=1)]
        html = report.build_html(books)
        self.assertIn('data-price="" data-ku="1"', html)

    def test_book_without_price_has_empty_price_attribute(self):
        books = [self._book("価格未取得属性本", "B0ATTR003", None)]
        html = report.build_html(books)
        self.assertIn('data-price="" data-ku="0"', html)

    def test_book_rows_have_sequential_data_index_for_default_sort_restore(self):
        """data-index は「登録順」への並べ替え復元(JS)専用の連番。1冊目は0、2冊目は1。
        この値が消えると sortMode==='default' の再ソートが parseInt(undefined)=NaN
        比較になり、登録順に戻せなくなる（JS側は静的検証できないためPython側で固定する）。"""
        books = [
            self._book("順序本1", "B0ORDER001", 100),
            self._book("順序本2", "B0ORDER002", 200),
        ]
        html = report.build_html(books)
        section_html = _extract_section_html(html, "section-all")
        self.assertIn('data-index="0"', section_html)
        self.assertIn('data-index="1"', section_html)

    def test_asin_missing_omits_amazon_link_instead_of_broken_url(self):
        """ASIN未確定(読書メーター経由で未クロール等)の本は、
        'https://www.amazon.co.jp/dp/' という壊れたリンクを出さないこと。"""
        books = [self._book("ASIN未確定本", "", None)]
        html = report.build_html(books)
        self.assertNotIn("amazon.co.jp/dp/\"", html)
        self.assertNotIn('href=""', html)

    def test_data_price_attribute_does_not_duplicate_escaped_asin_count(self):
        """R2: data-price/data-ku は数値/真偽値のみを持ち、asin文字列を複製しないこと
        （複製すると _build_book_row のエスケープ件数の契約(test_escapes_asin)が崩れる）。
        asinはcol-asinセルの1箇所にのみ出る（1セクション×1箇所=1）。"""
        books = [self._book("普通の本2", "B0<script>2", 500)]
        html = report.build_html(books)
        self.assertEqual(html.count("&lt;script&gt;"), 1)


class PublishedPageStructureTest(unittest.TestCase):
    """公開ページ（kindle-wishlist-site の index.html）と同じ構造を出すことのテスト。

    index.html を直接編集した変更は次の自動公開で report.py の出力に上書きされるため、
    公開ページにある要素は report.py 側で出していることをここで固定する。
    """

    def _book(self, title="構造確認本", asin="B0STRUCT01"):
        return {"title": title, "asin": asin, "actual_price": 1000, "is_unlimited": 0, "is_wanted": 0, "is_purchased": 0}

    def test_head_links_favicon(self):
        html = report.build_html([])
        self.assertIn('<link rel="icon" type="image/svg+xml" href="favicon.svg">', html)

    def test_marks_output_as_generated_file(self):
        html = report.build_html([])
        self.assertIn("report.py が生成する", html)

    def test_page_starts_in_grid_view(self):
        html = report.build_html([])
        self.assertIn('<body class="view-grid">', html)

    def test_includes_reset_button_and_notices(self):
        html = report.build_html([])
        self.assertIn('id="reset-controls-button"', html)
        self.assertIn('id="price-range-error"', html)
        self.assertIn('id="active-filter-notice"', html)

    def test_book_row_has_no_link_cell(self):
        """Amazon への遷移は行クリック（JS）で行い、行に <a> を出さないこと。"""
        section_html = _extract_section_html(report.build_html([self._book()]), "section-all")
        self.assertNotIn("<a ", section_html)
        self.assertNotIn("col-link", section_html)

    def test_table_header_has_tag_column_and_hidden_heading(self):
        section_html = _extract_section_html(report.build_html([self._book()]), "section-all")
        self.assertIn('<th scope="col">タグ</th>', section_html)
        self.assertIn('<h2 class="visually-hidden">全部</h2>', section_html)
        self.assertIn('<caption class="visually-hidden">蔵書一覧（全部）</caption>', section_html)

    def test_script_adds_tags_covers_and_view_toggle(self):
        html = report.build_html([self._book()])
        self.assertIn("MARK_STORAGE_PREFIXES", html)
        self.assertIn("COVER_URL_PREFIX", html)
        self.assertIn("VIEW_STORAGE_KEY", html)

    def test_tag_filter_select_has_all_options(self):
        """付けたタグ（ブラウザ保存）で一覧を絞り込む選択欄があり、初期値は「すべて」であること。"""
        html = report.build_html([])
        self.assertIn('<select id="tag-filter-select"', html)
        values = re.findall(
            r'<option value="([^"]+)">タグ: ',
            re.search(r'<select id="tag-filter-select".*?</select>', html, re.S).group(0),
        )
        self.assertEqual(values, ["all", "hide-unwanted", "wanted", "unwanted", "purchased", "seen", "untagged"])

    def test_script_applies_tag_filter_and_remembers_it(self):
        html = report.build_html([self._book()])
        self.assertIn("function matchesTagFilter(", html)
        self.assertIn("TAG_FILTER_STORAGE_KEY", html)
        self.assertIn("rowsByAsin", html)  # 同じ本の別タブの行にもタグを反映する

    def test_notice_and_no_results_mention_tag_condition(self):
        """タグ・種別で絞り込んで 0 件になったとき、見直す条件にタグ・種別が含まれると分かること。"""
        html = report.build_html([self._book()])
        self.assertIn("検索・種別・タグ・価格の条件を適用中です", html)
        self.assertIn("検索語・種別・タグ・価格の条件を見直してください。", _extract_section_html(html, "section-all"))


class MarksUiTest(unittest.TestCase):
    """「見た」タグ・★評価・種別（マンガ/本）・書き出しの UI と、行に載せる初期状態のテスト。

    ブラウザ上の操作は JS なので、ここでは JS が読む属性・要素・定数が出ていることを固定する。
    """

    def _book(self, title="普通の本", asin="B0MARKS001", mark=None):
        book = {"title": title, "asin": asin, "actual_price": 800, "is_unlimited": 0, "is_wanted": 0, "is_purchased": 0}
        if mark is not None:
            book["mark"] = mark
        return book

    def _row(self, book) -> str:
        section_html = _extract_section_html(report.build_html([book]), "section-all")
        return re.search(r'<tr class="book"[^>]*>', section_html).group(0)

    def test_kind_is_classified_from_title(self):
        self.assertIn('data-saved-kind="manga"', self._row(self._book("本なら売るほど 1 (ハルタコミックス)")))
        self.assertIn('data-saved-kind="book"', self._row(self._book("マンガの原理")))

    def test_imported_kind_overrides_classification(self):
        row = self._row(self._book("戦争は女の顔をしていない 6", mark={"tag": "", "rating": None, "kind": "manga"}))
        self.assertIn('data-saved-kind="manga"', row)

    def test_row_without_mark_has_empty_saved_tag_and_rating(self):
        row = self._row(self._book())
        self.assertIn('data-saved-tag="" data-saved-rating=""', row)

    def test_imported_seen_mark_and_rating_are_rendered(self):
        row = self._row(self._book(mark={"tag": "seen", "rating": 4, "kind": None}))
        self.assertIn('data-saved-tag="seen" data-saved-rating="4"', row)

    def test_rating_is_omitted_unless_seen(self):
        row = self._row(self._book(mark={"tag": "wanted", "rating": 4, "kind": None}))
        self.assertIn('data-saved-tag="wanted" data-saved-rating=""', row)

    def test_unknown_mark_values_are_not_rendered(self):
        """DB の値でも固定の集合に無いものは属性に出さないこと（属性値への想定外の文字列混入防止）。"""
        row = self._row(self._book(mark={"tag": '"><script>', "rating": 9, "kind": "anime"}))
        self.assertIn('data-saved-kind="book" data-saved-tag="" data-saved-rating=""', row)
        row = self._row(self._book(mark={"tag": "seen", "rating": True, "kind": None}))
        self.assertIn('data-saved-rating=""', row)

    def test_kind_filter_buttons_start_with_all_pressed(self):
        html = report.build_html([])
        buttons = re.findall(r'<button type="button" class="kind-filter-btn" data-kind-filter="([^"]+)" aria-pressed="([^"]+)">', html)
        self.assertEqual(buttons, [("all", "true"), ("manga", "false"), ("book", "false")])

    def test_sort_has_rating_option(self):
        self.assertIn('<option value="rating-desc">評価が高い順</option>', report.build_html([]))

    def test_script_has_seen_tag_stars_kind_toggle_and_export(self):
        html = report.build_html([self._book()])
        self.assertIn("seen: '見た'", html)
        self.assertIn("'star-btn'", html)
        self.assertIn("'kind-btn'", html)
        self.assertIn("KIND_FILTER_STORAGE_KEY", html)
        self.assertIn("MARKS_FILE_FORMAT = 'kindle-marks'", html)
        # 押した項目だけを書き出し、書き出し済みかどうかを時刻で判定すること
        self.assertIn("MARK_GROUPS = { tag: ['tag', 'rating'], kind: ['kind'] }", html)
        self.assertIn("MARKS_EXPORTED_AT_KEY", html)
        # タグは従来と同じキーで保存し、既に付けたタグを引き継ぐこと
        self.assertIn("tag: 'book-tag:'", html)

    def test_export_bar_is_present(self):
        html = report.build_html([])
        self.assertIn('id="marks-summary"', html)
        self.assertIn('<button type="button" id="export-marks-button" class="export-btn">見た・評価を書き出す</button>', html)
        self.assertIn('id="export-marks-status"', html)


class MainIntegrationTest(unittest.TestCase):
    """main() が get_books/get_price_history の結果をbuild_htmlへ正しく配線することのテスト。

    実DBには接続せず、report.get_books / report.get_price_history をモックする。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_main_test_")
        os.makedirs(os.path.join(self.tmpdir, ".git"))
        self._saved_env = {}
        for key in ("PUBLIC_SITE_DIR", "PUBLIC_SITE_URL"):
            self._saved_env[key] = os.environ.get(key)
        os.environ["PUBLIC_SITE_DIR"] = self.tmpdir
        os.environ["PUBLIC_SITE_URL"] = "https://example.invalid/"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run_main_with_marks(self, env):
        fake_book = {"title": "結合テスト本", "asin": "B0INTEG1", "actual_price": 1000, "is_unlimited": 0}
        marks = {"B0INTEG1": {"tag": "seen", "rating": 5, "kind": "manga"}}
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(
            report, "get_books", return_value=[fake_book]
        ), unittest.mock.patch.object(report, "get_price_history", return_value=[]), unittest.mock.patch.object(
            report, "get_book_marks", return_value=marks
        ):
            report.main()
        with open(os.path.join(self.tmpdir, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_main_publishes_only_kind_override_by_default(self):
        """「見た」・★は読書記録なので、既定では公開ページに載せない（種別の上書きだけ載せる）。"""
        os.environ.pop("PUBLISH_MARKS", None)
        html = self._run_main_with_marks({})
        self.assertIn('data-saved-kind="manga" data-saved-tag="" data-saved-rating=""', html)

    def test_main_publishes_tags_and_ratings_when_opted_in(self):
        html = self._run_main_with_marks({"PUBLISH_MARKS": "1"})
        self.assertIn('data-saved-kind="manga" data-saved-tag="seen" data-saved-rating="5"', html)

    def test_main_wires_get_books_and_price_history_into_output(self):
        fake_book = {
            "title": "結合テスト本",
            "asin": "B0INTEG1",
            "sell_price": 1000,
            "point_value": 0,
            "actual_price": 1000,
            "campaign_text": "",
            "timestamp": "2026-01-02T00:00:00",
            "is_unlimited": 0,
            "is_wanted": 1,
            "is_purchased": 0,
        }
        fake_history = [
            {"actual_price": 1200, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        with unittest.mock.patch.object(
            report, "get_books", return_value=[dict(fake_book)]
        ) as mock_get_books, unittest.mock.patch.object(
            report, "get_price_history", return_value=fake_history
        ) as mock_get_history, unittest.mock.patch.object(
            report, "get_book_marks", return_value={"B0INTEG1": {"tag": "seen", "rating": 5, "kind": "manga"}}
        ) as mock_get_marks:
            report.main()

        mock_get_books.assert_called_once_with(filter="all")
        mock_get_history.assert_called_once_with("B0INTEG1")
        mock_get_marks.assert_called_once_with()

        output_path = os.path.join(self.tmpdir, "index.html")
        with open(output_path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("結合テスト本", html)
        self.assertIn("<polyline", html)  # price_history が build_html まで伝播していること
        # 取り込み済みの種別の上書きがページの初期状態として行に載ること
        self.assertIn('data-saved-kind="manga"', html)


class BuildPriceHistorySvgTest(unittest.TestCase):
    """report._build_price_history_svg() のテスト（R1/R3/R4対策）。"""

    def test_returns_polyline_for_two_or_more_valid_points(self):
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("<svg", svg)
        self.assertIn("<polyline", svg)

    def test_excludes_unlimited_points_from_polyline(self):
        """R4: is_unlimited=1の点(価格0で保存される)は折れ線の座標計算から除外すること。

        座標の個数を検証する（除外フィルタが働かず3点分の座標が出ていないこと）。
        """
        history = [
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-03T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertIn("<polyline", svg)
        self.assertNotIn("データなし", svg)
        import re

        points = re.search(r'points="([^"]*)"', svg).group(1).split()
        self.assertEqual(len(points), 2)  # 有効点(KU除外後)は2点のみ

    def test_higher_price_maps_to_smaller_y_coordinate(self):
        """価格が高いほどグラフ上で上（y座標が小さい）に描画されること。"""
        history = [
            {"actual_price": 500, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        import re

        points = re.search(r'points="([^"]*)"', svg).group(1).split()
        y0 = float(points[0].split(",")[1])
        y1 = float(points[1].split(",")[1])
        self.assertLess(y1, y0)  # 1000円の点のほうが500円の点よりyが小さい(上にある)

    def test_shows_placeholder_when_fewer_than_two_valid_points(self):
        """R3: 有効な価格点が2点未満の場合はプレースホルダーを返すこと。"""
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertEqual(svg, "データなし")
        self.assertNotIn("<polyline", svg)

    def test_shows_placeholder_when_history_is_empty(self):
        svg = report._build_price_history_svg([])
        self.assertEqual(svg, "データなし")

    def test_shows_placeholder_when_all_points_are_unlimited(self):
        history = [
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 0, "is_unlimited": 1, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertEqual(svg, "データなし")

    def test_treats_missing_actual_price_as_invalid_point(self):
        history = [
            {"actual_price": None, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertEqual(svg, "データなし")

    def test_returns_no_change_text_when_all_valid_prices_are_equal(self):
        """全有効点が同価格のとき、ゼロ除算(max_price == min_price)を起こさず
        横線だけのグラフではなく「変動なし」を返すこと（カード表示の狭い欄向け）。"""
        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        self.assertEqual(svg, "変動なし")

    def test_polyline_points_are_numeric_only(self):
        """R1: 座標は数値のみで構成され、自由文字列(ユーザー由来の文字列)を含まないこと。"""

        history = [
            {"actual_price": 1000, "is_unlimited": 0, "timestamp": "2026-01-01T00:00:00"},
            {"actual_price": 800, "is_unlimited": 0, "timestamp": "2026-01-02T00:00:00"},
        ]
        svg = report._build_price_history_svg(history)
        match = re.search(r'points="([^"]*)"', svg)
        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r'^[\d.,\s\-]+$')


class LoadEnvFileTest(unittest.TestCase):
    """_load_env_file()（.env 読み込み）のテスト。

    P0対策: このリポジトリには dotenv ローダーが存在せず、.env に値を書いても
    読まれない状態だったため追加した最小実装のテスト。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="report_env_test_")
        self.env_path = os.path.join(self.tmpdir, ".env")
        self._saved_env = {}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _isolate_env_var(self, key):
        """テスト前後で当該環境変数を退避・復元する。"""
        self._saved_env[key] = os.environ.get(key)
        os.environ.pop(key, None)

    def test_loads_values_from_env_file(self):
        self._isolate_env_var("REPORT_TEST_VAR_A")
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("REPORT_TEST_VAR_A=hello\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_A"), "hello")

    def test_does_not_override_existing_env_var(self):
        self._isolate_env_var("REPORT_TEST_VAR_B")
        os.environ["REPORT_TEST_VAR_B"] = "existing"
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("REPORT_TEST_VAR_B=from_file\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_B"), "existing")

    def test_ignores_comments_and_blank_lines(self):
        self._isolate_env_var("REPORT_TEST_VAR_C")
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# comment\n\nREPORT_TEST_VAR_C=ok\n")

        report._load_env_file(self.env_path)

        self.assertEqual(os.environ.get("REPORT_TEST_VAR_C"), "ok")

    def test_missing_file_is_noop(self):
        # 例外が出ないことを確認する
        report._load_env_file(os.path.join(self.tmpdir, "does_not_exist.env"))


if __name__ == "__main__":
    unittest.main()
