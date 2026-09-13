# Kindle System

Kindle for PC のキャッシュから蔵書情報を抽出し、価格・キャンペーン情報をクロールして
読書メーターの「読みたい本」リストと同期し、結果を静的サイトとして公開する CLI ツール群。

常駐サーバー・ブラウザ操作の UI は無く、すべて `run.py` / `report.py` のコマンド実行で完結する。

## セットアップ

```
pip install -r requirements.txt
playwright install
copy .env.example .env
```

`.env.example` を参考に、必要な環境変数を `.env` に設定する。

- `KINDLE_XML_PATH`: 任意設定。Kindle for PC のキャッシュファイルのパスが標準と異なる場合のみ設定する。
- `PUBLIC_SITE_DIR` / `PUBLIC_SITE_URL`: 公開機能（`run.py sync` 内の `publish()`）を使う場合は必須。

## 使い方

### クロール → 読書メーター同期 → レポート生成 → 公開（一括実行）

```
python run.py sync [--workers N] [--limit N] [--start N]
```

- `--workers`: 並列ブラウザ数（デフォルト 1、1〜5 にクランプされる）
- `--limit`: 処理する最大件数
- `--start`: 開始するインデックス番号

### 「欲しい本」フラグの更新

```
python run.py want <asin> --on
python run.py want <asin> --off
```

### 購入済みフラグの更新

```
python run.py purchase <asin> --on
python run.py purchase <asin> --off
```

### 静的レポートの生成のみ実行（公開はしない）

`run.py sync` は内部でレポート生成と公開（git commit・push）の両方を行うが、
`report.py` は単体でも実行できる。ただし **`report.py` 単体では公開は行われない**
（`PUBLIC_SITE_DIR` 直下に `index.html` を書き出すだけで git 操作はしない）。
クロールをやり直さずに公開だけをやり直す CLI コマンドは現状無いため、
再公開が必要な場合は `python run.py sync` を実行する。

```
python report.py
```

蔵書一覧（読みたい本 / 購入済み本 / 全部）と価格履歴を `PUBLIC_SITE_DIR` 直下の
`index.html` として書き出す。

### クロールのみ実行（読書メーター同期・公開を含まない）

```
python main.py
python main.py --limit 3    (先頭から最大3件まで処理)
python main.py --test       (ダミーXMLを用いてテスト実行)
```

## テスト

```
python3 -m unittest discover -s test
```
