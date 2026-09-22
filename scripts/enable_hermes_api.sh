#!/usr/bin/env bash
# Одноразово (на asus): включает API server основного профиля Hermes (только
# loopback, 127.0.0.1:8642) и переключает Jarvis на него. Один и тот же
# случайный ключ пишется в ~/.hermes/.env (API_SERVER_KEY) и в ~/jarvis/.env
# (HERMES_API_KEY). Ключ в stdout не выводится.
set -euo pipefail

HERMES_ENV="$HOME/.hermes/.env"
JARVIS_ENV="$HOME/jarvis/.env"

if grep -q '^API_SERVER_KEY=' "$HERMES_ENV"; then
    echo "API_SERVER_KEY уже есть в $HERMES_ENV — ничего не меняю." >&2
    exit 1
fi

cp "$HERMES_ENV" "$HERMES_ENV.bak.$(date +%Y%m%d_%H%M%S)"
key=$(openssl rand -hex 32)

printf '\n# Jarvis (голосовой клиент) ходит сюда, только loopback\nAPI_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n' "$key" >> "$HERMES_ENV"

sed -i '/^\(AGENT_BACKEND\|HERMES_URL\|HERMES_API_KEY\)=/d' "$JARVIS_ENV"
printf '\nAGENT_BACKEND=hermes\nHERMES_URL=http://127.0.0.1:8642\nHERMES_API_KEY=%s\n' "$key" >> "$JARVIS_ENV"

# Gateway — системный юнит с Restart=always, процесс под нашим юзером:
# SIGTERM без sudo, systemd поднимет его заново через RestartSec=5.
kill -TERM "$(systemctl show -p MainPID --value hermes-gateway)"
echo "Жду рестарт gateway…"
for _ in $(seq 1 30); do
    sleep 2
    if curl -sf -o /dev/null http://127.0.0.1:8642/health; then
        echo "API server Hermes поднят на 127.0.0.1:8642"
        systemctl --user restart jarvis-core
        echo "jarvis-core перезапущен на AGENT_BACKEND=hermes"
        exit 0
    fi
done
echo "API server не поднялся за минуту — смотри: journalctl -u hermes-gateway -n 50" >&2
exit 1
