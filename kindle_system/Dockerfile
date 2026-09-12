# ─────────────────────────────────────────────────────────────
# Stage 1: フロントエンドビルド
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ─────────────────────────────────────────────────────────────
# Stage 2: Python クローラー専用コンテナ
# ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

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

# フロントエンドビルド成果物をコピー（src/server.py の DIST_DIR = /app/frontend/dist と一致させる）
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# データディレクトリを作成（ボリュームマウント先）
RUN mkdir -p /app/data

# ポート公開（クローラー API + SSE）
EXPOSE 8001

# 起動コマンド
CMD ["python", "-u", "src/server.py"]
