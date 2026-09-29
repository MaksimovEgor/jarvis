#!/usr/bin/env python3
"""Ежедневная сводка asus для cron Hermes: пороги проверяет сам скрипт, а не LLM.

Всё в норме или проблемы те же, что в прошлый раз, — stdout пустой, и Hermes
не зовёт модель (0 токенов). Есть новое или стало хуже — печатает список
проблем и короткий снимок, агент без инструментов пишет 2–5 строк в Telegram.
Состояние между запусками (что уже сообщили, вчерашние ошибки, порты) — в
~/.hermes/scripts/.server-digest.state.json; оно заменяет context_from.

Ручной прогон:  python3 server-digest.py --dry-run   (печатает всё, состояние не пишет)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("DIGEST_STATE", HOME / ".hermes/scripts/.server-digest.state.json"))
ERRORS_LOG = HOME / ".hermes/logs/errors.log"
GATEWAY_STATE = HOME / ".hermes/gateway_state.json"

SERVICES = ["hermes-gateway", "tailscaled", "ssh", "ufw", "point-proxy"]
USER_SERVICES = ["jarvis-core", "jarvis-tts"]
DISK_PCT, RAM_PCT, SWAP_PCT, TEMP_C, LOAD15 = 80, 80, 50, 85, 2.0
JOURNAL_MB = 2048
REMIND_DAYS = 3          # та же проблема без ухудшения — напомнить не чаще
BATTERY_DROP = 5         # пунктов здоровья батареи с прошлого сообщения
# Известный шум — не считается ни «новым классом», ни ростом ошибок.
NOISE = re.compile(r"photon|telegram_network|platforms__telegram\.adapter|snap\.|firmware-notifier|AER", re.I)


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def collect() -> tuple[dict, dict[str, tuple[str, float]]]:
    """Снимок метрик и найденные проблемы: key → (текст, «тяжесть» для сравнения)."""
    snap: dict = {}
    problems: dict[str, tuple[str, float]] = {}

    def problem(key: str, text: str, level: float = 1.0) -> None:
        problems[key] = (text, level)

    # Ресурсы
    du = shutil.disk_usage("/")
    disk = round(du.used * 100 / du.total)
    snap["диск /"] = f"{disk}%"
    if disk > DISK_PCT:
        problem("disk", f"диск / занят на {disk}%", disk)

    mem = dict(line.split(":", 1) for line in read("/proc/meminfo").splitlines())
    kb = lambda k: int(mem.get(k, "0 kB").split()[0])
    ram = round((kb("MemTotal") - kb("MemAvailable")) * 100 / max(kb("MemTotal"), 1))
    swap = round((kb("SwapTotal") - kb("SwapFree")) * 100 / max(kb("SwapTotal"), 1))
    snap["RAM"], snap["swap"] = f"{ram}%", f"{swap}%"
    if ram > RAM_PCT:
        problem("ram", f"RAM занята на {ram}%", ram)
    if swap > SWAP_PCT:
        problem("swap", f"swap занят на {swap}%", swap)

    temps = [int(read(str(z / "temp")) or 0) // 1000 for z in Path("/sys/class/thermal").glob("thermal_zone*")]
    temp = max(temps, default=0)
    snap["temp"] = f"{temp}°C"
    if temp > TEMP_C:
        problem("temp", f"температура {temp} °C", temp)

    load15 = float(read("/proc/loadavg").split()[2])
    snap["load15"] = load15
    if load15 > LOAD15:
        problem("load", f"load за 15 мин {load15}", load15)

    journal_mb = int((sh("du -sm /var/log/journal 2>/dev/null").split() or ["0"])[0])
    if journal_mb > JOURNAL_MB:
        problem("journal", f"журнал systemd {journal_mb} МБ", journal_mb)

    # Службы
    failed = sh("systemctl --failed --no-legend --plain | awk '{print $1}'").split()
    failed += [f"{u} (user)" for u in sh("systemctl --user --failed --no-legend --plain | awk '{print $1}'").split()]
    failed = [u for u in failed if not NOISE.search(u)]
    if failed:
        problem("failed:" + ",".join(sorted(failed)), "failed-юниты: " + ", ".join(failed))
    for s in SERVICES:
        st = sh(f"systemctl is-active {s}")
        if st != "active":
            problem(f"down:{s}", f"{s}: {st or 'unknown'}")
    for s in USER_SERVICES:
        st = sh(f"systemctl --user is-active {s}")
        if st != "active":
            problem(f"down:{s}", f"{s}: {st or 'unknown'}")

    started = sh("systemctl show hermes-gateway -p ActiveEnterTimestamp --value")
    try:
        started_at = datetime.strptime(" ".join(started.split()[1:3]), "%Y-%m-%d %H:%M:%S")
        if datetime.now() - started_at < timedelta(hours=24):
            problem(f"gw-restart:{started}", f"шлюз Hermes перезапускался: {started}")
    except ValueError:
        pass

    try:
        platforms = json.loads(GATEWAY_STATE.read_text()).get("platforms", {})
        tg = platforms.get("telegram", {})
        if tg.get("state") != "connected":
            since = tg.get("retrying_since") or tg.get("updated_at") or ""
            problem("telegram", f"Telegram: {tg.get('state')} с {since[:16]} ({tg.get('error_code') or ''})")
    except (OSError, ValueError):
        pass

    # Сеть: пропал интернет или прокси — видно по коду 000. Разовые сбои прокси бывают,
    # поэтому тревога только если не ответил три раза подряд.
    if sh("ping -c 3 -W 2 1.1.1.1 >/dev/null 2>&1 && echo ok") != "ok":
        problem("net", "нет пинга до 1.1.1.1")
    for _ in range(3):
        code = sh("curl -s -o /dev/null -w '%{http_code}' --max-time 8 -x http://127.0.0.1:8888 https://www.google.com/generate_204")
        if code not in ("", "000"):
            break
        time.sleep(3)
    else:
        problem("proxy", "прокси :8888 не отвечает (3 попытки)")

    # Питание
    bat = Path("/sys/class/power_supply/BAT0")
    if read("/sys/class/power_supply/AC0/online") == "0":
        problem("ac", f"asus на батарее: {read(str(bat / 'capacity'))}%")
    ef, ed = read(str(bat / "energy_full")), read(str(bat / "energy_full_design"))
    if ef and ed:
        snap["battery_health"] = int(ef) * 100 // int(ed)

    # Безопасность
    security = int(sh("apt list --upgradable 2>/dev/null | grep -ci security") or 0)
    if security:
        problem("security", f"security-обновлений: {security}", security)
    snap["ports"] = sorted({
        a.rsplit(":", 1)[1] for a in sh("ss -ltnH | awk '{print $4}'").split()
        if not re.match(r"^(127\.|\[::1\]|100\.|\[fd7a:)", a)
    })
    ssh_fail = int(sh("journalctl -u ssh --since '24 hours ago' --no-pager -o cat 2>/dev/null"
                      " | grep -cE 'Failed password|Invalid user'") or 0)
    if ssh_fail > 10:
        problem("ssh-fail", f"неудачных входов по ssh за сутки: {ssh_fail}", ssh_fail)

    # Ошибки шлюза за сутки (без известного шума)
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    classes: dict[str, int] = {}
    last_err = ""
    try:
        for line in ERRORS_LOG.read_text(errors="replace").splitlines():
            if line[:19] < since or NOISE.search(line):
                continue
            m = re.search(r"\b(ERROR|CRITICAL) ([\w.]+)", line)
            if m:
                classes[m.group(2)] = classes.get(m.group(2), 0) + 1
                last_err = line[:200]
    except OSError:
        pass
    snap["errors"] = sum(classes.values())
    snap["error_classes"] = sorted(classes)
    snap["last_error"] = last_err
    return snap, problems


def main() -> None:
    dry = "--dry-run" in sys.argv
    state = load_state()
    snap, problems = collect()
    first_run = not state

    # Сравнение с прошлым запуском — то, что раньше делала LLM через context_from.
    if not first_run:
        new_ports = set(snap["ports"]) - set(state.get("ports", []))
        if new_ports:
            problems["ports:" + ",".join(sorted(new_ports))] = (f"новые слушающие порты: {', '.join(sorted(new_ports))}", 1)
        new_classes = set(snap["error_classes"]) - set(state.get("known_error_classes", []))
        if new_classes:
            problems["errclass:" + ",".join(sorted(new_classes))] = (
                f"новый класс ошибок шлюза: {', '.join(sorted(new_classes))}", 1)
        prev = state.get("errors", 0)
        if snap["errors"] >= 20 and snap["errors"] >= 3 * max(prev, 1):
            problems["errgrowth"] = (f"ERROR/CRITICAL шлюза: {snap['errors']} за сутки (вчера {prev})", snap["errors"])
        health, prev_health = snap.get("battery_health"), state.get("battery_health_reported")
        if health is not None and prev_health is not None and prev_health - health >= BATTERY_DROP:
            problems["battery"] = (f"здоровье батареи упало до {health}% (было {prev_health}%)", 100 - health)

    # Сообщать только новое, ухудшившееся или давно не напоминавшееся.
    now = time.time()
    reported = state.get("reported", {})
    to_send = [
        text for key, (text, level) in problems.items()
        if key not in reported
        or level > reported[key]["level"]
        or now - reported[key]["at"] > REMIND_DAYS * 86400
    ]

    if to_send or dry:
        print("ПРОБЛЕМЫ (нашёл скрипт):" if to_send else "ПРОБЛЕМ НЕТ (dry-run)")
        for text in to_send:
            print(f"- {text}")
        print("\nСНИМОК:")
        print(f"  {', '.join(f'{k} {snap[k]}' for k in ('диск /', 'RAM', 'swap', 'temp', 'load15'))}")
        print(f"  ошибок шлюза за сутки (без шума): {snap['errors']}; последняя: {snap['last_error'] or '—'}")

    if dry:
        return
    new_reported = {}
    for key, (text, level) in problems.items():
        old = reported.get(key)
        sent = text in to_send
        new_reported[key] = {"level": level, "at": now if sent or not old else old["at"]}
    battery_reported = state.get("battery_health_reported", snap.get("battery_health"))
    if "battery" in problems:
        battery_reported = snap.get("battery_health")
    STATE.write_text(json.dumps({
        "reported": new_reported,
        "errors": snap["errors"],
        "known_error_classes": sorted(set(state.get("known_error_classes", [])) | set(snap["error_classes"])),
        "ports": snap["ports"],
        "battery_health_reported": battery_reported,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
