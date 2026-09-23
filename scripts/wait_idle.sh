#!/usr/bin/env bash
# ExecStop jarvis-core: перед остановкой ждём, пока ядро доделает ходы
# (GET /admin/busy), — перезапуск при выкатке не обрывает ответ на полуслове.
# Ядро не отвечает (зависло, уже упало) — не ждём. Больше MAX_WAIT не ждём:
# юнит всё-таки нужно остановить.
set -u

URL=${JARVIS_BUSY_URL:-http://127.0.0.1:8000/admin/busy}
MAX_WAIT=${MAX_WAIT:-120}

for ((waited = 0; waited < MAX_WAIT; waited += 2)); do
  busy=$(curl -sf -m 2 "$URL" | grep -o '"turns": *[0-9]*' | grep -o '[0-9]*$') || exit 0
  [ "${busy:-0}" -eq 0 ] && exit 0
  [ $((waited % 10)) -eq 0 ] && echo "wait_idle: ходов в работе: $busy, жду (${waited}/${MAX_WAIT} с)"
  sleep 2
done
echo "wait_idle: не дождался простоя за ${MAX_WAIT} с — останавливаю"
