@echo off
cd /d "%~dp0"

rem .env に PUBLIC_SITE_DIR / PUBLIC_SITE_URL があれば読み込む（既存の環境変数は上書きしない）。
rem このバッチ自身が %PUBLIC_SITE_DIR% / %PUBLIC_SITE_URL% を後続の cd / start で使うため、
rem report.py 側のローダーとは別にここでも読み込む必要がある（子プロセスの環境変数は
rem 呼び出し元バッチには反映されないため）。
if exist ".env" (
    for /f "usebackq eol=# tokens=1,2 delims==" %%a in (".env") do (
        if not defined %%a set "%%a=%%b"
    )
)

echo クロール情報を更新中...
python main.py
if errorlevel 1 (
    echo main.py の実行に失敗しました。中断します。
    pause
    exit /b 1
)

echo レポートを生成中...
python report.py
if errorlevel 1 (
    echo report.py の実行に失敗しました。中断します。
    pause
    exit /b 1
)

echo GitHub Pages へ公開中...
if not defined PUBLIC_SITE_DIR (
    echo エラー: 環境変数 PUBLIC_SITE_DIR が設定されていません。中断します。
    pause
    exit /b 1
)
cd /d "%PUBLIC_SITE_DIR%"
if errorlevel 1 (
    rem R3/R6対策: 移動に失敗した場合ここで中断しないと、カレントディレクトリが
    rem このリポジトリ(kindle_system)のまま以降のgit add/commit/pushが実行され、
    rem 意図しないリポジトリへコミット・pushしてしまう。
    echo エラー: PUBLIC_SITE_DIR（%PUBLIC_SITE_DIR%）へ移動できませんでした。中断します。
    pause
    exit /b 1
)
git add index.html
if errorlevel 1 (
    echo git add に失敗しました。中断します。
    pause
    exit /b 1
)

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore: update wishlist" -q
    if errorlevel 1 (
        echo git commit に失敗しました。中断します。
        pause
        exit /b 1
    )
    git push -q
    if errorlevel 1 (
        echo git push に失敗しました。中断します。
        pause
        exit /b 1
    )
) else (
    echo 変更なし（前回から内容が同じ）。push は行いません。
)

start "" "%PUBLIC_SITE_URL%"
pause
