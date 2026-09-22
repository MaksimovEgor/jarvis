#!/usr/bin/env bash
# Разовая ручная установка того, что требует root. Запускать на самом asus
# (или `ssh asus 'bash -s' < scripts/setup_asus.sh`) — попросит пароль sudo.
set -euo pipefail

sudo apt update
sudo apt install -y python3.12-venv python3-pip ffmpeg mpv alsa-utils

echo "Системные зависимости поставлены. Дальше: ./scripts/deploy.sh с Мака."
