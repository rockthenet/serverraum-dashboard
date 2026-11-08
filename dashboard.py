#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serverraum Dashboard - Clevo M815P
Clean rewrite: ANSI-aware Rendering, Proxmox-Panel, Animationen,
Instant-Keys (termios), Maus-Support (SSH/xterm).

Datenquellen (vom checker_loop geschrieben):
  state.json    - Ping-Status aller Geraete
  proxmox.json  - PVE Nodes + VMs/CTs
  speed.json    - Letzter Speedtest

Steuerung:
  q         Beenden
  r         Sofort-Refresh (Ping)
  p         Proxmox sofort neu abfragen
  Pfeil/jk  Guest auswaehlen (Proxmox)
  s         Selektierten Guest STARTEN
  x         Selektierten Guest HERUNTERFAHREN
  Maus      Guest anklicken (SSH/xterm)
"""
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

# Farbpalette (256-color)
CLR = {
    "green":  fg(40),
    "red":    fg(196),
    "yellow": fg(220),
    "orange": fg(208),
    "cyan":   fg(45),
    "blue":   fg(39),
    "gray":   fg(244),
    "white":  fg(255),
    "dark":   fg(240),
    "titlebg": bg(24) + fg(231),
    "footbg":  bg(236) + fg(231),
    "selbg":   bg(28) + fg(231),
}

BOX = {
    "h": "\u2500", "v": "\u2502",
    "tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
    "lt": "\u251c", "rt": "\u2524", "tt": "\u252c", "bt": "\u2534",
    "x": "\u253c",
}
SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
SPIN = ["\u280b", "\u2819", "\u2838", "\u2834", "\u2826", "\u2807"]  # braille spinner

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
    # restliche ANSI-Resets anhaengen
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


def bar(frac, width, color):
    """Fortschrittsbalken. frac 0..1."""
    frac = max(0.0, min(1.0, frac))
    fill = int(round(frac * width))
    return color + "\u2588" * fill + CLR["dark"] + "\u2591" * (width - fill) + RST


def human_bytes(b):
    """Bytes -> MB/GB string."""
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
    """Liste aller Guests fuer Navigation: (node_idx, guest)."""
    items = []
    if not prox:
        return items
    for ni, node in enumerate(prox.get("nodes", [])):
        for g in node.get("guests", []):
            items.append((ni, g))
    return items


# ═══════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════

def render(w, h, state, prox, speed, frame, sel_guest, msg):
    """Baut kompletten Screen als Liste von h Zeilen (je w sichtbar)."""
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
    mid = w - vlen(left) - vlen(right)
    if mid < 0:
        mid = 0
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
    # Countdown-Bar
    cd = 1.0 - (ago / poll if poll else 0)
    cdbar = bar(cd, 10, CLR["blue"])
    stat = " {}   {}   {}   next {}".format(inet, dev_txt, sp, cdbar)
    rows.append(padto(stat, w))

    # ─── Row 2: obere Trennlinie mit Spaltenteiler ───
    LW = int(w * 0.58)
    RW = w - LW - 3
    rows.append(CLR["cyan"] + BOX["tl"] + BOX["h"] * LW + BOX["tt"] + BOX["h"] * RW + BOX["tr"] + RST)

    # ─── Body ───
    # Zeilen: 0 title, 1 stat, 2 topborder, body..., botborder, footer
    body_h = h - 5
    if body_h < 1:
        body_h = 1

    left_lines = build_device_lines(devs, state, LW, frame)
    right_lines = build_proxmox_lines(prox, RW, sel_guest, frame)

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
    else:
        foot = " q=Quit  r=Ping  p=Proxmox  \u2191\u2193=Guest  s=Start  x=Stop  |  Check vor {}s".format(ago)
    rows.append(CLR["footbg"] + padto(foot, w) + RST)

    # exakt h Zeilen
    while len(rows) < h:
        rows.append(padto("", w))
    return rows[:h]


def build_device_lines(devs, state, w, frame):
    """Geraetegruppen in Sub-Spalten fuer die linke Zone."""
    cfg = CFG
    groups = cfg.get("groups", {})
    # Sub-Spalten
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
            block.append(line)
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


def build_proxmox_lines(prox, w, sel_guest, frame):
    """Proxmox-Panel: Nodes + Guests mit RAM-Bars."""
    out = []
    if not prox or not prox.get("nodes"):
        out.append(CLR["dark"] + " Proxmox: keine Daten" + RST)
        return out

    gi = 0  # globaler Guest-Index fuer Selektion
    for node in prox["nodes"]:
        label = node.get("label", "PVE")
        if not node.get("online"):
            out.append(CLR["red"] + BOLD + "\u2b24 " + label + " OFFLINE" + RST)
            out.append("")
            # trotzdem Guests zaehlen
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
            if sel:
                # Saubere Highlight-Zeile (plain text, voller Hintergrund)
                if running:
                    memtxt = "{}/{}".format(human_bytes(g["mem"]), human_bytes(g["maxmem"]))
                    plain = "\u25b8 {} {} {} {:>5.0f}% {}".format(
                        "\u25b6" if running else "\u25a0", g["type"], name, memf * 100, memtxt)
                else:
                    plain = "\u25b8 \u25a0 {} {}  stopped".format(g["type"], name)
                line = CLR["selbg"] + BOLD + padto(plain, w) + RST
            elif running:
                memtxt = "{}/{}".format(human_bytes(g["mem"]), human_bytes(g["maxmem"]))
                membar = bar(memf, 6, CLR["green"] if memf < 0.85 else CLR["orange"])
                line = "  {} {} {} {} {}".format(
                    sdot, typ, padto(name, w - 25), membar, memtxt)
            else:
                line = "  {} {} {} {}".format(
                    sdot, typ, padto(name, w - 17), CLR["dark"] + "stopped" + RST)
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
    """POST start/shutdown an die PVE-API. gtype VM|CT."""
    nodes = CFG.get("proxmox_nodes", [])
    if node_idx >= len(nodes):
        return "Node-Index ungueltig"
    nc = nodes[node_idx]
    kind = "qemu" if gtype == "VM" else "lxc"
    url = "{}/api2/json/nodes/{}/{}/{}/status/{}".format(
        nc["url"].rstrip("/"), node_name, kind, vmid, action)
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Authorization", "PVEAPIToken=" + nc["token"])
    try:
        urllib.request.urlopen(req, timeout=10, context=_CTX)
        return "{} {} {} -> {} OK".format(gtype, vmid, node_name, action)
    except Exception as e:
        return "Fehler: {}".format(str(e)[:40])


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
    frame = 0
    sel_guest = 0
    msg = None
    msg_until = 0

    # Maus-Tracking an (xterm/SSH)
    sys.stdout.write(ESC + "[?1000h" + ESC + "[?25l")
    try:
        tty.setcbreak(fd)
        while True:
            state = load(STATE)
            prox = load(PROX)
            speed = load(SPEED)
            guests = flat_guests(prox)
            if sel_guest >= len(guests):
                sel_guest = max(0, len(guests) - 1)

            try:
                w, h = os.get_terminal_size()
            except Exception:
                w, h = 100, 30
            w = max(w, 60)
            h = max(h, 16)

            if msg and time.time() > msg_until:
                msg = None

            rows = render(w, h, state, prox, speed, frame, sel_guest, msg)
            sys.stdout.write(ESC + "[H")
            sys.stdout.write("\n".join(rows))
            sys.stdout.write(RST)
            sys.stdout.flush()
            frame += 1

            r, _, _ = select.select([sys.stdin], [], [], 0.4)
            if not r:
                continue
            ch = sys.stdin.read(1)

            if ch in ("q", "Q"):
                break
            elif ch in ("r", "R"):
                subprocess.Popen([sys.executable, os.path.join(HERE, "checker.py")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = "Ping-Check gestartet..."
                msg_until = time.time() + 2
            elif ch in ("p", "P"):
                subprocess.Popen([sys.executable, os.path.join(HERE, "proxmox.py")],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = "Proxmox-Abfrage gestartet..."
                msg_until = time.time() + 2
            elif ch in ("j",):
                sel_guest = min(sel_guest + 1, max(0, len(guests) - 1))
            elif ch in ("k",):
                sel_guest = max(sel_guest - 1, 0)
            elif ch in ("s", "S"):
                if guests:
                    ni, g = guests[sel_guest]
                    node_name = prox["nodes"][ni]["node"]["name"]
                    msg = guest_action(ni, node_name, g["vmid"], g["type"], "start")
                    msg_until = time.time() + 3
            elif ch in ("x", "X"):
                if guests:
                    ni, g = guests[sel_guest]
                    node_name = prox["nodes"][ni]["node"]["name"]
                    msg = guest_action(ni, node_name, g["vmid"], g["type"], "shutdown")
                    msg_until = time.time() + 3
            elif ch == ESC:
                # Pfeiltasten oder Maus
                seq = sys.stdin.read(1)
                if seq == "[":
                    code = sys.stdin.read(1)
                    if code == "A":  # up
                        sel_guest = max(sel_guest - 1, 0)
                    elif code == "B":  # down
                        sel_guest = min(sel_guest + 1, max(0, len(guests) - 1))
                    elif code == "M":  # Maus
                        b = ord(sys.stdin.read(1)) - 32
                        mx = ord(sys.stdin.read(1)) - 32
                        my = ord(sys.stdin.read(1)) - 32
                        # Klick auf rechtes Panel -> Guest waehlen (grobe Heuristik)
                        # Header Proxmox beginnt ca. bei body-Zeile; wir mappen linear
                        idx = guest_row_from_y(prox, my)
                        if idx is not None and idx < len(guests):
                            sel_guest = idx
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        sys.stdout.write(ESC + "[?1000l" + ESC + "[?25h" + RST)
        sys.stdout.write(ESC + "[2J" + ESC + "[H")
        sys.stdout.flush()
        print("Dashboard beendet.")


def guest_row_from_y(prox, my):
    """Grobe Zuordnung Mausklick-Zeile -> Guest-Index (optional)."""
    # Body startet bei Zeile 3 (0-basiert). Rechtes Panel-Offset je Node: 3 Headerzeilen.
    if not prox:
        return None
    body_y = my - 3
    if body_y < 0:
        return None
    gi = 0
    cur = 0
    for node in prox.get("nodes", []):
        cur += 3  # node header lines
        for _ in node.get("guests", []):
            if cur == body_y:
                return gi
            gi += 1
            cur += 1
        cur += 1  # blank line
    return None


if __name__ == "__main__":
    main()