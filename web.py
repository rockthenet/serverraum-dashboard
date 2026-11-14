#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimaler HTTP-Webview fuer das Serverraum-Dashboard.

Rendert dieselbe TUI-Ansicht als HTML (nur lesend, ohne Aktionen).
Nutzt ausschliesslich die Python-Standardbibliothek, belegt keinen
interaktiven Dashboard-Platz und laeuft als eigener kleiner Dienst.

Die Seite aktualisiert sich per fetch() (kein Voll-Reload, kein Flackern)
und passt die Terminalgroesse automatisch an das Browserfenster an.

Per Maus lassen sich die Lightbox-Modale oeffnen und schliessen; die
Modal-Inhalte sind reine Anzeige (kein Start/Stop/Pause).
"""
import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

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

# Favicon: das "weisse Quadrat"-Symbol (U+1F533) als SVG-Data-URI.
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='88'>\U0001F533</text></svg>")
FAVICON = "data:image/svg+xml," + quote(FAVICON_SVG, safe="")

PAGE = (
    "<!doctype html>\n<html lang=\"de\"><head><meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>Serverraum Dashboard</title>\n"
    "<link rel=\"icon\" type=\"image/svg+xml\" href=\"__FAVICON__\">\n"
    "<noscript><meta http-equiv=\"refresh\" content=\"10\"></noscript>\n"
    "<style>\n"
    "html,body{margin:0;background:#0b0b0b;color:#dddddd;}\n"
    "body{padding:8px 10px;font-family:\"DejaVu Sans Mono\",\"Cascadia Mono\","
    "Consolas,\"Liberation Mono\",monospace;}\n"
    ".hint{display:flex;align-items:center;gap:8px;color:#777;font-size:11px;"
    "line-height:18px;height:18px;margin-bottom:4px;font-family:inherit;"
    "white-space:nowrap;overflow:hidden;}\n"
    ".hint.err{color:#ff6b6b;}\n"
    ".hint button{margin-left:auto;background:#132b33;color:#8fe3ee;"
    "border:1px solid #2b4a55;border-radius:4px;font:inherit;font-size:11px;"
    "padding:0 8px;height:18px;cursor:pointer;white-space:nowrap;}\n"
    ".hint button:hover{background:#1b3b45;}\n"
    "pre{margin:0;font-family:inherit;font-size:14px;line-height:16px;"
    "letter-spacing:0;font-variant-ligatures:none;font-kerning:none;"
    "font-synthesis:none;white-space:pre;tab-size:8;cursor:default;\n"
    "text-rendering:geometricPrecision;}\n"
    "</style></head><body data-w=\"__W__\" data-h=\"__H__\" "
    "data-auto=\"__AUTO__\">\n"
    "<div class=\"hint\" id=\"hint\"><span>Serverraum-Dashboard &middot; "
    "nur Ansicht &middot; Klick auf eine Zeile = Details &middot; "
    "<span id=\"stamp\">__STAMP__</span></span>"
    "<button id=\"fs\" type=\"button\" title=\"Vollbild\">&#9974; Vollbild"
    "</button></div>\n"
    "<pre id=\"dash\" data-hits=\"__HITS__\" data-mrect=\"__MRECT__\">"
    "__BODY__</pre>\n"
    "<script>\n"
    "(function(){\n"
    "var body=document.body,pre=document.getElementById('dash');\n"
    "var stamp=document.getElementById('stamp');\n"
    "var hint=document.getElementById('hint');\n"
    "var fs=document.getElementById('fs');\n"
    "var auto=body.getAttribute('data-auto')==='1';\n"
    "var curW=parseInt(body.getAttribute('data-w'),10);\n"
    "var curH=parseInt(body.getAttribute('data-h'),10);\n"
    "var modal='';\n"
    "try{modal=new URLSearchParams(location.search).get('m')||'';}catch(e){}\n"
    "var hits=[],mrect=null;\n"
    "function cellW(){\n"
    " var cs=getComputedStyle(pre);\n"
    " var c=document.createElement('canvas').getContext('2d');\n"
    " c.font=cs.fontStyle+' '+cs.fontWeight+' '+cs.fontSize+' '+cs.fontFamily;\n"
    " return c.measureText('MMMMMMMMMM').width/10;\n"
    "}\n"
    "function lineH(){\n"
    " var v=parseFloat(getComputedStyle(pre).lineHeight);\n"
    " return v||16;\n"
    "}\n"
    "function parseHits(s){\n"
    " hits=[];\n"
    " if(!s)return;\n"
    " s.split('|').forEach(function(e){\n"
    "  var p=e.split(',');\n"
    "  if(p.length===4)hits.push({r:+p[0],a:+p[1],b:+p[2],s:p[3]});\n"
    " });\n"
    "}\n"
    "function parseRect(s){\n"
    " if(!s)return null;\n"
    " var p=s.split(',').map(Number);\n"
    " if(p.length!==4||!(p[2]>0)||!(p[3]>0))return null;\n"
    " return p;\n"
    "}\n"
    "parseHits(pre.getAttribute('data-hits'));\n"
    "mrect=parseRect(pre.getAttribute('data-mrect'));\n"
    "function syncUrl(){\n"
    " try{\n"
    "  var u=new URL(location.href);\n"
    "  if(modal)u.searchParams.set('m',modal);\n"
    "  else u.searchParams.delete('m');\n"
    "  history.replaceState(null,'',u.toString());\n"
    " }catch(e){}\n"
    "}\n"
    "function frameUrl(){\n"
    " return '/frame?w='+curW+'&h='+curH"
    "+(modal?'&m='+encodeURIComponent(modal):'');\n"
    "}\n"
    "function target(){\n"
    " var vw=document.documentElement.clientWidth||window.innerWidth;\n"
    " var vh=document.documentElement.clientHeight||window.innerHeight;\n"
    " var w=Math.floor((vw-20)/cellW());\n"
    " var h=Math.floor((vh-40)/lineH());\n"
    " return {w:Math.max(80,Math.min(240,w)),h:Math.max(24,Math.min(80,h))};\n"
    "}\n"
    "function apply(){\n"
    " if(!auto)return;\n"
    " var t=target();\n"
    " if(t.w!==curW||t.h!==curH){\n"
    "  location.replace('/?w='+t.w+'&h='+t.h+'&auto=1'"
    "+(modal?'&m='+encodeURIComponent(modal):''));\n"
    " }\n"
    "}\n"
    "var rt;\n"
    "window.addEventListener('resize',function(){clearTimeout(rt);"
    "rt=setTimeout(apply,300);});\n"
    "window.addEventListener('load',apply);\n"
    "function setModal(s){\n"
    " s=s||'';\n"
    " if(s===modal)return;\n"
    " modal=s;\n"
    " syncUrl();\n"
    " refresh();\n"
    "}\n"
    "function refresh(){\n"
    " fetch(frameUrl(),{cache:'no-store'}).then(function(r){\n"
    "  if(!r.ok)throw new Error('HTTP '+r.status);\n"
    "  var o={s:r.headers.get('X-Stamp'),"
    "c:parseInt(r.headers.get('X-Cols'),10),"
    "r:parseInt(r.headers.get('X-Rows'),10),"
    "h:r.headers.get('X-Hits')||'',"
    "mr:r.headers.get('X-MRect')||'',"
    "has:r.headers.get('X-Modal')==='1'};\n"
    "  return r.text().then(function(t){o.t=t;return o;});\n"
    " }).then(function(o){\n"
    "  var top=pre.scrollTop;\n"
    "  pre.innerHTML=o.t;\n"
    "  pre.scrollTop=top;\n"
    "  if(o.s&&stamp)stamp.textContent=o.s;\n"
    "  if(o.c)curW=o.c; if(o.r)curH=o.r;\n"
    "  parseHits(o.h); mrect=parseRect(o.mr);\n"
    "  pre.setAttribute('data-hits',o.h);\n"
    "  pre.setAttribute('data-mrect',o.mr);\n"
    "  if(modal&&!o.has){modal='';syncUrl();}\n"
    "  hint.classList.remove('err');\n"
    " }).catch(function(){hint.classList.add('err');});\n"
    "}\n"
    "setInterval(refresh,10000);\n"
    "document.addEventListener('visibilitychange',function(){"
    "if(!document.hidden)refresh();});\n"
    "pre.addEventListener('click',function(ev){\n"
    " var box=pre.getBoundingClientRect();\n"
    " var cw=cellW(),lh=lineH();\n"
    " var col=Math.floor((ev.clientX-box.left+pre.scrollLeft)/cw);\n"
    " var row=Math.floor((ev.clientY-box.top+pre.scrollTop)/lh);\n"
    " if(mrect){\n"
    "  if(col>=mrect[0]&&col<mrect[0]+mrect[2]"
    "&&row>=mrect[1]&&row<mrect[1]+mrect[3])return;\n"
    "  setModal('');\n"
    "  return;\n"
    " }\n"
    " for(var i=0;i<hits.length;i++){\n"
    "  var h=hits[i];\n"
    "  if(h.r===row&&col>=h.a&&col<=h.b){setModal(h.s);return;}\n"
    " }\n"
    "});\n"
    "document.addEventListener('keydown',function(ev){"
    "if(ev.key==='Escape'&&modal)setModal('');});\n"
    "if(fs)fs.addEventListener('click',function(){\n"
    " if(document.fullscreenElement){document.exitFullscreen();}\n"
    " else if(document.documentElement.requestFullscreen){"
    "document.documentElement.requestFullscreen();}\n"
    "});\n"
    "document.addEventListener('fullscreenchange',function(){"
    "setTimeout(apply,300);});\n"
    "})();\n"
    "</script>\n"
    "</body></html>\n")


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


def _config():
    return dashboard.load(CONFIG) or {}


def _load(name):
    return dashboard.load(os.path.join(HERE, FILES[name]))


def _dims(cfg, qs):
    """Terminalgroesse: Query-Parameter vor config.json, mit sinnvollen Grenzen."""
    width = int(cfg.get("web_width") or 137)
    height = int(cfg.get("web_height") or 38)
    try:
        if qs.get("w"):
            width = int(qs["w"][0])
    except (TypeError, ValueError):
        pass
    try:
        if qs.get("h"):
            height = int(qs["h"][0])
    except (TypeError, ValueError):
        pass
    return max(80, min(240, width)), max(24, min(80, height))


def _auto(qs):
    """Automatische Fenstergroesse nur, wenn nichts fest vorgegeben wurde."""
    if not qs.get("w") and not qs.get("h"):
        return True
    return qs.get("auto", ["0"])[0] == "1"


def _make_modal(spec, cfg, state, prox, uni, fb, dc, hm, th):
    """Baut aus einem Klick-Ziel (z.B. 'guest:3') das passende Lightbox-Modal."""
    if not spec:
        return None
    kind, _sep, arg = str(spec).partition(":")
    try:
        idx = int(arg)
    except (TypeError, ValueError):
        idx = 0
    try:
        if kind == "dev":
            return dashboard.open_device_modal(
                dashboard.flat_devices(cfg), state, idx)
        if kind == "guest":
            items = dashboard.flat_guests(prox)
            if not (0 <= idx < len(items)):
                return None
            ni, guest = items[idx]
            node = (prox or {}).get("nodes", [])[ni]
            return {
                "type": "proxmox",
                "guest": guest,
                "node_name": node["node"]["name"],
                "node_idx": ni,
                "node_label": node.get("label", ""),
                "result": None,
            }
        if kind == "node":
            return dashboard.open_node_modal(prox, idx) if prox else None
        if kind == "ap":
            return dashboard.open_unifi_modal(uni, idx) if uni else None
        if kind == "dc":
            return dashboard.open_deconz_modal(dc) if dc else None
        if kind == "hm":
            return dashboard.open_homematic_modal(hm) if hm else None
        if kind == "th":
            return dashboard.open_thread_modal(th) if th else None
        if kind == "fritz":
            return {"type": "fritz", "data": fb} if fb else None
    except Exception:
        return None
    return None


def _hit_specs(width):
    """Klick-Ziele des zuletzt gerenderten Frames (Zeile, Spalte von/bis)."""
    layout = dashboard.LAST_LAYOUT or {}
    x0 = layout.get("x0", 0)
    x1 = layout.get("x1", -1)
    hits = []
    for row, left, right, di in layout.get("dev_hits") or []:
        hits.append((row, left, right, "dev:{}".format(di)))
    for row, kind, idx in layout.get("pve_hits") or []:
        spec = "node:{}".format(idx) if kind == "node" else \
            "guest:{}".format(idx)
        hits.append((row, x0, x1, spec))
    for row, ai in layout.get("ap_hits") or []:
        hits.append((row, x0, x1, "ap:{}".format(ai)))
    for key, spec in (("dc_hit", "dc"), ("hm_hit", "hm"), ("th_hit", "th")):
        row = layout.get(key)
        if row is not None:
            hits.append((row, x0, x1, spec))
    row = layout.get("fritz_hit")
    if row is not None:
        hits.append((row, 0, max(0, width - 1), "fritz"))
    return "|".join("{},{},{},{}".format(*hit) for hit in hits)


def build_frame(width, height, cfg=None, spec=None):
    """Rendert die TUI und liefert (HTML-Fragment, Zeitstempel, Meta)."""
    dashboard.CFG = cfg if isinstance(cfg, dict) else _config()
    state = _load("state")
    prox = _load("prox")
    speed = _load("speed")
    uni = _load("uni")
    dc = _load("dc")
    fb = _load("fb")
    hm = _load("hm")
    th = _load("th")
    modal = _make_modal(spec, dashboard.CFG, state, prox, uni, fb, dc, hm, th)
    rows = dashboard.render(
        width, height, state, prox, speed, uni, dc, 0, 1, 0, 0, 0, 0,
        None, modal, 0, "guest", fb=fb, hm=hm, th=th)
    body = "\n".join(ansi_to_html(row) for row in rows)
    rect = (dashboard.LAST_LAYOUT or {}).get("modal_rect") if modal else None
    meta = {
        "hits": _hit_specs(width),
        "rect": ",".join(str(v) for v in rect) if rect else "",
        "modal": bool(modal),
    }
    return body, time.strftime("%d.%m.%Y %H:%M:%S"), meta


def build_html(width, height, body, stamp, auto, meta=None):
    meta = meta or {}
    return (PAGE.replace("__FAVICON__", FAVICON)
            .replace("__W__", str(width))
            .replace("__H__", str(height))
            .replace("__AUTO__", "1" if auto else "0")
            .replace("__STAMP__", stamp)
            .replace("__HITS__", html.escape(meta.get("hits") or "", True))
            .replace("__MRECT__", html.escape(meta.get("rect") or "", True))
            .replace("__BODY__", body))


class Handler(BaseHTTPRequestHandler):
    server_version = "NetbookDashboard/1.2"

    def _send(self, code, payload, ctype, headers=None):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path or "/"
        qs = parse_qs(url.query)
        spec = (qs.get("m") or [""])[0]
        if path == "/frame":
            try:
                cfg = _config()
                width, height = _dims(cfg, qs)
                body, stamp, meta = build_frame(width, height, cfg, spec)
                headers = {
                    "X-Stamp": stamp,
                    "X-Cols": str(width),
                    "X-Rows": str(height),
                    "X-Hits": meta["hits"],
                    "X-MRect": meta["rect"],
                }
                if meta["modal"]:
                    headers["X-Modal"] = "1"
                self._send(200, body, "text/html; charset=utf-8", headers)
            except Exception as exc:
                self._send(500, "Dashboard-Fehler: {}\n".format(
                    html.escape(str(exc))), "text/plain; charset=utf-8")
        elif path in ("/", "/index.html"):
            try:
                cfg = _config()
                width, height = _dims(cfg, qs)
                body, stamp, meta = build_frame(width, height, cfg, spec)
                self._send(200, build_html(width, height, body, stamp,
                                           _auto(qs), meta),
                           "text/html; charset=utf-8")
            except Exception as exc:
                self._send(500, "Dashboard-Fehler: {}\n".format(
                    html.escape(str(exc))), "text/plain; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok\n", "text/plain; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(200, FAVICON_SVG, "image/svg+xml; charset=utf-8")
        else:
            self._send(404, "nicht gefunden\n", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass


def main():
    cfg = _config()
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
