"""
test_run.py
-----------
run.py（CLIバッチ運用エントリポイント: sync/want/purchase/import-marks/recommend）の単体テスト。

main.run_integration / src.bookmeter_sync.sync_bookmeter_wishlist / report.main /
git コマンドはすべてモックし、実クロール・実読書メーター通信・実git操作へは
一切接続しない。

使い方:
    python3 -m unittest test.test_run -v
"""

import argparse
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import run


class PublishMissingEnvTest(unittest.TestCase):
    """PUBLIC_SITE_DIR / PUBLIC_SITE_URL 未設定時は、明示エラーを出して
    SystemExit(1) し、git コマンドが一切呼ばれないこと（do_publish() と同じ検証パターン）。"""

    @patch.dict(os.environ, {}, clear=False)
    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_both_missing_raises_system_exit_and_does_not_run_subprocess(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        os.environ.pop("PUBLIC_SITE_DIR", None)
        os.environ.pop("PUBLIC_SITE_URL", None)

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)
        mock_report_main.assert_not_called()
        mock_subprocess_run.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_url_missing_only_raises_system_exit_and_does_not_run_subprocess(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        os.environ["PUBLIC_SITE_DIR"] = "/tmp/somewhere"
        os.environ.pop("PUBLIC_SITE_URL", None)

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)
        mock_report_main.assert_not_called()
        mock_subprocess_run.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_dir_missing_only_raises_system_exit_and_does_not_run_subprocess(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        os.environ.pop("PUBLIC_SITE_DIR", None)
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)
        mock_report_main.assert_not_called()
        mock_subprocess_run.assert_not_called()


class _FakeCompletedProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode


def _subprocess_run_side_effect(returncodes: dict):
    """
    call_args の cmd（第1引数）を空白結合した文字列の先頭一致で returncode を返す
    フェイク subprocess.run。辞書に無いコマンドは returncode=0 とする。
    """

    def _side_effect(cmd, *args, **kwargs):
        joined = " ".join(cmd)
        for prefix, returncode in returncodes.items():
            if joined.startswith(prefix):
                return _FakeCompletedProcess(returncode)
        return _FakeCompletedProcess(0)

    return _side_effect


class PublishGitSequenceTest(unittest.TestCase):
    """publish() が report.main() → git add → git diff --cached --quiet（差分判定）→
    （差分ありのみ）git commit → （常時）git push の順に呼ぶこと、各コマンド失敗時は
    後続を実行せず SystemExit(1) することを subprocess.run モックで検証する。"""

    def setUp(self):
        os.environ["PUBLIC_SITE_DIR"] = "/tmp/fake-public-site"
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

    def tearDown(self):
        os.environ.pop("PUBLIC_SITE_DIR", None)
        os.environ.pop("PUBLIC_SITE_URL", None)

    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_calls_report_then_add_diff_commit_push_in_order_when_diff_exists(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        mock_subprocess_run.side_effect = _subprocess_run_side_effect(
            {"git diff": 1}  # 差分あり
        )

        run.publish()

        mock_report_main.assert_called_once()
        called_cmds = [call.args[0] for call in mock_subprocess_run.call_args_list]
        self.assertEqual(
            called_cmds,
            [
                ["git", "add", "wishlist.json"],
                ["git", "diff", "--cached", "--quiet", "--", "wishlist.json"],
                ["git", "commit", "-m", "chore: update wishlist", "-q", "--", "wishlist.json"],
                ["git", "push", "-q"],
            ],
        )
        for call in mock_subprocess_run.call_args_list:
            self.assertEqual(call.kwargs.get("cwd"), "/tmp/fake-public-site")

    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_skips_commit_but_still_pushes_when_no_diff(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        mock_subprocess_run.side_effect = _subprocess_run_side_effect(
            {"git diff": 0}  # 差分なし
        )

        run.publish()

        called_cmds = [call.args[0] for call in mock_subprocess_run.call_args_list]
        self.assertEqual(
            called_cmds,
            [
                ["git", "add", "wishlist.json"],
                ["git", "diff", "--cached", "--quiet", "--", "wishlist.json"],
                ["git", "push", "-q"],
            ],
        )

    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_add_failure_stops_before_diff_commit_push(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        mock_subprocess_run.side_effect = _subprocess_run_side_effect({"git add": 1})

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)
        called_cmds = [call.args[0] for call in mock_subprocess_run.call_args_list]
        self.assertEqual(called_cmds, [["git", "add", "wishlist.json"]])

    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_commit_failure_stops_before_push(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        mock_subprocess_run.side_effect = _subprocess_run_side_effect(
            {"git diff": 1, "git commit": 1}
        )

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)
        called_cmds = [call.args[0] for call in mock_subprocess_run.call_args_list]
        self.assertEqual(
            called_cmds,
            [
                ["git", "add", "wishlist.json"],
                ["git", "diff", "--cached", "--quiet", "--", "wishlist.json"],
                ["git", "commit", "-m", "chore: update wishlist", "-q", "--", "wishlist.json"],
            ],
        )

    @patch("run.subprocess.run")
    @patch("run.report.main")
    @patch("run._load_env_file")
    def test_push_failure_raises_system_exit(
        self, mock_load_env, mock_report_main, mock_subprocess_run
    ):
        mock_subprocess_run.side_effect = _subprocess_run_side_effect(
            {"git diff": 0, "git push": 1}
        )

        with self.assertRaises(SystemExit) as cm:
            run.publish()

        self.assertEqual(cm.exception.code, 1)


class SyncCommandTest(unittest.TestCase):
    """sync サブコマンド: main.run_integration → sync_bookmeter_wishlist → publish()
    の順に1回ずつ呼ばれること、--workers/--limit/--start が main.py と同じ意味
    （--workers は1〜5にクランプ）で run_integration に渡ることを検証する。"""

    _EMPTY_SYNC_RESULT = {"total": 0, "registered": 0, "skipped": 0, "failed_titles": []}

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_calls_run_integration_then_sync_then_publish_in_order(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        mock_sync.return_value = dict(self._EMPTY_SYNC_RESULT)
        manager = Mock()
        manager.attach_mock(mock_run_integration, "run_integration")
        manager.attach_mock(mock_sync, "sync_bookmeter_wishlist")
        manager.attach_mock(mock_publish, "publish")

        run.cmd_sync(argparse.Namespace(workers=2, limit=5, start=3, target="both"))

        self.assertEqual(
            [c[0] for c in manager.mock_calls],
            ["run_integration", "sync_bookmeter_wishlist", "publish"],
        )
        mock_run_integration.assert_called_once()
        _, kwargs = mock_run_integration.call_args
        self.assertEqual(kwargs["limit"], 5)
        self.assertEqual(kwargs["start"], 3)
        self.assertEqual(kwargs["workers"], 2)
        mock_sync.assert_called_once()
        mock_publish.assert_called_once()

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_sync_result_is_reported_via_progress_cb_and_skip_list(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        """
        sync_bookmeter_wishlist() は progress_cb 省略時に一切出力せず、失敗した
        本は戻り値の failed_titles にしか載らない契約（src/bookmeter_sync.py）。
        無音のまま公開してしまう回帰を防ぐため、progress_cb=print が渡ること、
        failed_titles があればスキップ一覧が標準出力へ出ることを固定する。
        """
        mock_sync.return_value = {
            "total": 2,
            "registered": 1,
            "skipped": 1,
            "failed_titles": ["解決できなかった本"],
        }

        with patch("builtins.print") as mock_print:
            run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="both"))

        _, kwargs = mock_sync.call_args
        self.assertIs(kwargs.get("progress_cb"), mock_print)
        printed = [call.args[0] for call in mock_print.call_args_list if call.args]
        self.assertTrue(
            any("解決できなかった本" in line for line in printed),
            f"スキップ一覧が出力されていない: {printed}",
        )

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_clamps_workers_above_5_down_to_5(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        mock_sync.return_value = dict(self._EMPTY_SYNC_RESULT)
        run.cmd_sync(argparse.Namespace(workers=10, limit=None, start=None, target="both"))

        _, kwargs = mock_run_integration.call_args
        self.assertEqual(kwargs["workers"], 5)
        self.assertIsNone(kwargs["limit"])
        self.assertIsNone(kwargs["start"])

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_clamps_workers_below_1_up_to_1(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        mock_sync.return_value = dict(self._EMPTY_SYNC_RESULT)
        run.cmd_sync(argparse.Namespace(workers=0, limit=None, start=None, target="both"))

        _, kwargs = mock_run_integration.call_args
        self.assertEqual(kwargs["workers"], 1)

    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_exits_when_default_xml_is_missing(
        self, mock_run_integration, mock_sync, mock_publish
    ):
        with patch("run.os.path.exists", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="both"))

        self.assertEqual(cm.exception.code, 1)
        mock_run_integration.assert_not_called()
        mock_sync.assert_not_called()
        mock_publish.assert_not_called()


class SyncCommandTargetTest(unittest.TestCase):
    """--target による実行対象の絞り込み（kindle のみ / bookmeter のみ）を検証する。"""

    _EMPTY_SYNC_RESULT = {"total": 0, "registered": 0, "skipped": 0, "failed_titles": []}

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_target_kindle_skips_bookmeter_sync(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="kindle"))

        mock_run_integration.assert_called_once()
        mock_sync.assert_not_called()
        mock_publish.assert_called_once()

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_target_bookmeter_skips_kindle_crawl(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        mock_sync.return_value = dict(self._EMPTY_SYNC_RESULT)

        run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="bookmeter"))

        mock_run_integration.assert_not_called()
        mock_sync.assert_called_once()
        mock_publish.assert_called_once()

    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_target_bookmeter_does_not_require_xml_file(
        self, mock_run_integration, mock_sync, mock_publish
    ):
        """bookmeter 単独実行では Kindle キャッシュ XML の存在チェックを行わない。"""
        mock_sync.return_value = dict(self._EMPTY_SYNC_RESULT)

        with patch("run.os.path.exists", return_value=False) as mock_exists:
            run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="bookmeter"))

        mock_exists.assert_not_called()
        mock_run_integration.assert_not_called()
        mock_sync.assert_called_once()
        mock_publish.assert_called_once()

    @patch("run.os.path.exists", return_value=False)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_target_kindle_still_exits_when_xml_missing(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        with self.assertRaises(SystemExit) as cm:
            run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None, target="kindle"))

        self.assertEqual(cm.exception.code, 1)
        mock_run_integration.assert_not_called()
        mock_publish.assert_not_called()

    def test_sync_parser_defaults_target_to_both(self):
        parser = run.build_parser()
        args = parser.parse_args(["sync"])
        self.assertEqual(args.target, "both")

    def test_sync_parser_rejects_unknown_target(self):
        parser = run.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["sync", "--target", "unknown"])


class WantPurchaseArgparseTest(unittest.TestCase):
    """want / purchase サブコマンドは --on / --off のどちらか一方が必須
    （mutually exclusive group + required）であることを検証する。"""

    def test_want_requires_on_or_off(self):
        parser = run.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["want", "B0EXAMPLE"])

    def test_want_rejects_both_on_and_off(self):
        parser = run.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["want", "B0EXAMPLE", "--on", "--off"])

    def test_want_accepts_on_only(self):
        parser = run.build_parser()
        args = parser.parse_args(["want", "B0EXAMPLE", "--on"])
        self.assertEqual(args.asin, "B0EXAMPLE")
        self.assertTrue(args.on)
        self.assertFalse(args.off)
        self.assertIs(args.func, run.cmd_want)

    def test_purchase_requires_on_or_off(self):
        parser = run.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["purchase", "B0EXAMPLE"])

    def test_purchase_rejects_both_on_and_off(self):
        parser = run.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["purchase", "B0EXAMPLE", "--on", "--off"])

    def test_purchase_accepts_off_only(self):
        parser = run.build_parser()
        args = parser.parse_args(["purchase", "B0EXAMPLE", "--off"])
        self.assertEqual(args.asin, "B0EXAMPLE")
        self.assertFalse(args.on)
        self.assertTrue(args.off)
        self.assertIs(args.func, run.cmd_purchase)


class WantCommandTest(unittest.TestCase):
    @patch("run.set_wanted", return_value=True)
    def test_on_calls_set_wanted_with_status_1(self, mock_set_wanted):
        run.cmd_want(argparse.Namespace(asin="B0EXAMPLE", on=True, off=False))
        mock_set_wanted.assert_called_once_with("B0EXAMPLE", 1)

    @patch("run.set_wanted", return_value=True)
    def test_off_calls_set_wanted_with_status_0(self, mock_set_wanted):
        run.cmd_want(argparse.Namespace(asin="B0EXAMPLE", on=False, off=True))
        mock_set_wanted.assert_called_once_with("B0EXAMPLE", 0)

    @patch("run.set_wanted", return_value=False)
    def test_not_found_exits_with_code_1(self, mock_set_wanted):
        with self.assertRaises(SystemExit) as cm:
            run.cmd_want(argparse.Namespace(asin="B0MISSING", on=True, off=False))
        self.assertEqual(cm.exception.code, 1)


class PurchaseCommandTest(unittest.TestCase):
    @patch("run.set_purchased", return_value=True)
    def test_on_calls_set_purchased_with_status_1(self, mock_set_purchased):
        run.cmd_purchase(argparse.Namespace(asin="B0EXAMPLE", on=True, off=False))
        mock_set_purchased.assert_called_once_with("B0EXAMPLE", 1)

    @patch("run.set_purchased", return_value=True)
    def test_off_calls_set_purchased_with_status_0(self, mock_set_purchased):
        run.cmd_purchase(argparse.Namespace(asin="B0EXAMPLE", on=False, off=True))
        mock_set_purchased.assert_called_once_with("B0EXAMPLE", 0)

    @patch("run.set_purchased", return_value=False)
    def test_not_found_exits_with_code_1(self, mock_set_purchased):
        with self.assertRaises(SystemExit) as cm:
            run.cmd_purchase(argparse.Namespace(asin="B0MISSING", on=True, off=False))
        self.assertEqual(cm.exception.code, 1)


_MARKS_ITEMS = [{"asin": "B0SEEN0001", "title": "見た本", "tag": "seen", "rating": 4, "kind": "book"}]


@patch("run._load_env_file")
@patch("run.get_book_marks", return_value={})
@patch("run.get_books", return_value=[])
@patch("run.publish")
@patch("run.import_marks", return_value={"updated": 1, "deleted": 0, "skipped": []})
class ImportMarksCommandTest(unittest.TestCase):
    """`run.py import-marks`: 公開ページの書き出しファイルを検証して import_marks へ渡すこと。"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="import_marks_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
        return path

    def _args(self, *argv):
        return run.build_parser().parse_args(["import-marks", *argv])

    def test_valid_file_is_imported_without_publishing(self, mock_import, mock_publish, *_):
        path = self._write("kindle-marks-20260927-1200.json", {"format": "kindle-marks", "version": 1, "items": _MARKS_ITEMS})
        run.cmd_import_marks(self._args(path))
        mock_import.assert_called_once_with(_MARKS_ITEMS)
        mock_publish.assert_not_called()

    def test_publish_option_publishes_after_import(self, mock_import, mock_publish, *_):
        path = self._write("marks.json", {"format": "kindle-marks", "items": _MARKS_ITEMS})
        run.cmd_import_marks(self._args(path, "--publish"))
        mock_import.assert_called_once()
        mock_publish.assert_called_once_with()

    def test_file_with_bom_is_accepted(self, mock_import, *_):
        """Windows のエディタで保存し直して BOM が付いても読めること。"""
        path = self._write("bom.json", "﻿" + json.dumps({"format": "kindle-marks", "items": []}))
        run.cmd_import_marks(self._args(path))
        mock_import.assert_called_once_with([])

    def test_other_json_is_rejected(self, mock_import, *_):
        path = self._write("other.json", {"items": _MARKS_ITEMS})
        with self.assertRaises(SystemExit) as cm:
            run.cmd_import_marks(self._args(path))
        self.assertEqual(cm.exception.code, 1)
        mock_import.assert_not_called()

    def test_broken_json_is_rejected(self, mock_import, *_):
        path = self._write("broken.json", "{not json")
        with self.assertRaises(SystemExit) as cm:
            run.cmd_import_marks(self._args(path))
        self.assertEqual(cm.exception.code, 1)
        mock_import.assert_not_called()

    def test_missing_file_is_rejected(self, mock_import, *_):
        with self.assertRaises(SystemExit) as cm:
            run.cmd_import_marks(self._args(os.path.join(self.tmpdir, "nothing.json")))
        self.assertEqual(cm.exception.code, 1)
        mock_import.assert_not_called()

    def test_without_path_uses_latest_file_in_download_dir(self, mock_import, *_):
        old = self._write("kindle-marks-20260101-0000.json", {"format": "kindle-marks", "items": []})
        self._write("kindle-marks-20260927-1200.json", {"format": "kindle-marks", "items": _MARKS_ITEMS})
        os.utime(old, (1_000_000, 1_000_000))
        with patch.dict(os.environ, {"MARKS_DOWNLOAD_DIR": self.tmpdir}):
            run.cmd_import_marks(self._args())
        mock_import.assert_called_once_with(_MARKS_ITEMS)

    def test_db_write_error_exits_with_message(self, mock_import, *_):
        from sqlalchemy.exc import OperationalError
        mock_import.side_effect = OperationalError("INSERT", {}, Exception("attempt to write a readonly database"))
        path = self._write("marks.json", {"format": "kindle-marks", "items": _MARKS_ITEMS})
        with self.assertRaises(SystemExit) as cm:
            run.cmd_import_marks(self._args(path))
        self.assertEqual(cm.exception.code, 1)

    def test_without_path_and_no_file_exits(self, mock_import, *_):
        with patch.dict(os.environ, {"MARKS_DOWNLOAD_DIR": self.tmpdir}):
            with self.assertRaises(SystemExit) as cm:
                run.cmd_import_marks(self._args())
        self.assertEqual(cm.exception.code, 1)
        mock_import.assert_not_called()


_BOOKS = [
    {"asin": "B0MANGA001", "title": "読んだマンガ (ハルタコミックス)", "is_purchased": 0},
    {"asin": "B0MANGA002", "title": "候補のマンガ (バンチコミックス)", "is_purchased": 0},
    {"asin": "B0BOOK0001", "title": "候補の本", "is_purchased": 0},
]
_MARKS = {"B0MANGA001": {"tag": "seen", "rating": 5, "kind": None, "title": None, "updated_at": "2026-09-27"}}


@patch.dict(os.environ, {"LOCAL_LLM_API": "", "LOCAL_LLM_URL": "", "LOCAL_LLM_MODEL": ""})
@patch("run._load_env_file")
@patch("run.get_book_marks", return_value=_MARKS)
@patch("run.get_books", return_value=_BOOKS)
@patch("run.recommender.resolve_model", return_value="qwen2.5:7b")
@patch(
    "run.recommender.request_recommendations",
    return_value=json.dumps(
        {"taste": "静かな話が好き", "from_list": [{"asin": "B0MANGA002", "reason": "雰囲気が近い"}], "new_titles": []},
        ensure_ascii=False,
    ),
)
class RecommendCommandTest(unittest.TestCase):
    """`run.py recommend`: 種別ごとにローカル LLM へ問い合わせ、結果を表示すること（HTTP はモック）。"""

    def _run(self, *argv):
        import io
        from contextlib import redirect_stdout
        args = run.build_parser().parse_args(["recommend", *argv])
        out = io.StringIO()
        with redirect_stdout(out):
            args.func(args)
        return out.getvalue()

    def test_manga_recommendation_is_printed(self, mock_request, mock_resolve, *_):
        output = self._run("--kind", "manga")
        mock_request.assert_called_once()
        prompt = mock_request.call_args[0][0][1]["content"]
        self.assertIn("読んだマンガ (ハルタコミックス)（★5）", prompt)
        self.assertIn("[B0MANGA002]", prompt)
        self.assertNotIn("候補の本", prompt)  # 本はマンガの候補に混ぜない
        self.assertIn("■ マンガのおすすめ", output)
        self.assertIn("候補のマンガ (バンチコミックス)", output)
        self.assertIn("https://www.amazon.co.jp/dp/B0MANGA002", output)

    def test_kind_without_seen_is_skipped_without_llm_call(self, mock_request, mock_resolve, *_):
        output = self._run("--kind", "book")
        mock_request.assert_not_called()
        mock_resolve.assert_not_called()
        self.assertIn("「見た」本がまだ無いため", output)

    def test_all_queries_only_kinds_with_seen(self, mock_request, *_):
        output = self._run()
        self.assertEqual(mock_request.call_count, 1)
        self.assertIn("■ マンガのおすすめ", output)
        self.assertIn("「見た」本がまだ無いため", output)

    def test_dry_run_prints_prompt_without_llm_call(self, mock_request, mock_resolve, *_):
        output = self._run("--kind", "manga", "--dry-run")
        mock_request.assert_not_called()
        mock_resolve.assert_not_called()
        self.assertIn("ローカル LLM に送るプロンプト", output)
        self.assertIn("[B0MANGA002]", output)

    def test_llm_error_exits_with_code_1(self, mock_request, *_):
        mock_request.side_effect = run.recommender.LocalLlmError("接続できません")
        with self.assertRaises(SystemExit) as cm:
            self._run("--kind", "manga")
        self.assertEqual(cm.exception.code, 1)

    def test_invalid_api_setting_exits_with_code_1(self, mock_request, *_):
        with patch.dict(os.environ, {"LOCAL_LLM_API": "cloud"}):
            with self.assertRaises(SystemExit) as cm:
                self._run()
        self.assertEqual(cm.exception.code, 1)
        mock_request.assert_not_called()


class RecommendArgparseTest(unittest.TestCase):
    def test_defaults(self):
        args = run.build_parser().parse_args(["recommend"])
        self.assertEqual((args.kind, args.count, args.new, args.dry_run), ("all", 5, 3, False))
        self.assertIs(args.func, run.cmd_recommend)

    def test_rejects_unknown_kind_and_negative_count(self):
        parser = run.build_parser()
        for argv in (["recommend", "--kind", "anime"], ["recommend", "--count", "-1"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit):
                    parser.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
