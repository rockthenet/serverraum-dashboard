#!/usr/bin/env python3
"""HomeMatic-CCU-Poller ueber die XML-API.

Liest devicelist.cgi (Name, Typ, Interface) und statelist.cgi (UNREACH,
LOWBAT/LOW_BAT, OPERATING_VOLTAGE, RSSI) und schreibt homematic.json.
Nutzt nur stdlib (urllib, ElementTree), Python 3.7 tauglich.

Die CCU muss das Addon "XML-API" installiert haben
(http://<ccu>/addons/xmlapi/).
"""
import json
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "homematic.json")

# Nur diese Datenpunkte werden aus der (grossen) statelist.cgi behalten.
WANTED = ("UNREACH", "STICKY_UNREACH", "LOWBAT", "LOW_BAT",
          "OPERATING_VOLTAGE", "RSSI_DEVICE", "RSSI_PEER")

DEFAULT_URL = "http://192.168.178.6/addons/xmlapi"
DEFAULT_TIMEOUT = 8
DEFAULT_EMPTY_V = 2.2
DEFAULT_WEAK_V = 2.5
DEFAULT_RSSI_WEAK = -85
DEFAULT_RSSI_BAD = -95


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value):
    return str(value).strip().lower() == "true"


def _fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "serverraum-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _rssi(value):
    """HomeMatic-RSSI in dBm umrechnen.

    Die CCU liefert je nach Firmware negative dBm oder unsigned Bytes
    (z. B. 194 = -62 dBm). 0/1/128 und -65535 bedeuten "unbekannt".
    """
    raw = _float(value)
    if raw is None:
        return None
    val = int(raw)
    if val in (0, 1, 128, -65535):
        return None
    if val > 127:
        val -= 256
    if val >= 0:
        return None
    return val


def _parse_devices(raw):
    """devicelist.cgi -> {ise_id: {name, type, interface}}."""
    devices = {}
    root = ET.fromstring(raw)
    for dev in root.findall("device"):
        ise = dev.get("ise_id")
        if not ise:
            continue
        devices[ise] = {
            "name": (dev.get("name") or "").strip(),
            "type": (dev.get("device_type") or "").strip(),
            "interface": (dev.get("interface") or "").strip(),
            "address": (dev.get("address") or "").strip(),
        }
    return devices


def _parse_states(raw):
    """statelist.cgi -> {ise_id: {param: value}} (nur WANTED)."""
    states = {}
    root = ET.fromstring(raw)
    for dev in root.findall("device"):
        ise = dev.get("ise_id")
        if not ise:
            continue
        vals = {}
        for chan in dev.findall("channel"):
            for dp in chan.findall("datapoint"):
                ptype = dp.get("type") or ""
                if ptype in WANTED and ptype not in vals:
                    vals[ptype] = dp.get("value")
        if "UNREACH" in vals:
            states[ise] = vals
    return states


def _battery(vals, empty_v, weak_v):
    """-> (zustand, volt); zustand: empty/weak/ok/none."""
    lowbat = _bool(vals.get("LOWBAT")) or _bool(vals.get("LOW_BAT"))
    volt = _float(vals.get("OPERATING_VOLTAGE"))
    if volt is not None and volt < 0.1:
        volt = None
    if lowbat or (volt is not None and volt <= empty_v):
        return "empty", volt
    if volt is not None and volt <= weak_v:
        return "weak", volt
    if volt is not None:
        return "ok", volt
    return "none", None


def poll(cfg):
    hm = cfg.get("homematic") or {}
    base = (hm.get("url") or DEFAULT_URL).rstrip("/")
    timeout = _float(hm.get("timeout")) or DEFAULT_TIMEOUT
    empty_v = _float(hm.get("battery_empty_volt"), DEFAULT_EMPTY_V)
    weak_v = _float(hm.get("battery_weak_volt"), DEFAULT_WEAK_V)
    rssi_weak = int(_float(hm.get("rssi_weak"), DEFAULT_RSSI_WEAK))
    rssi_bad = int(_float(hm.get("rssi_bad"), DEFAULT_RSSI_BAD))

    dev_raw = _fetch(base + "/devicelist.cgi", timeout)
    state_raw = _fetch(base + "/statelist.cgi", timeout)
    meta = _parse_devices(dev_raw)
    states = _parse_states(state_raw)

    devices = []
    for ise, vals in states.items():
        info = meta.get(ise) or {}
        name = info.get("name") or "Geraet {}".format(ise)
        dtype = info.get("type") or "?"
        unreach = _bool(vals.get("UNREACH"))
        battery, volt = _battery(vals, empty_v, weak_v)
        rdev = _rssi(vals.get("RSSI_DEVICE"))
        rpeer = _rssi(vals.get("RSSI_PEER"))
        rvals = [r for r in (rdev, rpeer) if r is not None]
        rssi = min(rvals) if rvals else None
        devices.append({
            "ise_id": ise,
            "name": name,
            "type": dtype,
            "interface": info.get("interface") or "",
            "unreach": unreach,
            "sticky": _bool(vals.get("STICKY_UNREACH")),
            "battery": battery,
            "volt": volt,
            "rssi": rssi,
            "rssi_device": rdev,
            "rssi_peer": rpeer,
        })

    devices.sort(key=lambda d: d["name"].lower())

    total = len(devices)
    unreachable = [d for d in devices if d["unreach"]]
    empty = [d for d in devices if d["battery"] == "empty"]
    weak = [d for d in devices if d["battery"] == "weak"]
    bad = [d for d in devices if d["rssi"] is not None and d["rssi"] <= rssi_bad]
    poor = [d for d in devices if d["rssi"] is not None
            and rssi_bad < d["rssi"] <= rssi_weak]
    no_rssi = [d for d in devices if d["rssi"] is None]
    with_bat = [d for d in devices if d["battery"] != "none"]

    types = {}
    for dev in devices:
        item = types.setdefault(dev["type"], {
            "type": dev["type"], "total": 0, "offline": 0,
            "battery_problem": 0, "rssi_bad": 0})
        item["total"] += 1
        if dev["unreach"]:
            item["offline"] += 1
        if dev["battery"] in ("empty", "weak"):
            item["battery_problem"] += 1
        if dev["rssi"] is not None and dev["rssi"] <= rssi_weak:
            item["rssi_bad"] += 1

    def slim(items):
        return [{
            "name": d["name"],
            "type": d["type"],
            "volt": d["volt"],
            "rssi": d["rssi"],
        } for d in items]

    return {
        "ok": True,
        "ts": int(time.time()),
        "source": base,
        "total": total,
        "live": total - len(unreachable),
        "unreach": len(unreachable),
        "battery_total": len(with_bat),
        "battery_empty": len(empty),
        "battery_weak": len(weak),
        "battery_ok": len(with_bat) - len(empty) - len(weak),
        "rssi_bad": len(bad),
        "rssi_poor": len(poor),
        "rssi_none": len(no_rssi),
        "battery_empty_volt": empty_v,
        "battery_weak_volt": weak_v,
        "rssi_weak_limit": rssi_weak,
        "rssi_bad_limit": rssi_bad,
        "types": sorted(types.values(),
                        key=lambda t: (-t["total"], t["type"].lower())),
        "problems": {
            "empty": slim(empty),
            "weak": slim(weak),
            "bad": slim(bad),
            "poor": slim(poor),
            "offline": slim(unreachable),
        },
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


def _notify_problems(prev, out):
    """Meldet neu leere/schwache Batterien und Entwarnungen (nur bei Wechsel)."""
    # Ohne vorherigen, vollstaendigen Stand ist das der Basis-Lauf: kein Alarm.
    if not prev.get("ok") or "problems" not in prev:
        return
    try:
        import notify as ntfy
    except Exception:
        return
    pv = prev.get("problems") or {}
    nv = out.get("problems") or {}

    def names(bucket):
        return [p.get("name") or "?" for p in (bucket or [])]

    for key, label, tags, prio in (
            ("empty", "Batterie leer", "battery,warning", "high"),
            ("weak", "Batterie schwach", "battery", None)):
        added, gone = ntfy.diff(names(pv.get(key)), names(nv.get(key)))
        if added:
            ntfy.send("HomeMatic {}: {}".format(label, ntfy.join_names(added)),
                      title="HomeMatic: {}".format(label), tags=tags, priority=prio)
        elif gone:
            ntfy.send("HomeMatic {} wieder ok: {}".format(label, ntfy.join_names(gone)),
                      title="HomeMatic: {} behoben".format(label),
                      tags="battery,white_check_mark")


def main():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    hm = cfg.get("homematic") or {}
    if hm.get("enabled") is False:
        print("HomeMatic: deaktiviert")
        return 0

    prev = _previous()

    try:
        out = poll(cfg)
    except Exception as e:
        old = _previous()
        old["ok"] = False
        old["error"] = str(e)
        old.setdefault("total", 0)
        old.setdefault("live", 0)
        old.setdefault("unreach", 0)
        old.setdefault("battery_empty", 0)
        old.setdefault("battery_weak", 0)
        old.setdefault("rssi_bad", 0)
        old.setdefault("rssi_poor", 0)
        old.setdefault("types", [])
        old.setdefault("problems", {})
        old["ts"] = old.get("ts") or 0
        _write(old)
        print("HomeMatic FEHLER: {}".format(e))
        return 1

    _write(out)
    if hm.get("notify"):
        _notify_problems(prev, out)
    print("HomeMatic: {} Geraete, {} live, {} leer/{} schwach, "
          "{} schlecht/{} maeßig, {} offline".format(
              out["total"], out["live"], out["battery_empty"],
              out["battery_weak"], out["rssi_bad"], out["rssi_poor"],
              out["unreach"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
