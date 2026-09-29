#!/usr/bin/env bash
# Дешёвая ежедневная сводка asus: пороги проверяет server-digest.py, LLM — только при проблемах.
# Запуск с Mac:
#   scp scripts/hermes-digest/server-digest.py asus:/tmp/server-digest.py && ssh asus 'bash -s' < scripts/hermes-digest/install.sh
# Откат:
#   ssh asus 'bash -s' -- --rollback < scripts/hermes-digest/install.sh
#
# Было: агент с terminal + всеми MCP + context_from=self сам расследовал сервер —
# 145–260k токенов и до 4 мин каждый день. Стало: всё в норме — stdout пустой,
# Hermes модель не зовёт; есть новое — агент без инструментов пишет 2–5 строк.
# Старый server-digest.sh остаётся на месте (для отката).
set -euo pipefail

JOB=12ebf685b348
HERMES=~/.local/bin/hermes
PY=~/.hermes/hermes-agent/venv/bin/python
AGENT=~/.hermes/hermes-agent
SCRIPTS=~/.hermes/scripts

update_job() {  # $1 — JSON с полями задачи
  (cd "$AGENT" && "$PY" -c 'import json, sys; from cron.jobs import update_job; print("обновлено:", bool(update_job(sys.argv[1], json.loads(sys.argv[2]))))' "$JOB" "$1")
}

if [ "${1:-}" = "--rollback" ]; then
  last=$(ls -t ~/.hermes/cron/"$JOB".bak.*.json 2>/dev/null | head -1)
  [ -n "$last" ] || { echo "Нет бэкапа задачи"; exit 1; }
  update_job "$(cat "$last")"
  echo "Откат задачи из $last"
  exit 0
fi

[ -f /tmp/server-digest.py ] || { echo "Сначала scp server-digest.py в /tmp (см. шапку)"; exit 1; }
install -m 755 /tmp/server-digest.py "$SCRIPTS/server-digest.py"

# Бэкап полей задачи, которые меняем.
backup=~/.hermes/cron/"$JOB".bak.$(date +%Y%m%d_%H%M%S).json
"$PY" - "$JOB" > "$backup" <<'PY'
import json, sys
from pathlib import Path
data = json.loads((Path.home() / ".hermes/cron/jobs.json").read_text())
jobs = data["jobs"] if isinstance(data, dict) else data
job = next(j for j in jobs if j["id"] == sys.argv[1])
print(json.dumps({k: job.get(k) for k in ("prompt", "script", "enabled_toolsets", "context_from", "reasoning_effort")}, ensure_ascii=False))
PY
echo "Бэкап задачи: $backup"

PROMPT='Тебе передан stdout скрипта server-digest.py: проблемы домашнего сервера asus, которые скрипт уже отобрал (пороги, известный шум, повторы отсеяны), и короткий снимок метрик.

Напиши Егору 2–5 строк маркерами: сначала что случилось, потом что с этим сделать — конкретное действие или команду, если оно очевидно. Без преамбул, без «всё в порядке», без пересказа методики.

Инструментов у тебя нет — ничего не проверяй, работай только по тексту. Если в списке лишь косметический шум — ответь ровно [SILENT] и больше ничего.'

update_job "$("$PY" -c 'import json, sys; print(json.dumps({"prompt": sys.argv[1], "script": "server-digest.py", "enabled_toolsets": ["todo", "no_mcp"], "context_from": None, "reasoning_effort": "low"}, ensure_ascii=False))' "$PROMPT")"

# Первый прогон задаёт базу (порты, классы ошибок) и показывает, что уйдёт агенту.
echo "--- сейчас скрипт выдал бы агенту:"
"$PY" "$SCRIPTS/server-digest.py" --dry-run
"$PY" "$SCRIPTS/server-digest.py" > /dev/null

echo "--- задача:"
(cd "$AGENT" && "$PY" - "$JOB" <<'PY'
import sys
from cron.jobs import get_job
from cron.scheduler import _resolve_cron_enabled_toolsets
from hermes_cli.config import load_config
job = get_job(sys.argv[1])
print({k: job.get(k) for k in ("script", "enabled_toolsets", "context_from", "reasoning_effort", "next_run_at")})
print("инструменты агента:", _resolve_cron_enabled_toolsets(job, load_config()))
PY
)
