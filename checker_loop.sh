#!/bin/bash
# Serverraum Dashboard - Hintergrund-Datensammler
# Intervalle kommen aus config.json:
#   poll_interval          Sekunden zwischen Ping-Checks (Standard 15)
#   speedtest_interval_min Minuten zwischen Speedtests (Standard 30)
# Proxmox wird alle 30 s abgefragt.
cd "$(dirname "$0")" || cd /opt/netbook-dashboard

last_prox=0

cfg_int() {
    python3 - "$1" "$2" <<'PY'
import json, sys
key, default = sys.argv[1], int(sys.argv[2])
try:
    with open("config.json") as f:
        print(max(1, int(json.load(f).get(key, default))))
except Exception:
    print(default)
PY
}

while true; do
    interval=$(cfg_int poll_interval 15)
    speed_min=$(cfg_int speedtest_interval_min 30)
    now=$(date +%s)

    python3 checker.py >/dev/null 2>&1

    if [ "$last_prox" -eq 0 ] || [ $((now - last_prox)) -ge 30 ]; then
        python3 proxmox.py >/dev/null 2>&1 &
        last_prox=$now
    fi

    due=1
    if [ -f speed.json ]; then
        age=$(( now - $(stat -c %Y speed.json) ))
        [ "$age" -ge $((speed_min * 60)) ] && due=1 || due=0
    fi
    if [ "$due" -eq 1 ]; then
        python3 speedtest.py >/dev/null 2>&1 &
    fi

    sleep "$interval"
done
