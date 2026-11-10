#!/usr/bin/env python3
"""Minimaler Checker - testet alle IPs via fping, schreibt state.json.

Zusaetzlich pro Geraet:
  paused     - uebersprungen (config: "paused": true), zaehlt nicht in online/total
  since      - Zeitpunkt des letzten Statuswechsels (online <-> offline)
  last_down  - wann das Geraet zuletzt offline ging
  last_up    - wann es zuletzt wieder online ging
Damit kann das Dashboard "seit wann online/down" anzeigen, ohne eigene History.

Internet wird entprellt (Hysterese), damit einzelne verlorene Pings keine
Meldung ausloesen:
  offline_after - so viele Fehlversuche in Folge bis "offline" gilt (Standard 3)
  online_after  - so viele Erfolge in Folge bis "wieder online" gilt (Standard 2)
Gemeldet wird nur ein echter Zustandswechsel (und nach einem Neustart erst
wieder, wenn das Netz einmal stabil war). Parallele Laeufe verhindert flock.
"""
import fcntl
import json
import os
import re
import socket
import struct
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
LOCK = os.path.join(HERE, ".checker.lock")


def load_config():
    with open(os.path.join(HERE, "config.json"), "r") as f:
        return json.load(f)


def load_prev():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def collect_active(cfg):
    """IP -> [(gid, name)] nur fuer nicht pausierte Geraete."""
    ips = {}
    for gid, grp in cfg.get("groups", {}).items():
        for dev in grp.get("devices", []):
            if dev.get("paused"):
                continue
            ip = dev.get("ip", "").strip()
            if ip:
                ips.setdefault(ip, []).append((gid, dev["name"]))
    return ips


def run_fping(ip_list, timeout=800):
    if not ip_list:
        return {}
    try:
        proc = subprocess.run(
            ["fping", "-c", "1", "-t", str(timeout)] + ip_list,
            capture_output=True, text=True, timeout=30
        )
        raw = proc.stdout + proc.stderr
    except Exception:
        return {ip: (False, 0.0) for ip in ip_list}

    results = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\S+)\s+:.*\(([\d.]+)\s+avg", line)
        if m:
            results[m.group(1)] = (True, float(m.group(2)))
            continue
        m = re.match(r"^(\S+)\s+is alive.*\(([\d.]+)\s*ms", line)
        if m:
            results[m.group(1)] = (True, float(m.group(2)))
            continue
        # "1.2.3.4 is unreachable"
        m = re.match(r"^(\S+)\s+is unreachable", line)
        if m:
            results[m.group(1)] = (False, 0.0)
            continue
        # fallback: ip from error line
        ip = line.split(" ")[0].rstrip(":")
        if ip and ip not in results:
            results[ip] = (False, 0.0)
    for ip in ip_list:
        if ip not in results:
            results[ip] = (False, 0.0)
    return results


def _default_iface():
    """Interface der Default-Route aus /proc/net/route."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "00000000":
                    if int(parts[3], 16) & 0x0001:  # RTF_UP
                        return parts[0]
    except Exception:
        pass
    return None


def _iface_ip(iface):
    """IPv4-Adresse eines Interfaces via ioctl (ohne externe Tools)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", iface[:15].encode("utf-8"))
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24])
    except Exception:
        return ""
    finally:
        s.close()


def _wifi_ssid(iface):
    """SSID ermitteln - iwgetid, sonst nmcli."""
    for cmd in (["/usr/sbin/iwgetid", "-r", iface],
                ["iwgetid", "-r", iface],
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except Exception:
            continue
        if not out:
            continue
        if cmd[0] == "nmcli":
            for line in out.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1].strip()
            continue
        return out.splitlines()[0].strip()
    return ""


def net_info():
    """Art der Internetverbindung: LAN/WLAN, Interface, IP, ggf. SSID."""
    iface = _default_iface()
    if not iface or iface == "lo":
        return {"net_iface": iface or "", "net_type": "", "net_ip": "", "net_ssid": ""}
    wireless = (os.path.isdir("/sys/class/net/{}/wireless".format(iface))
                or iface.startswith(("wl", "wlan")))
    return {
        "net_iface": iface,
        "net_type": "WLAN" if wireless else "LAN",
        "net_ip": _iface_ip(iface),
        "net_ssid": _wifi_ssid(iface) if wireless else "",
    }


def read_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except Exception:
        return ""


def _transition(prev_rec, alive, now):
    """Liefert (since, last_down, last_up) unter Beibehaltung der Historie."""
    prev_alive = prev_rec.get("ping_ok")
    since = prev_rec.get("since")
    last_down = prev_rec.get("last_down")
    last_up = prev_rec.get("last_up")

    changed = prev_alive is None or bool(prev_alive) != bool(alive) or not since
    if changed:
        since = now
        if alive:
            last_up = now
        else:
            last_down = now
    if alive:
        last_up = last_up or since
    else:
        last_down = last_down or since
    return since, last_down, last_up


def update_internet(prev, raw, cfg, now):
    """Entprellt den Internet-Status und pflegt die Historie.

    Rueckgabe: (felder, meldung_erforderlich)
    felder enthaelt internet, internet_raw, internet_since, internet_fail,
    internet_ok_streak, internet_down_since, internet_last_down/up,
    internet_suppress.
    """
    offline_after = max(1, int(cfg.get("offline_after", 3)))
    online_after = max(1, int(cfg.get("online_after", 2)))

    state = prev.get("internet")
    fail = int(prev.get("internet_fail") or 0)
    ok_streak = int(prev.get("internet_ok_streak") or 0)
    since = prev.get("internet_since")
    down_since = prev.get("internet_down_since")
    last_down = prev.get("internet_last_down")
    last_up = prev.get("internet_last_up")
    suppress = bool(prev.get("internet_suppress"))

    if raw:
        ok_streak += 1
        fail = 0
    else:
        fail += 1
        ok_streak = 0

    changed = False
    if state is None:
        # Erster Lauf bzw. nach Neustart: Wert uebernehmen, aber nicht melden.
        state = raw
        since = now
        if raw:
            last_up = now
        else:
            last_down = now
            down_since = now
        changed = True
    elif state and not raw and fail >= offline_after:
        state = False
        since = now
        last_down = now
        changed = True
        # Ausfallbeginn = erster Fehlversuch dieser Serie
        down_since = now - (fail - 1) * max(1, int(cfg.get("poll_interval", 15)))
    elif not state and raw and ok_streak >= online_after:
        state = True
        since = now
        last_up = now
        changed = True

    notify = bool(changed and state and not suppress)
    if state and ok_streak >= online_after:
        suppress = False

    return {
        "internet": state,
        "internet_raw": raw,
        "internet_since": since,
        "internet_fail": fail,
        "internet_ok_streak": ok_streak,
        "internet_down_since": down_since,
        "internet_last_down": last_down,
        "internet_last_up": last_up,
        "internet_suppress": suppress,
        "internet_offline_after": offline_after,
        "internet_online_after": online_after,
    }, notify


def _lock_or_exit():
    """Verhindert parallele Checker-Laeufe (Race -> Doppelmeldungen)."""
    for attempt in range(5):
        try:
            fh = open(LOCK, "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            time.sleep(0.4)
        except Exception:
            return True  # Lock nicht verfuegbar -> trotzdem weiterarbeiten
    return None


def main():
    lock = _lock_or_exit()
    if lock is None:
        print("Checker laeuft bereits - uebersprungen")
        return
    cfg = load_config()
    prev = load_prev()
    prev_devs = prev.get("devices", {}) or {}

    # Nach einem Neustart nicht sofort "wieder online" melden: Netz erst
    # stabil werden lassen (boot_notify uebernimmt die Startmeldung).
    boot = read_boot_id()
    if prev.get("boot_id") != boot:
        prev["internet"] = None
        prev["internet_fail"] = 0
        prev["internet_ok_streak"] = 0
        prev["internet_suppress"] = True
        prev["boot_id"] = boot

    ips_map = collect_active(cfg)
    inet_ip = cfg.get("internet_check_ip", "8.8.8.8")
    all_ips = list(ips_map.keys())
    if inet_ip not in all_ips:
        all_ips.append(inet_ip)

    fping_to = cfg.get("fping_timeout", 800)
    ping_results = run_fping(all_ips, fping_to)
    internet_raw = ping_results.get(inet_ip, (False, 0))[0]

    now = time.time()
    inet_fields, notify = update_internet(prev, internet_raw, cfg, now)
    internet_ok = bool(inet_fields["internet"])
    inet_since = inet_fields["internet_since"]
    net = net_info()

    # Nur echte Wechsel melden - offline kann nicht zugestellt werden,
    # daher geht die Meldung beim Zurueckkommen inkl. Ausfalldauer raus.
    if notify:
        try:
            import notify as ntfy
            if net["net_type"] == "WLAN":
                conn = "WLAN '{}'".format(net["net_ssid"] or "?")
            else:
                conn = net["net_type"] or "Netzwerk"
            if net["net_ip"]:
                conn += " {}".format(net["net_ip"])
            start = inet_fields.get("internet_down_since")
            if start:
                secs = int(max(0, now - start))
                if secs >= 60:
                    dur = "{}m {:02d}s".format(secs // 60, secs % 60)
                else:
                    dur = "{}s".format(secs)
                extra = "Ausfall: {}".format(dur)
            else:
                extra = "vorher kein Ausfall erkannt"
            ntfy.send("Internet wieder online\n{}\n{}".format(conn, extra),
                      title="zero2: Internet wieder online",
                      tags="globe_with_meridians")
        except Exception:
            pass

    online = 0
    total = 0
    paused_total = 0
    devices = {}

    for gid, grp in cfg.get("groups", {}).items():
        for dev in grp.get("devices", []):
            ip = dev.get("ip", "").strip()
            name = dev.get("name", ip)
            key = "{}|{}".format(gid, name)
            prev_rec = prev_devs.get(key, {}) or {}

            if dev.get("paused"):
                paused_total += 1
                devices[key] = {
                    "name": name,
                    "ip": ip,
                    "group": gid,
                    "type": dev.get("type", ""),
                    "ping_ok": prev_rec.get("ping_ok", False),
                    "rtt_ms": prev_rec.get("rtt_ms", 0.0),
                    "checked_at": now,
                    "paused": True,
                    "since": prev_rec.get("since"),
                    "last_down": prev_rec.get("last_down"),
                    "last_up": prev_rec.get("last_up"),
                }
                continue

            total += 1
            alive, rtt = ping_results.get(ip, (False, 0.0))
            since, last_down, last_up = _transition(prev_rec, alive, now)
            devices[key] = {
                "name": name,
                "ip": ip,
                "group": gid,
                "type": dev.get("type", ""),
                "ping_ok": alive,
                "rtt_ms": round(rtt, 1),
                "checked_at": now,
                "since": since,
                "last_down": last_down,
                "last_up": last_up,
            }
            if alive:
                online += 1

    net_rtt = round(ping_results.get(inet_ip, (False, 0))[1], 1)
    devices["!network|INTERNET"] = {
        "name": "INTERNET",
        "ip": inet_ip,
        "group": "!network",
        "type": "inet",
        "ping_ok": internet_ok,
        "rtt_ms": net_rtt,
        "checked_at": now,
        "since": inet_since,
        "last_down": inet_fields.get("internet_last_down"),
        "last_up": inet_fields.get("internet_last_up"),
    }

    state = {
        "last_check": now,
        "online": online,
        "total": total,
        "paused": paused_total,
        "poll_interval": cfg.get("poll_interval", 15),
        "speedtest_interval_min": cfg.get("speedtest_interval_min", 30),
        "boot_id": boot,
        "devices": devices,
    }
    state.update(inet_fields)
    state.update(net)

    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE)

    msg = "OK: {}/{} online, {} pausiert, internet={}{}{}"
    print(msg.format(online, total, paused_total, internet_ok,
                     " (raw={})".format(internet_raw) if internet_ok != internet_raw else "",
                     ", {}{}".format(net["net_type"], " " + net["net_ip"] if net["net_ip"] else "")))


if __name__ == "__main__":
    main()
