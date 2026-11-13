#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thread-Border-Router-Check fuer das Serverraum-Dashboard.

Zwei Ebenen:

1. **Port-/Geraetecheck** (immer): TCP-Verbindung auf den Cast-Port
   (Standard 8008) plus optional `/setup/eureka_info` fuer Uptime und Name.
   Das sagt nur "Streamer ist an", nicht "Thread-Border-Router laeuft".

2. **Thread-Status per ADB** (optional): Thread ist ein 802.15.4-Funknetz und
   im LAN nicht direkt pruefbar (kein _meshcop._udp/_trel._udp bei Google).
   Nur ADB kann den Zustand des Android-Thread-Dienstes bzw. des
   `thread-wpan`-Interfaces lesen.

Ergebnis landet in ``thread.json``.
"""
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "thread.json")
ADB = "adb"

DEFAULT_PORT = 8008
DEFAULT_PROBES = [
    "dumpsys thread_network",
    "dumpsys connectivity",
    "ip -6 addr show dev thread-wpan",
    "getprop | grep -i thread",
    "dumpsys threadnetwork",
]

_ENABLED_RE = re.compile(
    r"(?i)\bthread[ _-]?network\b[^\n]{0,60}?\benabled\s*[:=]\s*(true|false)")
_STATE_RE = re.compile(r"(?i)\bthread\b[^\n]{0,20}?\bstate\s*[:=]\s*(\w+)")
_AGENT_RE = re.compile(r"NetworkAgentInfo.*?Transports:\s*THREAD")
_AGENT_OK_RE = re.compile(r"ni\{\s*CONNECTED")
_NET_NAME_RE = re.compile(r"networkName=([^,}\s]+)")
_NET_CHANNEL_RE = re.compile(r"\bchannel=(\d+)")
_INET6_RE = re.compile(r"inet6\s+(fd[0-9a-f]{2}:[0-9a-f:]+)/", re.I)
_NO_DEV_RE = re.compile(r"(does not exist|Cannot find device|No such device)", re.I)


def _config():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        return None
    th = cfg.get("thread") if isinstance(cfg, dict) else None
    if not isinstance(th, dict) or not th.get("enabled", True):
        return None
    return th


def _previous():
    try:
        with open(OUT) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, OUT)


def tcp_check(host, port, timeout):
    """(ok, rtt_ms) fuer einen TCP-Connect ohne Datenverkehr."""
    started = time.time()
    s = None
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        return True, round((time.time() - started) * 1000, 1)
    except Exception:
        return False, 0.0
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def eureka_info(host, port, timeout):
    """Name/Uptime aus dem Cast-Setup-Endpunkt (best effort)."""
    url = "http://{}:{}/setup/eureka_info".format(host, port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read(65536).decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _run(cmd, timeout):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        return proc.returncode, out, err
    except Exception as e:
        return -1, "", str(e)


def adb_state(host, port, timeout):
    """ADB-Verbindungszustand: device / unauthorized / offline / missing."""
    if not _which(ADB):
        return "missing"
    serial = "{}:{}".format(host, port)
    _run([ADB, "connect", serial], timeout)
    rc, out, err = _run([ADB, "-s", serial, "get-state"], timeout)
    text = (out + err).lower()
    if "unauthorized" in text:
        return "unauthorized"
    if rc == 0 and "device" in text:
        return "device"
    if "offline" in text:
        return "offline"
    return "unknown"


def _which(name):
    for path in (os.environ.get("PATH") or "").split(os.pathsep):
        cand = os.path.join(path, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def adb_probe(host, port, commands, timeout):
    """Fuehrt die Probe-Kommandos aus und sammelt die Ausgaben."""
    serial = "{}:{}".format(host, port)
    results = []
    texts = []
    for cmd in commands:
        rc, out, err = _run([ADB, "-s", serial, "shell", cmd], timeout)
        full = (out or err).strip()
        texts.append(full)
        results.append({"cmd": cmd, "rc": rc,
                        "out": full[:4000]})
        # Fruehester eindeutiger Treffer reicht; der Rest waere nur Redundanz.
        state, evidence = _classify(texts)
        if state != "unknown":
            break
    state, evidence = _classify(texts)
    return state, evidence, results


def _classify(texts):
    """Heuristik: Connectivity-Agent, enabled-Flag, Interface/IPv6 oder Zustandswort."""
    combined = "\n".join(texts)

    # 1. Beste Quelle: der Thread-Netzwerk-Agent in `dumpsys connectivity`.
    for line in combined.splitlines():
        if not _AGENT_RE.search(line):
            continue
        if _AGENT_OK_RE.search(line):
            parts = []
            m = _NET_NAME_RE.search(line)
            if m:
                parts.append(m.group(1))
            m = _NET_CHANNEL_RE.search(line)
            if m:
                parts.append("Kanal {}".format(m.group(1)))
            detail = " ({})".format(", ".join(parts)) if parts else ""
            return "active", "Thread CONNECTED{}".format(detail)
        return "inactive", "Thread-Agent vorhanden, aber nicht CONNECTED"

    # 2. Explizite Flags/Zustaende, falls eine andere Android-Version sie liefert.
    for m in _ENABLED_RE.finditer(combined):
        value = m.group(1).lower() == "true"
        line = m.group(0).strip().splitlines()[0]
        return ("active" if value else "inactive"), line[:160]
    for m in _STATE_RE.finditer(combined):
        word = m.group(1).lower()
        if word in ("enabled", "active", "started", "running", "connected", "leader",
                    "router", "child"):
            return "active", m.group(0).strip()[:160]
        if word in ("disabled", "inactive", "stopped", "disconnected", "offline"):
            return "inactive", m.group(0).strip()[:160]

    # 3. Interface-Ebene: fehlendes thread-wpan = aus, globale Adresse = aktiv.
    if _NO_DEV_RE.search(combined):
        return "inactive", "thread-wpan nicht vorhanden"
    for line in combined.splitlines():
        if "inet6" in line and _INET6_RE.search(line) and "deprecated" not in line:
            return "active", "thread-wpan hat globale Thread-Adresse"
    if re.search(r"(?i)thread-wpan", combined):
        return "inactive", "thread-wpan ohne globale Adresse"
    return "unknown", ""


def _send(msg, title, tags):
    try:
        import notify
        notify.send(msg, title=title, tags=tags)
    except Exception:
        pass


def main():
    th = _config()
    if not th:
        return 0
    host = str(th.get("host") or "").strip()
    if not host:
        return 0
    port = int(th.get("port") or DEFAULT_PORT)
    timeout = float(th.get("timeout") or 3)
    name = th.get("name") or host
    prev = _previous()

    ok, rtt = tcp_check(host, port, timeout)
    info = eureka_info(host, port, timeout) if ok else {}

    adb_cfg = th.get("adb", True)
    adb = {"state": "disabled", "thread": "unknown", "evidence": "", "probes": []}
    if adb_cfg:
        adb["state"] = adb_state(host, int(th.get("adb_port") or 5555), timeout)
        if adb["state"] == "device":
            cmds = th.get("adb_commands") or DEFAULT_PROBES
            state, evidence, probes = adb_probe(
                host, int(th.get("adb_port") or 5555), cmds,
                float(th.get("adb_timeout") or 6))
            adb["thread"] = state
            adb["evidence"] = evidence
            adb["probes"] = probes

    data = {
        "ok": ok,
        "ts": time.time(),
        "host": host,
        "port": port,
        "name": info.get("name") or name,
        "rtt_ms": rtt,
        "uptime": info.get("uptime"),
        "connected": info.get("connected"),
        "adb": adb,
        "error": None if ok else "TCP {}:{} nicht erreichbar".format(host, port),
    }
    _write(data)

    # Nur bei echten Wechseln melden (kein Alarm beim ersten Lauf).
    if th.get("notify", True) and prev:
        was_ok = bool(prev.get("ok"))
        old = ((prev.get("adb") or {}).get("thread") or "unknown")
        new = adb["thread"]
        if was_ok and not ok:
            _send("Thread-Border-Router {} ({}) ist nicht erreichbar "
                  "(Port {} zu).".format(name, host, port),
                  "Thread-BR offline", "warning")
        elif not was_ok and ok:
            _send("Thread-Border-Router {} ({}) ist wieder erreichbar "
                  "(Port {} offen).".format(name, host, port),
                  "Thread-BR online", "white_check_mark")
        elif ok and old == "active" and new == "inactive":
            _send("Thread-Border-Router {} ({}): Thread-Netz meldet "
                  "inaktiv{}.".format(
                      name, host,
                      " ({})".format(adb["evidence"]) if adb["evidence"] else ""),
                  "Thread-BR inaktiv", "warning")
        elif ok and old == "inactive" and new == "active":
            _send("Thread-Border-Router {} ({}): Thread-Netz ist wieder "
                  "aktiv{}.".format(
                      name, host,
                      " ({})".format(adb["evidence"]) if adb["evidence"] else ""),
                  "Thread-BR aktiv", "white_check_mark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
