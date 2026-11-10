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
import ssl
import subprocess
import sys
import termios
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

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
PROX = os.path.join(HERE, "proxmox.json")
SPEED = os.path.join(HERE, "speed.json")
UNI = os.path.join(HERE, "unifi.json")
DECONZ = os.path.join(HERE, "deconz.json")
CONFIG = os.path.join(HERE, "config.json")

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


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
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
           sel_dev, sel_guest, sel_ap, sel_dc, msg, modal):
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
    title_variants = [
        " SERVERRAUM DASHBOARD  {}  {}  up {} ".format(spin, HOSTINFO, up),
        " SERVERRAUM DASHBOARD  {}  {}  up {} ".format(spin, host, up),
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
    if speed and speed.get("ok"):
        sp = "{}\u2193{:.1f}{} / {}\u2191{:.1f}{} Mbps".format(
            CLR["cyan"], speed["download_mbps"], RST,
            CLR["cyan"], speed["upload_mbps"], RST)
    else:
        sp = CLR["dark"] + "Speedtest..." + RST
    cd = 1.0 - (ago / poll if poll else 0)

    stat = None
    for conn in conn_variants:
        head_txt = " {}{}   {}   {}   next ".format(inet, conn, dev_txt, sp)
        avail = w - vlen(head_txt)
        if avail >= 3:
            stat = head_txt + bar(cd, min(10, avail), CLR["blue"])
            break
    if stat is None:
        stat = " {}   {}   {}   next ".format(inet, dev_txt, sp) + bar(cd, 10, CLR["blue"])
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

    # Header-Linie
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
    prox_lines = build_proxmox_lines(prox, RW,
                                     sel_guest if active_panel == 1 else -1,
                                     prox_hits)
    right_lines = list(prox_lines)
    rest = max(0, body_h - len(right_lines))
    uni_hits = []
    uni_len = 0
    if rest > 0:
        reserve = 1 if (dc and rest >= 5) else 0
        uni_max = max(1, rest - reserve) if reserve else rest
        uni_lines = build_unifi_lines(
            uni, RW, uni_max,
            sel_ap if active_panel == 2 else -1, uni_hits)
        right_lines += uni_lines
        uni_len = len(uni_lines)
        rest = max(0, rest - uni_len)
    dc_hits = []
    if dc and rest > 0:
        right_lines += build_deconz_lines(
            dc, RW, rest, active_panel == 3, dc_hits)

    # Selektierbare Zeilen als 2D-Layout merken (Pfeilnavigation + Maus)
    nc_dev = 2 if LW >= 70 else 1
    cw_dev = (LW - (nc_dev - 1)) // nc_dev
    items = []
    for r, di, ci in dev_hits:
        items.append({"panel": 0, "idx": di, "row": 3 + r,
                      "col": 1 + ci * (cw_dev + 1) + cw_dev // 2})
    for r, gi in prox_hits:
        items.append({"panel": 1, "idx": gi, "row": 3 + r,
                      "col": LW + 2 + RW // 2})
    for off, ai in uni_hits:
        items.append({"panel": 2, "idx": ai, "row": 3 + len(prox_lines) + off,
                      "col": LW + 2 + RW // 2})
    if dc_hits:
        items.append({"panel": 3, "idx": 0,
                      "row": 3 + len(prox_lines) + uni_len + dc_hits[0],
                      "col": LW + 2 + RW // 2})

    global LAST_LAYOUT
    LAST_LAYOUT = {
        "ap_hits": [(3 + len(prox_lines) + off, ai) for off, ai in uni_hits],
        "dc_hit": ((3 + len(prox_lines) + uni_len + dc_hits[0])
                   if dc_hits else None),
        "items": items,
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
                "d=Loeschen  c=Config  r=Ping  q=Ende")
    elif active_panel == 2:
        foot = (" Tab=Geraete  \u2191\u2193=AP waehlen  Enter=Geraete am AP  "
                "c=Config  q=Ende")
    elif active_panel == 3:
        foot = (" Tab=Geraete  \u2191\u2193\u2190\u2192=frei wechseln  Enter=deCONZ-Details  "
                "c=Config  q=Ende")
    else:
        foot = " Tab=Geraete/UniFi  \u2191\u2193=Waehlen  Enter=Aktions-Modal  s=Start  x=Stop  p=Refresh  c=Config  q=Ende"
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
            else:
                mw = min(60, w - 4)
                if modal["type"] == "proxmox":
                    mlines = build_proxmox_modal(modal, mw)
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


def build_proxmox_lines(prox, w, sel_guest, hit_rows=None):
    """Proxmox-Panel: Nodes + Guests mit RAM-Bars und sauberem Highlight."""
    out = []
    if not prox or not prox.get("nodes"):
        out.append(CLR["dark"] + " Proxmox: keine Daten" + RST)
        return out

    gi = 0
    for node in prox["nodes"]:
        label = node.get("label", "PVE")
        if not node.get("online"):
            out.append(CLR["red"] + BOLD + "\u2b24 " + label + " OFFLINE" + RST)
            out.append("")
            gi += len(node.get("guests", []))
            continue
        nd = node.get("node") or {}
        cpu = nd.get("cpu", 0)
        memf = nd.get("mem", 0) / max(nd.get("maxmem", 1), 1)
        dskf = nd.get("disk", 0) / max(nd.get("maxdisk", 1), 1)
        up = human_uptime(nd.get("uptime", 0))
        head = "{}\u2b24 {}{}  {}up {}".format(CLR["green"], BOLD, label, RST, up)
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
            sel = (gi == sel_guest)
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
                hit_rows.append((len(out), gi))
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


def _unifi_ap_line(ap, w):
    name = ap.get("name") or ap.get("mac") or "AP"
    if ap.get("up"):
        dot = CLR["green"] + "\u25b2" + RST
        head = "  {} {}{}".format(
            dot, name,
            CLR["gray"] + "  up " + human_uptime(ap.get("uptime", 0)) + RST)
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
    """Kompakter deCONZ-Block: im Hauptbildschirm nur die Anzahl."""
    out = []
    if not dc:
        out.append(CLR["dark"] + " \u2b21 deCONZ: keine Daten" + RST)
        return out

    total, online, offline = _deconz_counts(dc)
    if not dc.get("ok"):
        age = human_age(time.time() - (dc.get("ts") or 0))
        line = (CLR["red"] + " \u2b21 deCONZ  nicht erreichbar" + RST
                + CLR["dark"] + " (Daten vor {})".format(age) + RST)
    else:
        if total and offline == 0:
            hc = CLR["green"]
        elif online:
            hc = CLR["yellow"]
        else:
            hc = CLR["red"]
        line = "{}\u2b21 {}{}deCONZ{}  {} Geraete".format(
            hc, BOLD, hc, RST, total)
    if sel:
        line = hl(line, w)
    if hit_rows is not None:
        hit_rows.append(0)
    out.append(line)
    return out[:max_lines] if max_lines is not None else out


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
    else:
        fw = dc.get("fwversion") or "-"
        sw = dc.get("swversion") or "-"
        ch = dc.get("zigbeechannel")
        extra = "Gateway {} \u00b7 Firmware {}".format(sw, fw)
        if ch:
            extra += " \u00b7 Kanal {}".format(ch)
        lines.append(mline(CLR["dark"] + clip(extra, mw - 4) + RST))

    lines.append(msep())
    inner = mw - 4
    num_w = 21
    label_w = max(8, inner - num_w - 1)
    hdr = (padto("TYP", label_w)
           + "{:>5} {:>7} {:>7}".format("GES", "ONLINE", "OFFLINE"))
    lines.append(mline(CLR["cyan"] + BOLD + hdr + RST))

    budget = None
    if max_rows is not None:
        # Platz fuer Rahmen, Fusszeile und ggf. "+N weitere Typen"
        budget = max(0, max_rows - len(lines) - 4)
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


# ═══════════════════════════════════════════════════════════════════
# INPUT-PARSER (ROBUST & NON-BLOCKING)
# ═══════════════════════════════════════════════════════════════════

def _selection(panel, sel_dev, sel_guest, sel_ap, sel_dc):
    return {0: sel_dev, 1: sel_guest, 2: sel_ap, 3: sel_dc}.get(panel, 0)


def spatial_target(panel, sel_dev, sel_guest, sel_ap, sel_dc, key):
    """Naechstes selektierbares Element in Pfeilrichtung (2D, frei)."""
    items = LAST_LAYOUT.get("items") or []
    if not items:
        return None
    cur = None
    idx = _selection(panel, sel_dev, sel_guest, sel_ap, sel_dc)
    for it in items:
        if it["panel"] == panel and it["idx"] == idx:
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
                 num_devs, num_guests, num_aps, num_dc, modal):
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
                             res["sel_guest"], res["sel_ap"], res["sel_dc"], key)
        if not tgt:
            return
        res["active_panel"] = tgt["panel"]
        if tgt["panel"] == 0:
            res["sel_dev"] = tgt["idx"]
        elif tgt["panel"] == 1:
            res["sel_guest"] = tgt["idx"]
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
                if c in ("r", "R"):
                    res["modal_action"] = "ping"
                elif c in ("d", "D"):
                    res["modal_action"] = "delete"
                elif c in ("n", "N"):
                    res["modal_action"] = "notify"
                elif b == 0x20:
                    res["modal_action"] = "pause"
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
    msg = None
    msg_until = 0
    modal = None

    mouse_on = ESC + "[?1000h" + ESC + "[?1006h"
    mouse_off = ESC + "[?1006l" + ESC + "[?1000l"
    sys.stdout.write(ESC + "[?25l" + mouse_on)
    try:
        if is_tty:
            tty.setcbreak(fd)

        while True:
            state = load(STATE)
            prox = load(PROX)
            speed = load(SPEED)
            uni = load(UNI)
            dc = load(DECONZ)

            dev_items = flat_devices(CFG)
            guest_items = flat_guests(prox)
            ap_items = (uni or {}).get("aps") or []
            has_dc = bool(dc)

            if sel_dev >= len(dev_items):
                sel_dev = max(0, len(dev_items) - 1)
            if sel_guest >= len(guest_items):
                sel_guest = max(0, len(guest_items) - 1)
            if sel_ap >= len(ap_items):
                sel_ap = max(0, len(ap_items) - 1)
            if active_panel == 2 and not ap_items:
                active_panel = 1
            if active_panel == 3 and not has_dc:
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
                          msg, modal)
            safe_write(ESC + "[H" + "\n".join(rows) + RST)
            frame += 1

            # Warten auf Input (0.35s Timeout fuer Animation)
            r, _, _ = select.select([fd], [], [], 0.35)
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
                        for yy, ai in LAST_LAYOUT.get("ap_hits", []):
                            if (yy == my
                                    and LAST_LAYOUT.get("x0", 0) <= mx
                                    <= LAST_LAYOUT.get("x1", -1)):
                                active_panel = 2
                                sel_ap = ai
                                modal = open_unifi_modal(uni, ai)
                                hit = True
                                break
                        if (not hit and dc
                                and LAST_LAYOUT.get("dc_hit") == my
                                and LAST_LAYOUT.get("x0", 0) <= mx
                                <= LAST_LAYOUT.get("x1", -1)):
                            active_panel = 3
                            sel_dc = 0
                            modal = open_deconz_modal(dc)
                data = MOUSE_RE.sub(b"", data)
                if not data:
                    continue

            inp = handle_input(data, active_panel, sel_dev, sel_guest, sel_ap,
                               sel_dc, len(dev_items), len(guest_items),
                               len(ap_items), 1 if has_dc else 0, modal)

            if inp["quit"]:
                break

            active_panel = inp["active_panel"]
            sel_dev = inp["sel_dev"]
            sel_guest = inp["sel_guest"]
            sel_ap = inp["sel_ap"]
            sel_dc = inp["sel_dc"]

            # Modal oeffnen
            if inp["open_modal"] and not modal:
                if active_panel == 3 and dc:
                    modal = open_deconz_modal(dc)
                elif active_panel == 2 and ap_items:
                    modal = open_unifi_modal(uni, sel_ap)
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
                    gid, lbl, d = dev_items[sel_dev]
                    st = (state or {}).get("devices", {}).get("{}|{}".format(gid, d["name"]))
                    modal = {
                        "type": "device",
                        "dev": d,
                        "gid": gid,
                        "label": lbl,
                        "state": st,
                        "result": None,
                    }

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
            elif act == "quick_start" and guest_items and active_panel == 1:
                ni, g = guest_items[sel_guest]
                node_name = prox["nodes"][ni]["node"]["name"]
                msg = guest_action(ni, node_name, g["vmid"], g["type"], "start")
                msg_until = time.time() + 3
            elif act == "quick_stop" and guest_items and active_panel == 1:
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


if __name__ == "__main__":
    main()