# ─────────────────────────────────────────────────────────────
# Stage 1: フロントエンドビルド
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

# 依存関係のインストール（キャッシュ活用のため package.json を先にコピー）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# ソースをコピーしてビルド
COPY frontend/ ./
RUN npm run build

# ─────────────────────────────────────────────────────────────
# Stage 2: バックエンド（Playwright 対応イメージ）
# ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy AS app

WORKDIR /app

# Python 依存関係のインストール
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright ブラウザのインストール（Chromium のみ）
RUN playwright install chromium

# アプリケーションコードをコピー
COPY main.py ./
COPY src/ ./src/
COPY test/ ./test/

# フロントエンドのビルド成果物をコピー
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# データディレクトリを作成（ボリュームマウント先）
RUN mkdir -p /app/data

# ポート公開
EXPOSE 8765

# 起動コマンド
CMD ["python", "-u", "src/server.py"]
