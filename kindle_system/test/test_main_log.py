"""
test_main_log.py
----------------
main.py のワーカー並列時のログ出力（出力契約）の単体テスト。

scraping-hub は stdout の各行を 1 冊単位に束ねて表示するため、次の形式に依存する:
  - 1 冊の処理中に出る行は、先頭の空白を除くと `[Worker-N]` で始まる
  - 1 冊ごとに必ず 1 行 `[Worker-N][i/total] 結果: 成功|失敗|スキップ ...` を出す
  - 全ワーカー終了後に `集計: 成功 N 件 / 失敗 N 件 / スキップ N 件` を 1 行出す

ASIN 解決・クロール・DB 保存はすべてモックし、実クロールや実 DB へは一切接続しない。

使い方:
    python -m unittest discover -s test -p "test_main_log.py" -v
"""

import asyncio
import contextlib
import io
import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import main


class FormatHelpersTest(unittest.TestCase):
    def test_success_result_line(self):
        self.assertEqual(
            main.format_result_line(1, 3, 12, main.RESULT_SUCCESS, "価格=¥2,695 ポイント=81pt"),
            "[Worker-1][3/12] 結果: 成功 価格=¥2,695 ポイント=81pt",
        )

    def test_result_line_without_detail_has_no_trailing_space(self):
        self.assertEqual(
            main.format_result_line(2, 1, 1, main.RESULT_SKIP),
            "[Worker-2][1/1] 結果: スキップ",
        )

    def test_price_summary_uses_thousands_separator(self):
        self.assertEqual(
            main.format_price_summary({"sell_price": 2695, "point_value": 81}),
            "価格=¥2,695 ポイント=81pt",
        )

    def test_classify_treats_zero_price_as_success(self):
        # Kindle Unlimited は安全弁で価格 0 円になるが、取得には成功している
        status, detail = main.classify_crawl_result({"sell_price": 0, "point_value": 0})
        self.assertEqual(status, main.RESULT_SUCCESS)
        self.assertEqual(detail, "価格=¥0 ポイント=0pt")

    def test_classify_treats_missing_price_as_failure(self):
        status, detail = main.classify_crawl_result({"sell_price": None, "point_value": 0})
        self.assertEqual(status, main.RESULT_FAILURE)
        self.assertIn("価格を取得できませんでした", detail)

    def test_summary_line(self):
        counts = {main.RESULT_SUCCESS: 3, main.RESULT_FAILURE: 1, main.RESULT_SKIP: 2}
        self.assertEqual(
            main.format_summary_line(counts),
            "集計: 成功 3 件 / 失敗 1 件 / スキップ 2 件",
        )
        self.assertEqual(
            main.format_summary_line(counts, resumed=4),
            "集計: 成功 3 件 / 失敗 1 件 / スキップ 2 件（前回処理済み 4 件）",
        )


START_RE = re.compile(r"^\[Worker-(\d+)\]\[(\d+)/(\d+)\] ")
RESULT_RE = re.compile(r"^\[Worker-(\d+)\]\[(\d+)/(\d+)\] 結果: (成功|失敗|スキップ)(?: (.*))?$")


class RunIntegrationLogContractTest(unittest.TestCase):
    """run_integration を外部接続なしで回し、出力契約を満たすことを確認する。"""

    SAMPLES = [
        {"asin": "S1", "title": "成功する本"},
        {"asin": "S2", "title": "ASIN が解決できない本"},
        {"asin": "S3", "title": "ASIN 解決で例外が出る本"},
        {"asin": "S4", "title": "クロールで例外が出る本"},
        {"asin": "S5", "title": "前回処理済みの本"},
        {"asin": "S6", "title": "価格が取れない本"},
        {"asin": "S7", "title": "process_book の外まで例外が出る本"},
    ]

    def _run(self, workers: int) -> list:
        paid_calls = {}

        def fake_get_paid_asin(sample_asin):
            paid_calls[sample_asin] = paid_calls.get(sample_asin, 0) + 1
            if sample_asin == "S7" and paid_calls[sample_asin] >= 2:
                raise RuntimeError("DB が壊れています")
            return {"S1": "P1", "S4": "P4", "S5": "P5", "S6": "P6"}.get(sample_asin)

        async def fake_resolve(sample_asin, **kwargs):
            if sample_asin == "S3":
                raise RuntimeError("タイムアウト")
            return None

        async def fake_crawl(paid_asin, **kwargs):
            if paid_asin == "P4":
                raise RuntimeError("ブラウザが落ちました")
            if paid_asin == "P6":
                return {"asin": paid_asin, "sell_price": None, "point_value": 0}
            return {"asin": paid_asin, "sell_price": 2695, "point_value": 81}

        real_sleep = asyncio.sleep

        async def fast_sleep(_seconds):
            await real_sleep(0)

        buf = io.StringIO()
        with patch.object(main, "init_db"), \
             patch.object(main, "get_or_create_session_start", return_value="2026-01-01T00:00:00"), \
             patch.object(main, "get_session_processed_asins", return_value={"P5"}), \
             patch.object(main, "extract_samples", return_value=list(self.SAMPLES)), \
             patch.object(main, "get_purchased_asins", return_value=set()), \
             patch.object(main, "get_paid_asin", side_effect=fake_get_paid_asin), \
             patch.object(main, "resolve_sample_to_paid", new=AsyncMock(side_effect=fake_resolve)), \
             patch.object(main, "crawl_price_info", new=AsyncMock(side_effect=fake_crawl)), \
             patch.object(main, "save_mapping"), \
             patch.object(main, "save_price_history"), \
             patch.object(main, "clear_session"), \
             patch.object(main.asyncio, "sleep", new=fast_sleep), \
             contextlib.redirect_stdout(buf):
            asyncio.run(main.run_integration(xml_path="dummy.xml", start=1, workers=workers))
        return buf.getvalue().splitlines()

    def _results(self, lines):
        results = {}
        for line in lines:
            m = RESULT_RE.match(line)
            if m:
                index = int(m.group(2))
                self.assertNotIn(index, results, f"結果行が重複しています: {line}")
                results[index] = (m.group(4), m.group(5) or "")
        return results

    def test_every_book_gets_exactly_one_result_line(self):
        for workers in (1, 3):
            with self.subTest(workers=workers):
                results = self._results(self._run(workers))
                self.assertEqual(results[1], (main.RESULT_SUCCESS, "価格=¥2,695 ポイント=81pt"))
                self.assertEqual(results[2], (main.RESULT_SKIP, "本編 ASIN を解決できませんでした"))
                self.assertEqual(results[3], (main.RESULT_FAILURE, "ASIN 解決中にエラー: タイムアウト"))
                self.assertEqual(results[4], (main.RESULT_FAILURE, "クロール中にエラー: ブラウザが落ちました"))
                self.assertNotIn(5, results)  # Resume-Skip はワーカーに渡らない
                self.assertEqual(results[6][0], main.RESULT_FAILURE)
                self.assertEqual(results[7], (main.RESULT_FAILURE, "処理エラー: DB が壊れています"))

    def test_summary_line_counts_results_and_resumed_books(self):
        lines = self._run(3)
        summary = [line for line in lines if line.startswith("集計: ")]
        self.assertEqual(summary, ["集計: 成功 1 件 / 失敗 4 件 / スキップ 1 件（前回処理済み 1 件）"])
        done_index = lines.index("  全処理が完了しました。")
        self.assertEqual(lines[done_index + 1], summary[0])

    def test_all_lines_while_processing_books_are_worker_prefixed(self):
        lines = self._run(3)
        first = next(n for n, line in enumerate(lines) if START_RE.match(line))
        last = max(n for n, line in enumerate(lines) if RESULT_RE.match(line))
        for line in lines[first:last + 1]:
            stripped = line.strip()
            if not stripped:
                continue
            self.assertRegex(stripped, r"^\[Worker-\d+\]", f"ワーカー接頭辞がない行: {line!r}")

    def test_start_lines_are_unindented_and_one_per_book(self):
        # 工程行（`  [Worker-1][3/4] 価格情報をクロール中`）も `[Worker-N][x/y]` を含むため、
        # 開始行・結果行は「行頭に空白が無い」ことで工程行と区別する
        for workers in (1, 3):
            with self.subTest(workers=workers):
                lines = self._run(workers)
                starts = [l for l in lines if START_RE.match(l) and not RESULT_RE.match(l)]
                self.assertEqual(sorted(int(START_RE.match(l).group(2)) for l in starts), [1, 2, 3, 4, 6, 7])
                step_lines = [l for l in lines if re.match(r"^\s+\[Worker-\d+\]\[\d+/\d+\]", l)]
                self.assertTrue(step_lines, "工程行が見つかりません")

    def test_single_worker_lines_belong_to_the_book_between_start_and_result(self):
        # 並列数 1 では行が混ざらないため、開始行〜結果行の間はすべて同じワーカーの行になる
        lines = self._run(1)
        current = None
        for line in lines:
            stripped = line.strip()
            start = START_RE.match(line)  # 開始行は行頭に空白が無い（工程行と区別）
            if start and not RESULT_RE.match(line):
                current = start.group(1)
                continue
            if current is None or not stripped:
                continue
            self.assertTrue(stripped.startswith(f"[Worker-{current}]"), f"接頭辞が違う行: {line!r}")
            if RESULT_RE.match(line):
                current = None

    def test_no_pictographic_symbols_in_output(self):
        output = "\n".join(self._run(1))
        self.assertNotIn("✗", output)


class RunIntegrationOnlyAsinsTest(unittest.TestCase):
    """only_asins を渡すと、その Sample ASIN の本だけを処理し直す（失敗した本の再実行用。
    scraping-hub backlog 20260926-retry-failed-books）。"""

    SAMPLES = [
        {"asin": "S1", "title": "本1"},
        {"asin": "S2", "title": "本2（前回処理済みだが失敗していた）"},
        {"asin": "S3", "title": "本3"},
    ]

    def _run(self, only_asins):
        crawled = []

        async def fake_crawl(paid_asin, **kwargs):
            crawled.append(paid_asin)
            return {"asin": paid_asin, "sell_price": 100, "point_value": 1}

        buf = io.StringIO()
        with patch.object(main, "init_db"), \
             patch.object(main, "get_or_create_session_start") as session_start, \
             patch.object(main, "get_session_processed_asins", return_value={"P2"}), \
             patch.object(main, "extract_samples", return_value=list(self.SAMPLES)), \
             patch.object(main, "get_purchased_asins", return_value=set()), \
             patch.object(main, "get_paid_asin", side_effect=lambda s: {"S1": "P1", "S2": "P2", "S3": "P3"}[s]), \
             patch.object(main, "crawl_price_info", new=AsyncMock(side_effect=fake_crawl)), \
             patch.object(main, "save_price_history"), \
             patch.object(main, "clear_session") as clear_session, \
             patch("builtins.input") as prompt, \
             contextlib.redirect_stdout(buf):
            asyncio.run(main.run_integration(xml_path="dummy.xml", workers=1, only_asins=only_asins))
        return buf.getvalue().splitlines(), crawled, session_start, clear_session, prompt

    def test_processes_only_given_books_even_if_resume_would_skip_them(self):
        lines, crawled, *_ = self._run({"S2", "S3", "S9"})

        self.assertEqual(sorted(crawled), ["P2", "P3"])
        starts = [l for l in lines if START_RE.match(l) and not RESULT_RE.match(l)]
        # 番号と全体数は今回処理する本の中で振り直す（進捗表示の全体数が今回の冊数になる）
        self.assertEqual([START_RE.match(l).group(2) + "/" + START_RE.match(l).group(3) for l in starts], ["1/2", "2/2"])
        self.assertTrue(any("S9" in l and "見つかりません" in l for l in lines))

    def test_does_not_touch_resume_session_or_ask_start_index(self):
        _, _, session_start, clear_session, prompt = self._run({"S2"})

        session_start.assert_not_called()
        clear_session.assert_not_called()
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
