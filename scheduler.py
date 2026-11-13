#!/usr/bin/env python3
"""Sequenzieller Poller-Scheduler fuer das Serverraum-Dashboard.

Statt alle Abfragen gleichzeitig zu starten, laufen sie nacheinander:
- jeder Job hat ein eigenes Intervall und einen Zeitversatz (Phase)
- es laeuft immer nur ein Kindprozess
- Fehlversuche fuehren zu einem Backoff (max. 4x Intervall)
- Timeouts verhindern, dass ein haengender Job die Warteschlange blockiert
- unter hoher Systemlast werden unkritische Jobs kurz verschoben
- der eigene Speedtest laeuft nur, wenn die FRITZ!Box keine Daten liefert

Der Dienst netbook-checker startet weiterhin checker_loop.sh; das ist nur
noch eine duenne Huelle um dieses Skript.
"""
import fcntl
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
FRITZ = os.path.join(HERE, "fritzbox.json")
LOCK = os.path.join(HERE, "scheduler.lock")

# Reihenfolge = Prioritaet, wenn mehrere Jobs gleichzeitig faellig sind.
# phase = Startversatz in Sekunden, damit die Abfragen nicht buendeln.
JOBS = (
    {"name": "checker", "script": "checker.py", "key": "poll_interval",
     "default": 15, "phase": 0, "timeout": 40, "critical": True},
    {"name": "proxmox", "script": "proxmox.py", "key": "proxmox_interval_sec",
     "default": 30, "phase": 4, "timeout": 45, "critical": False},
    {"name": "fritzbox", "script": "fritzbox.py", "key": "fritzbox_interval_sec",
     "default": 60, "phase": 12, "timeout": 30, "critical": False},
    {"name": "unifi", "script": "unifi.py", "key": "unifi_interval_sec",
     "default": 60, "phase": 28, "timeout": 40, "critical": False},
    {"name": "deconz", "script": "deconz.py", "key": "deconz_interval_sec",
     "default": 60, "phase": 44, "timeout": 30, "critical": False},
    {"name": "homematic", "script": "homematic.py", "key": "homematic_interval_sec",
     "default": 60, "phase": 36, "timeout": 30, "slow": 12, "critical": False},
    {"name": "thread", "script": "thread.py", "key": "thread_interval_sec",
     "default": 60, "phase": 52, "timeout": 45, "slow": 8, "critical": False},
    {"name": "speedtest", "script": "speedtest.py", "key": "speedtest_interval_min",
     "default": 30, "phase": 120, "timeout": 90, "critical": False,
     "factor": 60, "fritz_guard": True},
)


def _lock():
    """Nur ein Scheduler gleichzeitig (flock, automatisch beim Prozessende frei)."""
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
    except Exception:
        pass
    return fd


def _config():
    try:
        with open(CONFIG) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _interval(job, cfg):
    try:
        value = int(cfg.get(job["key"], job["default"]))
    except (TypeError, ValueError):
        value = job["default"]
    return max(1, value) * job.get("factor", 1)


def _fritz_ok():
    try:
        with open(FRITZ) as f:
            data = json.load(f)
        return bool(data.get("ok")) and int(data.get("down_bps") or 0) > 0
    except Exception:
        return False


def _load_high():
    """Unkritische Jobs kurz zurueckstellen, wenn die Kiste ausgelastet ist."""
    try:
        cores = os.cpu_count() or 1
        return os.getloadavg()[0] > max(1.5, cores * 0.5)
    except Exception:
        return False


def _run(job):
    cmd = [sys.executable, os.path.join(HERE, job["script"])]

    def _nice():
        try:
            os.nice(10)
        except Exception:
            pass

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=HERE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=job["timeout"],
            preexec_fn=_nice)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = -9
    except Exception:
        rc = -1
    return rc, time.time() - started


def main():
    lock_fd = _lock()
    if lock_fd is None:
        print("Scheduler laeuft bereits - keine zweite Instanz gestartet.")
        return 0

    try:
        os.nice(5)
    except Exception:
        pass

    print("Scheduler gestartet: {} Jobs, sequenziell.".format(len(JOBS)))
    now = time.time()
    due = {}
    fails = {}
    for job in JOBS:
        due[job["name"]] = now + job["phase"]
        fails[job["name"]] = 0

    try:
        while True:
            cfg = _config()
            now = time.time()
            ready = [j for j in JOBS if now >= due[j["name"]]]

            if ready:
                job = ready[0]
                name = job["name"]
                interval = _interval(job, cfg)

                # Speedtest nur, wenn die FRITZ!Box keine Leitungsdaten liefert.
                if job.get("fritz_guard") and _fritz_ok():
                    due[name] = now + 60
                    continue

                # Unter Last zuerst den Checker, unkritische Jobs kurz warten.
                if not job["critical"] and _load_high():
                    due[name] = now + 3
                    continue

                rc, duration = _run(job)
                if rc == 0:
                    fails[name] = 0
                    delay = interval
                else:
                    fails[name] = min(fails[name] + 1, 3)
                    delay = interval * min(2 ** fails[name], 4)
                    print("{}: {} fehlgeschlagen (rc={}, {:.1f}s), naechster Versuch in {}s"
                          .format(time.strftime("%H:%M:%S"), name, rc,
                                  duration, delay), flush=True)
                if duration > job.get("slow", 5.0) and rc == 0:
                    print("{}: {} langsam ({:.1f}s)".format(
                        time.strftime("%H:%M:%S"), name, duration), flush=True)
                due[name] = time.time() + delay
                continue

            soonest = min(due.values())
            time.sleep(max(0.05, min(1.0, soonest - time.time())))
    finally:
        try:
            os.close(lock_fd)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
