#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serverraum Dashboard - TUI fuer Netbook (Debian 10) und Raspberry Pi
Multi-Panel TUI: Ping-Monitoring (links) + Proxmox-Cluster (rechts)
Mit Lightbox-Modals, Tastaturnavigation, Proxmox Start/Stop/Reboot/Backup.

Steuerung:
  Tab / Links / Rechts    Panel wechseln (Geraete <-> Proxmox)
  Pfeil Oben / Unten      Geraet oder Guest auswaehlen (auch j / k)
  Enter                   Lightbox-Modal oeffnen (Aktionen & Details)
  f                       FRITZ!Box-Details (Leitung, Auslastung, Traffic)
  q                       Modal schliessen (wenn offen) bzw. Dashboard beenden
  r                       Ping-Check sofort ausfuehren
  p                       Proxmox-Status sofort neu abfragen
  s / x                   Quick-Start / Quick-Stop (fuer selektierten Guest)
"""
import fcntl
import json
import os
import re
import select
import signal
import ssl
import subprocess
import sys
import termios
import threading
import time
import tty
import urllib.error
import urllib.parse
import urllib.request


def _hostinfo():
    """Kurze Hardware-/OS-Kennung fuer die Titelleiste (host / SoC / Distro)."""
    import platform
    host = platform.node() or "?"
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f:
            info = f.read().splitlines()
        model = next((l.split(":", 1)[1].strip() for l in info if l.startswith("Model")), "")
        name = next((l.split(":", 1)[1].strip() for l in info if l.startswith("model name")), "")
        cpu = model or name
        cpu = re.sub(r"\s+Rev\s+\S+.*$", "", cpu)
        cpu = cpu.replace("Raspberry Pi ", "")
        cpu = re.sub(r"\((?:R|TM)\)", "", cpu).replace("CPU", "")
        cpu = re.sub(r"\s+", " ", cpu).strip()
        cpu = cpu[:22].strip()
    except Exception:
        pass
    osname = ""
    try:
        fields = {}
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    fields[k] = v.strip().strip('"')
        osname = fields.get("NAME", "").replace(" GNU/Linux", "")
        if fields.get("VERSION_ID"):
            osname = (osname + " " + fields["VERSION_ID"]).strip()
    except Exception:
        pass
    parts = [p for p in (host, cpu or "?", osname or "?") if p]
    return " / ".join(parts)


HOSTINFO = _hostinfo()


def host_uptime():
    """Laufzeit des eigenen Systems in Sekunden."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


_CPU_LAST = {"total": 0, "idle": 0}
_CPU_PCT = None


def host_cpu_percent():
    """CPU-Auslastung des Dashboard-Rechners seit dem letzten Aufruf."""
    global _CPU_PCT
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        prev = dict(_CPU_LAST)
        _CPU_LAST["total"] = total
        _CPU_LAST["idle"] = idle
        dt = total - prev["total"]
        if dt < 20:
            return _CPU_PCT
        di = idle - prev["idle"]
        pct = int(round(100.0 * (dt - di) / dt))
        _CPU_PCT = max(0, min(100, pct))
        return _CPU_PCT
    except Exception:
        return None


def host_ram_percent():
    """RAM-Auslastung des Dashboard-Rechners (MemAvailable, sonst Schaetzung)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0])
        total = info.get("MemTotal") or 0
        if not total:
            return None
        avail = info.get("MemAvailable")
        if avail is None:
            avail = (info.get("MemFree", 0) + info.get("Buffers", 0)
                     + info.get("Cached", 0))
        return max(0, min(100, int(round(100.0 * (total - avail) / total))))
    except Exception:
        return None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
PROX = os.path.join(HERE, "proxmox.json")
SPEED = os.path.join(HERE, "speed.json")
FRITZ = os.path.join(HERE, "fritzbox.json")
UNI = os.path.join(HERE, "unifi.json")
DECONZ = os.path.join(HERE, "deconz.json")
HOMEMATIC = os.path.join(HERE, "homematic.json")
CONFIG = os.path.join(HERE, "config.json")
LOCK_FILE = os.path.join(HERE, "dashboard.lock")

# ─── ANSI ───
ESC = "\033"
RST = ESC + "[0m"
BOLD = ESC + "[1m"
DIM = ESC + "[2m"
REV = ESC + "[7m"

def fg(n): return ESC + "[38;5;" + str(n) + "m"
def bg(n): return ESC + "[48;5;" + str(n) + "m"

CLR = {
    "green":   fg(40),
    "red":     fg(196),
    "yellow":  fg(220),
    "orange":  fg(208),
    "cyan":    fg(45),
    "blue":    fg(39),
    "gray":    fg(244),
    "white":   fg(255),
    "dark":    fg(240),
    "titlebg": bg(24) + fg(231),
    "footbg":  bg(236) + fg(231),
    "selbg":   bg(28) + fg(231),
    "modalbg": bg(235) + fg(255),
    "modalhi": bg(240) + fg(220) + BOLD,
}

BOX = {
    "h": "\u2500", "v": "\u2502",
    "tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
    "lt": "\u251c", "rt": "\u2524", "tt": "\u252c", "bt": "\u2534",
    "x": "\u253c",
}
MBOX = {
    "h": "\u2550", "v": "\u2551",
    "tl": "\u2554", "tr": "\u2557", "bl": "\u255a", "br": "\u255d",
    "lt": "\u2560", "rt": "\u2563",
}


def _pct_fg(value):
    """Farbe fuer CPU/RAM-Prozentwerte (ohne Hintergrund/Reset)."""
    if value is None:
        return CLR["dark"]
    if value < 70:
        return CLR["green"]
    if value < 85:
        return CLR["yellow"]
    if value < 95:
        return CLR["orange"]
    return CLR["red"]


def _is_kiosk():
    """Laeuft diese Instanz auf dem physischen Bildschirm (tty1)?"""
    try:
        return os.ttyname(0) == "/dev/tty1"
    except Exception:
        return False


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _read_registry(fd):
    """Eintraege (pid, tty, rolle) aus der Sperrdatei lesen (flock gehalten)."""
    entries = []
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096).decode("utf-8", "replace")
    except Exception:
        return entries
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        tty = parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 else ("k" if tty == "/dev/tty1" else "e")
        entries.append((pid, tty, role))
    return entries


def _write_registry(fd, entries):
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        text = "".join("{} {} {}\n".format(pid, tty or "-", role)
                       for pid, tty, role in entries)
        os.write(fd, text.encode("utf-8"))
    except Exception:
        pass


def _who_sessions():
    """tty -> (nutzer, host) aus `who`; nur fuer die Sitzungs-Meldung."""
    out = {}
    try:
        res = subprocess.run(["who"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=3)
        text = res.stdout.decode("utf-8", "replace")
    except Exception:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        user = parts[0]
        # Die Spalte "line" kann Zusatzwoerter enthalten (z.B. "sshd pts/0"),
        # daher das eigentliche TTY heraussuchen.
        tty = ""
        for part in parts[1:]:
            cand = part.replace("/dev/", "")
            if re.match(r"^(pts/\d+|tty\w*|seat\w*)$", cand):
                tty = cand
                break
        if not tty:
            continue
        host = ""
        m = re.search(r"\(([^)]*)\)", " ".join(parts[2:]))
        if m:
            host = m.group(1).strip()
        out[tty] = (user, host)
    return out


def _proc_session(pid):
    """(nutzer, client-ip) einer laufenden Instanz aus /proc/<pid>/environ."""
    user = host = ""
    try:
        with open("/proc/{}/environ".format(int(pid)), "rb") as f:
            raw = f.read()
    except Exception:
        return user, host
    env = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    user = env.get("LOGNAME") or env.get("USER") or ""
    conn = env.get("SSH_CONNECTION") or ""
    host = conn.split()[0] if conn.split() else ""
    return user, host


def _session_label(tty, sessions):
    """Lesbare Kennung 'nutzer von host (tty)' fuer eine Sitzung."""
    key = (tty or "").replace("/dev/", "")
    user, host = (sessions or {}).get(key, ("unbekannt", ""))
    label = user
    if host:
        label += " von " + host
    if key and key != "-":
        label += " ({})".format(key)
    return label


def _notify_session_swap(old_tty, new_tty, old_proc=None, new_proc=None):
    """ntfy-Meldung, wenn eine externe Dashboard-Sitzung verdraengt wurde."""
    sessions = _who_sessions()

    def label(tty, proc):
        user, host = proc or ("", "")
        if user or host:
            return "{} von {} ({})".format(user or "unbekannt",
                                            host or "unbekannt",
                                            (tty or "-").replace("/dev/", ""))
        return _session_label(tty, sessions)

    old = label(old_tty, old_proc)
    new = label(new_tty, new_proc)

    def work():
        try:
            import notify
            notify.send(
                "Neue Dashboard-Sitzung: {}\nVerdraengt: {}".format(new, old),
                title="Serverraum-Dashboard: Sitzungswechsel",
                tags="warning")
        except Exception:
            pass

    try:
        t = threading.Thread(target=work)
        t.daemon = True
        t.start()
    except Exception:
        pass


def _acquire_instance_lock():
    """Bis zu zwei Dashboards: der Kiosk auf tty1 und eine externe Sitzung.

    Startet eine neue Instanz, beendet sie die jeweils aeltere Instanz ihrer
    Art: eine neue Shell-Sitzung verdraengt die vorherige Shell-Sitzung, der
    Kiosk auf dem Host laeuft weiter. Ein Kiosk-Start verdraengt nur einen
    alten Kiosk-Prozess, nie die externe Sitzung.

    Rueckgabe (fd, info): fd=None -> Start abgelehnt (alte Instanz reagiert
    nicht), fd=-1 -> Sperrdatei nicht verfuegbar (Dashboard laeuft trotzdem).
    """
    kiosk = _is_kiosk()
    role = "k" if kiosk else "e"
    tty = ""
    try:
        tty = os.ttyname(0)
    except Exception:
        pass
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    except Exception:
        return -1, ""
    got_lock = False
    for _ in range(30):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got_lock = True
            break
        except OSError:
            time.sleep(0.1)
    if not got_lock:
        # Alte Instanz haelt die Sperre noch -> Dashboard trotzdem starten.
        try:
            os.close(fd)
        except Exception:
            pass
        return -1, ""
    try:
        entries = [(p, t, r) for p, t, r in _read_registry(fd)
                   if p != os.getpid() and _pid_alive(p)]
        keep = []
        kill = []
        swapped = []
        for pid, etty, erole in entries:
            if role == erole:
                # gleiche Art: die neue Instanz verdraengt die alte
                kill.append(pid)
                if role == "e":
                    swapped.append((pid, etty))
            else:
                keep.append((pid, etty, erole))
        old_proc = _proc_session(swapped[0][0]) if swapped else None
        new_proc = _proc_session(os.getpid()) if swapped else None
        for pid in kill:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        deadline = time.time() + 5.0
        remaining = list(kill)
        while remaining and time.time() < deadline:
            time.sleep(0.1)
            remaining = [p for p in remaining if _pid_alive(p)]
        if remaining and not kiosk:
            # Alte externe Instanz liess sich nicht beenden -> nicht starten.
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            return None, " ".join(str(p) for p in remaining)
        _write_registry(fd, keep + [(os.getpid(), tty, role)])
        fcntl.flock(fd, fcntl.LOCK_UN)
        if swapped:
            _notify_session_swap(swapped[0][1], tty, old_proc, new_proc)
        return fd, ""
    except Exception:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except Exception:
            pass
        return -1, ""


def _release_instance_lock(fd):
    """Eigenen Eintrag entfernen, ohne andere Instanzen zu verlieren."""
    if not isinstance(fd, int) or fd < 0:
        return
    got = False
    for _ in range(10):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            break
        except OSError:
            time.sleep(0.1)
    if got:
        try:
            entries = [(p, t, r) for p, t, r in _read_registry(fd)
                       if p != os.getpid()]
            _write_registry(fd, entries)
        except Exception:
            pass
    try:
        os.close(fd)
    except Exception:
        pass

SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
SPIN = ["\u280b", "\u2819", "\u2838", "\u2834", "\u2826", "\u2807"]

ANSI_RE = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")
MOUSE_RE = re.compile(rb"\033\[<(\d+);(\d+);(\d+)([Mm])")


def vlen(s):
    """Sichtbare Laenge (ohne ANSI-Codes)."""
    return len(ANSI_RE.sub("", s))


def clip(s, w):
    """Auf w sichtbare Zeichen kuerzen, ANSI erhalten."""
    if w <= 0:
        return ""
    out = []
    vis = 0
    i = 0
    n = len(s)
    while i < n and vis < w:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
        else:
            out.append(s[i])
            i += 1
            vis += 1
    while i < n:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
        else:
            break
    return "".join(out)


def padto(s, w):
    """Auf exakt w sichtbare Zeichen bringen (clip oder pad)."""
    v = vlen(s)
    if v > w:
        return clip(s, w)
    return s + " " * (w - v)


def hl(line, w):
    """Selektions-Highlight: Hintergrund ueber volle Breite w."""
    bgc = CLR["selbg"]
    v = vlen(line)
    if v < w:
        line = line + " " * (w - v)
    else:
        line = clip(line, w)
    return bgc + line.replace(RST, RST + bgc) + RST


def bar(frac, width, color):
    frac = max(0.0, min(1.0, frac))
    fill = int(round(frac * width))
    return color + "\u2588" * fill + CLR["dark"] + "\u2591" * (width - fill) + RST


def human_bytes(b):
    if b >= 1024 ** 3:
        return "{:.1f}G".format(b / 1024 ** 3)
    return "{:.0f}M".format(b / 1024 ** 2)


def human_uptime(sec):
    try:
        sec = int(sec)
    except Exception:
        return "-"
    if sec <= 0:
        return "-"
    d = sec // 86400
    h = (sec % 86400) // 3600
    if d > 0:
        return "{}d{}h".format(d, h)
    m = (sec % 3600) // 60
    return "{}h{}m".format(h, m)


def human_age(sec):
    """Kompakte Dauer fuer die Statusspalte: 12s, 45m, 5h12, 4d3h."""
    if sec is None:
        return "-"
    try:
        sec = int(max(0, sec))
    except Exception:
        return "-"
    if sec < 60:
        return "{}s".format(sec)
    if sec < 3600:
        return "{}m".format(sec // 60)
    if sec < 86400:
        return "{}h{:02d}".format(sec // 3600, (sec % 3600) // 60)
    return "{}d{}h".format(sec // 86400, (sec % 86400) // 3600)


def fmt_ts(ts):
    """Zeitstempel als 10.11. 18:07 (leer bei None)."""
    if not ts:
        return "-"
    try:
        return time.strftime("%d.%m. %H:%M", time.localtime(ts))
    except Exception:
        return "-"


def age_of(rec):
    """Sekunden seit letztem Statuswechsel eines Geraete-Records."""
    if not rec or not rec.get("since"):
        return None
    return time.time() - rec["since"]


def group_choices():
    """(gid, label)-Paare fuer Formulare."""
    return [(gid, CFG.get("groups", {}).get(gid, {}).get("label", gid))
            for gid in sorted(CFG.get("groups", {}))]


def save_config(cfg):
    """config.json atomar schreiben (Rechte/Format bleiben erhalten)."""
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG)
    try:
        os.chmod(CONFIG, 0o600)
    except Exception:
        pass


_LOAD_CACHE = {}


def load(path):
    """JSON laden, aber nur neu parsen, wenn sich mtime/Groesse geaendert haben."""
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        cached = _LOAD_CACHE.get(path)
        if cached and cached[0] == key:
            return cached[1]
        with open(path) as f:
            data = json.load(f)
        _LOAD_CACHE[path] = (key, data)
        return data
    except Exception:
        return None


def flat_guests(prox):
    """Liste aller Guests: [(node_idx, guest_dict), ...]."""
    items = []
    if not prox:
        return items
    for ni, node in enumerate(prox.get("nodes", [])):
        for g in node.get("guests", []):
            items.append((ni, g))
    return items


def flat_devices(cfg):
    """Liste aller Ping-Geraete: [(gid, group_label, dev_dict), ...]."""
    items = []
    groups = (cfg or {}).get("groups", {})
    for gid in sorted(groups):
        grp = groups[gid]
        label = grp.get("label", gid)
        for dev in grp.get("devices", []):
            items.append((gid, label, dev))
    return items


# ═══════════════════════════════════════════════════════════════════
# MODAL OVERLAY (LIGHTBOX)
# ═══════════════════════════════════════════════════════════════════

def splice_row(row, insert, sx, mw):
    """Ersetzt sichtbare Spalten [sx .. sx+mw] von row durch insert."""
    parts = []
    vis = 0
    i = 0
    n = len(row)
    inserted = False
    while i < n:
        m = ANSI_RE.match(row, i)
        if m:
            parts.append(m.group())
            i = m.end()
            continue
        if vis < sx:
            parts.append(row[i])
            vis += 1
            i += 1
        elif vis == sx and not inserted:
            parts.append(RST + insert + RST)
            inserted = True
            vis += 1
            i += 1
        elif sx <= vis < sx + mw:
            vis += 1
            i += 1
        else:
            parts.append(row[i])
            vis += 1
            i += 1
    return "".join(parts)


def build_proxmox_modal(modal, mw):
    """Baut Box-Zeilen fuer Proxmox Guest Modal."""
    g = modal["guest"]
    node_name = modal["node_name"]
    res = modal.get("result")
    lines = []

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        # Zentrierter Inhalt im Modal-Stil
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)

    # Titel
    head = "PROXMOX GUEST: [{} {}] {}".format(g["type"], g["vmid"], g["name"])
    lines.append(mhi + MBOX["v"] + " " + padto(head, mw - 4) + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    # Details
    running = g["status"] == "running"
    stat_str = (CLR["green"] + "RUNNING" if running else CLR["gray"] + "STOPPED") + RST
    lines.append(mline("Node:    {} ({})".format(node_name, modal.get("node_label", ""))))
    lines.append(mline("Status:  {} (Uptime: {})".format(stat_str, human_uptime(g.get("uptime", 0)))))

    if running:
        memf = g["mem"] / max(g["maxmem"], 1)
        memtxt = "{}/{} ({:.0f}%)".format(human_bytes(g["mem"]), human_bytes(g["maxmem"]), memf * 100)
        lines.append(mline("RAM:     {} {}".format(bar(memf, 10, CLR["green"]), memtxt)))
        lines.append(mline("CPU:     {:.1f}%".format(g.get("cpu", 0))))
    else:
        lines.append(mline("RAM:     {}".format(human_bytes(g["maxmem"]))))

    lines.append(msep())

    # Aktionen
    lines.append(mline(CLR["cyan"] + BOLD + "AKTIONEN (Taste druecken):" + RST))
    lines.append(mline("  " + CLR["green"] + "[1] Start" + RST))
    lines.append(mline("  " + CLR["yellow"] + "[2] Shutdown (ACPI sauber)" + RST))
    lines.append(mline("  " + CLR["yellow"] + "[3] Reboot" + RST))
    lines.append(mline("  " + CLR["red"] + "[4] Stop / Reset (hart)" + RST))
    lines.append(mline("  " + CLR["cyan"] + "[5] Backup erstellen (Snapshot)" + RST))

    lines.append(msep())
    if res:
        lines.append(mline(CLR["orange"] + BOLD + res + RST))
    else:
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] zum Schliessen" + RST))

    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def open_node_modal(prox, node_index):
    """Baut das Node-Modal (gesamten Proxmox-Server steuern)."""
    nodes = (prox or {}).get("nodes") or []
    if node_index < 0 or node_index >= len(nodes):
        return None
    return {
        "type": "pve_node",
        "node_idx": node_index,
        "node": nodes[node_index],
        "result": None,
        "pending": None,
    }


def build_pve_node_modal(modal, mw):
    """Node-Modal: Status + Herunterfahren/Neustart des ganzen Servers."""
    node = modal.get("node") or {}
    nd = node.get("node") or {}
    label = node.get("label") or "PVE"
    res = modal.get("result")
    pending = modal.get("pending")
    online = bool(node.get("online"))
    lines = []

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)
    head = "PROXMOX SERVER: {}".format(label)
    lines.append(mhi + MBOX["v"] + " " + padto(clip(head, mw - 4), mw - 4)
                 + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    if online:
        stat = CLR["green"] + BOLD + "ONLINE" + RST
    else:
        stat = CLR["red"] + BOLD + "OFFLINE" + RST
    lines.append(mline("Node:    {} ({})".format(nd.get("name") or "-", stat)))
    lines.append(mline("Uptime:  {}".format(human_uptime(nd.get("uptime", 0)))))

    cpu = nd.get("cpu", 0)
    mem, maxmem = nd.get("mem", 0), max(nd.get("maxmem", 1), 1)
    dsk, maxdsk = nd.get("disk", 0), max(nd.get("maxdisk", 1), 1)
    memf = mem / maxmem
    dskf = dsk / maxdsk
    lines.append(mline("CPU:     {} {:.0f}%".format(
        bar(cpu / 100.0, 10, CLR["cyan"]), cpu)))
    lines.append(mline("RAM:     {} {}/{} ({:.0f}%)".format(
        bar(memf, 10, CLR["green"] if memf < 0.85 else CLR["orange"]),
        human_bytes(mem), human_bytes(maxmem), memf * 100)))
    lines.append(mline("Disk:    {} {}/{} ({:.0f}%)".format(
        bar(dskf, 10, CLR["green"] if dskf < 0.85 else CLR["orange"]),
        human_bytes(dsk), human_bytes(maxdsk), dskf * 100)))

    guests = node.get("guests") or []
    running = sum(1 for g in guests if g.get("status") == "running")
    lines.append(mline("Gaeste:  {} laufend, {} gesamt".format(running, len(guests))))

    lines.append(msep())

    if pending:
        action = "HERUNTERFAHREN" if pending == "shutdown" else "NEU STARTEN"
        lines.append(mline(CLR["red"] + BOLD + "ACHTUNG: {} {}".format(
            label, action) + RST))
        lines.append(mline(CLR["gray"] + "Alle VMs/Container werden sauber beendet." + RST))
        lines.append(mline(CLR["yellow"] + BOLD
                           + "  [j] Ja, jetzt ausfuehren    [n] Abbrechen" + RST))
    elif not online:
        lines.append(mline(CLR["red"] + "Server nicht erreichbar - keine Aktionen" + RST))
    else:
        lines.append(mline(CLR["cyan"] + BOLD + "SERVER-AKTIONEN:" + RST))
        lines.append(mline("  " + CLR["orange"] + "[s] Gesamten Server herunterfahren" + RST))
        lines.append(mline("  " + CLR["yellow"] + "[r] Gesamten Server neu starten" + RST))
        lines.append(mline(CLR["dark"] + "  (alle VMs/Container werden mit gestoppt)" + RST))

    lines.append(msep())
    if res:
        lines.append(mline(CLR["orange"] + BOLD + clip(res, mw - 4) + RST))
    else:
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] schliessen" + RST))

    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def build_fritz_modal(modal, mw):
    """FRITZ!Box-Modal: Leitungsrate, aktuelle Auslastung und Traffic."""
    fb = modal.get("data") or {}
    lines = []

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    def rate(value, digits=1):
        return format((value or 0) / 1e6, ".{}f".format(digits))

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)
    head = (fb.get("model") or "FRITZ!Box").upper()
    lines.append(mhi + MBOX["v"] + " " + padto(clip(head, mw - 4), mw - 4)
                 + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    if not fb.get("ok"):
        err = fb.get("error") or "keine Daten"
        lines.append(mline(CLR["red"] + BOLD + "NICHT ERREICHBAR" + RST))
        lines.append(mline(CLR["gray"] + clip(err, mw - 4) + RST))
        if fb.get("ts"):
            lines.append(mline(CLR["dark"] + "Letzte Daten: "
                               + fmt_ts(fb.get("ts")) + RST))
        lines.append(msep())
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] schliessen" + RST))
        lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
        return lines

    fw = fb.get("firmware") or "-"
    hw = fb.get("hardware") or ""
    fw_txt = "Firmware: {}".format(fw)
    if hw and hw != fb.get("model"):
        fw_txt += " ({})".format(hw)
    lines.append(mline("Modell:   {}".format(fb.get("model") or "-")))
    lines.append(mline(clip(fw_txt, mw - 4)))
    status = fb.get("link_status") or "-"
    access = fb.get("access_type") or ""
    scol = CLR["green"] if status.lower() == "up" else CLR["red"]
    lines.append(mline("Status:   {}{}{}{}{}".format(
        scol, BOLD, status, RST, " / " + access if access else "")))
    lines.append(mline("Uptime:   {}".format(human_uptime(fb.get("uptime", 0)))))

    lines.append(msep())
    lines.append(mline(CLR["cyan"] + BOLD + "MAXIMAL (Anbieter)" + RST))
    lines.append(mline("  {}\u2193 {} Mbps{}   {}\u2191 {} Mbps{}".format(
        CLR["cyan"], rate(fb.get("down_bps")), RST,
        CLR["cyan"], rate(fb.get("up_bps")), RST)))
    layer1_d = fb.get("layer1_down_bps") or 0
    layer1_u = fb.get("layer1_up_bps") or 0
    if layer1_d or layer1_u:
        lines.append(mline(CLR["dark"] + "  DOCSIS/Layer 1: \u2193 {} / \u2191 {} Mbps".format(
            rate(layer1_d), rate(layer1_u)) + RST))

    down_f = (fb.get("down_cur_bps") or 0) / max(fb.get("down_bps") or 1, 1)
    up_f = (fb.get("up_cur_bps") or 0) / max(fb.get("up_bps") or 1, 1)
    lines.append(mline("{}{}AKTUELL  {:.1f}% / {:.1f}%{}".format(
        CLR["cyan"], BOLD, down_f * 100, up_f * 100, RST)))
    lines.append(mline("  \u2193 {} {:>6} Mbps".format(
        bar(down_f, 8, CLR["green"] if down_f < 0.8 else CLR["orange"]),
        rate(fb.get("down_cur_bps"), 2))))
    lines.append(mline("  \u2191 {} {:>6} Mbps".format(
        bar(up_f, 8, CLR["green"] if up_f < 0.8 else CLR["orange"]),
        rate(fb.get("up_cur_bps"), 2))))

    lines.append(mline("Peak:     \u2193 {}   \u2191 {} Mbps".format(
        rate(fb.get("down_peak_bps")), rate(fb.get("up_peak_bps")))))
    rx = fb.get("bytes_received") or 0
    tx = fb.get("bytes_sent") or 0
    if rx or tx:
        lines.append(mline("Traffic:  \u2193 {}   \u2191 {}".format(
            human_bytes(rx), human_bytes(tx))))
    grp = fb.get("sync_group") or ""
    mode = fb.get("sync_mode") or ""
    if grp:
        lines.append(mline(CLR["dark"] + "Sync: {} ({})".format(grp, mode) + RST))

    lines.append(msep())
    lines.append(mline(CLR["gray"] + "Stand {}   [ESC]/[q] schliessen".format(
        fmt_ts(fb.get("ts"))) + RST))
    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def build_device_modal(modal, mw):
    """Baut Box-Zeilen fuer Ping-Geraet Modal."""
    dev = modal["dev"]
    gid = modal["gid"]
    label = modal["label"]
    st = modal.get("state") or {}
    res = modal.get("result")
    lines = []

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep():
        return mbg + MBOX["v"] + "\u2500" * (mw - 2) + MBOX["v"] + RST

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)

    head = "GERAET: {}".format(dev["name"])
    lines.append(mhi + MBOX["v"] + " " + padto(head, mw - 4) + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    lines.append(mline("Gruppe:  {}".format(label)))
    lines.append(mline("IP:      {}".format(dev.get("ip", "-"))))
    lines.append(mline("Typ:     {}".format(dev.get("type", "-"))))

    paused = dev.get("paused")
    ok = st.get("ping_ok", False)
    if paused:
        stat_txt = CLR["gray"] + "PAUSIERT (wird nicht geprueft)" + RST
    elif ok:
        stat_txt = CLR["green"] + "ONLINE ({:.1f} ms)".format(st.get("rtt_ms", 0)) + RST
    else:
        stat_txt = CLR["red"] + "DOWN / nicht erreichbar" + RST
    lines.append(mline("Status:  {}".format(stat_txt)))

    # Zeitangaben (auch bei Pause aus der letzten Messung)
    since = st.get("since")
    if since:
        dur = human_age(time.time() - since)
        word = "Online seit" if ok else "Offline seit"
        if paused:
            word = "Letzter Status"
        lines.append(mline("{}: {} ({})".format(word, fmt_ts(since), dur)))
    last_down = st.get("last_down")
    if last_down:
        d_age = human_age(time.time() - last_down)
        lines.append(mline("Zuletzt down: {} (vor {})".format(fmt_ts(last_down), d_age)))
    else:
        lines.append(mline("Zuletzt down: -"))
    last_up = st.get("last_up")
    if last_up:
        u_age = human_age(time.time() - last_up)
        lines.append(mline("Zuletzt up:   {} (vor {})".format(fmt_ts(last_up), u_age)))
    else:
        lines.append(mline("Zuletzt up:   -"))

    http_p = dev.get("http") or dev.get("port")
    if http_p:
        http_ok = st.get("http_ok", False)
        code = st.get("http_code", 0)
        hstatus = (CLR["green"] + "HTTP {}".format(code) if http_ok else CLR["red"] + "Timeout/Fehler") + RST
        lines.append(mline("HTTP:    Port {} ({})".format(http_p, hstatus)))

    mode = dev.get("notify", "none")
    if mode == "both":
        mode_txt = CLR["yellow"] + "UP + DOWN" + RST
    elif mode == "down":
        mode_txt = CLR["red"] + "nur DOWN" + RST
    elif mode == "up":
        mode_txt = CLR["green"] + "nur UP" + RST
    else:
        mode_txt = CLR["gray"] + "aus" + RST
    lines.append(mline("Meldung: {}  {}".format(
        mode_txt, CLR["dark"] + "(ntfy bei Statuswechsel)" + RST)))

    lines.append(msep())
    lines.append(mline(CLR["cyan"] + BOLD + "AKTIONEN:" + RST))
    lines.append(mline("  " + CLR["cyan"] + "[e] Bearbeiten (Name, IP, Port, Gruppe)" + RST))
    lines.append(mline("  " + CLR["cyan"] + "[r] Ping jetzt wiederholen" + RST))
    lines.append(mline("  " + CLR["yellow"] + "[n] Meldung: aus / down / up / beides" + RST))
    if paused:
        lines.append(mline("  " + CLR["green"] + "[Space] Pausierung aufheben" + RST))
    else:
        lines.append(mline("  " + CLR["yellow"] + "[Space] Pausieren (kein Ping)" + RST))
    lines.append(mline("  " + CLR["red"] + "[d] Geraet loeschen" + RST))

    lines.append(msep())
    if res:
        lines.append(mline(CLR["orange"] + BOLD + res + RST))
    else:
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] zum Schliessen" + RST))

    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def build_form_modal(modal, mw):
    """Formular (Geraet anlegen / Intervalle)."""
    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]
    lines = []

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep():
        return mbg + MBOX["v"] + "\u2500" * (mw - 2) + MBOX["v"] + RST

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)
    lines.append(mhi + MBOX["v"] + " " + padto(modal.get("title", "EINGABE"), mw - 4)
                 + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    for idx, f in enumerate(modal["fields"]):
        val = f["value"]
        if f.get("type") == "select":
            val = "< {} >".format(val if val else "-")
        cursor = "\u258f" if idx == modal.get("focus") else ""
        content = " {:<10}{}{}".format(f["label"] + ":", val, cursor)
        if idx == modal.get("focus"):
            content = CLR["modalhi"] + padto(content, mw - 4) + RST
        lines.append(mline(content))

    if modal.get("hint"):
        lines.append(msep())
        lines.append(mline(CLR["gray"] + modal["hint"] + RST))
    if modal.get("error"):
        lines.append(mline(CLR["red"] + BOLD + modal["error"] + RST))
    else:
        lines.append(msep())
        lines.append(mline(CLR["gray"] + "Tab/\u2191\u2193=Feld  Enter=OK  ESC=abbrechen" + RST))

    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def build_confirm_modal(modal, mw):
    """Ja/Nein-Rueckfrage."""
    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]
    lines = []

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)
    lines.append(mhi + MBOX["v"] + " " + padto(modal.get("title", "BESTAETIGEN"), mw - 4)
                 + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)
    for text in modal.get("text", []):
        lines.append(mline(text))
    lines.append(mbg + MBOX["v"] + "\u2500" * (mw - 2) + MBOX["v"] + RST)
    lines.append(mline(CLR["red"] + BOLD + "[j] Ja" + RST + "    "
                       + CLR["gray"] + "[n]/[ESC] Abbrechen" + RST))
    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


def build_unifi_modal(modal, mw, max_rows=None):
    """AP-Modal: alle WLAN-Geraete dieses APs in mehreren Spalten."""
    ap = modal.get("ap") or {}
    clients = list(modal.get("clients") or [])
    clients.sort(key=lambda c: (c.get("name") or c.get("hostname") or "").lower())

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    lines = []
    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)

    title = "UNIFI AP: {}  ({})".format(
        ap.get("name") or "AP",
        ap.get("model_name") or ap.get("model") or "?")
    lines.append(mhi + MBOX["v"] + " " + padto(title, mw - 4) + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    if ap.get("up"):
        stat = CLR["green"] + BOLD + "ONLINE" + RST
    else:
        stat = CLR["red"] + BOLD + "OFFLINE" + RST
    info = "Status: {}  \u00b7  {} Geraete  \u00b7  up {}  \u00b7  {}".format(
        stat, len(clients), human_uptime(ap.get("uptime", 0)),
        ap.get("ip") or "-")
    lines.append(mline(info))
    ver = ap.get("version") or "-"
    lines.append(mline(CLR["dark"] + "Modell: {}  \u00b7  Firmware: {}".format(
        ap.get("model_name") or ap.get("model") or "?", ver) + RST))

    sat = ap.get("satisfaction")
    qc = _quality_color(sat)
    link = int(ap.get("uplink_speed") or 0)
    maxlink = int(ap.get("uplink_max_speed") or 0)
    cpu = ap.get("cpu")
    mem = ap.get("mem")
    link_c = (CLR["orange"] if (maxlink and link and link < maxlink)
              else CLR["gray"])
    link_txt = "{} Mbit/s".format(link) if link else "-"
    if maxlink and link and link < maxlink:
        link_txt += " (max {})".format(maxlink)
    qtxt = "{}%".format(sat) if sat is not None else "?"
    extra = []
    if cpu is not None:
        extra.append("CPU {:.0f}%".format(cpu))
    if mem is not None:
        extra.append("RAM {:.0f}%".format(mem))
    qline = ("Qualitaet: {}{}{} ({})  \u00b7  Link: {}{}{}".format(
        qc, BOLD, qtxt, _quality_text(sat),
        link_c, link_txt, RST))
    if extra:
        qline += "  \u00b7  " + CLR["dark"] + " \u00b7 ".join(extra) + RST
    lines.append(mline(qline))

    band_names = {"ng": "2.4 GHz", "na": "5 GHz", "ac": "5 GHz",
                  "ax": "6 GHz", "ad": "60 GHz"}
    for radio in ap.get("radios") or []:
        rq = radio.get("satisfaction")
        rc = _quality_color(rq)
        band = band_names.get(radio.get("radio"), radio.get("radio") or "?")
        ch = radio.get("channel")
        cu = radio.get("cu_total")
        line = "  {}{:<7}{} Kanal {:<3}  {}Q{}{}  {:>2} Ger".format(
            CLR["blue"], band, RST,
            ch if ch is not None else "-",
            rc, ("{}%".format(rq) if rq is not None else "?"), RST,
            int(radio.get("num_sta") or 0))
        if cu is not None:
            line += CLR["dark"] + "  Last {}".format(cu) + "%" + RST
        lines.append(mline(line))

    essids = {}
    for cl in clients:
        key = cl.get("essid") or "?"
        essids[key] = essids.get(key, 0) + 1
    if essids:
        parts = ["{} {}".format(k, v) for k, v in
                 sorted(essids.items(), key=lambda kv: (-kv[1], kv[0]))]
        lines.append(mline(CLR["dark"] + "WLAN: " + " \u00b7 ".join(parts) + RST))

    lines.append(msep())
    lines.append(mline(CLR["cyan"] + BOLD + "GERAETE AN DIESEM AP" + RST))

    # Spaltenbreite: bei breitem Terminal 4 Spalten, sonst weniger
    inner = mw - 4
    nc = 1
    if inner >= 100:
        nc = 4
    elif inner >= 72:
        nc = 3
    elif inner >= 44:
        nc = 2
    gap = 1
    cw = max(12, (inner - gap * (nc - 1)) // nc)
    name_w = max(5, cw - 9)

    def cell(cl):
        return _unifi_client_cell(cl, name_w)

    total = len(clients)
    rows_needed = (total + nc - 1) // nc if total else 0
    hidden = 0
    if max_rows is not None:
        budget = max(0, max_rows - len(lines) - 3)
        if rows_needed > budget:
            if budget >= 2:
                budget -= 1
                rows_needed = budget
            else:
                rows_needed = 0
            hidden = total - rows_needed * nc
    if total == 0:
        lines.append(mline(CLR["dark"] + "keine WLAN-Geraete an diesem AP" + RST))
    else:
        for r in range(rows_needed):
            parts = []
            for ci in range(nc):
                idx = ci * rows_needed + r
                parts.append(cell(clients[idx]) if idx < total else "")
            lines.append(mline((" " * gap).join(padto(p, cw) for p in parts)))
        if hidden > 0:
            lines.append(mline(CLR["dark"] + "+{} weitere Geraete".format(hidden) + RST))

    lines.append(msep())
    lines.append(mline(CLR["gray"] + "[ESC] oder [q] schliessen" + RST))
    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


# ═══════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════

LAST_LAYOUT = {}


def render(w, h, state, prox, speed, uni, dc, frame, active_panel,
           sel_dev, sel_guest, sel_ap, sel_dc, msg, modal,
           sel_node=0, sel_kind="guest", fb=None, hm=None):
    """Baut den kompletten Screen (inkl. evtl. Modal-Overlay)."""
    rows = []
    devs = (state or {}).get("devices", {})
    online = (state or {}).get("online", 0)
    total = (state or {}).get("total", 0)
    lc = (state or {}).get("last_check", 0)
    iok = (state or {}).get("internet", False)
    poll = (state or {}).get("poll_interval", 15) or 15
    paused_n = (state or {}).get("paused", 0)
    ago = int(time.time() - lc) if lc else 999

    spin = SPIN[frame % len(SPIN)]

    # ─── Row 0: Titelleiste ───
    up = human_age(host_uptime())
    host = HOSTINFO.split(" / ")[0]
    clock = time.strftime("%H:%M:%S ")
    cpu = host_cpu_percent()
    ram = host_ram_percent()
    cpu_s = str(cpu) if cpu is not None else "-"
    ram_s = str(ram) if ram is not None else "-"
    load_full = "{}CPU {}  {}RAM {}{}".format(
        _pct_fg(cpu), cpu_s, _pct_fg(ram), ram_s, CLR["titlebg"])
    load_short = "{}C{}  {}R{}{}".format(
        _pct_fg(cpu), cpu_s, _pct_fg(ram), ram_s, CLR["titlebg"])
    title_variants = [
        " SERVERRAUM DASHBOARD  {}  {}  {}  up {} ".format(
            spin, HOSTINFO, load_full, up),
        " SERVERRAUM DASHBOARD  {}  {}  {}  up {} ".format(
            spin, host, load_full, up),
        " SERVERRAUM DASHBOARD  {}  {}  up {} ".format(
            spin, load_short, up),
        " SERVERRAUM DASHBOARD  {}  up {} ".format(load_short, up),
        " SERVERRAUM DASHBOARD  up {} ".format(up),
    ]
    left = title_variants[-1]
    for cand in title_variants:
        if vlen(cand) + vlen(clock) <= w:
            left = cand
            break
    right = clock
    mid = max(0, w - vlen(left) - vlen(right))
    rows.append(CLR["titlebg"] + BOLD + left + " " * mid + right + RST)

    # ─── Row 1: Statuszeile ───
    isec = (state or {}).get("internet_since")
    iage = human_age(time.time() - isec) if isec else "-"
    if iok:
        inet = CLR["green"] + "\u25cf INTERNET ONLINE" + RST + CLR["dark"] + " " + iage + RST
    else:
        inet = CLR["red"] + BOLD + "\u25cf INTERNET OFFLINE" + RST + CLR["red"] + " " + iage + RST

    # Verbindungsart + IP (und SSID im WLAN) direkt hinter dem Internet-Status.
    # Bei schmalem Terminal wird schrittweise gekuerzt, damit nichts wegfaellt.
    ntype = (state or {}).get("net_type") or ""
    nssid = (state or {}).get("net_ssid") or ""
    nip = (state or {}).get("net_ip") or ""
    conn_variants = []
    if ntype:
        base = " \u00b7 " + ntype
        if ntype == "WLAN" and nssid:
            base += " " + clip(nssid, 20)
        if nip:
            conn_variants.append(CLR["blue"] + base + " " + nip + RST)
        conn_variants.append(CLR["blue"] + base + RST)
        conn_variants.append(CLR["blue"] + " \u00b7 " + ntype + RST)
    conn_variants.append("")

    oc = CLR["green"] if online == total else (CLR["yellow"] if online else CLR["red"])
    dev_txt = oc + "{}/{} Geraete".format(online, total) + RST
    if paused_n:
        dev_txt += CLR["gray"] + " \u23f8{}".format(paused_n) + RST
    sp_variants = []
    if fb and fb.get("ok") and fb.get("down_bps"):
        dm = (fb.get("down_bps") or 0) / 1e6
        um = (fb.get("up_bps") or 0) / 1e6
        cdm = (fb.get("down_cur_bps") or 0) / 1e6
        cum = (fb.get("up_cur_bps") or 0) / 1e6
        sp_variants.append(
            "{}WAN max \u2193{:.1f}{} / {}\u2191{:.1f}{} Mbps {}jetzt {:.2f}/{:.2f}{}".format(
                CLR["cyan"], dm, RST, CLR["cyan"], um, RST,
                CLR["dark"], cdm, cum, RST))
        sp_variants.append(
            "{}WAN max \u2193{:.1f}{} / {}\u2191{:.1f}{} Mbps".format(
                CLR["cyan"], dm, RST, CLR["cyan"], um, RST))
        sp_variants.append(
            "{}WAN \u2193{:.1f}/\u2191{:.1f} Mbps{}".format(
                CLR["cyan"], dm, um, RST))
        sp_variants.append(
            "{}WAN {:.0f}/{:.0f} Mbps{}".format(CLR["cyan"], dm, um, RST))
    elif speed and speed.get("ok"):
        sp_variants.append(
            "{}\u2193{:.1f}{} / {}\u2191{:.1f}{} Mbps".format(
                CLR["cyan"], speed["download_mbps"], RST,
                CLR["cyan"], speed["upload_mbps"], RST))
    else:
        sp_variants.append(CLR["dark"] + "Speedtest..." + RST)
    cd = 1.0 - (ago / poll if poll else 0)

    stat = None
    for sp in sp_variants:
        for conn in conn_variants:
            head_txt = " {}{}   {}   {}   next ".format(inet, conn, dev_txt, sp)
            avail = w - vlen(head_txt)
            if avail >= 3:
                stat = head_txt + bar(cd, min(10, avail), CLR["blue"])
                break
        if stat is not None:
            break
    if stat is None:
        stat = " {}   {}   {} ".format(inet, dev_txt, sp_variants[-1])
        avail = w - vlen(stat) - 5
        if avail >= 3:
            stat += "next " + bar(cd, min(10, avail), CLR["blue"])
        else:
            avail = w - vlen(stat)
            if avail > 0:
                stat += bar(cd, avail, CLR["blue"])
    rows.append(padto(stat, w))

    # ─── Row 2: obere Trennlinie mit Panel-Beschriftung ───
    LW = int(w * 0.58)
    RW = w - LW - 3

    # Aktives Panel optisch hervorheben
    if active_panel == 0:
        l_hdr = " GERAETE [AKTIV] "
        r_hdr = " PROXMOX CLUSTER "
        l_border = CLR["cyan"] + BOLD
        r_border = CLR["dark"]
    elif active_panel == 1:
        l_hdr = " GERAETE "
        r_hdr = " PROXMOX CLUSTER [AKTIV] "
        l_border = CLR["dark"]
        r_border = CLR["cyan"] + BOLD
    elif active_panel in (2, 3):
        l_hdr = " GERAETE "
        r_hdr = " PROXMOX + UNIFI [AKTIV] "
        l_border = CLR["dark"]
        r_border = CLR["cyan"] + BOLD
    elif active_panel == 4:
        l_hdr = " GERAETE "
        r_hdr = " HOMEMATIC [AKTIV] "
        l_border = CLR["dark"]
        r_border = CLR["cyan"] + BOLD

    # Header-Linie (auf schmalen Terminals kuerzen)
    if vlen(r_hdr) > RW - 2:
        r_hdr = r_hdr.replace(" + UNIFI", "").replace(" + HM", "").replace(" CLUSTER", "")
    if vlen(r_hdr) > RW - 2:
        r_hdr = " ".join(r_hdr.split()[:2]) + " "
    if vlen(l_hdr) > LW - 2:
        l_hdr = " GERAETE "
    l_bar = BOX["h"] * 2 + l_hdr + BOX["h"] * max(0, LW - 2 - len(l_hdr))
    r_bar = BOX["h"] * 2 + r_hdr + BOX["h"] * max(0, RW - 2 - len(r_hdr))
    top_line = (CLR["cyan"] + BOX["tl"] + RST
                + l_border + l_bar + RST
                + CLR["cyan"] + BOX["tt"] + RST
                + r_border + r_bar + RST
                + CLR["cyan"] + BOX["tr"] + RST)
    rows.append(top_line)

    # ─── Body ───
    body_h = max(1, h - 5)
    dev_hits = []
    left_lines = build_device_lines(devs, state, LW,
                                    sel_dev if active_panel == 0 else -1,
                                    dev_hits)
    prox_hits = []
    prox_lines = build_proxmox_lines(
        prox, RW,
        sel_guest if (active_panel == 1 and sel_kind == "guest") else -1,
        prox_hits,
        sel_node if (active_panel == 1 and sel_kind == "node") else -1,
        sel_kind if active_panel == 1 else "guest")
    right_lines = list(prox_lines)
    rest = max(0, body_h - len(right_lines))
    uni_hits = []
    uni_len = 0
    if rest > 0:
        reserve = 0
        if dc:
            reserve += 3          # Leerzeile + deCONZ-Block (2 Zeilen)
        if hm:
            reserve += 3          # Leerzeile + HomeMatic-Block (2 Zeilen)
        if reserve >= rest:
            reserve = max(0, rest - 1)
        uni_max = max(1, rest - reserve) if reserve else rest
        uni_lines = build_unifi_lines(
            uni, RW, uni_max,
            sel_ap if active_panel == 2 else -1, uni_hits)
        right_lines += uni_lines
        uni_len = len(uni_lines)
        rest = max(0, rest - uni_len)
    dc_hits = []
    dc_off = 0
    dc_len = 0
    if dc and rest > 0:
        if rest >= 2:
            right_lines.append("")       # Abstand zum UniFi-Block
            dc_off = 1
            rest -= 1
        dc_lines = build_deconz_lines(dc, RW, rest, active_panel == 3, dc_hits)
        right_lines += dc_lines
        dc_len = len(dc_lines)
        rest = max(0, rest - dc_len)
    hm_hits = []
    hm_off = 0
    hm_len = 0
    if hm and rest > 0:
        if rest >= 2:
            right_lines.append("")       # Abstand zum deCONZ-Block
            hm_off = 1
            rest -= 1
        hm_lines = build_homematic_lines(
            hm, RW, rest, active_panel == 4, hm_hits)
        right_lines += hm_lines
        hm_len = len(hm_lines)
        rest = max(0, rest - hm_len)

    # Selektierbare Zeilen als 2D-Layout merken (Pfeilnavigation + Maus)
    nc_dev = 2 if LW >= 70 else 1
    cw_dev = (LW - (nc_dev - 1)) // nc_dev
    items = []
    for r, di, ci in dev_hits:
        if r >= body_h:
            continue
        items.append({"panel": 0, "idx": di, "row": 3 + r,
                      "col": 1 + ci * (cw_dev + 1) + cw_dev // 2})
    for r, kind, idx in prox_hits:
        if r >= body_h:
            continue
        items.append({"panel": 1, "idx": idx, "kind": kind, "row": 3 + r,
                      "col": LW + 2 + RW // 2})
    for off, ai in uni_hits:
        items.append({"panel": 2, "idx": ai, "row": 3 + len(prox_lines) + off,
                      "col": LW + 2 + RW // 2})
    if dc_hits:
        items.append({"panel": 3, "idx": 0,
                      "row": 3 + len(prox_lines) + uni_len + dc_off + dc_hits[0],
                      "col": LW + 2 + RW // 2})
    if hm_hits:
        items.append({"panel": 4, "idx": 0,
                      "row": 3 + len(prox_lines) + uni_len + dc_off + dc_len
                             + hm_off + hm_hits[0],
                      "col": LW + 2 + RW // 2})

    global LAST_LAYOUT
    LAST_LAYOUT = {
        "ap_hits": [(3 + len(prox_lines) + off, ai) for off, ai in uni_hits],
        "pve_hits": [(3 + r, kind, idx) for r, kind, idx in prox_hits
                     if r < body_h],
        "dc_hit": ((3 + len(prox_lines) + uni_len + dc_off + dc_hits[0])
                   if dc_hits else None),
        "hm_hit": ((3 + len(prox_lines) + uni_len + dc_off + dc_len
                    + hm_off + hm_hits[0]) if hm_hits else None),
        "items": items,
        "dev_hits": [(3 + r, 1 + ci * (cw_dev + 1),
                      1 + ci * (cw_dev + 1) + cw_dev - 1, di)
                     for r, di, ci in dev_hits if r < body_h],
        "fritz_hit": 1 if (fb and fb.get("ok")) else None,
        "x0": LW + 2,
        "x1": LW + 2 + RW - 1,
    }

    for i in range(body_h):
        lc_cell = left_lines[i] if i < len(left_lines) else ""
        rc_cell = right_lines[i] if i < len(right_lines) else ""
        row = (CLR["cyan"] + BOX["v"] + RST + padto(lc_cell, LW)
               + CLR["cyan"] + BOX["v"] + RST + padto(rc_cell, RW)
               + CLR["cyan"] + BOX["v"] + RST)
        rows.append(row)

    # ─── untere Trennlinie ───
    rows.append(CLR["cyan"] + BOX["bl"] + BOX["h"] * LW + BOX["bt"] + BOX["h"] * RW + BOX["br"] + RST)

    # ─── Footer ───
    if msg:
        foot = " " + msg
    elif modal and modal["type"] == "form":
        foot = " Tab/\u2191\u2193=Feld wechseln  Enter=naechstes Feld/speichern  ESC=abbrechen"
    elif modal and modal["type"] == "confirm":
        foot = " [j] Ja  [n]/[ESC] Nein"
    elif active_panel == 0:
        foot = (" Tab=Proxmox  \u2191\u2193=Waehlen  Enter=Details  a=Neu  Space=Pause  "
                "d=Loeschen  f=FritzBox  c=Config  r=Ping  q=Ende")
    elif active_panel == 2:
        foot = (" Tab=Geraete  \u2191\u2193=AP waehlen  Enter=Geraete am AP  "
                "f=FritzBox  c=Config  q=Ende")
    elif active_panel == 3:
        foot = (" Tab=Geraete  \u2191\u2193\u2190\u2192=frei wechseln  Enter=deCONZ-Details  "
                "m=HomeMatic  f=FritzBox  c=Config  q=Ende")
    elif active_panel == 4:
        foot = (" Tab=Geraete  \u2191\u2193\u2190\u2192=frei wechseln  Enter=HomeMatic-Details  "
                "f=FritzBox  c=Config  q=Ende")
    else:
        foot = " Tab=Geraete/UniFi  \u2191\u2193=Waehlen  Enter=Node/Gast-Modal  f=FritzBox  s=Start  x=Stop  p=Refresh  c=Config  q=Ende"
    rows.append(CLR["footbg"] + padto(foot, w) + RST)

    while len(rows) < h:
        rows.append(padto("", w))
    rows = rows[:h]

    # ─── Lightbox Modal Overlay (wenn aktiv) ───
    if modal:
        if modal["type"] == "unifi":
            mw = min(120, w - 4)
            mlines = build_unifi_modal(modal, mw, max(8, h - 4))
        else:
            if modal["type"] == "deconz":
                mw = min(70, w - 4)
                mlines = build_deconz_modal(modal, mw, max(8, h - 4))
            elif modal["type"] == "homematic":
                mw = min(72, w - 4)
                mlines = build_homematic_modal(modal, mw, max(8, h - 4))
            else:
                mw = min(60, w - 4)
                if modal["type"] == "proxmox":
                    mlines = build_proxmox_modal(modal, mw)
                elif modal["type"] == "pve_node":
                    mw = min(64, w - 4)
                    mlines = build_pve_node_modal(modal, mw)
                elif modal["type"] == "fritz":
                    mw = min(66, w - 4)
                    mlines = build_fritz_modal(modal, mw)
                elif modal["type"] == "device":
                    mlines = build_device_modal(modal, mw)
                elif modal["type"] == "form":
                    mlines = build_form_modal(modal, mw)
                else:
                    mlines = build_confirm_modal(modal, mw)
        mh = len(mlines)
        sx = (w - mw) // 2
        sy = max(2, (h - mh) // 2)

        for ri, mline in enumerate(mlines):
            y = sy + ri
            if 0 <= y < len(rows):
                rows[y] = splice_row(rows[y], mline, sx, mw)

    return rows


def open_device_modal(dev_items, state, idx):
    """Geraete-Modal fuer einen Eintrag der linken Liste (Tastatur/Maus)."""
    if not (0 <= idx < len(dev_items)):
        return None
    gid, lbl, d = dev_items[idx]
    st = ((state or {}).get("devices") or {}).get("{}|{}".format(gid, d["name"]))
    return {
        "type": "device",
        "dev": d,
        "gid": gid,
        "label": lbl,
        "state": st,
        "result": None,
    }


def build_device_lines(devs, state, w, sel_dev, hit_rows=None):
    """Geraetegruppen in Sub-Spalten fuer die linke Zone mit Highlight."""
    cfg = CFG
    groups = cfg.get("groups", {})
    nc = 2 if w >= 70 else 1
    gap = 1
    cw = (w - gap * (nc - 1)) // nc

    cols = [[] for _ in range(nc)]
    ht = [0] * nc
    global_di = 0
    now = time.time()

    for gid in sorted(groups):
        grp = groups[gid]
        dv = grp.get("devices", [])
        if not dv:
            continue
        active = [d for d in dv if not d.get("paused")]
        paused_n = len(dv) - len(active)
        gn = sum(1 for d in active
                 if devs.get("{}|{}".format(gid, d["name"]), {}).get("ping_ok"))
        gt = len(active)
        gc = CLR["green"] if gn == gt else (CLR["yellow"] if gn else CLR["red"])
        block = []
        dev_cells = []
        label = grp.get("label", gid)
        head = gc + BOLD + "\u25b8 " + label + RST + gc + " [{}/{}]".format(gn, gt) + RST
        if paused_n:
            head += CLR["gray"] + " \u23f8{}".format(paused_n) + RST
        block.append(head)
        for d in dv:
            rec = devs.get("{}|{}".format(gid, d["name"]), {}) or {}
            nm = d["name"]
            if d.get("paused"):
                ic = CLR["gray"] + "\u2016" + RST
                nm = CLR["dark"] + nm + RST
                rts_c, rts = CLR["gray"], "PAUSE"
                age_c, age_s = CLR["dark"], "-"
            else:
                ok = rec.get("ping_ok", False)
                rt = rec.get("rtt_ms", 0) or 0
                if ok and rt < 20:
                    ic = CLR["green"] + "\u25cf" + RST
                elif ok and rt < 100:
                    ic = CLR["yellow"] + "\u25c9" + RST
                elif ok:
                    ic = CLR["orange"] + "\u25ce" + RST
                else:
                    ic = CLR["red"] + "\u25cb" + RST
                if ok:
                    rts_c, rts = CLR["green"], "{:.0f}ms".format(rt)
                else:
                    rts_c, rts = CLR["red"], "DOWN"
                age_c = CLR["green"] if ok else CLR["red"]
                age_s = human_age(now - rec["since"]) if rec.get("since") else "-"
            nm_w = cw - 17
            armed = (not d.get("paused")) and d.get("notify") in ("down", "up", "both")
            mark = CLR["yellow"] + "\u2022" + RST if armed else " "
            cell = (" " + ic + " " + mark + " " + padto(nm, nm_w) + " "
                    + rts_c + clip(rts, 5).rjust(5) + RST + " "
                    + age_c + clip(age_s, 5).rjust(5) + RST)
            if global_di == sel_dev:
                cell = hl(cell, cw)
            dev_cells.append((len(block), global_di))
            block.append(cell)
            global_di += 1
        block.append("")
        ci = ht.index(min(ht))
        if hit_rows is not None:
            for off, di in dev_cells:
                hit_rows.append((len(cols[ci]) + off, di, ci))
        cols[ci].extend(block)
        ht[ci] += len(block)

    maxlen = max((len(c) for c in cols), default=0)
    out = []
    for r in range(maxlen):
        parts = []
        for ci in range(nc):
            cell = cols[ci][r] if r < len(cols[ci]) else ""
            parts.append(padto(cell, cw))
        out.append((" " * gap).join(parts))
    return out


def build_proxmox_lines(prox, w, sel_guest, hit_rows=None,
                        sel_node=-1, sel_kind="guest"):
    """Proxmox-Panel: Nodes + Guests mit RAM-Bars und sauberem Highlight."""
    out = []
    if not prox or not prox.get("nodes"):
        out.append(CLR["dark"] + " Proxmox: keine Daten" + RST)
        return out

    gi = 0
    for ni, node in enumerate(prox["nodes"]):
        label = node.get("label", "PVE")
        node_sel = sel_kind == "node" and sel_node == ni
        if not node.get("online"):
            head = CLR["red"] + BOLD + "\u2b24 " + label + " OFFLINE" + RST
            if node_sel:
                head = hl(head, w)
            if hit_rows is not None:
                hit_rows.append((len(out), "node", ni))
            out.append(head)
            out.append("")
            gi += len(node.get("guests", []))
            continue
        nd = node.get("node") or {}
        cpu = nd.get("cpu", 0)
        memf = nd.get("mem", 0) / max(nd.get("maxmem", 1), 1)
        dskf = nd.get("disk", 0) / max(nd.get("maxdisk", 1), 1)
        up = human_uptime(nd.get("uptime", 0))
        head = "{}\u2b24 {}{}  {}up {}".format(CLR["green"], BOLD, label, RST, up)
        if node_sel:
            head = hl(head, w)
        if hit_rows is not None:
            hit_rows.append((len(out), "node", ni))
        out.append(head)
        info = "  CPU {} RAM {} DSK {}".format(
            bar(cpu / 100.0, 7, CLR["cyan"]),
            bar(memf, 7, CLR["green"] if memf < 0.85 else CLR["orange"]),
            bar(dskf, 7, CLR["green"] if dskf < 0.85 else CLR["orange"]))
        out.append(info)
        subline = "  {}CPU{:.0f}% RAM{:.0f}% DSK{:.0f}%{}".format(
            CLR["dark"], cpu, memf * 100, dskf * 100, RST)
        out.append(subline)

        for g in node.get("guests", []):
            running = g["status"] == "running"
            if running:
                sdot = CLR["green"] + "\u25b6" + RST
            elif g["status"] == "stopped":
                sdot = CLR["gray"] + "\u25a0" + RST
            else:
                sdot = CLR["yellow"] + "\u25cf" + RST
            memf = g["mem"] / max(g["maxmem"], 1) if running else 0
            typ = (CLR["blue"] if g["type"] == "VM" else CLR["cyan"]) + g["type"] + RST
            name = g["name"]
            sel = (sel_kind == "guest" and gi == sel_guest)
            if running:
                memtxt = "{}/{}".format(human_bytes(g["mem"]), human_bytes(g["maxmem"]))
                membar = bar(memf, 6, CLR["green"] if memf < 0.85 else CLR["orange"])
                up = human_uptime(g.get("uptime", 0))
                # Uptime direkt hinter dem Namen, RAM rechtsbuendig
                head = "  {} {} {}{}".format(sdot, typ, name,
                                             CLR["gray"] + "  up " + up + RST)
                tail = " {} {}".format(membar, memtxt)
                if vlen(head) + vlen(tail) > w:
                    tail = " {} {}".format(
                        bar(memf, 4, CLR["green"] if memf < 0.85 else CLR["orange"]),
                        memtxt)
            else:
                head = "  {} {} {}{}".format(sdot, typ, name,
                                             CLR["dark"] + "  stopped" + RST)
                tail = ""
            line = head + " " * max(0, w - vlen(head) - vlen(tail)) + tail
            if vlen(line) > w:
                line = clip(line, w)
            if sel:
                line = hl(line, w)
            if hit_rows is not None:
                hit_rows.append((len(out), "guest", gi))
            out.append(line)
            gi += 1
        out.append("")
    return out


def _signal_color(sig):
    """Farbe nach WLAN-Signalstaerke (dBm)."""
    if sig is None:
        return CLR["dark"]
    if sig >= -60:
        return CLR["green"]
    if sig >= -70:
        return CLR["yellow"]
    if sig >= -78:
        return CLR["orange"]
    return CLR["red"]


def _quality_color(q):
    """Farbe fuer UniFi-Satisfaction/Qualitaet (0-100)."""
    if q is None:
        return CLR["dark"]
    if q >= 90:
        return CLR["green"]
    if q >= 75:
        return CLR["yellow"]
    if q >= 60:
        return CLR["orange"]
    return CLR["red"]


def _quality_text(q):
    if q is None:
        return "?"
    if q >= 90:
        return "sehr gut"
    if q >= 75:
        return "gut"
    if q >= 60:
        return "mittel"
    return "schlecht"


def _unifi_ap_line(ap, w):
    name = ap.get("name") or ap.get("mac") or "AP"
    sat = ap.get("satisfaction")
    qc = _quality_color(sat)
    sat_txt = "  Q{}%".format(sat) if sat is not None else ""
    if ap.get("up"):
        dot = CLR["green"] + "\u25b2" + RST
        head = "  {} {}{}{}".format(
            dot, name,
            CLR["gray"] + "  up " + human_uptime(ap.get("uptime", 0)) + RST,
            qc + sat_txt + RST)
        tail = "{} {} Ger".format(
            ap.get("model_name") or ap.get("model") or "",
            int(ap.get("num_sta") or 0)).strip()
    else:
        dot = CLR["red"] + "\u25bc" + RST
        head = "  {} {}{}".format(dot, name, CLR["red"] + "  offline" + RST)
        tail = ""
    if tail and vlen(head) + vlen(tail) + 1 > w:
        tail = "{} Ger".format(int(ap.get("num_sta") or 0))
    if tail and vlen(head) + vlen(tail) + 1 > w:
        tail = ""
    line = head + " " * max(0, w - vlen(head) - vlen(tail)) + tail
    return clip(line, w)


def _unifi_client_cell(cl, name_w):
    """Eine Zelle fuer das AP-Modal: Name + Signalstaerke."""
    sig = cl.get("signal")
    color = _signal_color(sig)
    name = cl.get("name") or cl.get("hostname") or cl.get("ip") or "?"
    sigtxt = ("{}dBm".format(sig) if sig is not None else "-").rjust(6)
    return (color + "\u00b7" + RST + " " + padto(clip(name, name_w), name_w)
            + " " + color + sigtxt + RST)


def build_unifi_lines(uni, w, max_lines, sel_ap=-1, hit_rows=None):
    """UniFi-Block: nur Access Points. Die Geraete stehen im AP-Modal."""
    out = []
    if not uni:
        out.append(CLR["dark"] + " \u2b24 UniFi: keine Daten" + RST)
        return out

    aps = uni.get("aps") or []
    clients = uni.get("clients") or []
    ap_total = int(uni.get("ap_total") or len(aps))
    ap_up = int(uni.get("ap_up") if uni.get("ap_up") is not None
              else sum(1 for a in aps if a.get("up")))
    wlan = int(uni.get("wlan_count") if uni.get("wlan_count") is not None
               else len(clients))
    wired = int(uni.get("wired_count") or 0)

    if not uni.get("ok"):
        hc = CLR["red"]
    elif ap_total and ap_up == ap_total:
        hc = CLR["green"]
    elif ap_up:
        hc = CLR["yellow"]
    else:
        hc = CLR["red"]

    head = "{}\u2b24 {}{}UniFi{}  {}/{} APs \u00b7 {} WLAN".format(
        hc, BOLD, hc, RST, ap_up, ap_total, wlan)
    if wired:
        head += CLR["dark"] + " \u00b7 {} LAN".format(wired) + RST
    out.append(head)
    if max_lines is not None and len(out) >= max_lines:
        return out

    if not uni.get("ok"):
        age = human_age(time.time() - (uni.get("ts") or 0))
        out.append(CLR["red"] + "  Controller nicht erreichbar" + RST
                   + CLR["dark"] + " (Daten vor {})".format(age) + RST)
        if max_lines is not None and len(out) >= max_lines:
            return out

    room = None if max_lines is None else max(0, max_lines - len(out))
    if room is not None and room <= 0:
        return out

    shown = 0
    for idx, ap in enumerate(aps):
        if room is not None and shown >= room:
            break
        line = _unifi_ap_line(ap, w)
        if idx == sel_ap:
            line = hl(line, w)
        if hit_rows is not None:
            hit_rows.append((len(out), idx))
        out.append(line)
        shown += 1

    hidden = len(aps) - shown
    if hidden > 0 and (max_lines is None or len(out) < max_lines):
        out.append(CLR["dark"] + "  +{} weitere APs".format(hidden) + RST)
    return out


def open_unifi_modal(uni, ap_index):
    """Baut das AP-Modal (Geräte des gewaehlten Access Points)."""
    aps = (uni or {}).get("aps") or []
    if ap_index < 0 or ap_index >= len(aps):
        return None
    ap = aps[ap_index]
    mac = ap.get("mac")
    clients = [c for c in (uni.get("clients") or [])
               if c.get("ap_mac") == mac]
    return {"type": "unifi", "ap": ap, "clients": clients}


def _deconz_counts(dc):
    total = int(dc.get("total") or 0)
    online = int(dc.get("online") or 0)
    offline = int(dc.get("offline") if dc.get("offline") is not None
                  else total - online)
    return total, online, offline


def build_deconz_lines(dc, w, max_lines, sel=False, hit_rows=None):
    """Kompakter deCONZ-Block: Anzahl, Akku- und Funk-Auffaelligkeiten."""
    out = []
    if not dc:
        out.append(CLR["dark"] + " \u2b21 deCONZ: keine Daten" + RST)
        if hit_rows is not None:
            hit_rows.append(0)
        return out

    total, online, offline = _deconz_counts(dc)
    if not dc.get("ok"):
        age = human_age(time.time() - (dc.get("ts") or 0))
        line = (CLR["red"] + " \u2b21 deCONZ  nicht erreichbar" + RST
                + CLR["dark"] + " (Daten vor {})".format(age) + RST)
        if sel:
            line = hl(line, w)
        if hit_rows is not None:
            hit_rows.append(0)
        out.append(line)
        return out[:max_lines] if max_lines is not None else out

    bat_weak = int(dc.get("battery_weak") or 0)
    bat_empty = int(dc.get("battery_empty") or 0)
    # Leere Akkus nicht doppelt als "schwach" zaehlen.
    weak = max(0, bat_weak - bat_empty)
    # deCONZ liefert keinen RSSI/LQI; nicht erreichbar = schlechte Funkstrecke.
    bad = offline

    if total and offline == 0:
        hc = CLR["green"]
    elif online:
        hc = CLR["yellow"]
    else:
        hc = CLR["red"]

    if max_lines is not None and max_lines < 2:
        # Nur eine Zeile Platz: alles in eine kompakte Zusammenfassung.
        variants = [
            "deCONZ {} \u00b7 {} live \u00b7 Akku {} \u00b7 Funk {}".format(
                total, online, bat_empty, bad),
            "deCONZ {} \u00b7 {} live \u00b7 A{} \u00b7 F{}".format(
                total, online, bat_empty, bad),
            "dC{} {}live A{} F{}".format(total, online, bat_empty, bad),
        ]
        text = variants[-1]
        for cand in variants:
            if vlen(cand) + 2 <= w:
                text = cand
                break
        line1 = "{}\u2b21 {}{}".format(hc, text, RST)
        if sel:
            line1 = hl(line1, w)
        if hit_rows is not None:
            hit_rows.append(0)
        return [clip(line1, w) if vlen(line1) > w else line1]

    if w >= 40:
        line1 = "{}\u2b21 {}{}deCONZ{}  {} Geraete  {} live".format(
            hc, BOLD, hc, RST, total, online)
    else:
        line1 = "{}\u2b21 deCONZ {} \u00b7 {} live{}".format(
            hc, total, online, RST)
    if sel:
        line1 = hl(line1, w)
    if hit_rows is not None:
        hit_rows.append(0)
    out.append(line1)

    def _num(value, color):
        return color + str(value) + RST

    if w >= 40:
        line2 = "  Akku {} leer / {} schwach \u00b7 Funk {} schlecht{}".format(
            _num(bat_empty, CLR["red"] if bat_empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]),
            RST)
    elif w >= 24:
        line2 = "  {} leer \u00b7 {} schwach \u00b7 {} schlecht{}".format(
            _num(bat_empty, CLR["red"] if bat_empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]),
            RST)
    else:
        line2 = "  A{}/{}  F{}{}".format(
            _num(bat_empty, CLR["red"] if bat_empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]), RST)
    out.append(clip(line2, w) if vlen(line2) > w else line2)
    return out[:max_lines] if max_lines is not None else out


def _hm_counts(hm):
    """(total, live, leer, schwach, schlecht, offline)."""
    hm = hm or {}
    total = int(hm.get("total") or 0)
    live = int(hm.get("live") or 0)
    return (total, live, int(hm.get("battery_empty") or 0),
            int(hm.get("battery_weak") or 0), int(hm.get("rssi_bad") or 0),
            int(hm.get("unreach") or 0))


def build_homematic_lines(hm, w, max_lines, sel=False, hit_rows=None):
    """Kompakter HomeMatic-Block: Anzahl, Akku- und Funk-Auffaelligkeiten."""
    out = []
    if not hm:
        out.append(CLR["dark"] + " \u2302 HomeMatic: keine Daten" + RST)
        if hit_rows is not None:
            hit_rows.append(0)
        return out

    total, live, empty, weak, bad, offline = _hm_counts(hm)
    if not hm.get("ok"):
        age = human_age(time.time() - (hm.get("ts") or 0))
        out.append(CLR["red"] + " \u2302 HomeMatic nicht erreichbar" + RST
                   + CLR["dark"] + " (vor {})".format(age) + RST)
        if hit_rows is not None:
            hit_rows.append(0)
        return out

    if offline or empty or bad:
        hc = CLR["yellow"]
    else:
        hc = CLR["green"]

    if max_lines is not None and max_lines < 2:
        # Nur eine Zeile Platz: kompakte Zusammenfassung statt reiner Anzahl.
        variants = [
            "HomeMatic {} \u00b7 {} live \u00b7 Akku {} \u00b7 Funk {}".format(
                total, live, empty, bad),
            "HM {} \u00b7 {} live \u00b7 Akku {} \u00b7 Funk {}".format(
                total, live, empty, bad),
            "HM {} \u00b7 {} live \u00b7 A{} \u00b7 F{}".format(
                total, live, empty, bad),
            "HM{} {}live A{} F{}".format(total, live, empty, bad),
        ]
        text = variants[-1]
        for cand in variants:
            if vlen(cand) + 2 <= w:
                text = cand
                break
        line1 = "{}\u2302 {}{}".format(hc, text, RST)
        if sel:
            line1 = hl(line1, w)
        if hit_rows is not None:
            hit_rows.append(0)
        return [clip(line1, w) if vlen(line1) > w else line1]

    if w >= 40:
        line1 = "{}\u2302 {}{}HomeMatic{}  {} Geraete  {} live".format(
            hc, BOLD, hc, RST, total, live)
    else:
        line1 = "{}\u2302 HM {} \u00b7 {} live{}".format(hc, total, live, RST)
    if sel:
        line1 = hl(line1, w)
    if hit_rows is not None:
        hit_rows.append(0)
    out.append(line1)

    def _num(value, color):
        return color + str(value) + RST

    if w >= 40:
        line2 = "  Akku {} leer / {} schwach \u00b7 Funk {} schlecht{}".format(
            _num(empty, CLR["red"] if empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]),
            RST)
    elif w >= 24:
        line2 = "  {} leer \u00b7 {} schwach \u00b7 {} schlecht{}".format(
            _num(empty, CLR["red"] if empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]),
            RST)
    else:
        line2 = "  A{}/{}  F{}{}".format(
            _num(empty, CLR["red"] if empty else CLR["green"]),
            _num(weak, CLR["yellow"] if weak else CLR["green"]),
            _num(bad, CLR["red"] if bad else CLR["green"]), RST)
    out.append(clip(line2, w) if vlen(line2) > w else line2)
    return out[:max_lines] if max_lines is not None else out


def open_homematic_modal(hm):
    if not hm:
        return None
    return {"type": "homematic", "data": hm}


def build_homematic_modal(modal, mw, max_rows=None):
    """HomeMatic-Modal: Akku- und Funkprobleme nach Gruppen, danach Typen."""
    hm = modal.get("data") or {}
    problems = hm.get("problems") or {}

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    def volt_txt(item):
        v = item.get("volt")
        return " ({:.1f} V)".format(v) if v is not None else ""

    def rssi_txt(item):
        r = item.get("rssi")
        return " ({} dBm)".format(r) if r is not None else ""

    lines = []
    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)

    total, live, empty, weak, bad, offline = _hm_counts(hm)
    title = "HOMEMATIC CCU: {} Geraete".format(total)
    lines.append(mhi + MBOX["v"] + " " + padto(clip(title, mw - 4), mw - 4)
                 + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    if not hm.get("ok"):
        err = hm.get("error") or "keine Daten"
        lines.append(mline(CLR["red"] + BOLD + "NICHT ERREICHBAR" + RST))
        lines.append(mline(CLR["gray"] + clip(err, mw - 4) + RST))
        lines.append(msep())
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] schliessen" + RST))
        lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
        return lines

    stat = "{}live {}{}".format(CLR["green"] + BOLD, live, RST)
    if offline:
        stat += "   " + CLR["red"] + "{} offline".format(offline) + RST
    lines.append(mline(stat))

    lines.append(mline("{}Akku:{} {}{} leer{}, {}{} schwach{}, {} ok".format(
        CLR["cyan"], RST,
        CLR["red"], empty, RST,
        CLR["yellow"], weak, RST,
        int(hm.get("battery_ok") or 0))))
    lines.append(mline("{}Funk:{} {}{} sehr schlecht{}, {}{} maessig{} (\u2264 {} / {} dBm)".format(
        CLR["cyan"], RST,
        CLR["red"], bad, RST,
        CLR["yellow"], int(hm.get("rssi_poor") or 0), RST,
        int(hm.get("rssi_bad_limit") or -95),
        int(hm.get("rssi_weak_limit") or -85))))

    # Abschnitte in Prioritaetsreihenfolge; die Liste wird gescrollt.
    sections = [
        ("BATTERIE LEER", CLR["red"],
         [(p["name"] + volt_txt(p), "") for p in (problems.get("empty") or [])]),
        ("NICHT ERREICHBAR", CLR["red"],
         [(p["name"], "") for p in (problems.get("offline") or [])]),
        ("BATTERIE SCHWACH", CLR["yellow"],
         [(p["name"] + volt_txt(p), "") for p in (problems.get("weak") or [])]),
        ("FUNK SEHR SCHLECHT", CLR["red"],
         [(p["name"], rssi_txt(p)) for p in (problems.get("bad") or [])]),
        ("FUNK MAESSIG", CLR["yellow"],
         [(p["name"], rssi_txt(p)) for p in (problems.get("poor") or [])]),
    ]

    # Typen-Uebersicht als Zeilenblöcke (unten, wenn Platz bleibt).
    types = hm.get("types") or []
    type_chunks = []
    text = ""
    for t in types:
        part = "{}x {}".format(t.get("total", 0), t.get("type") or "?")
        if text and vlen(text) + len(part) + 3 > mw - 6:
            type_chunks.append(text)
            text = part
        else:
            text = (text + " \u00b7 " + part) if text else part
    if text:
        type_chunks.append(text)

    for sec_head, color, items in sections:
        if not items:
            continue
        lines.append(msep())
        lines.append(mline(color + BOLD + "{} ({}){}".format(
            sec_head, len(items), RST)))
        for name, extra in items:
            text = "  " + clip(name, max(10, mw - 10 - vlen(extra))) + extra
            lines.append(mline(text))

    if type_chunks:
        lines.append(msep())
        lines.append(mline(CLR["cyan"] + BOLD + "NACH TYP" + RST))
        for chunk in type_chunks:
            lines.append(mline("  " + chunk))

    lines.append(msep())
    lines.append(mline(CLR["gray"] + "Stand {}   [ESC]/[q] schliessen".format(
        fmt_ts(hm.get("ts"))) + RST))
    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)

    if not max_rows:
        modal["scroll"] = 0
        modal["max_scroll"] = 0
        return lines

    # Kopf + Zusammenfassung bleiben stehen, die Listen darunter scrollen.
    head_n = 6 if (max_rows >= 12 and len(lines) >= 10) else 3
    head = lines[:head_n]
    foot = lines[-3:]
    content = lines[head_n:-3]
    body_h = max(1, max_rows - head_n - len(foot))
    max_scroll = max(0, len(content) - body_h)
    scroll = max(0, min(int(modal.get("scroll") or 0), max_scroll))
    modal["scroll"] = scroll
    modal["max_scroll"] = max_scroll

    if max_scroll:
        pos = "[{}-{}/{}]".format(scroll + 1,
                                  min(len(content), scroll + body_h),
                                  len(content))
        title = "HOMEMATIC CCU: {} Geraete  {}".format(total, pos)
        head[1] = (mhi + MBOX["v"] + " "
                   + padto(clip(title, mw - 4), mw - 4) + " " + MBOX["v"] + RST)
        foot[1] = mline(CLR["gray"]
                        + "Stand {}   \u2191\u2193 bl\u00e4ttern  ESC/q".format(
                            fmt_ts(hm.get("ts"))) + RST)

    return head + content[scroll:scroll + body_h] + foot


def open_deconz_modal(dc):
    """Baut das deCONZ-Modal (Geraete nach Typ gruppiert)."""
    if not dc:
        return None
    return {"type": "deconz", "data": dc}


def build_deconz_modal(modal, mw, max_rows=None):
    """deCONZ-Modal: Anzahl je Geraetetyp, online/offline."""
    dc = modal.get("data") or {}
    groups = list(dc.get("groups") or [])

    mbg = CLR["modalbg"]
    mhi = CLR["modalhi"]

    def mline(content):
        return mbg + MBOX["v"] + " " + padto(content, mw - 4) + " " + MBOX["v"] + RST

    def msep(char="\u2500"):
        return mbg + MBOX["v"] + char * (mw - 2) + MBOX["v"] + RST

    lines = []
    lines.append(mbg + MBOX["tl"] + MBOX["h"] * (mw - 2) + MBOX["tr"] + RST)

    gw = dc.get("gateway") or "deCONZ"
    title = "DECONZ / PHOSCON: {}".format(gw)
    lines.append(mhi + MBOX["v"] + " " + padto(clip(title, mw - 4), mw - 4)
                  + " " + MBOX["v"] + RST)
    lines.append(mbg + MBOX["lt"] + MBOX["h"] * (mw - 2) + MBOX["rt"] + RST)

    total, online, offline = _deconz_counts(dc)
    resources = int(dc.get("resources_total") or total)
    bat_total = int(dc.get("battery_total") or 0)
    bat_weak = int(dc.get("battery_weak") or 0)
    bat_empty = int(dc.get("battery_empty") or 0)
    bat_none = int(dc.get("battery_none")
                   if dc.get("battery_none") is not None
                   else total - bat_total)
    weak_limit = int(dc.get("battery_weak_limit") or 25)

    stat = (CLR["green"] + BOLD + "{} verbunden".format(online) + RST
            + CLR["gray"] + "  \u00b7  " + RST
            + (CLR["red"] if offline else CLR["gray"])
            + "{} offline".format(offline) + RST)
    lines.append(mline("{} Geraete   {} {}".format(
        total, stat, CLR["dark"] + "({} Typen)".format(len(groups)) + RST)))

    if not dc.get("ok"):
        age = human_age(time.time() - (dc.get("ts") or 0))
        lines.append(mline(CLR["red"] + "Gateway nicht erreichbar"
                           + CLR["dark"] + " (Daten vor {})".format(age) + RST))
    elif max_rows is None or max_rows >= 16:
        fw = dc.get("fwversion") or "-"
        sw = dc.get("swversion") or "-"
        ch = dc.get("zigbeechannel")
        extra = "Gateway {} \u00b7 Firmware {}".format(sw, fw)
        if ch:
            extra += " \u00b7 Kanal {}".format(ch)
        lines.append(mline(CLR["dark"] + clip(extra, mw - 4) + RST))

    # ─── Akku-Uebersicht + Konsolidierung ───
    wc = CLR["orange"] if bat_weak else CLR["gray"]
    ec = CLR["red"] if bat_empty else CLR["gray"]
    if mw >= 62:
        battery = ("Akku: " + CLR["gray"] + "{} mit Akku".format(bat_total) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + wc + "{} schwach (<={}%)".format(bat_weak, weak_limit) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + ec + "{} leer".format(bat_empty) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + CLR["dark"] + "{} ohne".format(bat_none) + RST)
    else:
        battery = ("Akku: " + CLR["gray"] + "{}".format(bat_total) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + wc + "{} schwach".format(bat_weak) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + ec + "{} leer".format(bat_empty) + RST
                   + CLR["gray"] + " \u00b7 " + RST
                   + CLR["dark"] + "{} ohne".format(bat_none) + RST)
    lines.append(mline(battery))
    if resources and resources > total and (max_rows is None or max_rows >= 16):
        lines.append(mline(
            CLR["dark"] + "Konsolidiert: {} Eintraege -> {} Geraete".format(
                resources, total) + RST))

    # Schwache Akkus fuer die Detail-Liste sammeln
    weak_devs = []
    for grp in groups:
        for dev in grp.get("devices") or []:
            b = dev.get("battery")
            if b is not None and int(b) <= weak_limit:
                weak_devs.append((int(b), dev.get("name") or "?"))
    weak_devs.sort(key=lambda x: (x[0], x[1].lower()))
    show_weak = max_rows is None or max_rows >= 24
    weak_cap = min(len(weak_devs), 5) if show_weak else 0

    lines.append(msep())
    inner = mw - 4
    num_w = 21
    label_w = max(8, inner - num_w - 1)
    hdr = (padto("TYP", label_w)
           + "{:>5} {:>7} {:>7}".format("GES", "ONLINE", "OFFLINE"))
    lines.append(mline(CLR["cyan"] + BOLD + hdr + RST))

    budget = None
    if max_rows is not None:
        weak_block = ((2 + weak_cap + (1 if len(weak_devs) > weak_cap else 0))
                      if (weak_devs and weak_cap) else 0)
        # Rahmen-Tail: Trenner + Fusszeile + unterer Rahmen; plus Reserve "+N weitere"
        budget = max(0, max_rows - len(lines) - 3 - weak_block - 1)
        if budget < 3 and weak_cap:
            # Kleines Terminal: zuerst die Akku-Liste kuerzen
            weak_cap = max(0, weak_cap - (4 - budget))
            weak_block = ((2 + weak_cap + (1 if len(weak_devs) > weak_cap else 0))
                          if weak_cap else 0)
            budget = max(0, max_rows - len(lines) - 3 - weak_block - 1)
    shown = 0
    for grp in groups:
        if budget is not None and shown >= budget:
            break
        total_g = int(grp.get("total") or 0)
        on_g = int(grp.get("online") or 0)
        off_g = int(grp.get("offline") if grp.get("offline") is not None
                    else total_g - on_g)
        gc = CLR["green"] if off_g == 0 else (CLR["yellow"] if on_g else CLR["red"])
        label = grp.get("label") or grp.get("type") or "?"
        if grp.get("virtual"):
            label = label.replace(" (virtuell)", "")
        mark = gc + "\u25b8" + RST + " "
        label_cell = padto(clip(label, label_w - 2), label_w - 2)
        row = (mark + label_cell + " "
               + "{:>5}".format(total_g)
               + " " + CLR["green"] + "{:>7}".format(on_g) + RST
               + " " + (CLR["red"] if off_g else CLR["dark"])
               + "{:>7}".format(off_g) + RST)
        lines.append(mline(row))
        shown += 1

    if shown < len(groups):
        lines.append(mline(CLR["dark"] + "+{} weitere Typen".format(
            len(groups) - shown) + RST))

    # ─── Schwache Akkus ───
    if weak_devs and weak_cap:
        lines.append(msep())
        lines.append(mline(CLR["orange"] + BOLD
                           + "AKKU SCHWACH (<={}%)".format(weak_limit) + RST))
        for b, name in weak_devs[:weak_cap]:
            bc = CLR["red"] if b <= 10 else CLR["orange"]
            name_w = max(6, inner - 7)
            row = ("  " + padto(clip(name, name_w), name_w)
                   + bc + "{:>4}%".format(b) + RST)
            lines.append(mline(row))
        if len(weak_devs) > weak_cap:
            lines.append(mline(CLR["dark"] + "+{} weitere".format(
                len(weak_devs) - weak_cap) + RST))

    lines.append(msep())
    lines.append(mline(CLR["gray"] + "[ESC] oder [q] schliessen" + RST))
    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


# ═══════════════════════════════════════════════════════════════════
# PROXMOX ACTIONS
# ═══════════════════════════════════════════════════════════════════

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


_BACKUP_STORAGE_CACHE = {}


def get_backup_storage(node_cfg, node_name):
    """Ermittelt Storage mit Content-Typ 'backup'.
    Config-Override (proxmox_nodes[].backup_storage) hat Vorrang."""
    override = node_cfg.get("backup_storage")
    if override:
        return override
    key = (node_cfg.get("url"), node_name)
    if key in _BACKUP_STORAGE_CACHE:
        return _BACKUP_STORAGE_CACHE[key]
    try:
        url = "{}/api2/json/nodes/{}/storage".format(
            node_cfg["url"].rstrip("/"), node_name)
        req = urllib.request.Request(url)
        req.add_header("Authorization", "PVEAPIToken=" + node_cfg["token"])
        resp = urllib.request.urlopen(req, timeout=8, context=_CTX)
        data = json.loads(resp.read().decode("utf-8")).get("data") or []
        candidates = []
        for s in data:
            content = [c.strip() for c in s.get("content", "").split(",")]
            if "backup" in content and s.get("active", 1):
                candidates.append(s.get("storage"))
        if candidates:
            # Prioritaet: Name enthaelt 'nas' > 'backup' > sonstige (nicht 'local')
            def score(n):
                ln = (n or "").lower()
                s = 0
                if "nas" in ln:
                    s += 20
                if "backup" in ln:
                    s += 10
                if ln == "local":
                    s -= 50
                return -s
            candidates.sort(key=score)
            name = candidates[0]
            _BACKUP_STORAGE_CACHE[key] = name
            return name
    except Exception:
        pass
    return None


def _api_error(e):
    """Liest die echte Fehlermeldung aus einem HTTPError-Body."""
    try:
        body = e.read().decode("utf-8", "replace")
        data = json.loads(body)
        errs = data.get("errors")
        if isinstance(errs, dict) and errs:
            return "; ".join("{}: {}".format(k, v) for k, v in errs.items())
        return data.get("message") or body
    except Exception:
        return str(e)


def guest_action(node_idx, node_name, vmid, gtype, action):
    """POST an PVE-API: start, shutdown, reboot, stop, backup."""
    nodes = CFG.get("proxmox_nodes", [])
    if node_idx >= len(nodes):
        return "Node-Index ungueltig"
    nc = nodes[node_idx]

    if action == "backup":
        storage = get_backup_storage(nc, node_name)
        if not storage:
            return "Fehler: kein Backup-Storage auf {}".format(node_name)
        # Modus-Fallback: snapshot -> suspend -> stop
        last_err = ""
        for mode in ("snapshot", "suspend", "stop"):
            url = "{}/api2/json/nodes/{}/vzdump".format(
                nc["url"].rstrip("/"), node_name)
            data = urllib.parse.urlencode({
                "vmid": vmid,
                "storage": storage,
                "mode": mode,
                "compress": "zstd",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Authorization", "PVEAPIToken=" + nc["token"])
            try:
                resp = urllib.request.urlopen(req, timeout=15, context=_CTX)
                resp.read()
                return "Backup -> {} ({}) laeuft".format(storage, mode)
            except urllib.error.HTTPError as e:
                last_err = _api_error(e)
                if "snapshot" not in last_err.lower():
                    return "Fehler: " + last_err[:55]
            except Exception as e:
                return "Fehler: " + str(e)[:55]
        return "Fehler: " + last_err[:55]

    kind = "qemu" if gtype == "VM" else "lxc"
    url = "{}/api2/json/nodes/{}/{}/{}/status/{}".format(
        nc["url"].rstrip("/"), node_name, kind, vmid, action)
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Authorization", "PVEAPIToken=" + nc["token"])
    try:
        urllib.request.urlopen(req, timeout=12, context=_CTX)
        return "{} {} -> {} OK".format(gtype, vmid, action)
    except urllib.error.HTTPError as e:
        return "Fehler: " + _api_error(e)[:55]
    except Exception as e:
        return "Fehler: {}".format(str(e)[:55])


def node_action(node_idx, node_name, action):
    """POST an PVE-API: gesamten Node herunterfahren oder neu starten."""
    nodes = CFG.get("proxmox_nodes", [])
    if node_idx >= len(nodes):
        return "Node-Index ungueltig"
    nc = nodes[node_idx]

    url = "{}/api2/json/nodes/{}/status".format(
        nc["url"].rstrip("/"), node_name)
    data = urllib.parse.urlencode({"command": action}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "PVEAPIToken=" + nc["token"])
    try:
        urllib.request.urlopen(req, timeout=15, context=_CTX)
        word = "Herunterfahren" if action == "shutdown" else "Neustart"
        return "{} {} gestartet".format(word, node_name)
    except urllib.error.HTTPError as e:
        return "Fehler: " + _api_error(e)[:55]
    except Exception as e:
        return "Fehler: {}".format(str(e)[:55])


# ═══════════════════════════════════════════════════════════════════
# INPUT-PARSER (ROBUST & NON-BLOCKING)
# ═══════════════════════════════════════════════════════════════════

def _selection(panel, sel_dev, sel_guest, sel_ap, sel_dc, sel_node=0,
               sel_kind="guest"):
    if panel == 1:
        return (sel_node if sel_kind == "node" else sel_guest), sel_kind
    return {0: sel_dev, 2: sel_ap, 3: sel_dc}.get(panel, 0), None


def spatial_target(panel, sel_dev, sel_guest, sel_ap, sel_dc, key,
                   sel_node=0, sel_kind="guest"):
    """Naechstes selektierbares Element in Pfeilrichtung (2D, frei)."""
    items = LAST_LAYOUT.get("items") or []
    if not items:
        return None
    cur = None
    idx, kind = _selection(panel, sel_dev, sel_guest, sel_ap, sel_dc,
                           sel_node, sel_kind)
    for it in items:
        if it["panel"] == panel and it["idx"] == idx and (
                panel != 1 or it.get("kind") == kind):
            cur = it
            break
    if cur is None:
        for it in items:
            if it["panel"] == panel and (
                    panel != 1 or it.get("kind") == kind):
                cur = it
                break
    if cur is None:
        for it in items:
            if it["panel"] == panel:
                cur = it
                break
    if cur is None:
        return None

    dx, dy = {"left": (-1, 0), "right": (1, 0),
              "up": (0, -1), "down": (0, 1)}[key]
    best = None
    for it in items:
        if it is cur:
            continue
        if dx > 0 and it["col"] <= cur["col"]:
            continue
        if dx < 0 and it["col"] >= cur["col"]:
            continue
        if dy > 0 and it["row"] <= cur["row"]:
            continue
        if dy < 0 and it["row"] >= cur["row"]:
            continue
        ddx = abs(it["col"] - cur["col"])
        ddy = abs(it["row"] - cur["row"])
        if dx:
            score = ddx + ddy * 2.5
        else:
            score = ddy + ddx * 2.5
        if best is None or score < best[0]:
            best = (score, it)
    return best[1] if best else None


def handle_input(data, active_panel, sel_dev, sel_guest, sel_ap, sel_dc,
                 num_devs, num_guests, num_aps, num_dc, modal,
                 sel_node=0, sel_kind="guest", num_hm=0):
    """
    Parst beliebige Terminal-Sequenzen vollstaendig und nicht-blockierend.
    Returns: dict mit 'quit', 'active_panel', 'sel_dev', 'sel_guest',
             'sel_ap', 'sel_dc', 'action', 'modal_action', 'open_modal',
             'close_modal', 'form_submit', 'form_cancel', 'confirm_yes'
    """
    res = {
        "quit": False,
        "active_panel": active_panel,
        "sel_dev": sel_dev,
        "sel_guest": sel_guest,
        "sel_ap": sel_ap,
        "sel_dc": sel_dc,
        "sel_node": sel_node,
        "sel_kind": sel_kind,
        "action": None,
        "modal_action": None,
        "open_modal": False,
        "close_modal": False,
        "form_submit": False,
        "form_cancel": False,
        "confirm_yes": False,
    }

    def move(key):
        """Raeumliche Navigation: naechstes Element in Richtung key."""
        tgt = spatial_target(res["active_panel"], res["sel_dev"],
                             res["sel_guest"], res["sel_ap"], res["sel_dc"],
                             key, res["sel_node"], res["sel_kind"])
        if not tgt:
            return
        res["active_panel"] = tgt["panel"]
        if tgt["panel"] == 0:
            res["sel_dev"] = tgt["idx"]
        elif tgt["panel"] == 1:
            if tgt.get("kind") == "node":
                res["sel_node"] = tgt["idx"]
                res["sel_kind"] = "node"
            else:
                res["sel_guest"] = tgt["idx"]
                res["sel_kind"] = "guest"
        elif tgt["panel"] == 2:
            res["sel_ap"] = tgt["idx"]
        else:
            res["sel_dc"] = 0

    def form_field():
        return modal["fields"][modal.get("focus", 0)]

    def move_focus(step):
        modal["focus"] = (modal.get("focus", 0) + step) % len(modal["fields"])

    def cycle_select(step):
        f = form_field()
        if f.get("type") == "select" and f.get("options"):
            f["idx"] = (f.get("idx", 0) + step) % len(f["options"])
            f["value"] = f["options"][f["idx"]][1]

    i = 0
    n = len(data)
    while i < n:
        b = data[i]

        # ── Formular-Modus ──
        if modal and modal.get("type") == "form":
            f = form_field()
            if b == 0x1b:                      # ESC / Pfeiltasten
                if i + 2 < n and data[i + 1] == 0x5b:
                    code = data[i + 2]
                    if code == 0x41:
                        move_focus(-1)
                    elif code == 0x42:
                        move_focus(1)
                    elif code == 0x44:
                        cycle_select(-1)
                    elif code == 0x43:
                        cycle_select(1)
                    i += 3
                    continue
                res["form_cancel"] = True
                i += 1
                continue
            if b in (0x09, 0x0e, 0x0d, 0x0a):  # Tab / Enter
                if b in (0x0d, 0x0a) and modal.get("focus", 0) == len(modal["fields"]) - 1:
                    res["form_submit"] = True
                else:
                    move_focus(1)
                i += 1
                continue
            if b in (0x7f, 0x08):              # Backspace
                if f.get("type") == "text" and f.get("value"):
                    f["value"] = f["value"][:-1]
                i += 1
                continue
            if 32 <= b < 127:
                ch = chr(b)
                if f.get("type") == "text":
                    if len(f.get("value", "")) < f.get("max", 40):
                        f["value"] = f.get("value", "") + ch
                elif ch in ("<", "-", "h"):
                    cycle_select(-1)
                elif ch in (">", "+", "l"):
                    cycle_select(1)
                i += 1
                continue
            i += 1
            continue

        # ── Bestaetigungs-Modus ──
        if modal and modal.get("type") == "confirm":
            if b in (0x6a, 0x4a):              # j / J
                res["confirm_yes"] = True
            else:
                res["close_modal"] = True
            i += 1
            continue

        # ── ESC-Sequenzen ──
        if b == 0x1b:
            # Standalone ESC (z.B. ESC allein gedrueckt)
            if i + 1 == n:
                res["close_modal"] = True
                i += 1
                continue

            if i + 2 < n and data[i + 1] == 0x5b:  # ESC [
                code = data[i + 2]
                if modal:
                    if modal.get("type") == "homematic":
                        cur = int(modal.get("scroll") or 0)
                        mx = int(modal.get("max_scroll") or 0)
                        if code == 0x41:        # Up
                            cur -= 1
                        elif code == 0x42:      # Down
                            cur += 1
                        elif code == 0x48:      # Home
                            cur = 0
                        elif code == 0x46:      # End
                            cur = mx
                        elif (code in (0x35, 0x36) and i + 3 < n
                              and data[i + 3] == 0x7e):   # PgUp/PgDn
                            cur += -10 if code == 0x35 else 10
                            modal["scroll"] = max(0, min(mx, cur))
                            i += 4
                            continue
                        modal["scroll"] = max(0, min(mx, cur))
                    i += 3
                    continue
                if code == 0x41:  # Up
                    move("up")
                    i += 3; continue
                elif code == 0x42:  # Down
                    move("down")
                    i += 3; continue
                elif code == 0x44:  # Left
                    move("left")
                    i += 3; continue
                elif code == 0x43:  # Right
                    move("right")
                    i += 3; continue
                else:
                    i += 3; continue
            else:
                res["close_modal"] = True
                i += 1
                continue

        # ── Tab ──
        if b == 0x09:
            order = [0]
            if num_guests:
                order.append(1)
            if num_aps:
                order.append(2)
            if num_dc:
                order.append(3)
            if num_hm:
                order.append(4)
            if res["active_panel"] in order:
                pos = order.index(res["active_panel"])
                res["active_panel"] = order[(pos + 1) % len(order)]
            else:
                res["active_panel"] = order[-1]
            i += 1
            continue

        # ── Enter (CR / LF) ──
        if b in (0x0d, 0x0a):
            if modal:
                res["close_modal"] = True
            else:
                res["open_modal"] = True
            i += 1
            continue

        # ── ASCII-Tasten ──
        c = chr(b) if b < 128 else ""

        # Im Modal-Modus: Ziffern oder Abbrechen
        if modal:
            if c in ("q", "Q"):
                res["close_modal"] = True
            elif modal["type"] == "proxmox":
                if c == "1": res["modal_action"] = "start"
                elif c == "2": res["modal_action"] = "shutdown"
                elif c == "3": res["modal_action"] = "reboot"
                elif c == "4": res["modal_action"] = "stop"
                elif c == "5": res["modal_action"] = "backup"
            elif modal["type"] == "device":
                if c in ("e", "E"):
                    res["modal_action"] = "edit"
                elif c in ("r", "R"):
                    res["modal_action"] = "ping"
                elif c in ("d", "D"):
                    res["modal_action"] = "delete"
                elif c in ("n", "N"):
                    res["modal_action"] = "notify"
                elif b == 0x20:
                    res["modal_action"] = "pause"
            elif modal["type"] == "homematic":
                cur = int(modal.get("scroll") or 0)
                mx = int(modal.get("max_scroll") or 0)
                if c in ("j", "J"):
                    cur += 1
                elif c in ("k", "K"):
                    cur -= 1
                elif c == " ":
                    cur += 10
                elif c == "b":
                    cur -= 10
                elif c == "g":
                    cur = 0
                elif c == "G":
                    cur = mx
                modal["scroll"] = max(0, min(mx, cur))
            elif modal["type"] == "pve_node":
                if modal.get("pending"):
                    if c in ("j", "J"):
                        res["modal_action"] = "confirm_node"
                    elif c in ("n", "N"):
                        res["modal_action"] = "cancel_node"
                elif c in ("s", "S"):
                    res["modal_action"] = "shutdown_node"
                elif c in ("r", "R"):
                    res["modal_action"] = "reboot_node"
            i += 1
            continue

        # Normaler Modus (kein Modal)
        if c in ("q", "Q"):
            res["quit"] = True
            return res
        elif c in ("r", "R"):
            res["action"] = "ping"
        elif c in ("p", "P"):
            res["action"] = "prox"
        elif c in ("a", "A"):
            res["action"] = "add_device"
        elif c in ("c", "C"):
            res["action"] = "config"
        elif c in ("f", "F"):
            res["action"] = "fritz"
        elif c in ("m", "M"):
            res["action"] = "homematic"
        elif c in ("d", "D"):
            res["action"] = "delete_device"
        elif b == 0x20:
            res["action"] = "toggle_pause"
        elif c in ("j",):
            move("down")
        elif c in ("k",):
            move("up")
        elif c in ("h",):
            move("left")
        elif c in ("l",):
            move("right")
        elif c in ("s", "S"):
            res["action"] = "quick_start"
        elif c in ("x", "X"):
            res["action"] = "quick_stop"

        i += 1

    return res


def safe_write(text):
    """Schreibt text sicher auf stdout (faengt BlockingIOError ueber SSH ab)."""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (BlockingIOError, OSError):
        data = text.encode("utf-8")
        while data:
            try:
                n = os.write(1, data)
                data = data[n:]
            except (BlockingIOError, OSError):
                time.sleep(0.005)


# ═══════════════════════════════════════════════════════════════════
# CONFIG-EDITOR (Geraete + Intervalle)
# ═══════════════════════════════════════════════════════════════════

def run_checker_now():
    subprocess.Popen([sys.executable, os.path.join(HERE, "checker.py")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_device(gid, name):
    for dev in CFG.get("groups", {}).get(gid, {}).get("devices", []):
        if dev.get("name") == name:
            return dev
    return None


def open_add_form(default_gid=None):
    choices = group_choices() or [("eigene", "Eigene")]
    idx = 0
    for n, (gid, _lbl) in enumerate(choices):
        if gid == default_gid:
            idx = n
            break
    return {
        "type": "form",
        "kind": "add",
        "title": "NEUES GERAET ANLEGEN",
        "focus": 0,
        "error": None,
        "fields": [
            {"key": "name", "label": "Name", "type": "text", "value": "", "max": 30},
            {"key": "ip", "label": "IP/Host", "type": "text", "value": "", "max": 40},
            {"key": "port", "label": "HTTP-Port", "type": "text", "value": "", "max": 5},
            {"key": "group", "label": "Gruppe", "type": "select",
             "value": choices[idx][1], "idx": idx, "options": choices},
        ],
        "hint": "HTTP-Port optional (nur fuer Web-Check)",
    }


def open_edit_form(gid, name):
    """Bestehendes Geraet bearbeiten (Name, IP, HTTP-Port, Gruppe)."""
    dev = find_device(gid, name)
    if not dev:
        return None
    choices = group_choices() or [("eigene", "Eigene")]
    idx = 0
    for n, (g, _lbl) in enumerate(choices):
        if g == gid:
            idx = n
            break
    port = dev.get("http") or dev.get("port") or ""
    return {
        "type": "form",
        "kind": "edit",
        "title": "GERAET BEARBEITEN",
        "orig_gid": gid,
        "orig_name": name,
        "focus": 0,
        "error": None,
        "fields": [
            {"key": "name", "label": "Name", "type": "text",
             "value": name, "max": 30},
            {"key": "ip", "label": "IP/Host", "type": "text",
             "value": dev.get("ip", ""), "max": 40},
            {"key": "port", "label": "HTTP-Port", "type": "text",
             "value": str(port), "max": 5},
            {"key": "group", "label": "Gruppe", "type": "select",
             "value": choices[idx][1], "idx": idx, "options": choices},
        ],
        "hint": "Enter speichert | HTTP-Port optional (nur fuer Web-Check)",
    }


def open_config_form():
    return {
        "type": "form",
        "kind": "config",
        "title": "INTERVALLE & MELDUNGEN",
        "focus": 0,
        "error": None,
        "fields": [
            {"key": "poll", "label": "Ping (s)", "type": "text",
             "value": str(CFG.get("poll_interval", 15)), "max": 4},
            {"key": "speed", "label": "Speed (min)", "type": "text",
             "value": str(CFG.get("speedtest_interval_min", 30)), "max": 4},
            {"key": "offline", "label": "Offline ab", "type": "text",
             "value": str(CFG.get("offline_after", 3)), "max": 2},
            {"key": "online", "label": "Online ab", "type": "text",
             "value": str(CFG.get("online_after", 2)), "max": 2},
        ],
        "hint": "Ping 5-3600 s | Speed 1-1440 min | Offline/Online ab = Fehlversuche bzw. Erfolge in Folge (1-20)",
    }


def valid_host(ip):
    """IPv4 (Oktette 0-255) oder einfacher Hostname."""
    if re.match(r"^\d+(\.\d+){3}$", ip):
        return all(0 <= int(o) <= 255 for o in ip.split("."))
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$", ip))


def apply_form(modal):
    """Validiert und uebernimmt ein Formular. Rueckgabe: (fehler, meldung)."""
    vals = {f["key"]: f["value"].strip() for f in modal["fields"]}
    if modal.get("kind") == "add":
        name = vals.get("name", "")
        ip = vals.get("ip", "")
        port = vals.get("port", "")
        if not name:
            return "Name fehlt", None
        if not valid_host(ip):
            return "IP/Hostname ungueltig", None
        if port and not port.isdigit():
            return "HTTP-Port muss eine Zahl sein", None
        sel = next((f for f in modal["fields"] if f["key"] == "group"), None)
        if sel and sel.get("options"):
            gid = sel["options"][sel.get("idx", 0)][0]
        else:
            gid = "eigene"
        grp = CFG.setdefault("groups", {}).setdefault(
            gid, {"label": gid.replace("_", " ").title(), "devices": []})
        if find_device(gid, name):
            return "Name existiert in dieser Gruppe", None
        dev = {"name": name, "ip": ip, "type": "linux"}
        if port:
            dev["http"] = int(port)
        grp.setdefault("devices", []).append(dev)
        save_config(CFG)
        run_checker_now()
        return None, "{} angelegt ({})".format(name, gid)

    if modal.get("kind") == "edit":
        orig_gid = modal.get("orig_gid")
        orig_name = modal.get("orig_name")
        dev = find_device(orig_gid, orig_name)
        if not dev:
            return "Geraet nicht mehr vorhanden", None
        name = vals.get("name", "")
        ip = vals.get("ip", "")
        port = vals.get("port", "")
        if not name:
            return "Name fehlt", None
        if not valid_host(ip):
            return "IP/Hostname ungueltig", None
        if port and not port.isdigit():
            return "HTTP-Port muss eine Zahl sein", None
        sel = next((f for f in modal["fields"] if f["key"] == "group"), None)
        if sel and sel.get("options"):
            gid = sel["options"][sel.get("idx", 0)][0]
        else:
            gid = orig_gid
        if (gid != orig_gid or name != orig_name) and find_device(gid, name):
            return "Name existiert in dieser Gruppe", None
        dev["name"] = name
        dev["ip"] = ip
        if port:
            dev["http"] = int(port)
        else:
            dev.pop("http", None)
            dev.pop("port", None)
        if gid != orig_gid:
            old = CFG.get("groups", {}).get(orig_gid, {})
            if dev in (old.get("devices") or []):
                old["devices"].remove(dev)
            grp = CFG.setdefault("groups", {}).setdefault(
                gid, {"label": gid.replace("_", " ").title(), "devices": []})
            grp.setdefault("devices", []).append(dev)
        save_config(CFG)
        run_checker_now()
        return None, "{} gespeichert ({})".format(name, ip)

    if modal.get("kind") == "config":
        try:
            poll = int(vals.get("poll", ""))
            speed = int(vals.get("speed", ""))
            off = int(vals.get("offline", "3"))
            on = int(vals.get("online", "2"))
        except ValueError:
            return "Bitte Zahlen eingeben", None
        if not 5 <= poll <= 3600:
            return "Ping-Intervall: 5-3600 s", None
        if not 1 <= speed <= 1440:
            return "Speedtest: 1-1440 min", None
        if not 1 <= off <= 20:
            return "Offline ab: 1-20 Fehlversuche", None
        if not 1 <= on <= 20:
            return "Online ab: 1-20 Erfolge", None
        CFG["poll_interval"] = poll
        CFG["speedtest_interval_min"] = speed
        CFG["offline_after"] = off
        CFG["online_after"] = on
        save_config(CFG)
        run_checker_now()
        return None, "Ping {}s, Speed {}min, offline ab {}, online ab {}".format(
            poll, speed, off, on)

    return "unbekanntes Formular", None


NOTIFY_MODES = ["none", "down", "up", "both"]
NOTIFY_LABELS = {
    "none": "aus",
    "down": "nur DOWN",
    "up": "nur UP",
    "both": "UP + DOWN",
}


def next_notify(current):
    cur = current if current in NOTIFY_MODES else "none"
    return NOTIFY_MODES[(NOTIFY_MODES.index(cur) + 1) % len(NOTIFY_MODES)]


def set_notify(gid, name, mode):
    dev = find_device(gid, name)
    if not dev:
        return False
    if mode in (None, "", "none"):
        dev.pop("notify", None)
    else:
        dev["notify"] = mode
    save_config(CFG)
    run_checker_now()
    return True


def set_paused(gid, name, paused):
    dev = find_device(gid, name)
    if not dev:
        return False
    dev["paused"] = paused
    if not paused:
        dev.pop("paused", None)
    save_config(CFG)
    run_checker_now()
    return True


def delete_device(gid, name):
    grp = CFG.get("groups", {}).get(gid) or {}
    devs = grp.get("devices", [])
    for idx, dev in enumerate(devs):
        if dev.get("name") == name:
            devs.pop(idx)
            save_config(CFG)
            run_checker_now()
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════

CFG = {}


def main():
    global CFG
    lock_fd, other = _acquire_instance_lock()
    if lock_fd is None:
        msg = "Start abgelehnt: bestehende Instanz reagiert nicht"
        if other:
            msg += " (PID {})".format(other)
        sys.stdout.write(msg + ". Bitte dort mit q beenden.\n")
        sys.stdout.flush()
        return
    os.environ.setdefault("TERM", "linux")
    CFG = load(CONFIG) or {"groups": {}}

    fd = sys.stdin.fileno()
    is_tty = os.isatty(fd)
    old = termios.tcgetattr(fd) if is_tty else None

    # Sicherstellen, dass stdout blockierend bleibt (vermeidet BlockingIOError ueber SSH)
    try:
        out_flags = fcntl.fcntl(1, fcntl.F_GETFL)
        if out_flags & os.O_NONBLOCK:
            fcntl.fcntl(1, fcntl.F_SETFL, out_flags & ~os.O_NONBLOCK)
    except Exception:
        pass

    frame = 0
    active_panel = 1  # Standard: Proxmox
    sel_dev = 0
    sel_guest = 0
    sel_ap = 0
    sel_dc = 0
    sel_node = 0
    sel_kind = "guest"
    msg = None
    msg_until = 0
    modal = None

    mouse_on = ESC + "[?1000h" + ESC + "[?1006h"
    mouse_off = ESC + "[?1006l" + ESC + "[?1000l"

    def _term(signum, frame):
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _term)
    except Exception:
        pass

    sys.stdout.write(ESC + "[?25l" + mouse_on)
    try:
        if is_tty:
            tty.setcbreak(fd)

        while True:
            state = load(STATE)
            prox = load(PROX)
            speed = load(SPEED)
            fb = load(FRITZ)
            uni = load(UNI)
            dc = load(DECONZ)
            hm = load(HOMEMATIC)

            dev_items = flat_devices(CFG)
            guest_items = flat_guests(prox)
            ap_items = (uni or {}).get("aps") or []
            has_dc = bool(dc)
            has_hm = bool(hm)

            if sel_dev >= len(dev_items):
                sel_dev = max(0, len(dev_items) - 1)
            if sel_guest >= len(guest_items):
                sel_guest = max(0, len(guest_items) - 1)
            n_nodes = len((prox or {}).get("nodes") or [])
            if sel_node >= n_nodes:
                sel_node = max(0, n_nodes - 1)
            if sel_ap >= len(ap_items):
                sel_ap = max(0, len(ap_items) - 1)
            if active_panel == 2 and not ap_items:
                active_panel = 1
            if active_panel == 3 and not has_dc:
                active_panel = 1
            if active_panel == 4 and not has_hm:
                active_panel = 1

            # Offenes AP-Modal mit frischen Daten versorgen
            if modal and modal.get("type") == "unifi":
                mac = (modal.get("ap") or {}).get("mac")
                cur = next((a for a in ap_items if a.get("mac") == mac), None)
                if cur:
                    modal["ap"] = cur
                    modal["clients"] = [c for c in (uni.get("clients") or [])
                                        if c.get("ap_mac") == mac]
            if modal and modal.get("type") == "deconz":
                modal["data"] = dc or modal.get("data")
            if modal and modal.get("type") == "homematic":
                modal["data"] = hm or modal.get("data")
            if modal and modal.get("type") == "pve_node":
                ni = modal.get("node_idx", 0)
                nodes = (prox or {}).get("nodes") or []
                if 0 <= ni < len(nodes):
                    modal["node"] = nodes[ni]
            if modal and modal.get("type") == "fritz":
                modal["data"] = fb or modal.get("data")

            # Offenes AP-Modal mit frischen Daten versorgen
            if modal and modal.get("type") == "unifi":
                mac = (modal.get("ap") or {}).get("mac")
                cur = next((a for a in ap_items if a.get("mac") == mac), None)
                if cur:
                    modal["ap"] = cur
                    modal["clients"] = [c for c in (uni.get("clients") or [])
                                        if c.get("ap_mac") == mac]

            try:
                w, h = os.get_terminal_size()
            except Exception:
                w, h = 100, 30
            w = max(w, 60)
            h = max(h, 16)

            if msg and time.time() > msg_until:
                msg = None

            rows = render(w, h, state, prox, speed, uni, dc, frame,
                          active_panel, sel_dev, sel_guest, sel_ap, sel_dc,
                          msg, modal, sel_node, sel_kind, fb=fb, hm=hm)
            safe_write(ESC + "[H" + "\n".join(rows) + RST)
            frame += 1

            # Warten auf Input (0.35s Timeout fuer Animation)
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue

            try:
                data = os.read(fd, 128)
            except OSError:
                time.sleep(0.05)
                continue
            if not data:
                continue

            # Maus: Klick auf eine AP-Zeile oeffnet das Geraete-Modal
            m = MOUSE_RE.search(data)
            if m:
                if not modal:
                    btn = int(m.group(1))
                    mx = int(m.group(2)) - 1
                    my = int(m.group(3)) - 1
                    kind = m.group(4)
                    if kind == b"M" and btn == 0:
                        hit = False
                        for yy, dx0, dx1, di in LAST_LAYOUT.get("dev_hits", []):
                            if yy == my and dx0 <= mx <= dx1:
                                m_dev = open_device_modal(dev_items, state, di)
                                if m_dev:
                                    active_panel = 0
                                    sel_dev = di
                                    modal = m_dev
                                    hit = True
                                break
                        if my == LAST_LAYOUT.get("fritz_hit"):
                            modal = {"type": "fritz", "data": fb}
                            hit = True
                        x0 = LAST_LAYOUT.get("x0", 0)
                        x1 = LAST_LAYOUT.get("x1", -1)
                        if x0 <= mx <= x1:
                            for yy, pkind, idx in LAST_LAYOUT.get("pve_hits", []):
                                if yy != my:
                                    continue
                                active_panel = 1
                                if pkind == "node":
                                    sel_node = idx
                                    sel_kind = "node"
                                    modal = open_node_modal(prox, idx)
                                elif 0 <= idx < len(guest_items):
                                    sel_guest = idx
                                    sel_kind = "guest"
                                    ni, g = guest_items[idx]
                                    node = prox["nodes"][ni]
                                    modal = {
                                        "type": "proxmox",
                                        "guest": g,
                                        "node_name": node["node"]["name"],
                                        "node_idx": ni,
                                        "node_label": node.get("label", ""),
                                        "result": None,
                                    }
                                hit = bool(modal)
                                break
                        if not hit:
                            for yy, ai in LAST_LAYOUT.get("ap_hits", []):
                                if (yy == my
                                        and x0 <= mx <= x1):
                                    active_panel = 2
                                    sel_ap = ai
                                    modal = open_unifi_modal(uni, ai)
                                    hit = True
                                    break
                        if (not hit and dc
                                and LAST_LAYOUT.get("dc_hit") == my
                                and x0 <= mx <= x1):
                            active_panel = 3
                            sel_dc = 0
                            modal = open_deconz_modal(dc)
                            hit = True
                        if (not hit and hm
                                and LAST_LAYOUT.get("hm_hit") == my
                                and x0 <= mx <= x1):
                            active_panel = 4
                            modal = open_homematic_modal(hm)
                data = MOUSE_RE.sub(b"", data)
                if not data:
                    continue

            inp = handle_input(data, active_panel, sel_dev, sel_guest, sel_ap,
                               sel_dc, len(dev_items), len(guest_items),
                               len(ap_items), 1 if has_dc else 0, modal,
                               sel_node, sel_kind, 1 if has_hm else 0)

            if inp["quit"]:
                break

            active_panel = inp["active_panel"]
            sel_dev = inp["sel_dev"]
            sel_guest = inp["sel_guest"]
            sel_ap = inp["sel_ap"]
            sel_dc = inp["sel_dc"]
            sel_node = inp["sel_node"]
            sel_kind = inp["sel_kind"]

            # Modal oeffnen
            if inp["open_modal"] and not modal:
                if active_panel == 4 and hm:
                    modal = open_homematic_modal(hm)
                elif active_panel == 3 and dc:
                    modal = open_deconz_modal(dc)
                elif active_panel == 2 and ap_items:
                    modal = open_unifi_modal(uni, sel_ap)
                elif active_panel == 1 and sel_kind == "node":
                    modal = open_node_modal(prox, sel_node)
                elif active_panel == 1 and guest_items:
                    ni, g = guest_items[sel_guest]
                    node = prox["nodes"][ni]
                    modal = {
                        "type": "proxmox",
                        "guest": g,
                        "node_name": node["node"]["name"],
                        "node_idx": ni,
                        "node_label": node.get("label", ""),
                        "result": None,
                    }
                elif active_panel == 0 and dev_items:
                    modal = open_device_modal(dev_items, state, sel_dev)

            # Formular abbrechen
            if inp["form_cancel"]:
                modal = None

            # Formular absenden
            if inp["form_submit"] and modal and modal.get("type") == "form":
                err, okmsg = apply_form(modal)
                if err:
                    modal["error"] = err
                else:
                    CFG = load(CONFIG) or CFG
                    modal = None
                    msg = okmsg
                    msg_until = time.time() + 3

            # Bestaetigung (Loeschen)
            if inp["confirm_yes"] and modal and modal.get("type") == "confirm":
                ok = delete_device(modal["gid"], modal["name"])
                CFG = load(CONFIG) or CFG
                msg = "{} geloescht".format(modal["name"]) if ok else "Loeschen fehlgeschlagen"
                msg_until = time.time() + 3
                modal = None

            # Modal schliessen
            if inp["close_modal"]:
                modal = None

            # Aktion im Modal
            if inp["modal_action"] and modal:
                mact = inp["modal_action"]
                if modal["type"] == "proxmox":
                    res = guest_action(
                        modal["node_idx"], modal["node_name"],
                        modal["guest"]["vmid"], modal["guest"]["type"], mact)
                    modal["result"] = res
                    # Proxmox-Poller triggern damit Status frisch wird
                    subprocess.Popen([sys.executable, os.path.join(HERE, "proxmox.py")],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif modal["type"] == "pve_node":
                    if mact in ("shutdown_node", "reboot_node"):
                        modal["pending"] = ("shutdown" if mact == "shutdown_node"
                                            else "reboot")
                        modal["result"] = None
                    elif mact == "confirm_node":
                        action = modal.get("pending")
                        modal["pending"] = None
                        if action:
                            nd = ((modal.get("node") or {}).get("node") or {})
                            res = node_action(modal["node_idx"],
                                              nd.get("name") or "", action)
                            modal["result"] = res
                            subprocess.Popen(
                                [sys.executable, os.path.join(HERE, "proxmox.py")],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                    elif mact == "cancel_node":
                        modal["pending"] = None
                elif modal["type"] == "device" and mact == "edit":
                    form = open_edit_form(modal["gid"], modal["dev"]["name"])
                    if form:
                        modal = form
                elif modal["type"] == "device" and mact == "ping":
                    subprocess.Popen([sys.executable, os.path.join(HERE, "checker.py")],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    modal["result"] = "Ping gestartet..."
                elif modal["type"] == "device" and mact == "pause":
                    gid, nm = modal["gid"], modal["dev"]["name"]
                    newp = not modal["dev"].get("paused")
                    if set_paused(gid, nm, newp):
                        CFG = load(CONFIG) or CFG
                        msg = "{} {}".format(nm, "pausiert" if newp else "wieder aktiv")
                        modal = None
                    msg_until = time.time() + 3
                elif modal["type"] == "device" and mact == "notify":
                    gid, nm = modal["gid"], modal["dev"]["name"]
                    newmode = next_notify(modal["dev"].get("notify"))
                    if set_notify(gid, nm, newmode):
                        CFG = load(CONFIG) or CFG
                        modal["dev"] = find_device(gid, nm) or modal["dev"]
                        modal["result"] = "Meldung: {}".format(NOTIFY_LABELS[newmode])
                elif modal["type"] == "device" and mact == "delete":
                    modal = {
                        "type": "confirm",
                        "gid": modal["gid"],
                        "name": modal["dev"]["name"],
                        "title": "GERAET LOESCHEN",
                        "text": [modal["dev"]["name"],
                                 "aus Gruppe '{}' entfernen?".format(modal["label"])],
                    }

            # Aktionen ausserhalb des Modals
            act = inp["action"]
            if act == "ping":
                subprocess.Popen([sys.executable, os.path.join(HERE, "checker.py")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = "Ping-Check gestartet..."
                msg_until = time.time() + 2
            elif act == "prox":
                subprocess.Popen([sys.executable, os.path.join(HERE, "proxmox.py")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = "Proxmox-Abfrage gestartet..."
                msg_until = time.time() + 2
            elif act == "add_device":
                gid0 = None
                if active_panel == 0 and dev_items:
                    gid0 = dev_items[sel_dev][0]
                modal = open_add_form(gid0)
            elif act == "config":
                modal = open_config_form()
            elif act == "fritz":
                modal = {"type": "fritz", "data": fb}
            elif act == "homematic":
                modal = open_homematic_modal(hm)
            elif act == "delete_device" and active_panel == 0 and dev_items:
                gid, lbl, d = dev_items[sel_dev]
                modal = {
                    "type": "confirm",
                    "gid": gid,
                    "name": d["name"],
                    "title": "GERAET LOESCHEN",
                    "text": [d["name"], "aus Gruppe '{}' entfernen?".format(lbl)],
                }
            elif act == "toggle_pause" and active_panel == 0 and dev_items:
                gid, lbl, d = dev_items[sel_dev]
                newp = not d.get("paused")
                if set_paused(gid, d["name"], newp):
                    CFG = load(CONFIG) or CFG
                    msg = "{} {}".format(d["name"], "pausiert" if newp else "wieder aktiv")
                    msg_until = time.time() + 3
            elif (act == "quick_start" and guest_items and active_panel == 1
                  and sel_kind == "guest"):
                ni, g = guest_items[sel_guest]
                node_name = prox["nodes"][ni]["node"]["name"]
                msg = guest_action(ni, node_name, g["vmid"], g["type"], "start")
                msg_until = time.time() + 3
            elif (act == "quick_stop" and guest_items and active_panel == 1
                  and sel_kind == "guest"):
                ni, g = guest_items[sel_guest]
                node_name = prox["nodes"][ni]["node"]["name"]
                msg = guest_action(ni, node_name, g["vmid"], g["type"], "shutdown")
                msg_until = time.time() + 3

    finally:
        if is_tty and old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, old)
            except Exception:
                pass
        sys.stdout.write(mouse_off + ESC + "[?25h" + RST)
        sys.stdout.write(ESC + "[2J" + ESC + "[H")
        sys.stdout.flush()
        print("Dashboard beendet. Neustart mit: dash")
        _release_instance_lock(lock_fd)


if __name__ == "__main__":
    main()