"""
test_server_publish.py
------------------------
src/server.py の do_publish() の単体テスト（環境変数未設定時の防御）。

report.py のサブプロセス実行・git コマンドはすべてモックし、実ネットワーク・
実サブプロセス・実 git 操作へは一切接続しない。R2 対策（_require_env() は
sys.exit(1) するため in-process では絶対に呼ばない）が守られていることを検証する。

使い方:
    python3 -m unittest test.test_server_publish -v
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src import server


class DoPublishMissingEnvTest(unittest.TestCase):
    """PUBLIC_SITE_DIR / PUBLIC_SITE_URL 未設定時は、サーバープロセスを落とさず
    明示エラーをジョブログへ出して終了すること（R2対策）。"""

    @patch.dict(os.environ, {}, clear=False)
    @patch("src.server._run_proc", new_callable=AsyncMock)
    @patch("src.server._load_env_file")
    def test_both_missing_emits_error_and_does_not_run_subprocess(
        self, mock_load_env, mock_run_proc
    ):
        os.environ.pop("PUBLIC_SITE_DIR", None)
        os.environ.pop("PUBLIC_SITE_URL", None)

        asyncio.run(server.do_publish())

        self.assertFalse(server.job.running)
        self.assertTrue(
            any(
                "PUBLIC_SITE_DIR" in line and "PUBLIC_SITE_URL" in line
                for line in server.job.lines
            )
        )
        mock_run_proc.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    @patch("src.server._run_proc", new_callable=AsyncMock)
    @patch("src.server._load_env_file")
    def test_url_missing_only_emits_error_and_does_not_run_subprocess(
        self, mock_load_env, mock_run_proc
    ):
        os.environ["PUBLIC_SITE_DIR"] = "/tmp/somewhere"
        os.environ.pop("PUBLIC_SITE_URL", None)

        asyncio.run(server.do_publish())

        self.assertFalse(server.job.running)
        mock_run_proc.assert_not_called()

    @patch.dict(os.environ, {}, clear=False)
    @patch("src.server._run_proc", new_callable=AsyncMock)
    @patch("src.server._load_env_file")
    def test_dir_missing_only_emits_error_and_does_not_run_subprocess(
        self, mock_load_env, mock_run_proc
    ):
        os.environ.pop("PUBLIC_SITE_DIR", None)
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        asyncio.run(server.do_publish())

        self.assertFalse(server.job.running)
        mock_run_proc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
