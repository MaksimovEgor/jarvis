#!/usr/bin/env bash
# Синхронизирует код на asus и ставит python-зависимости в venv.
# Требует, чтобы scripts/setup_asus.sh уже был выполнен (нужен python3-venv/pip).
set -euo pipefail

HOST=asus
REMOTE_DIR=jarvis

ssh "$HOST" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'data/models' --exclude 'data/music' \
  --exclude 'docker/searxng/settings.yml' --exclude 'web/node_modules' \
  ./ "$HOST:$REMOTE_DIR/"

ssh "$HOST" bash -s <<EOF
set -euo pipefail
cd $REMOTE_DIR
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
mkdir -p data/models/piper data/music
[ -f .env ] || cp .env.example .env
if [ ! -f data/models/piper/ru_RU-voice.onnx ]; then
  echo "⚠ Голос Piper не скачан — см. README (раздел TTS)."
fi
EOF

echo "Деплой готов. Запуск вручную:"
echo "  ssh asus 'cd jarvis && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000'"
