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

import os
import sys
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
