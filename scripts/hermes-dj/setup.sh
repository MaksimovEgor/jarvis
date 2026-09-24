#!/usr/bin/env bash
# Профиль Hermes «dj» на asus: музыкальный вкус для «Моей волны» (app/music/taste.py).
# Запуск с Mac:  ssh asus 'bash -s' < scripts/hermes-dj/setup.sh
# (код Jarvis уже выкачен в ~/jarvis — нужен SOUL.md оттуда). Повторный
# запуск безопасен: ключ API и память не перезаписываются.
#
# С Hermes 0.21.5 один gateway на хост обслуживает все профили: dj — своя память
# и свой ключ, API — основной gateway по префиксу 127.0.0.1:8642/p/dj. Из .env
# основного копируются только ключ DeepSeek и прокси — НЕ токен Telegram.
set -euo pipefail

HERMES=~/.local/bin/hermes
PY=~/.hermes/hermes-agent/venv/bin/python
DJ=~/.hermes/profiles/dj
JARVIS=~/jarvis

[ -d "$DJ" ] || "$HERMES" profile create dj --no-alias \
  --description "Музыкальный куратор Джарвиса: вкус хозяина, зёрна для «Моей волны», разметка треков."

cp "$JARVIS/scripts/hermes-dj/SOUL.md" "$DJ/SOUL.md"

touch "$DJ/.env" && chmod 600 "$DJ/.env"
grep -E '^(DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|HTTPS_PROXY|HTTP_PROXY|ALL_PROXY|https_proxy|http_proxy|all_proxy|NO_PROXY|no_proxy)=' ~/.hermes/.env \
  | while IFS= read -r line; do
      key=${line%%=*}
      grep -q "^$key=" "$DJ/.env" || echo "$line" >> "$DJ/.env"
    done
grep -q '^API_SERVER_ENABLED=' "$DJ/.env" || echo 'API_SERVER_ENABLED=true' >> "$DJ/.env"
grep -q '^API_SERVER_HOST=' "$DJ/.env" || echo 'API_SERVER_HOST=127.0.0.1' >> "$DJ/.env"
grep -q '^API_SERVER_KEY=' "$DJ/.env" || echo "API_SERVER_KEY=$(openssl rand -hex 24)" >> "$DJ/.env"
KEY=$(grep '^API_SERVER_KEY=' "$DJ/.env" | cut -d= -f2-)

# Модель как у основного; инструменты API — только память, веб-поиск и история.
"$PY" - "$DJ/config.yaml" ~/.hermes/config.yaml <<'PY'
import sys, yaml
path, main_path = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(path)) or {}
main = yaml.safe_load(open(main_path)) or {}
cfg["model"] = main.get("model", cfg.get("model"))
cfg.setdefault("memory", {}).update({"memory_enabled": True, "user_profile_enabled": True})
cfg.setdefault("platform_toolsets", {})["api_server"] = ["memory", "web", "session_search"]
yaml.safe_dump(cfg, open(path, "w"), allow_unicode=True, sort_keys=False)
PY

mkdir -p "$DJ/memories"
[ -s "$DJ/memories/MEMORY.md" ] || cp "$JARVIS/scripts/hermes-dj/MEMORY.seed.md" "$DJ/memories/MEMORY.md"

# Ключ — ядру Jarvis.
if grep -q '^DJ_HERMES_API_KEY=' "$JARVIS/.env"; then
  sed -i "s|^DJ_HERMES_API_KEY=.*|DJ_HERMES_API_KEY=$KEY|" "$JARVIS/.env"
else
  printf '\nDJ_HERMES_API_KEY=%s\n' "$KEY" >> "$JARVIS/.env"
fi

# Отдельный gateway dj (до 0.21.5) больше не нужен — основной подхватывает профиль сам.
systemctl --user disable --now hermes-dj 2>/dev/null || true
kill -USR1 "$(systemctl show -p MainPID --value hermes-gateway)"
echo "dj: ждём API…"
for _ in $(seq 1 45); do
  curl -sf -m 2 -H "Authorization: Bearer $KEY" http://127.0.0.1:8642/p/dj/v1/models >/dev/null \
    && { echo "dj работает: 127.0.0.1:8642/p/dj"; exit 0; }
  sleep 2
done
echo "dj не отвечает: journalctl -u hermes-gateway -n 50"; exit 1
