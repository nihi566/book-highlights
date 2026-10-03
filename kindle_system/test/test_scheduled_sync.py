"""
test_scheduled_sync.py
----------------------
scripts/register_scheduled_sync.ps1（タスクスケジューラへの登録）と
scripts/scheduled_sync.ps1（登録したタスクが実行する本体）の単体テスト。

登録は -DryRun で中身を JSON に出すだけにし、実際のタスクスケジューラには登録しない。
本体は -Python に偽の python（引数を表示して決まった終了コードで終わる .cmd）を渡し、
実クロール・実公開へは一切接続しない。Windows 以外ではスキップする。

使い方:
    python -m unittest discover -s test -p "test_scheduled_sync.py" -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER_SCRIPT = os.path.join(BASE_DIR, "scripts", "register_scheduled_sync.ps1")
SYNC_SCRIPT = os.path.join(BASE_DIR, "scripts", "scheduled_sync.ps1")
POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None


def run_powershell(script, *args):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


@unittest.skipUnless(POWERSHELL, "Windows PowerShell が無い環境では確かめられない")
class RegisterScheduledSyncDryRunTest(unittest.TestCase):
    """-DryRun は登録せず、登録する内容（実行するもの・時刻・設定）を JSON で出す。"""

    def dry_run(self, *args):
        result = run_powershell(REGISTER_SCRIPT, "-DryRun", "-Python", r"C:\py\python.exe", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_runs_scheduled_sync_script_daily_at_given_time_in_repo_dir(self):
        task = self.dry_run("-At", "07:30")
        self.assertEqual(task["TaskName"], "kindle_system wishlist sync")
        self.assertEqual(task["Execute"], "powershell.exe")
        self.assertIn(f'-File "{SYNC_SCRIPT}"', task["Arguments"])
        self.assertIn(r'-Python "C:\py\python.exe"', task["Arguments"])
        self.assertEqual(os.path.normcase(task["WorkingDirectory"]), os.path.normcase(BASE_DIR))
        self.assertEqual(task["DaysInterval"], 1, "Amazon への取得は 1 日 1 回まで")
        self.assertTrue(task["StartBoundary"].endswith("T07:30:00"), task["StartBoundary"])

    def test_runs_missed_sync_after_pc_starts_and_never_overlaps(self):
        task = self.dry_run()
        self.assertTrue(task["StartBoundary"].endswith("T06:00:00"), "既定の時刻は 6:00")
        self.assertTrue(task["StartWhenAvailable"], "PC が止まっていて逃した回は起動後に実行する")
        self.assertEqual(task["MultipleInstances"], "IgnoreNew", "前回が終わっていなければ重ねて起動しない")
        self.assertEqual(task["RunLevel"], "Limited", "管理者権限では動かさない")

    def test_rejects_invalid_time(self):
        result = run_powershell(REGISTER_SCRIPT, "-DryRun", "-Python", r"C:\py\python.exe", "-At", "25:00")
        self.assertNotEqual(result.returncode, 0)


@unittest.skipUnless(POWERSHELL, "Windows PowerShell が無い環境では確かめられない")
class ScheduledSyncScriptTest(unittest.TestCase):
    """本体は run.py sync を実行し、出力と終了コードをログに追記して、同じ終了コードで終わる。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scheduled_sync_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = os.path.join(self.tmp, "logs", "scheduled_sync.log")

    def fake_python(self, exit_code):
        path = os.path.join(self.tmp, "fake_python.cmd")
        with open(path, "w", encoding="ascii") as f:
            f.write(f"@echo off\r\necho ARGS %*\r\nexit /b {exit_code}\r\n")
        return path

    def test_appends_output_and_exit_code_to_log_and_exits_with_same_code(self):
        result = run_powershell(SYNC_SCRIPT, "-Python", self.fake_python(3), "-LogPath", self.log)
        self.assertEqual(result.returncode, 3)
        with open(self.log, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("ARGS run.py sync", text)
        self.assertIn("開始", text)
        self.assertIn("終了（終了コード 3）", text)

    def test_success_exits_zero_and_keeps_previous_log(self):
        os.makedirs(os.path.dirname(self.log))
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("前回の記録\n")
        result = run_powershell(SYNC_SCRIPT, "-Python", self.fake_python(0), "-LogPath", self.log)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.log, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("前回の記録"), "ログは上書きせず追記する")
        self.assertIn("終了（終了コード 0）", text)


if __name__ == "__main__":
    unittest.main()
