<#
欲しい本の公開データを毎日 1 回更新するタスク（python run.py sync）を、Windows のタスクスケジューラに登録する。

  登録:       powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_scheduled_sync.ps1 [-At 06:00] [-Python <python.exe>]
  中身を確認: 上に -DryRun を付ける（登録せず、登録する内容を JSON で出す）
  解除:       powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_scheduled_sync.ps1 -Unregister

- Amazon への取得は 1 日 1 回まで（-At の時刻に 1 回）。
- PC が止まっていて時刻を逃した回は、起動後（ログオン中）に 1 回だけ実行する（StartWhenAvailable）。
- 前回の実行が終わっていなければ重ねて起動しない。3 時間で打ち切る。
- 登録したユーザーがログオンしている間だけ動く（パスワードを保存しない・管理者権限を使わない）。
- 実行結果は data\logs\scheduled_sync.log に追記される（scripts\scheduled_sync.ps1）。
#>
param(
    [string]$At = '06:00',
    [string]$Python,
    [switch]$Unregister,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$TaskName = 'kindle_system wishlist sync'
$RepoDir = Split-Path -Parent $PSScriptRoot
$SyncScript = Join-Path $PSScriptRoot 'scheduled_sync.ps1'

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "タスク「$TaskName」を解除しました。"
    exit 0
}

$time = [datetime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)

if (-not $Python) {
    $venvPython = Join-Path $RepoDir '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        $Python = $venvPython
    } else {
        $found = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $found) { throw 'python.exe が見つかりません。-Python で python.exe のパスを指定してください。' }
        $Python = $found.Source
    }
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SyncScript`" -Python `"$Python`"" `
    -WorkingDirectory $RepoDir
$trigger = New-ScheduledTaskTrigger -Daily -At $time
# 既定では UTC（末尾 Z）で記録されるので、PC の時計の時刻で記録し直す
$trigger.StartBoundary = $time.ToString('s')
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

if ($DryRun) {
    [ordered]@{
        TaskName           = $TaskName
        Execute            = $action.Execute
        Arguments          = $action.Arguments
        WorkingDirectory   = $action.WorkingDirectory
        StartBoundary      = $trigger.StartBoundary
        DaysInterval       = $trigger.DaysInterval
        StartWhenAvailable = $settings.StartWhenAvailable
        MultipleInstances  = [string]$settings.MultipleInstances
        ExecutionTimeLimit = $settings.ExecutionTimeLimit
        RunLevel           = [string]$principal.RunLevel
    } | ConvertTo-Json
    exit 0
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description '欲しい本の公開データを更新する（python run.py sync）。kindle_system\scripts\register_scheduled_sync.ps1 で登録' `
    -Force | Out-Null
Write-Output "タスク「$TaskName」を登録しました（毎日 $At・python: $Python）。ログ: $(Join-Path $RepoDir 'data\logs\scheduled_sync.log')"
