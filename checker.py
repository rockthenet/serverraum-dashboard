#!/usr/bin/env python3
"""Minimaler Checker - testet alle IPs via fping, schreibt state.json.

Zusaetzlich pro Geraet:
  paused     - uebersprungen (config: "paused": true), zaehlt nicht in online/total
  since      - Zeitpunkt des letzten Statuswechsels (online <-> offline)
  last_down  - wann das Geraet zuletzt offline ging
  last_up    - wann es zuletzt wieder online ging
Damit kann das Dashboard "seit wann online/down" anzeigen, ohne eigene History.
"""
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")


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


def main():
    cfg = load_config()
    prev = load_prev()
    prev_devs = prev.get("devices", {}) or {}

    ips_map = collect_active(cfg)
    inet_ip = cfg.get("internet_check_ip", "8.8.8.8")
    all_ips = list(ips_map.keys())
    if inet_ip not in all_ips:
        all_ips.append(inet_ip)

    prev_inet = prev.get("internet")
    fping_to = cfg.get("fping_timeout", 800)
    ping_results = run_fping(all_ips, fping_to)
    internet_ok = ping_results.get(inet_ip, (False, 0))[0]

    # Internet wieder online -> ntfy (offline geht ja eh nicht raus)
    if internet_ok and prev_inet is False:
        try:
            import notify
            notify.send("Internet ist wieder ONLINE",
                        title="Internet", tags="globe_with_meridians", priority="high")
        except Exception:
            pass

    now = time.time()
    inet_since = prev.get("internet_since")
    if prev_inet is None or bool(prev_inet) != bool(internet_ok) or not inet_since:
        inet_since = now

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
        "last_down": None if internet_ok else inet_since,
        "last_up": inet_since if internet_ok else None,
    }

    state = {
        "last_check": now,
        "online": online,
        "total": total,
        "paused": paused_total,
        "internet": internet_ok,
        "internet_since": inet_since,
        "poll_interval": cfg.get("poll_interval", 15),
        "speedtest_interval_min": cfg.get("speedtest_interval_min", 30),
        "devices": devices,
    }

    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)

    msg = "OK: {}/{} online, {} pausiert, internet={}"
    print(msg.format(online, total, paused_total, internet_ok))


if __name__ == "__main__":
    main()
