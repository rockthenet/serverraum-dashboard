#!/bin/bash
# Serverraum Dashboard - Hintergrund-Datensammler
# Intervalle kommen aus config.json:
#   poll_interval          Sekunden zwischen Ping-Checks (Standard 15)
#   speedtest_interval_min Minuten zwischen Speedtests (Standard 30)
#   fritzbox_interval_sec  Sekunden zwischen FRITZ!Box-Abfragen (Standard 60)
#   unifi_interval_sec     Sekunden zwischen UniFi-Abfragen (Standard 60)
#   deconz_interval_sec    Sekunden zwischen deCONZ-Abfragen (Standard 60)
# Proxmox wird alle 30 s abgefragt.
cd "$(dirname "$0")" || cd /opt/netbook-dashboard

last_prox=0
last_unifi=0
last_deconz=0
last_fritz=0

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

fritz_ok() {
    python3 - <<'PY'
import json
try:
    with open("fritzbox.json") as f:
        d = json.load(f)
    print(1 if d.get("ok") and int(d.get("down_bps") or 0) > 0 else 0)
except Exception:
    print(0)
PY
}

while true; do
    interval=$(cfg_int poll_interval 15)
    speed_min=$(cfg_int speedtest_interval_min 30)
    unifi_sec=$(cfg_int unifi_interval_sec 60)
    deconz_sec=$(cfg_int deconz_interval_sec 60)
    fritz_sec=$(cfg_int fritzbox_interval_sec 60)
    now=$(date +%s)

    python3 checker.py >/dev/null 2>&1

    if [ "$last_prox" -eq 0 ] || [ $((now - last_prox)) -ge 30 ]; then
        python3 proxmox.py >/dev/null 2>&1 &
        last_prox=$now
    fi

    if [ "$last_unifi" -eq 0 ] || [ $((now - last_unifi)) -ge "$unifi_sec" ]; then
        python3 unifi.py >/dev/null 2>&1 &
        last_unifi=$now
    fi

    if [ "$last_deconz" -eq 0 ] || [ $((now - last_deconz)) -ge "$deconz_sec" ]; then
        python3 deconz.py >/dev/null 2>&1 &
        last_deconz=$now
    fi

    if [ "$last_fritz" -eq 0 ] || [ $((now - last_fritz)) -ge "$fritz_sec" ]; then
        # Beim ersten Lauf synchron, damit der Speedtest unten schon weiss,
        # ob die FRITZ!Box die Leitungsdaten liefert.
        if [ "$last_fritz" -eq 0 ]; then
            python3 fritzbox.py >/dev/null 2>&1
        else
            python3 fritzbox.py >/dev/null 2>&1 &
        fi
        last_fritz=$now
    fi

    due=1
    if [ -f speed.json ]; then
        age=$(( now - $(stat -c %Y speed.json) ))
        [ "$age" -ge $((speed_min * 60)) ] && due=1 || due=0
    fi
    # Eigener Speedtest nur, wenn die FRITZ!Box keine Leitungsdaten liefert.
    if [ "$due" -eq 1 ] && [ "$(fritz_ok)" -ne 1 ]; then
        python3 speedtest.py >/dev/null 2>&1 &
    fi

    sleep "$interval"
done
