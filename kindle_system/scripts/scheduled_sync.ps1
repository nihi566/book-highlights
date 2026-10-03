<#
タスクスケジューラから毎日実行される本体（登録は scripts\register_scheduled_sync.ps1）。
python run.py sync を実行し、開始・出力・終了コードを data\logs\scheduled_sync.log に追記して、同じ終了コードで終わる。
タスクスケジューラの「前回の実行結果」にも同じ終了コードが出る（0 以外なら失敗。中身はログで確かめる）。
#>
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'
$RepoDir = Split-Path -Parent $PSScriptRoot
if (-not $LogPath) { $LogPath = Join-Path $RepoDir 'data\logs\scheduled_sync.log' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null

$utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Log([string]$message) {
    [IO.File]::AppendAllText($LogPath, "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $message`r`n", $utf8)
}

Set-Location $RepoDir
Write-Log "開始: $Python run.py sync"
# run.py の出力は UTF-8。PowerShell を通すと文字コードを読み替えてしまうので、cmd のリダイレクトでそのまま追記する
$env:PYTHONIOENCODING = 'utf-8'
$process = Start-Process -FilePath 'cmd.exe' -NoNewWindow -Wait -PassThru `
    -ArgumentList "/d /s /c `"`"$Python`" run.py sync >> `"$LogPath`" 2>&1`""
$exitCode = $process.ExitCode
Write-Log "終了（終了コード $exitCode）"
exit $exitCode
