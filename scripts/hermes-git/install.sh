#!/usr/bin/env bash
# История конфигурации Hermes: локальный git + снимок раз в час. Вместо *.bak.* рядом с конфигами.
# Запуск с Mac:
#   scp scripts/hermes-git/snapshot.sh asus:/tmp/hermes-snapshot.sh && ssh asus 'bash -s' < scripts/hermes-git/install.sh
# Смотреть:  ssh asus '~/.local/bin/hermes-git log --stat -5'      (обёртка над git с нужными --git-dir/--work-tree)
# Откатить файл:  ssh asus '~/.local/bin/hermes-git checkout <commit> -- config.yaml'
# Удалить всё:    systemctl --user disable --now hermes-snapshot.timer; rm -rf ~/.hermes-history ~/.hermes/.history
#
# Git-каталог лежит отдельно (~/.hermes-history), в ~/.hermes нет .git — Hermes и профиль coder
# (его workspace внутри ~/.hermes) эту историю не видят. Никуда не пушится. .env и auth.json не
# попадают; в config.yaml — только хеш пароля дашборда.
set -euo pipefail

H=~/.hermes-history
[ -f /tmp/hermes-snapshot.sh ] || { echo "Сначала scp snapshot.sh в /tmp (см. шапку)"; exit 1; }

if [ ! -d "$H" ]; then
  git init -q --bare "$H"
  git --git-dir="$H" config core.bare false
  git --git-dir="$H" config core.worktree "$HOME/.hermes"
  git --git-dir="$H" config user.name "hermes-snapshot"
  git --git-dir="$H" config user.email "hermes-snapshot@asus"
  git --git-dir="$H" config status.showUntrackedFiles no
  chmod 700 "$H"
fi

# Whitelist: всё исключено, кроме того, что правят люди и агенты.
cat > "$H/info/exclude" <<'EOF'
/*
!/config.yaml
!/SOUL.md
!/memories/
/memories/*
!/memories/*.md
!/skills/
/skills/.curator_backups/
!/scripts/
!/plugins/
!/hooks/
!/.history/
!/profiles/
/profiles/*/*
!/profiles/*/config.yaml
!/profiles/*/profile.yaml
!/profiles/*/SOUL.md
!/profiles/*/memories/
/profiles/*/memories/*
!/profiles/*/memories/*.md
!/profiles/*/skills/
*.bak*
*.lock
__pycache__/
.hermes-state.json
.server-digest.state.json
EOF

install -m 755 /tmp/hermes-snapshot.sh "$H/snapshot.sh"
mkdir -p ~/.local/bin
cat > ~/.local/bin/hermes-git <<EOF
#!/bin/sh
exec git --git-dir="$H" --work-tree="\$HOME/.hermes" "\$@"
EOF
chmod 755 ~/.local/bin/hermes-git
HG=~/.local/bin/hermes-git  # в `ssh asus bash -s` ~/.local/bin нет в PATH

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/hermes-snapshot.service <<EOF
[Unit]
Description=Снимок конфигурации Hermes в ~/.hermes-history

[Service]
Type=oneshot
Nice=10
ExecStart=$H/snapshot.sh
EOF
cat > ~/.config/systemd/user/hermes-snapshot.timer <<'EOF'
[Unit]
Description=Снимок конфигурации Hermes раз в час

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now hermes-snapshot.timer

"$H/snapshot.sh" "начальный снимок"
echo "--- в истории: $("$HG" ls-files | wc -l) файлов, $(du -sh "$H" | cut -f1)"
"$HG" ls-files | awk -F/ '{print ($1=="skills"||($1=="profiles"&&$3=="skills")) ? "(скиллы)" : $0}' | sort | uniq -c | sort -rn | head -20
echo "--- таймер:"
systemctl --user list-timers hermes-snapshot.timer --no-pager | head -2
echo "--- старые бэкапы рядом с конфигами (теперь не нужны, удалить — вручную):"
find ~/.hermes -maxdepth 3 -name '*.bak*' -not -path '*/hermes-agent/*' -printf '  %p\n' | sort
