#!/usr/bin/env bash
# Защита голосового канала Hermes: урезать инструменты api_server (голос и веб-чат Jarvis).
# Запуск с Mac:  ssh asus 'bash -s' < scripts/hermes-harden/apply.sh
# Откат:         ssh asus 'bash -s' -- --rollback < scripts/hermes-harden/apply.sh
#
# Зачем: у api_server не было своей строки в platform_toolsets, и он получал набор
# по умолчанию — terminal, file, code_execution, browser (с хранилищем логинов),
# cronjob, delegation. Голос читает веб, а значит, prompt injection со страницы
# мог выполнить команду на asus. Остаётся: MCP jarvis, веб-поиск, память, история,
# todo, скиллы (для чтения). Запись скиллов проверяет сканер (guard_agent_created).
#
# Перезапуск gateway не нужен: api_server перечитывает config.yaml на каждый запрос.
# Если YAML сломан, Hermes молча отдаёт полный набор, поэтому после записи
# скрипт перечитывает конфиг и сверяет итоговый список инструментов.
set -euo pipefail

CFG=${HERMES_CFG:-~/.hermes/config.yaml}
PY=~/.hermes/hermes-agent/venv/bin/python
AGENT=~/.hermes/hermes-agent

if [ "${1:-}" = "--rollback" ]; then
  last=$(ls -t "$CFG".bak.harden.* 2>/dev/null | head -1)
  [ -n "$last" ] || { echo "Нет бэкапа $CFG.bak.harden.*"; exit 1; }
  cp -p "$last" "$CFG"
  echo "Откат: восстановлен $last"
  exit 0
fi

backup="$CFG.bak.harden.$(date +%Y%m%d_%H%M%S)"
cp -p "$CFG" "$backup"
echo "Бэкап: $backup"

"$PY" - "$CFG" <<'PY'
import sys
from ruamel.yaml import YAML

path = sys.argv[1]
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)  # как пишет сам Hermes — diff только по делу
yaml.width = 4096
with open(path) as f:
    cfg = yaml.load(f)

cfg.setdefault("platform_toolsets", {})["api_server"] = [
    "jarvis",          # MCP Jarvis: музыка, громкость, таймеры; другие MCP-серверы не попадают
    "web",             # web_search, web_extract
    "memory",
    "session_search",
    "skills",          # car-owner-consulting, place-and-weather-lookup и т.п.
    "todo",
]
cfg.setdefault("skills", {})["guard_agent_created"] = True

with open(path, "w") as f:
    yaml.dump(cfg, f)
PY

# Проверка тем же кодом, которым пользуется gateway.
if ! (cd "$AGENT" && "$PY" - "$CFG" <<'PY'
import sys
sys.path.insert(0, ".")
from gateway.run import _load_gateway_config
from hermes_cli.tools_config import _get_platform_tools
from pathlib import Path
from toolsets import resolve_toolset

cfg = _load_gateway_config(Path(sys.argv[1]))
if not cfg:
    sys.exit("config.yaml не читается — gateway вернул бы полный набор")
toolsets = sorted(_get_platform_tools(cfg, "api_server"))
tools = sorted({t for ts in toolsets for t in resolve_toolset(ts, include_registry=False)})
print("api_server toolsets:", ", ".join(toolsets))
print("api_server tools:   ", ", ".join(tools))
banned = {"terminal", "execute_code", "write_file", "patch", "read_file", "browser_exec", "cronjob_manage", "delegate_task"}
if banned & set(tools) or "vkusvill" in toolsets:
    sys.exit(f"Опасные инструменты остались: {sorted(banned & set(tools))}")
PY
); then
  cp -p "$backup" "$CFG"
  echo "Проверка не прошла — конфиг возвращён из бэкапа"
  exit 1
fi

echo "Готово. Gateway подхватит на следующем запросе, перезапуск не нужен."
