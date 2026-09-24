#!/usr/bin/env bash
# Плагин Hermes «jarvis-local»: команда /local <текст> — ответ локальной модели
# Джарвиса мимо LLM Hermes (app/services/local_chat.py).
# Запуск с Mac:  ssh asus 'bash -s' < scripts/hermes-local/setup.sh
# (код Jarvis уже выкачен в ~/jarvis). Повторный запуск безопасен.
set -euo pipefail

JARVIS=~/jarvis
DEST=~/.hermes/plugins/jarvis-local

mkdir -p "$DEST"
cp "$JARVIS/scripts/hermes-local/jarvis-local/"{plugin.yaml,__init__.py} "$DEST/"

# Плагины Hermes по умолчанию выключены — включаем по имени (бэкап конфига рядом).
~/.hermes/hermes-agent/venv/bin/python - <<'PY'
import pathlib, shutil, time, yaml
p = pathlib.Path.home() / ".hermes/config.yaml"
cfg = yaml.safe_load(p.read_text())
enabled = (cfg.get("plugins") or {}).get("enabled") or []
if "jarvis-local" not in enabled:
    shutil.copy(p, p.with_name(f"config.yaml.bak.{time.strftime('%Y%m%d_%H%M%S')}"))
    s = p.read_text()
    anchor = "plugins:\n  enabled:\n"
    assert s.count(anchor) == 1, "не нашёл plugins.enabled в config.yaml"
    p.write_text(s.replace(anchor, anchor + "    - jarvis-local\n"))
    print("jarvis-local включён в plugins.enabled")
PY

# Мягкий перезапуск gateway (ExecReload юнита, sudo не нужен).
kill -USR1 "$(systemctl show -p MainPID --value hermes-gateway)"
echo "Готово: в Telegram — /local <текст>"
