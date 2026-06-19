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
# Stage 2: Laravel (PHP-FPM + Nginx)
# ─────────────────────────────────────────────────────────────
FROM php:8.4-fpm AS app

# 必要な PHP 拡張と Nginx をインストール
RUN apt-get update && apt-get install -y \
    nginx \
    libsqlite3-dev \
    libzip-dev \
    zip \
    unzip \
    curl \
    && docker-php-ext-install pdo pdo_sqlite zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Composer のインストール
COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

WORKDIR /var/www/html

# Composer 依存関係のインストール（キャッシュ活用）
COPY backend/composer.json backend/composer.lock ./
RUN composer install --no-dev --optimize-autoloader --no-interaction --no-scripts

# Laravel アプリケーションコードをコピー
COPY backend/ ./

# スクリプトを実行（アプリキーの生成など）
RUN php artisan key:generate --force

# React ビルド成果物を Laravel の public/ にコピー
COPY --from=frontend-builder /frontend/dist/assets ./public/assets
COPY --from=frontend-builder /frontend/dist/index.html ./public/index.html
RUN test -f /frontend/dist/favicon.png && cp /frontend/dist/favicon.png ./public/favicon.png || true
RUN test -f /frontend/dist/favicon.svg && cp /frontend/dist/favicon.svg ./public/favicon.svg || true

# Nginx 設定
COPY nginx/default.conf /etc/nginx/sites-available/default
RUN ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

# ディレクトリ権限
RUN chown -R www-data:www-data /var/www/html/storage /var/www/html/bootstrap/cache \
    && chmod -R 775 /var/www/html/storage /var/www/html/bootstrap/cache

# SQLite データディレクトリ（ボリュームマウント先）
RUN mkdir -p /app/data

# 起動スクリプト
COPY <<'EOF' /start.sh
#!/bin/sh
# PHP-FPM をバックグラウンドで起動
php-fpm -D
# Nginx をフォアグラウンドで起動
nginx -g "daemon off;"
EOF
RUN chmod +x /start.sh

EXPOSE 80

CMD ["/start.sh"]
