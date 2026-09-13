"""
test_run.py
-----------
run.py（CLIバッチ運用エントリポイント: sync/want/purchase）の単体テスト。

main.run_integration / src.bookmeter_sync.sync_bookmeter_wishlist / report.main /
git コマンドはすべてモックし、実クロール・実読書メーター通信・実git操作へは
一切接続しない。

使い方:
    python3 -m unittest test.test_run -v
"""

import argparse
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
                ["git", "add", "index.html"],
                ["git", "diff", "--cached", "--quiet", "--", "index.html"],
                ["git", "commit", "-m", "chore: update wishlist", "-q", "--", "index.html"],
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
                ["git", "add", "index.html"],
                ["git", "diff", "--cached", "--quiet", "--", "index.html"],
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
        self.assertEqual(called_cmds, [["git", "add", "index.html"]])

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
                ["git", "add", "index.html"],
                ["git", "diff", "--cached", "--quiet", "--", "index.html"],
                ["git", "commit", "-m", "chore: update wishlist", "-q", "--", "index.html"],
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

    @patch("run.os.path.exists", return_value=True)
    @patch("run.publish")
    @patch("run.sync_bookmeter_wishlist", new_callable=AsyncMock)
    @patch("run.main_module.run_integration", new_callable=AsyncMock)
    def test_calls_run_integration_then_sync_then_publish_in_order(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        manager = Mock()
        manager.attach_mock(mock_run_integration, "run_integration")
        manager.attach_mock(mock_sync, "sync_bookmeter_wishlist")
        manager.attach_mock(mock_publish, "publish")

        run.cmd_sync(argparse.Namespace(workers=2, limit=5, start=3))

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
    def test_clamps_workers_above_5_down_to_5(
        self, mock_run_integration, mock_sync, mock_publish, mock_exists
    ):
        run.cmd_sync(argparse.Namespace(workers=10, limit=None, start=None))

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
        run.cmd_sync(argparse.Namespace(workers=0, limit=None, start=None))

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
                run.cmd_sync(argparse.Namespace(workers=1, limit=None, start=None))

        self.assertEqual(cm.exception.code, 1)
        mock_run_integration.assert_not_called()
        mock_sync.assert_not_called()
        mock_publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
