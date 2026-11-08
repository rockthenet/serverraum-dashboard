#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serverraum Dashboard - Clevo M815P
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
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
PROX = os.path.join(HERE, "proxmox.json")
SPEED = os.path.join(HERE, "speed.json")
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
    if sec <= 0:
        return "-"
    d = sec // 86400
    h = (sec % 86400) // 3600
    if d > 0:
        return "{}d{}h".format(d, h)
    m = (sec % 3600) // 60
    return "{}h{}m".format(h, m)


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

    ok = st.get("ping_ok", False)
    if ok:
        stat_txt = CLR["green"] + "ONLINE ({:.1f} ms)".format(st.get("rtt_ms", 0)) + RST
    else:
        stat_txt = CLR["red"] + "DOWN / nicht erreichbar" + RST
    lines.append(mline("Status:  {}".format(stat_txt)))

    http_p = dev.get("http") or dev.get("port")
    if http_p:
        http_ok = st.get("http_ok", False)
        code = st.get("http_code", 0)
        hstatus = (CLR["green"] + "HTTP {}".format(code) if http_ok else CLR["red"] + "Timeout/Fehler") + RST
        lines.append(mline("HTTP:    Port {} ({})".format(http_p, hstatus)))

    lines.append(msep())
    lines.append(mline(CLR["cyan"] + BOLD + "AKTIONEN:" + RST))
    lines.append(mline("  " + CLR["cyan"] + "[r] Ping jetzt wiederholen" + RST))

    lines.append(msep())
    if res:
        lines.append(mline(CLR["orange"] + BOLD + res + RST))
    else:
        lines.append(mline(CLR["gray"] + "[ESC] oder [q] zum Schliessen" + RST))

    lines.append(mbg + MBOX["bl"] + MBOX["h"] * (mw - 2) + MBOX["br"] + RST)
    return lines


# ═══════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════

def render(w, h, state, prox, speed, frame, active_panel, sel_dev, sel_guest, msg, modal):
    """Baut den kompletten Screen (inkl. evtl. Modal-Overlay)."""
    rows = []
    devs = (state or {}).get("devices", {})
    online = (state or {}).get("online", 0)
    total = (state or {}).get("total", 0)
    lc = (state or {}).get("last_check", 0)
    iok = (state or {}).get("internet", False)
    poll = 15
    ago = int(time.time() - lc) if lc else 999

    spin = SPIN[frame % len(SPIN)]

    # ─── Row 0: Titelleiste ───
    title = " SERVERRAUM DASHBOARD  {}  Clevo M815P / Atom N450 / Debian 10 ".format(spin)
    clock = time.strftime("%H:%M:%S ")
    left = title
    right = clock
    mid = max(0, w - vlen(left) - vlen(right))
    rows.append(CLR["titlebg"] + BOLD + left + " " * mid + right + RST)

    # ─── Row 1: Statuszeile ───
    if iok:
        inet = CLR["green"] + "\u25cf INTERNET ONLINE" + RST
    else:
        inet = CLR["red"] + BOLD + "\u25cf INTERNET OFFLINE!" + RST
    oc = CLR["green"] if online == total else (CLR["yellow"] if online else CLR["red"])
    dev_txt = oc + "{}/{} Geraete".format(online, total) + RST
    if speed and speed.get("ok"):
        sp = "{}\u2193{:.1f}{} / {}\u2191{:.1f}{} Mbps".format(
            CLR["cyan"], speed["download_mbps"], RST,
            CLR["cyan"], speed["upload_mbps"], RST)
    else:
        sp = CLR["dark"] + "Speedtest..." + RST
    cd = 1.0 - (ago / poll if poll else 0)
    cdbar = bar(cd, 10, CLR["blue"])
    stat = " {}   {}   {}   next {}".format(inet, dev_txt, sp, cdbar)
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
    else:
        l_hdr = " GERAETE "
        r_hdr = " PROXMOX CLUSTER [AKTIV] "
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
    left_lines = build_device_lines(devs, state, LW, sel_dev if active_panel == 0 else -1)
    right_lines = build_proxmox_lines(prox, RW, sel_guest if active_panel == 1 else -1)

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
    elif active_panel == 0:
        foot = " Tab=Proxmox  \u2191\u2193=Waehlen  Enter=Details  r=Ping  q=Beenden"
    else:
        foot = " Tab=Geraete  \u2191\u2193=Waehlen  Enter=Aktions-Modal  s=Start  x=Stop  p=Refresh  q=Beenden"
    rows.append(CLR["footbg"] + padto(foot, w) + RST)

    while len(rows) < h:
        rows.append(padto("", w))
    rows = rows[:h]

    # ─── Lightbox Modal Overlay (wenn aktiv) ───
    if modal:
        mw = min(56, w - 4)
        if modal["type"] == "proxmox":
            mlines = build_proxmox_modal(modal, mw)
        else:
            mlines = build_device_modal(modal, mw)
        mh = len(mlines)
        sx = (w - mw) // 2
        sy = max(2, (h - mh) // 2)

        for ri, mline in enumerate(mlines):
            y = sy + ri
            if 0 <= y < len(rows):
                rows[y] = splice_row(rows[y], mline, sx, mw)

    return rows


def build_device_lines(devs, state, w, sel_dev):
    """Geraetegruppen in Sub-Spalten fuer die linke Zone mit Highlight."""
    cfg = CFG
    groups = cfg.get("groups", {})
    if w >= 96:
        nc = 3
    elif w >= 60:
        nc = 2
    else:
        nc = 1
    gap = 1
    cw = (w - gap * (nc - 1)) // nc

    cols = [[] for _ in range(nc)]
    ht = [0] * nc
    global_di = 0

    for gid in sorted(groups):
        grp = groups[gid]
        dv = grp.get("devices", [])
        if not dv:
            continue
        gn = sum(1 for d in dv if devs.get("{}|{}".format(gid, d["name"]), {}).get("ping_ok"))
        gt = len(dv)
        gc = CLR["green"] if gn == gt else (CLR["yellow"] if gn else CLR["red"])
        block = []
        label = grp.get("label", gid)
        block.append(gc + BOLD + "\u25b8 " + label + RST + gc + " [{}/{}]".format(gn, gt) + RST)
        for d in dv:
            rec = devs.get("{}|{}".format(gid, d["name"]), {})
            ok = rec.get("ping_ok", False)
            rt = rec.get("rtt_ms", 0)
            if ok and rt < 20:
                ic = CLR["green"] + "\u25cf" + RST
            elif ok and rt < 100:
                ic = CLR["yellow"] + "\u25c9" + RST
            elif ok:
                ic = CLR["orange"] + "\u25ce" + RST
            else:
                ic = CLR["red"] + "\u25cb" + RST
            nm = d["name"]
            rts = "{:.0f}ms".format(rt) if ok else (CLR["red"] + "DOWN" + RST)
            name_w = cw - 10
            line = " {} {} {}".format(ic, padto(nm, name_w), rts)
            if global_di == sel_dev:
                line = hl(line, cw)
            block.append(line)
            global_di += 1
        block.append("")
        ci = ht.index(min(ht))
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


def build_proxmox_lines(prox, w, sel_guest):
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
                line = "  {} {} {} {} {}".format(
                    sdot, typ, padto(name, w - 25), membar, memtxt)
            else:
                line = "  {} {} {} {}".format(
                    sdot, typ, padto(name, w - 17), CLR["dark"] + "stopped" + RST)
            if sel:
                line = hl(line, w)
            out.append(line)
            gi += 1
        out.append("")
    return out


# ═══════════════════════════════════════════════════════════════════
# PROXMOX ACTIONS
# ═══════════════════════════════════════════════════════════════════

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def guest_action(node_idx, node_name, vmid, gtype, action):
    """POST an PVE-API: start, shutdown, reboot, stop, backup."""
    nodes = CFG.get("proxmox_nodes", [])
    if node_idx >= len(nodes):
        return "Node-Index ungueltig"
    nc = nodes[node_idx]

    if action == "backup":
        url = "{}/api2/json/nodes/{}/vzdump".format(nc["url"].rstrip("/"), node_name)
        data = urllib.parse.urlencode({
            "vmid": vmid,
            "mode": "snapshot",
            "compress": "zstd",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
    else:
        kind = "qemu" if gtype == "VM" else "lxc"
        url = "{}/api2/json/nodes/{}/{}/{}/status/{}".format(
            nc["url"].rstrip("/"), node_name, kind, vmid, action)
        req = urllib.request.Request(url, data=b"", method="POST")

    req.add_header("Authorization", "PVEAPIToken=" + nc["token"])
    try:
        resp = urllib.request.urlopen(req, timeout=12, context=_CTX)
        payload = json.loads(resp.read().decode("utf-8"))
        task_id = payload.get("data", "")
        if action == "backup":
            return "Backup gestartet: {}".format(task_id[:25])
        return "{} {} -> {} OK".format(gtype, vmid, action)
    except Exception as e:
        return "Fehler: {}".format(str(e)[:45])


# ═══════════════════════════════════════════════════════════════════
# INPUT-PARSER (ROBUST & NON-BLOCKING)
# ═══════════════════════════════════════════════════════════════════

def handle_input(data, active_panel, sel_dev, sel_guest, num_devs, num_guests, modal):
    """
    Parst beliebige Terminal-Sequenzen vollstaendig und nicht-blockierend.
    Returns: dict mit 'quit', 'active_panel', 'sel_dev', 'sel_guest', 'action', 'modal_action'
    """
    res = {
        "quit": False,
        "active_panel": active_panel,
        "sel_dev": sel_dev,
        "sel_guest": sel_guest,
        "action": None,
        "modal_action": None,
        "open_modal": False,
        "close_modal": False,
    }

    max_d = max(0, num_devs - 1)
    max_g = max(0, num_guests - 1)

    i = 0
    n = len(data)
    while i < n:
        b = data[i]

        # ── ESC-Sequenzen ──
        if b == 0x1b:
            # Standalone ESC (z.B. ESC allein gedrueckt)
            if i + 1 == n:
                res["close_modal"] = True
                i += 1
                continue

            if i + 2 < n and data[i + 1] == 0x5b:  # ESC [
                code = data[i + 2]
                if code == 0x41:  # Up
                    if res["active_panel"] == 0:
                        res["sel_dev"] = max(0, res["sel_dev"] - 1)
                    else:
                        res["sel_guest"] = max(0, res["sel_guest"] - 1)
                    i += 3; continue
                elif code == 0x42:  # Down
                    if res["active_panel"] == 0:
                        res["sel_dev"] = min(max_d, res["sel_dev"] + 1)
                    else:
                        res["sel_guest"] = min(max_g, res["sel_guest"] + 1)
                    i += 3; continue
                elif code == 0x44:  # Left
                    res["active_panel"] = 0
                    i += 3; continue
                elif code == 0x43:  # Right
                    res["active_panel"] = 1
                    i += 3; continue
                else:
                    i += 3; continue
            else:
                res["close_modal"] = True
                i += 1
                continue

        # ── Tab ──
        if b == 0x09:
            res["active_panel"] = 1 - res["active_panel"]
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
                if c in ("r", "R"): res["modal_action"] = "ping"
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
        elif c in ("j",):
            if res["active_panel"] == 0:
                res["sel_dev"] = min(max_d, res["sel_dev"] + 1)
            else:
                res["sel_guest"] = min(max_g, res["sel_guest"] + 1)
        elif c in ("k",):
            if res["active_panel"] == 0:
                res["sel_dev"] = max(0, res["sel_dev"] - 1)
            else:
                res["sel_guest"] = max(0, res["sel_guest"] - 1)
        elif c in ("h",):
            res["active_panel"] = 0
        elif c in ("l",):
            res["active_panel"] = 1
        elif c in ("s", "S"):
            res["action"] = "quick_start"
        elif c in ("x", "X"):
            res["action"] = "quick_stop"

        i += 1

    return res


# ═══════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════

CFG = {}


def main():
    global CFG
    os.environ.setdefault("TERM", "linux")
    CFG = load(CONFIG) or {"groups": {}}

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)

    frame = 0
    active_panel = 1  # Standard: Proxmox
    sel_dev = 0
    sel_guest = 0
    msg = None
    msg_until = 0
    modal = None

    sys.stdout.write(ESC + "[?25l")
    try:
        tty.setcbreak(fd)
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

        while True:
            state = load(STATE)
            prox = load(PROX)
            speed = load(SPEED)

            dev_items = flat_devices(CFG)
            guest_items = flat_guests(prox)

            if sel_dev >= len(dev_items):
                sel_dev = max(0, len(dev_items) - 1)
            if sel_guest >= len(guest_items):
                sel_guest = max(0, len(guest_items) - 1)

            try:
                w, h = os.get_terminal_size()
            except Exception:
                w, h = 100, 30
            w = max(w, 60)
            h = max(h, 16)

            if msg and time.time() > msg_until:
                msg = None

            rows = render(w, h, state, prox, speed, frame,
                          active_panel, sel_dev, sel_guest, msg, modal)
            sys.stdout.write(ESC + "[H")
            sys.stdout.write("\n".join(rows))
            sys.stdout.write(RST)
            sys.stdout.flush()
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

            inp = handle_input(data, active_panel, sel_dev, sel_guest,
                               len(dev_items), len(guest_items), modal)

            if inp["quit"]:
                break

            active_panel = inp["active_panel"]
            sel_dev = inp["sel_dev"]
            sel_guest = inp["sel_guest"]

            # Modal oeffnen
            if inp["open_modal"] and not modal:
                if active_panel == 1 and guest_items:
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
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
            termios.tcsetattr(fd, termios.TCSANOW, old)
        except Exception:
            pass
        sys.stdout.write(ESC + "[?25h" + RST)
        sys.stdout.write(ESC + "[2J" + ESC + "[H")
        sys.stdout.flush()
        print("Dashboard beendet. Neustart mit: dash")


if __name__ == "__main__":
    main()