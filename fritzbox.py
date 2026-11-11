#!/usr/bin/env python3
"""FRITZ!Box-Poller (TR-064): liest Geraete-, WAN- und Online-Monitor-Daten
   und schreibt fritzbox.json. Nutzt nur stdlib (urllib), Python 3.7 tauglich.

   Die Box liefert die vom Anbieter bereitgestellte Leitungsrate und die
   aktuelle Auslastung. Damit ersetzt der Checker den eigenen Speedtest.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "fritzbox.json")

SVC_DEVICE = "urn:dslforum-org:service:DeviceInfo:1"
SVC_WAN = "urn:dslforum-org:service:WANCommonInterfaceConfig:1"

ACCESS_LABELS = {
    "X_AVM-DE_Cable": "Cable",
    "X_AVM-DE_DSL": "DSL",
    "X_AVM-DE_Fiber": "Fiber",
    "X_AVM-DE_XDSL": "xDSL",
    "X_AVM-DE_LTE": "LTE",
}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _series(value):
    """Kommagetrennte Messwerte -> Liste int. Index 0 ist der neueste Wert."""
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out


class FritzBox(object):
    """Minimaler TR-064-Client mit Digest-Authentifizierung."""

    def __init__(self, host, port, user, password, timeout=8):
        self.base = "http://{}:{}".format(host, port)
        self.timeout = timeout
        self.mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        self.mgr.add_password(None, self.base, user, password)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(self.mgr))

    def call(self, service, action, control, body=""):
        url = self.base + control
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
            'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            '<u:{action} xmlns:u="{service}">{body}</u:{action}>'
            '</s:Body></s:Envelope>'
        ).format(action=action, service=service, body=body)
        req = urllib.request.Request(
            url, data=envelope.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "text/xml; charset=utf-8")
        req.add_header("SOAPACTION", '"{}#{}"'.format(service, action))
        try:
            resp = self.opener.open(req, timeout=self.timeout)
            raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                text = e.read().decode("utf-8", "replace")
                match = re.search(r"<errorDescription>(.*?)</errorDescription>",
                                  text)
                if match:
                    detail = " " + match.group(1)
            except Exception:
                pass
            raise RuntimeError("HTTP {}{}".format(e.code, detail))
        except urllib.error.URLError as e:
            raise RuntimeError("nicht erreichbar: {}".format(e.reason))

        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise RuntimeError("ungueltige SOAP-Antwort")
        out = {}
        for el in root.iter():
            name = el.tag.split("}")[-1]
            if name.startswith("New"):
                out[name] = el.text if el.text is not None else ""
        return out


def poll(cfg):
    conf = cfg.get("fritzbox") or {}
    if conf.get("enabled") is False:
        raise RuntimeError("FRITZ!Box ist in config.json deaktiviert")
    host = conf.get("host") or "192.168.178.1"
    port = _int(conf.get("port"), 49000)
    user = conf.get("user") or ""
    password = conf.get("password") or ""
    timeout = float(conf.get("timeout") or 8)
    if not user or not password:
        raise RuntimeError("fritzbox.user/password fehlt in config.json")

    box = FritzBox(host, port, user, password, timeout)

    info = box.call(SVC_DEVICE, "GetInfo", "/upnp/control/deviceinfo")
    link = box.call(SVC_WAN, "GetCommonLinkProperties",
                    "/upnp/control/wancommonifconfig1")
    monitor = box.call(SVC_WAN, "X_AVM-DE_GetOnlineMonitor",
                       "/upnp/control/wancommonifconfig1",
                       "<NewSyncGroupIndex>0</NewSyncGroupIndex>")

    traffic_rx = 0
    traffic_tx = 0
    try:
        rx = box.call(SVC_WAN, "GetTotalBytesReceived",
                      "/upnp/control/wancommonifconfig1")
        traffic_rx = _int(rx.get("NewTotalBytesReceived"))
    except Exception:
        pass
    try:
        tx = box.call(SVC_WAN, "GetTotalBytesSent",
                      "/upnp/control/wancommonifconfig1")
        traffic_tx = _int(tx.get("NewTotalBytesSent"))
    except Exception:
        pass

    down_samples = _series(monitor.get("Newds_current_bps"))
    up_samples = _series(monitor.get("Newus_current_bps"))
    down_bps = _int(monitor.get("Newmax_ds"))
    up_bps = _int(monitor.get("Newmax_us"))
    access = link.get("NewWANAccessType") or ""

    return {
        "ok": True,
        "ts": time.time(),
        "host": host,
        "model": info.get("NewModelName") or "FRITZ!Box",
        "firmware": info.get("NewSoftwareVersion") or "",
        "hardware": info.get("NewHardwareVersion") or "",
        "serial": info.get("NewSerialNumber") or "",
        "uptime": _int(info.get("NewUpTime")),
        "access_type": ACCESS_LABELS.get(access, access.replace("X_AVM-DE_", "")),
        "link_status": link.get("NewPhysicalLinkStatus") or "",
        "layer1_down_bps": _int(link.get("NewLayer1DownstreamMaxBitRate")),
        "layer1_up_bps": _int(link.get("NewLayer1UpstreamMaxBitRate")),
        "sync_group": monitor.get("NewSyncGroupName") or "",
        "sync_mode": monitor.get("NewSyncGroupMode") or "",
        "sync_groups": _int(monitor.get("NewTotalNumberSyncGroups")),
        "down_bps": down_bps,
        "up_bps": up_bps,
        "down_cur_bps": down_samples[0] if down_samples else 0,
        "up_cur_bps": up_samples[0] if up_samples else 0,
        "down_peak_bps": max(down_samples) if down_samples else 0,
        "up_peak_bps": max(up_samples) if up_samples else 0,
        "down_samples": down_samples,
        "up_samples": up_samples,
        "bytes_received": traffic_rx,
        "bytes_sent": traffic_tx,
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
        old.setdefault("model", "FRITZ!Box")
        old.setdefault("firmware", "")
        old.setdefault("uptime", 0)
        old.setdefault("access_type", "")
        old.setdefault("link_status", "")
        old.setdefault("down_bps", 0)
        old.setdefault("up_bps", 0)
        old.setdefault("down_cur_bps", 0)
        old.setdefault("up_cur_bps", 0)
        old.setdefault("down_peak_bps", 0)
        old.setdefault("up_peak_bps", 0)
        old.setdefault("bytes_received", 0)
        old.setdefault("bytes_sent", 0)
        old["ts"] = old.get("ts") or 0
        _write(old)
        print("FRITZ!Box FEHLER: {}".format(e))
        return 1

    _write(out)
    print("FRITZ!Box: {} {} - Leitung {:.1f}/{:.1f} Mbps, jetzt {:.2f}/{:.2f}".format(
        out["model"], out["firmware"], out["down_bps"] / 1e6,
        out["up_bps"] / 1e6, out["down_cur_bps"] / 1e6,
        out["up_cur_bps"] / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
