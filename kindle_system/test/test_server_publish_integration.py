"""
test_server_publish_integration.py
------------------------------------
src/server.py の do_publish() の統合テスト。

report.py の実サブプロセス実行と実 git コマンド（add/commit/push）を、
tempdir に用意したダミーの GitHub Pages 公開リポジトリ（bare remote + clone）に対して
実際に走らせ、(a) 変更があれば commit + push まで実行されること、
(b) 変更が無ければ commit を作らず push だけを試みること、
(c) push が失敗した場合に明示エラーが出て正常終了に見えないことを確認する。

DB は本 worktree 専有の data/kindle_monitor.db を使う（Phase ごとに自然分離される。
phases/dashboard-publish-button.md frontmatter 参照）。テーブルが無ければ init_db() で
作成する（冪等）。読みたい本を登録しないため report.py の生成物は
「読みたい本はまだ登録されていません。」の空一覧になるが、生成内容が両テストで
決定的である（= commit の有無が git diff の判定だけで決まる）ことが重要であり、
中身そのものは本テストの検証対象ではない。

使い方:
    python3 -m unittest test.test_server_publish_integration -v
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.repository import init_db
from src import server


def _run_git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)


class DoPublishGitIntegrationTest(unittest.TestCase):
    def setUp(self):
        # book_mappings / price_history テーブルが無いと report.py 側の
        # get_wanted_books() が "no such table" で落ちるため、先に用意する。
        init_db()

        self._tmp = tempfile.mkdtemp(prefix="publish_integration_")
        self.remote_dir = os.path.join(self._tmp, "remote.git")
        self.clone_dir = os.path.join(self._tmp, "clone")

        _run_git(["init", "--bare", "-q", self.remote_dir], cwd=self._tmp)
        _run_git(["clone", "-q", self.remote_dir, self.clone_dir], cwd=self._tmp)
        _run_git(["config", "user.email", "test@example.com"], cwd=self.clone_dir)
        _run_git(["config", "user.name", "Test"], cwd=self.clone_dir)

        # 空の bare リポジトリには branch が無いため、初回コミット + push -u で
        # upstream 追跡を確立しておく（do_publish() 内の `git push -q` は
        # upstream 前提のため）。
        with open(os.path.join(self.clone_dir, ".gitkeep"), "w") as f:
            f.write("")
        _run_git(["add", ".gitkeep"], cwd=self.clone_dir)
        _run_git(["commit", "-q", "-m", "init"], cwd=self.clone_dir)
        _run_git(["push", "-q", "-u", "origin", "HEAD"], cwd=self.clone_dir)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _remote_log(self):
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=self.remote_dir, capture_output=True, text=True
        )
        return result.stdout

    @patch.dict(os.environ, {}, clear=False)
    def test_changes_are_committed_and_pushed(self):
        os.environ["PUBLIC_SITE_DIR"] = self.clone_dir
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        asyncio.run(server.do_publish())

        joined_lines = "\n".join(server.job.lines)
        self.assertFalse(server.job.running)
        self.assertTrue(os.path.exists(os.path.join(self.clone_dir, "index.html")))
        self.assertIn("chore: update wishlist", self._remote_log())
        self.assertNotIn("差分なし", joined_lines)
        self.assertNotIn("[エラー]", joined_lines)
        self.assertIn("[完了] 公開しました: https://example.github.io/site/", joined_lines)

    @patch.dict(os.environ, {}, clear=False)
    def test_no_changes_are_not_committed_again_but_push_is_still_attempted(self):
        os.environ["PUBLIC_SITE_DIR"] = self.clone_dir
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        # 1 回目で index.html を生成・commit・push させておく
        asyncio.run(server.do_publish())
        first_log = self._remote_log()

        # 2 回目は同じ内容が再生成されるだけなので commit すべき差分は無いが、
        # push 自体は（no-op として）必ず試みられる。
        asyncio.run(server.do_publish())
        second_log = self._remote_log()

        self.assertEqual(first_log, second_log, "同じ内容で 2 度目の commit が作られてはいけない")
        joined_lines = "\n".join(server.job.lines)
        self.assertIn("差分なし（前回から内容が同じ）。", joined_lines)
        self.assertNotIn("[エラー]", joined_lines)
        self.assertIn("[完了] 公開しました: https://example.github.io/site/", joined_lines)

    @patch.dict(os.environ, {}, clear=False)
    def test_no_diff_still_pushes_a_previously_committed_but_unpushed_commit(self):
        """
        前回の公開で git push だけが失敗し、commit だけがローカルに残っている状況を
        再現する。index.html 自体に差分は無いため git diff --cached --quiet は
        「差分なし」を返すが、それでも push は必ず試みられ、残っていたコミットが
        remote に届くことを確認する（「差分なし」判定だけで復旧不能になる回帰を防ぐ）。
        """
        os.environ["PUBLIC_SITE_DIR"] = self.clone_dir
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        # 1 回目で index.html を生成・commit・push させ、2 回目の呼び出しが
        # 「差分なし」判定になる状態を作っておく。
        asyncio.run(server.do_publish())

        # 前回の push 失敗を模した、ローカルにだけ残っている未 push コミット
        _run_git(
            ["commit", "-q", "--allow-empty", "-m", "unpushed-from-last-publish"],
            cwd=self.clone_dir,
        )

        asyncio.run(server.do_publish())

        self.assertIn("unpushed-from-last-publish", self._remote_log())
        self.assertIn(
            "差分なし（前回から内容が同じ）。", "\n".join(server.job.lines)
        )

    @patch.dict(os.environ, {}, clear=False)
    def test_push_failure_emits_error_and_keeps_local_commit(self):
        os.environ["PUBLIC_SITE_DIR"] = self.clone_dir
        os.environ["PUBLIC_SITE_URL"] = "https://example.github.io/site/"

        # remote を消して push を確実に失敗させる（ネットワーク断・認証切れ相当）。
        shutil.rmtree(self.remote_dir, ignore_errors=True)

        asyncio.run(server.do_publish())

        joined_lines = "\n".join(server.job.lines)
        self.assertFalse(server.job.running)
        # commit 自体はローカルに残ること（公開データが消えたわけではない）
        self.assertTrue(os.path.exists(os.path.join(self.clone_dir, "index.html")))
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"], cwd=self.clone_dir, capture_output=True, text=True
        )
        self.assertIn("chore: update wishlist", result.stdout)
        # push 失敗が明示エラーとして出て、「完了しました」的な誤解を生む表示にならないこと
        self.assertIn("[エラー]", joined_lines)
        self.assertIn("git push", joined_lines)
        self.assertNotIn("[完了] 公開しました", joined_lines)


if __name__ == "__main__":
    unittest.main()
