#!/usr/bin/env python3
"""Minimaler Checker – testet alle IPs via fping, schreibt state.json."""
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.json"), "r") as f:
        return json.load(f)


def collect_ips(cfg):
    ips = {}
    for gid, grp in cfg.get("groups", {}).items():
        for dev in grp.get("devices", []):
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


def main():
    cfg = load_config()
    ips_map = collect_ips(cfg)
    inet_ip = cfg.get("internet_check_ip", "8.8.8.8")
    all_ips = list(ips_map.keys())
    if inet_ip not in all_ips:
        all_ips.append(inet_ip)

    fping_to = cfg.get("fping_timeout", 800)
    ping_results = run_fping(all_ips, fping_to)
    internet_ok = ping_results.get(inet_ip, (False, 0))[0]

    now = time.time()
    online = 0
    total = 0
    devices = {}

    for gid, grp in cfg.get("groups", {}).items():
        for dev in grp.get("devices", []):
            ip = dev.get("ip", "").strip()
            name = dev.get("name", ip)
            total += 1
            key = "{}|{}".format(gid, name)

            alive, rtt = ping_results.get(ip, (False, 0.0))
            devices[key] = {
                "name": name,
                "ip": ip,
                "group": gid,
                "type": dev.get("type", ""),
                "ping_ok": alive,
                "rtt_ms": round(rtt, 1),
                "checked_at": now,
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
    }

    state = {
        "last_check": now,
        "online": online,
        "total": total,
        "internet": internet_ok,
        "devices": devices,
    }

    with open(os.path.join(HERE, "state.json"), "w") as f:
        json.dump(state, f, indent=2)

    msg = "OK: {}/{} online, internet={}"
    print(msg.format(online, total, internet_ok))


if __name__ == "__main__":
    main()