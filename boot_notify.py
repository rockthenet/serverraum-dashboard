#!/usr/bin/env python3
"""Boot-Benachrichtigung: meldet sich, sobald Dashboard laeuft und Internet da ist.

Wird von systemd (zero2-boot-notify.service) gestartet und wartet maximal
180 s auf:
  - frischen state.json (Checker laeuft)
  - internet == true (entprellt)
  - laufenden dashboard.py-Prozess
Danach geht genau eine ntfy-Nachricht raus, inkl. Art der Internetverbindung.
"""
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
STAMP = "/run/zero2-boot-notify.done"
MAX_WAIT = 180


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except Exception:
        return ""


def uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def dashboard_running():
    try:
        return subprocess.run(["pgrep", "-f", "dashboard.py"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def already_sent(bid):
    try:
        with open(STAMP) as f:
            return f.read().strip() == bid
    except Exception:
        return False


def mark_sent(bid):
    try:
        with open(STAMP, "w") as f:
            f.write(bid)
    except Exception:
        pass


def human(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return "{} s".format(sec)
    return "{} min {:02d} s".format(sec // 60, sec % 60)


def main():
    bid = boot_id()
    if already_sent(bid):
        print("Boot-Meldung fuer diesen Boot bereits gesendet")
        return 0

    deadline = time.time() + MAX_WAIT
    st = {}
    ok = False
    while time.time() < deadline:
        st = load_state()
        lc = st.get("last_check") or 0
        fresh = lc and (time.time() - lc) < 60
        if fresh and st.get("internet") and dashboard_running():
            ok = True
            break
        time.sleep(3)

    if not ok:
        print("Timeout: Dashboard oder Internet nicht bereit")
        return 1

    host = socket.gethostname()
    ntype = st.get("net_type") or "Netzwerk"
    nip = st.get("net_ip") or "-"
    nssid = st.get("net_ssid") or ""
    if ntype == "WLAN":
        conn = "WLAN{}{}".format(" '{}'".format(nssid) if nssid else "", " - " + nip)
    else:
        conn = "{} - {}".format(ntype, nip)

    lines = ["{} ist gestartet und online.".format(host),
             "Verbindung: {}".format(conn),
             "Dashboard geladen nach {}".format(human(uptime())),
             "{}/{} Geraete erreichbar".format(st.get("online", 0), st.get("total", 0))]
    if st.get("paused"):
        lines[-1] += ", {} pausiert".format(st["paused"])

    try:
        sys.path.insert(0, HERE)
        import notify
        sent = notify.send("\n".join(lines), title="{} gestartet".format(host),
                           tags="rocket")
    except Exception:
        sent = False

    if sent:
        mark_sent(bid)
        print("Boot-Meldung gesendet")
        return 0
    print("Senden fehlgeschlagen (kein ntfy_topic?)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
