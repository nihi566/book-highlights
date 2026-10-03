"""
test_publish_guard.py
---------------------
run.ensure_safe_to_publish() のテスト。公開先は人や他のセッションが作業する book-highlights の作業ツリーの中なので、
main 以外のブランチ・rebase / merge の途中・公開する 2 ファイル以外の変更があるときは git を触らずに止めること。

一時フォルダに本物の git リポジトリを作って確かめる（git の判定そのものを確かめたいので、モックしない）。

使い方:
    python -m unittest discover -s test -p test_publish_guard.py -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import run


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True,
    )


class EnsureSafeToPublishTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="publish_guard_test_")
        _git(self.root, "init", "-q", "-b", "main")
        self.site = os.path.join(self.root, "web", "wishlist-site")
        os.makedirs(self.site)
        for name in ("wishlist.json", "feed.xml"):
            self._write(os.path.join(self.site, name), "old")
        self._write(os.path.join(self.root, "app.js"), "old")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _assert_stops(self):
        with self.assertRaises(SystemExit) as cm:
            run.ensure_safe_to_publish(self.site, os.environ.copy())
        self.assertEqual(cm.exception.code, 1)

    def test_passes_on_clean_main(self):
        run.ensure_safe_to_publish(self.site, os.environ.copy())

    def test_passes_when_only_published_files_changed(self):
        self._write(os.path.join(self.site, "wishlist.json"), "new")
        self._write(os.path.join(self.site, "feed.xml"), "new")
        run.ensure_safe_to_publish(self.site, os.environ.copy())

    def test_passes_with_untracked_files(self):
        # 未追跡のファイルは pull --rebase の邪魔をせず、commit にも入らない
        self._write(os.path.join(self.root, "scratch.txt"), "x")
        run.ensure_safe_to_publish(self.site, os.environ.copy())

    def test_stops_on_other_branch(self):
        _git(self.root, "checkout", "-q", "-b", "feature/x")
        self._assert_stops()

    def test_stops_when_other_tracked_file_changed(self):
        self._write(os.path.join(self.root, "app.js"), "edited by someone")
        self._assert_stops()

    def test_stops_when_other_file_staged(self):
        self._write(os.path.join(self.root, "new.js"), "x")
        _git(self.root, "add", "new.js")
        self._assert_stops()

    def test_stops_during_rebase(self):
        os.makedirs(os.path.join(self.root, ".git", "rebase-merge"))
        self._assert_stops()

    def test_stops_during_merge(self):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout.strip()
        self._write(os.path.join(self.root, ".git", "MERGE_HEAD"), head + "\n")
        self._assert_stops()


if __name__ == "__main__":
    unittest.main()
