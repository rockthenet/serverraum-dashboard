#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimaler HTTP-Webview fuer das Serverraum-Dashboard.

Rendert dieselbe TUI-Ansicht als HTML (nur lesend, ohne Eingaben).
Nutzt ausschliesslich die Python-Standardbibliothek, belegt keinen
interaktiven Dashboard-Platz und laeuft als eigener kleiner Dienst.
"""
import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import dashboard

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")

FILES = {
    "state": "state.json",
    "prox": "proxmox.json",
    "speed": "speed.json",
    "uni": "unifi.json",
    "dc": "deconz.json",
    "fb": "fritzbox.json",
    "hm": "homematic.json",
    "th": "thread.json",
}

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
BASE16 = [
    "#000000", "#800000", "#008000", "#808000",
    "#000080", "#800080", "#008080", "#c0c0c0",
    "#808080", "#ff0000", "#00ff00", "#ffff00",
    "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
]


def _xterm(index):
    """256-Farben-Palette nach RGB."""
    if index < 16:
        return BASE16[index]
    if index < 232:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        return "#{:02x}{:02x}{:02x}".format(
            levels[index // 36], levels[(index % 36) // 6], levels[index % 6])
    value = 8 + (index - 232) * 10
    return "#{:02x}{:02x}{:02x}".format(value, value, value)


def _apply_sgr(state, params):
    if not params:
        params = [0]
    i = 0
    while i < len(params):
        p = params[i]
        if p == 0:
            state.update({"fg": None, "bg": None, "bold": False,
                          "reverse": False})
        elif p == 1:
            state["bold"] = True
        elif p == 22:
            state["bold"] = False
        elif p == 7:
            state["reverse"] = True
        elif p == 27:
            state["reverse"] = False
        elif 30 <= p <= 37:
            state["fg"] = BASE16[p - 30]
        elif 90 <= p <= 97:
            state["fg"] = BASE16[p - 90 + 8]
        elif 40 <= p <= 47:
            state["bg"] = BASE16[p - 40]
        elif 39 == p:
            state["fg"] = None
        elif 49 == p:
            state["bg"] = None
        elif p in (38, 48):
            mode = params[i + 1] if i + 1 < len(params) else None
            color = None
            if mode == 5 and i + 2 < len(params):
                color = _xterm(params[i + 2])
                i += 2
            elif mode == 2 and i + 4 < len(params):
                color = "#{:02x}{:02x}{:02x}".format(
                    params[i + 2], params[i + 3], params[i + 4])
                i += 4
            if p == 38:
                state["fg"] = color
            else:
                state["bg"] = color
        i += 1


def _span(text, state):
    if not text:
        return ""
    fg = state.get("fg")
    bg = state.get("bg")
    if state.get("reverse"):
        fg, bg = bg or "#111111", fg or "#dddddd"
    style = []
    if fg:
        style.append("color:" + fg)
    if bg:
        style.append("background:" + bg)
    if state.get("bold"):
        style.append("font-weight:bold")
    body = html.escape(text)
    if not style:
        return body
    return '<span style="{}">{}</span>'.format(";".join(style), body)


def ansi_to_html(line):
    """Wandelt eine ANSI-Zeile in HTML-Spans um."""
    state = {"fg": None, "bg": None, "bold": False, "reverse": False}
    out = []
    pos = 0
    for match in ANSI_RE.finditer(line):
        out.append(_span(line[pos:match.start()], state))
        raw = match.group(1)
        params = [int(x) for x in raw.split(";") if x != ""] if raw else []
        _apply_sgr(state, params)
        pos = match.end()
    out.append(_span(line[pos:], state))
    return "".join(out)


def _load(name):
    return dashboard.load(os.path.join(HERE, FILES[name]))


def build_html():
    cfg = dashboard.load(CONFIG) or {}
    dashboard.CFG = cfg
    width = int(cfg.get("web_width") or 137)
    height = int(cfg.get("web_height") or 38)
    width = max(80, min(240, width))
    height = max(24, min(80, height))

    state = _load("state")
    rows = dashboard.render(
        width, height, state, _load("prox"), _load("speed"), _load("uni"),
        _load("dc"), 0, 1, 0, 0, 0, 0, None, None, 0, "guest",
        fb=_load("fb"), hm=_load("hm"), th=_load("th"))
    body = "\n".join(ansi_to_html(row) for row in rows)
    stamp = time.strftime("%d.%m.%Y %H:%M:%S")
    page = (
        "<!doctype html>\n<html lang=\"de\"><head><meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta http-equiv=\"refresh\" content=\"10\">\n"
        "<title>Serverraum Dashboard</title>\n"
        "<style>\n"
        "html,body{margin:0;background:#0b0b0b;color:#dddddd;}\n"
        "body{padding:10px;font-family:\"DejaVu Sans Mono\",Consolas,monospace;}\n"
        ".hint{color:#777;font-size:11px;margin-bottom:6px;}\n"
        "pre{font-size:13px;line-height:1.16;white-space:pre;margin:0;"
        "overflow:auto;}\n"
        "</style></head><body>\n"
        "<div class=\"hint\">Serverraum-Dashboard &middot; nur Ansicht &middot; "
        "Stand __STAMP__</div>\n<pre>__BODY__</pre>\n</body></html>\n")
    return page.replace("__STAMP__", stamp).replace("__BODY__", body)


class Handler(BaseHTTPRequestHandler):
    server_version = "NetbookDashboard/1.0"

    def _send(self, code, payload, ctype):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                self._send(200, build_html(), "text/html; charset=utf-8")
            except Exception as exc:
                self._send(500, "Dashboard-Fehler: {}\n".format(
                    html.escape(str(exc))), "text/plain; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok\n", "text/plain; charset=utf-8")
        else:
            self._send(404, "nicht gefunden\n", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass


def main():
    cfg = dashboard.load(CONFIG) or {}
    host = str(cfg.get("web_bind") or "0.0.0.0")
    port = int(cfg.get("web_port") or 80)
    try:
        server = HTTPServer((host, port), Handler)
    except PermissionError:
        if port >= 1024:
            raise
        port = 8088
        server = HTTPServer((host, port), Handler)
        print("Port 80 nicht erlaubt, nutze {}.".format(port))
    print("Dashboard-Web: http://{}:{}/".format(host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
