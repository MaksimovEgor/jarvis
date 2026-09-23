#!/usr/bin/env bash
# Выкатка на asus с Mac (из корня репозитория): сборка фронта, код, юниты,
# python-зависимости, перезапуск ядра. Перезапуск ждёт, пока ядро доделает
# ходы (ExecStop → scripts/wait_idle.sh), — ответ не оборвётся.
# Требует, чтобы scripts/setup_asus.sh уже был выполнен (нужен python3-venv/pip).
#   SKIP_PIP=1 ./scripts/deploy.sh — без pip install (зависимости не менялись).
set -euo pipefail

HOST=asus
REMOTE_DIR=jarvis

cd "$(dirname "$0")/.."
(cd web && npm run build)

# Исключённое rsync --delete не трогает: .env, data/ (история, таймеры,
# позиции, модели, ключи пушей) и образцы голосов живут только на asus.
ssh "$HOST" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude '/data' --exclude '.env' \
  --exclude 'web/dist/voice-samples' \
  --exclude 'docker/searxng/settings.yml' --exclude 'web/node_modules' \
  ./ "$HOST:$REMOTE_DIR/"

ssh "$HOST" SKIP_PIP="${SKIP_PIP:-}" bash -s <<EOS
set -euo pipefail
cd $REMOTE_DIR
if [ -z "\$SKIP_PIP" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/pip install -q -r requirements.txt
fi
mkdir -p data/models/piper data/music
[ -f .env ] || cp .env.example .env

mkdir -p ~/.config/systemd/user
cp scripts/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
echo "Перезапуск ядра (ждёт окончания ходов)…"
systemctl --user restart jarvis-core
for _ in \$(seq 1 60); do
  curl -sf http://127.0.0.1:8000/health >/dev/null && break
  sleep 2
done
curl -sf http://127.0.0.1:8000/health >/dev/null && echo "Ядро работает." || { echo "Ядро не поднялось: journalctl --user -u jarvis-core"; exit 1; }
EOS
