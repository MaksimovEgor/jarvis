#!/usr/bin/env bash
# Снимок конфигурации Hermes в локальный git (~/.hermes-history, рабочее дерево ~/.hermes).
# Запускается user-таймером hermes-snapshot раз в час и вручную перед/после правок:
#   ~/.hermes-history/snapshot.sh "что поменял"
# Коммит только если что-то изменилось. Что попадает — whitelist в ~/.hermes-history/info/exclude.
set -euo pipefail

export GIT_DIR=${HERMES_HISTORY:-~/.hermes-history} GIT_WORK_TREE=${HERMES_DIR:-~/.hermes}  # переопределение — для прогона на копии
PY=~/.hermes/hermes-agent/venv/bin/python

# Определения cron-задач без счётчиков и времени запусков — иначе коммит после каждого запуска.
mkdir -p "$GIT_WORK_TREE/.history"
"$PY" - "$GIT_WORK_TREE" <<'PY'
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
keep = ("id", "name", "enabled", "schedule", "prompt", "script", "no_agent", "skills", "model", "provider",
        "enabled_toolsets", "context_from", "reasoning_effort", "deliver", "workdir", "monitor_script", "monitor_url")
out = {}
for path in sorted([home / "cron/jobs.json", *home.glob("profiles/*/cron/jobs.json")]):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    jobs = data["jobs"] if isinstance(data, dict) else data
    out[str(path.relative_to(home))] = [{k: j.get(k) for k in keep if j.get(k) is not None} for j in jobs]
target = home / ".history/cron-jobs.json"
text = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
if not target.exists() or target.read_text() != text:
    target.write_text(text)
PY

git add --all
git diff --cached --quiet && exit 0

# В сообщение — что поменялось, скиллы сворачиваются до категории: skills/media, profiles/dj/skills.
changed=$(git diff --cached --name-only \
  | awk -F/ '$1=="skills"{print $1"/"$2; next} $1=="profiles"&&$3=="skills"{print $1"/"$2"/skills"; next} {print}' \
  | sort -u | head -8 | paste -sd ' ' -)
git commit -q -m "${1:-snapshot}: $changed"
git log -1 --format='%h %s'
