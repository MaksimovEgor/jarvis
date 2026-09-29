#!/usr/bin/env bash
# Резервная модель Hermes: основной провайдер упал (402 баланс, 429, 5xx, сеть) —
# ход доигрывает резерв, следующий ход снова пробует основной.
# Запуск с Mac:  ssh asus 'bash -s' < scripts/hermes-fallback/apply.sh
# Откат:         ssh asus 'bash -s' -- --rollback < scripts/hermes-fallback/apply.sh
#
# Цепочка — бесплатные модели Nous (OAuth уже есть у основного профиля и у dj).
# Замер 2026-09-29, короткий вопрос через CLI: step-3.7-flash 9,8 с, solar-pro4 11,4 с,
# основной deepseek-flash 5,2 с (в каждом ~4 с старта CLI). Бесплатные модели могут
# логировать запросы — это цена резерва без второго платного ключа.
# Меняется только config.yaml основного профиля и dj; перезапуск не нужен.
set -euo pipefail

PY=~/.hermes/hermes-agent/venv/bin/python
AGENT=~/.hermes/hermes-agent
CONFIGS=(~/.hermes/config.yaml ~/.hermes/profiles/dj/config.yaml)
[ -n "${HERMES_CONFIGS:-}" ] && read -ra CONFIGS <<< "$HERMES_CONFIGS"  # для прогона на копиях

if [ "${1:-}" = "--rollback" ]; then
  for cfg in "${CONFIGS[@]}"; do
    last=$(ls -t "$cfg".bak.fallback.* 2>/dev/null | head -1)
    [ -n "$last" ] && cp -p "$last" "$cfg" && echo "Откат: $last"
  done
  exit 0
fi

stamp=$(date +%Y%m%d_%H%M%S)
for cfg in "${CONFIGS[@]}"; do
  cp -p "$cfg" "$cfg.bak.fallback.$stamp"
  echo "Бэкап: $cfg.bak.fallback.$stamp"
done

"$PY" - "${CONFIGS[@]}" <<'PY'
import sys
from ruamel.yaml import YAML

CHAIN = [
    {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
    {"provider": "nous", "model": "upstage/solar-pro4:free"},
]
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.width = 4096
for path in sys.argv[1:]:
    with open(path) as f:
        cfg = yaml.load(f)
    cfg["fallback_providers"] = CHAIN
    cfg.pop("fallback_model", None)  # устаревший ключ — одна точка правды
    with open(path, "w") as f:
        yaml.dump(cfg, f)
PY

# Проверка тем же кодом, которым читает Hermes; YAML не читается — откат.
for cfg in "${CONFIGS[@]}"; do
  if ! (cd "$AGENT" && "$PY" - "$cfg" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from gateway.run import _load_gateway_config
from hermes_cli.fallback_config import get_fallback_chain

cfg = _load_gateway_config(Path(sys.argv[1]))
if not cfg:
    sys.exit("config.yaml не читается")
chain = get_fallback_chain(cfg)
print(f"{sys.argv[1]}: основной {cfg['model'].get('provider')}/{cfg['model'].get('default')}, резерв:",
      ", ".join(f"{e['provider']}/{e['model']}" for e in chain))
if len(chain) != 2:
    sys.exit("цепочка не прочиталась")
PY
  ); then
    cp -p "$cfg.bak.fallback.$stamp" "$cfg"
    echo "Проверка не прошла — $cfg возвращён из бэкапа"
    exit 1
  fi
done
echo "Готово. Действует со следующего хода, перезапуск не нужен."
