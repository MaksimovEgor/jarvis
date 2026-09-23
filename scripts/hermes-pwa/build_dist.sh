#!/usr/bin/env bash
# ExecStartPre дашборда Hermes (drop-in scripts/systemd/hermes-dashboard.service.d).
# Копирует собранный web_dist Hermes в отдельный каталог и дописывает в index.html
# манифест и apple-touch-icon — без них iPhone открывает «На экран Домой» как
# вкладку Safari. Оригинал не трогаем: `hermes update` пересобирает web_dist,
# а свежая копия делается при каждом старте дашборда (update его перезапускает).
# Файлы кладутся в assets/: этот префикс гейт авторизации Hermes пропускает без
# логина, а Safari запрашивает манифест без cookie.
set -euo pipefail

SRC="$HOME/.hermes/hermes-agent/hermes_cli/web_dist"
DST="$HOME/.hermes/web_dist_pwa"
PWA="$(cd "$(dirname "$0")" && pwd)"

rsync -a --delete "$SRC/" "$DST/"
cp "$PWA/manifest.json" "$DST/assets/pwa-manifest.json"
cp "$PWA/icon-180.png" "$DST/assets/pwa-icon-180.png"
cp "$PWA/icon-512.png" "$DST/assets/pwa-icon-512.png"

tags='<link rel="manifest" href="/assets/pwa-manifest.json" />'
tags+='<link rel="apple-touch-icon" href="/assets/pwa-icon-180.png" />'
tags+='<meta name="apple-mobile-web-app-capable" content="yes" />'
tags+='<meta name="mobile-web-app-capable" content="yes" />'
tags+='<meta name="apple-mobile-web-app-title" content="Hermes" />'
tags+='<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />'
tags+='<meta name="theme-color" content="#0a0a0a" />'
sed -i "s|</head>|${tags}</head>|" "$DST/index.html"
