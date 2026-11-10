#!/usr/bin/env python3
"""UniFi-Poller: fragt den Controller ab (APs + WLAN-Clients), schreibt unifi.json.
   Nutzt nur stdlib (urllib + ssl). Laeuft im checker_loop mit."""
import json
import os
import ssl
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "unifi.json")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Lesbare Namen fuer die haeufigsten Modelle
MODEL_NAMES = {
    "U7PG2": "AC Pro",
    "U7LR": "AC LR",
    "U7LITE": "AC Lite",
    "U7HD": "AC HD",
    "U7MSH": "AC Mesh",
    "U7IW": "AC IW",
    "U7NHD": "nanoHD",
    "UFLHD": "AC HD",
    "U6L": "U6 Lite",
    "U6LR": "U6 LR",
    "U6PRO": "U6 Pro",
    "UAL6": "U6 Mesh",
    "U6M": "U6 Mesh",
    "U6ENT": "U6 Enterprise",
    "U7PRO": "U7 Pro",
}


def _rc(payload):
    if isinstance(payload, dict):
        return ((payload.get("meta") or {}).get("rc") or "").lower()
    return ""


def _msg(payload):
    if isinstance(payload, dict):
        meta = payload.get("meta") or {}
        return meta.get("msg") or meta.get("rc") or "unbekannter Fehler"
    return str(payload)[:160]


class Controller(object):
    """Minimaler HTTP-Client mit Cookie-Session (klassisch + UniFi OS)."""

    def __init__(self, url, timeout=8, verify_tls=False):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.cookies = {}
        self.prefix = ""
        self.ctx = ssl.create_default_context()
        if not verify_tls:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _store_cookies(self, headers):
        try:
            raw = headers.get_all("Set-Cookie") or []
        except Exception:
            raw = []
        for item in raw:
            part = item.split(";")[0]
            if "=" in part:
                key, val = part.split("=", 1)
                self.cookies[key.strip()] = val.strip()

    def request(self, path, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            self.url + self.prefix + path,
            data=body,
            method="POST" if data is not None else "GET")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if self.cookies:
            req.add_header("Cookie", "; ".join(
                "{}={}".format(k, v) for k, v in self.cookies.items()))
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx)
            self._store_cookies(resp.headers)
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"meta": {"rc": "error", "msg": raw[:160]}}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"meta": {"rc": "error", "msg": raw[:160]}}
        except Exception as e:
            return 0, {"meta": {"rc": "error", "msg": str(e)}}

    def login(self, username, password):
        creds = {"username": username, "password": password, "remember": True}
        # Klassischer Controller: /api/login
        status, payload = self.request("/api/login", creds)
        if status == 200 and _rc(payload) == "ok":
            self.prefix = ""
            return
        # UniFi OS: /api/auth/login, API danach unter /proxy/network
        self.cookies = {}
        status, payload = self.request("/api/auth/login", creds)
        if status == 200 and _rc(payload) == "ok":
            self.prefix = "/proxy/network"
            return
        raise RuntimeError("Login fehlgeschlagen (HTTP {}): {}".format(
            status, _msg(payload)))

    def get(self, path):
        status, payload = self.request(path)
        if status != 200 or _rc(payload) != "ok":
            raise RuntimeError("{} -> HTTP {}: {}".format(path, status, _msg(payload)))
        return payload.get("data")


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _lower(value):
    return (value or "").lower()


def poll(cfg):
    """Fragt den Controller komplett ab und liefert das Ausgabe-Dict."""
    uni_cfg = cfg.get("unifi") or {}
    url = (uni_cfg.get("url") or "").strip()
    user = (uni_cfg.get("username") or "").strip()
    if not url or not user:
        raise RuntimeError("unifi nicht konfiguriert (url/username)")

    ctl = Controller(url, timeout=_int(uni_cfg.get("timeout")) or 8,
                     verify_tls=bool(uni_cfg.get("verify_tls")))
    ctl.login(user, uni_cfg.get("password") or "")

    site = (uni_cfg.get("site") or "default").strip() or "default"
    sites = ctl.get("/api/self/sites") or []
    names = [s.get("name") for s in sites if s.get("name")]
    if site not in names and names:
        site = names[0]

    devices = ctl.get("/api/s/{}/stat/device".format(site)) or []
    clients = ctl.get("/api/s/{}/stat/sta".format(site)) or []
    health = ctl.get("/api/s/{}/stat/health".format(site)) or []

    version = ""
    try:
        status, payload = ctl.request("/status")
        if isinstance(payload, dict):
            version = (payload.get("meta") or {}).get("server_version") or ""
    except Exception:
        pass

    aps = []
    ap_by_mac = {}
    for dev in devices:
        if dev.get("type") != "uap" and not dev.get("is_access_point"):
            continue
        mac = _lower(dev.get("mac"))
        name = dev.get("name") or mac or "AP"
        model = dev.get("model") or ""
        rec = {
            "name": name,
            "mac": mac,
            "model": model,
            "model_name": MODEL_NAMES.get(model, model),
            "state": dev.get("state"),
            "up": dev.get("state") == 1,
            "num_sta": _int(dev.get("num_sta")),
            "ip": dev.get("ip") or "",
            "version": dev.get("version") or dev.get("displayable_version") or "",
            "uptime": _int(dev.get("uptime")),
        }
        aps.append(rec)
        ap_by_mac[mac] = name
    aps.sort(key=lambda a: (not a["up"], a["name"].lower()))

    wlan = []
    wired = 0
    for cl in clients:
        if cl.get("is_wired"):
            wired += 1
            continue
        mac = _lower(cl.get("mac"))
        name = (cl.get("name") or cl.get("hostname") or cl.get("ip")
                or mac[-8:] or "?")
        wlan.append({
            "name": name,
            "hostname": cl.get("hostname") or "",
            "ip": cl.get("ip") or "",
            "mac": mac,
            "essid": cl.get("essid") or "",
            "ap_mac": _lower(cl.get("ap_mac")),
            "ap_name": ap_by_mac.get(_lower(cl.get("ap_mac")), ""),
            "signal": cl.get("signal"),
            "uptime": _int(cl.get("uptime") or cl.get("_uptime_by_uap")),
            "satisfaction": cl.get("satisfaction"),
            "last_seen": cl.get("last_seen"),
        })
    # Schwaechste Verbindung zuerst - die faellt im Alltag am ehesten auf.
    wlan.sort(key=lambda x: (x["signal"] is None,
                             x["signal"] if x["signal"] is not None else 0,
                             x["name"].lower()))

    subsystems = {}
    for item in health:
        sub = item.get("subsystem")
        if sub:
            subsystems[sub] = {
                "status": item.get("status"),
                "num_sta": _int(item.get("num_sta") or item.get("num_user")),
            }

    return {
        "ok": True,
        "ts": time.time(),
        "controller": url,
        "version": version,
        "site": site,
        "ap_total": len(aps),
        "ap_up": sum(1 for a in aps if a["up"]),
        "wlan_count": len(wlan),
        "wired_count": wired,
        "aps": aps,
        "clients": wlan,
        "health": subsystems,
        "error": None,
    }


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
        json.dump(data, f, indent=1)
    os.replace(tmp, OUT)


def main():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    try:
        out = poll(cfg)
    except Exception as e:
        # Alte Daten behalten, nur den Fehler vermerken
        old = _previous()
        old["ok"] = False
        old["error"] = str(e)
        old.setdefault("aps", [])
        old.setdefault("clients", [])
        old.setdefault("ap_total", len(old.get("aps") or []))
        old.setdefault("ap_up", sum(1 for a in old.get("aps") or [] if a.get("up")))
        old.setdefault("wlan_count", len(old.get("clients") or []))
        old.setdefault("wired_count", 0)
        old["ts"] = old.get("ts") or 0
        _write(old)
        print("UniFi FEHLER: {}".format(e))
        return 1

    _write(out)
    print("UniFi: {}/{} APs online, {} WLAN, {} LAN".format(
        out["ap_up"], out["ap_total"], out["wlan_count"], out["wired_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
