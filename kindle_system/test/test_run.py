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


if __name__ == "__main__":
    unittest.main()
