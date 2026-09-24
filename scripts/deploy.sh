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
  # openwakeword, vosk-tts, faster-whisper тянут onnxruntime (CPU); он и
  # onnxruntime-gpu делят один модуль — CPU-сборка поверх ломает Whisper на
  # видеокарте. На машине с NVIDIA ставим GPU-сборку поверх (--force-reinstall:
  # удаление onnxruntime стирает и общие файлы модуля).
  if nvidia-smi >/dev/null 2>&1; then
    .venv/bin/pip uninstall -y -q onnxruntime 2>/dev/null || true
    .venv/bin/pip install -q -r requirements-gpu.txt
    .venv/bin/pip install -q --force-reinstall --no-deps "onnxruntime-gpu==1.24.1"
  fi
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

# Резервная LLM (Ollama ставится вручную, README) — только если установлена.
if [ -x ~/.local/ollama/bin/ollama ]; then
  systemctl --user enable -q --now jarvis-llm
fi

# jarvis-tts держит модель Vosk (~60 с загрузки) — перезапускаем, только если
# изменился его код или юнит; ядро тем временем говорит Piper'ом. После ядра:
# старое ядро само держало копию модели, две копии сразу — лишние ~ГБ памяти.
systemctl --user enable -q jarvis-tts
TTS_HASH=\$(cat app/tts_server.py scripts/systemd/jarvis-tts.service | sha256sum)
if [ "\$TTS_HASH" != "\$(cat ~/.cache/jarvis-tts.sha 2>/dev/null)" ] || ! systemctl --user is-active -q jarvis-tts; then
  echo "Перезапуск jarvis-tts (модель грузится ~60 с)…"
  systemctl --user restart jarvis-tts
  mkdir -p ~/.cache && echo "\$TTS_HASH" > ~/.cache/jarvis-tts.sha
fi
EOS
