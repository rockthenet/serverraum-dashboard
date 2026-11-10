#!/usr/bin/env python3
"""deCONZ/Phoscon-Poller: liest Lights + Sensoren und schreibt deconz.json.
   Nutzt nur stdlib (urllib). Laeuft im checker_loop mit."""
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "deconz.json")

# Lesbare deutsche Namen fuer die deCONZ-Typen (ASCII, Netbook-tauglich)
TYPE_LABELS = {
    # Lights
    "On/Off plug-in unit": "Steckdose",
    "Smart plug": "Steckdose",
    "On/Off light": "Lampe",
    "Dimmable light": "Dimmbare Lampe",
    "Color temperature light": "Weisslicht",
    "Extended color light": "Farblampe",
    "Color light": "Farblampe",
    # Sensoren
    "ZHAOpenClose": "Tuer/Fenster",
    "ZHAPresence": "Bewegung/Praesenz",
    "ZHALightLevel": "Helligkeit",
    "ZHATemperature": "Temperatur",
    "ZHAHumidity": "Luftfeuchte",
    "ZHAPressure": "Luftdruck",
    "ZHASwitch": "Schalter/Fernbedienung",
    "ZHAPower": "Leistungsmessung",
    "ZHAConsumption": "Verbrauchsmessung",
    "ZHAWater": "Wassersensor",
    "ZHAVibration": "Vibrationssensor",
    "ZHACarbonMonoxide": "CO-Sensor",
    "ZHASmokeDetector": "Rauchmelder",
    "Daylight": "Tageslicht (virtuell)",
    "CLIPDaylightOffset": "Tageslicht-Offset (virtuell)",
}

VIRTUAL_TYPES = ("Daylight", "CLIPDaylightOffset")


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _reachable(obj):
    """deCONZ meldet reachable je nach Ressource in state oder config."""
    state = obj.get("state") or {}
    config = obj.get("config") or {}
    if isinstance(state, dict) and "reachable" in state:
        return bool(state.get("reachable"))
    if isinstance(config, dict) and "reachable" in config:
        return bool(config.get("reachable"))
    return True


def _fetch(api, timeout):
    req = urllib.request.Request(api)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP {}: {}".format(e.code, raw[:120]))
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError("keine JSON-Antwort: {}".format(raw[:120]))
    if isinstance(data, list):
        # deCONZ liefert bei Fehlern eine Liste mit [{"error": ...}]
        err = (data[0] or {}).get("error") if data else None
        if err:
            raise RuntimeError(err.get("description") or "API-Fehler")
    if not isinstance(data, dict):
        raise RuntimeError("unerwartete Antwort: {}".format(str(data)[:120]))
    if "lights" not in data and "sensors" not in data:
        err = data.get("error")
        if isinstance(err, dict):
            raise RuntimeError(err.get("description") or "API-Fehler")
        raise RuntimeError("Antwort ohne lights/sensors")
    return data


def _device(kind, obj):
    return {
        "name": obj.get("name") or "?",
        "model": obj.get("modelid") or "",
        "manufacturer": obj.get("manufacturername") or "",
        "type": obj.get("type") or "?",
        "kind": kind,
        "reachable": _reachable(obj),
        "lastseen": obj.get("lastseen") or "",
        "battery": ((obj.get("config") or {}).get("battery")
                    if isinstance(obj.get("config"), dict) else None),
    }


def poll(cfg):
    dc_cfg = cfg.get("deconz") or {}
    base = (dc_cfg.get("url") or "").strip().rstrip("/")
    key = (dc_cfg.get("apikey") or "").strip()
    if not base or not key:
        raise RuntimeError("deconz nicht konfiguriert (url/apikey)")
    timeout = _int(dc_cfg.get("timeout")) or 6

    api = "{}/api/{}".format(base, key)
    data = _fetch(api, timeout)

    groups = {}
    for kind, bucket in (("light", data.get("lights") or {}),
                         ("sensor", data.get("sensors") or {})):
        if not isinstance(bucket, dict):
            continue
        for obj in bucket.values():
            if not isinstance(obj, dict):
                continue
            rec = _device(kind, obj)
            typ = rec["type"]
            label = TYPE_LABELS.get(typ, typ)
            grp = groups.setdefault(label, {
                "type": typ,
                "label": label,
                "total": 0,
                "online": 0,
                "virtual": typ in VIRTUAL_TYPES,
                "devices": [],
            })
            grp["total"] += 1
            if rec["reachable"]:
                grp["online"] += 1
            grp["devices"].append(rec)

    out_groups = []
    for label in groups:
        grp = groups[label]
        grp["offline"] = grp["total"] - grp["online"]
        grp["devices"].sort(key=lambda d: d["name"].lower())
        out_groups.append(grp)
    out_groups.sort(key=lambda g: (g["label"].lower(), g["type"]))

    lights = data.get("lights") or {}
    sensors = data.get("sensors") or {}
    conf = data.get("config") or {}
    total = sum(g["total"] for g in out_groups)
    online = sum(g["online"] for g in out_groups)

    return {
        "ok": True,
        "ts": time.time(),
        "url": base,
        "gateway": conf.get("name") or "deCONZ",
        "devicename": conf.get("devicename") or "",
        "swversion": conf.get("swversion") or "",
        "fwversion": conf.get("fwversion") or "",
        "apiversion": conf.get("apiversion") or "",
        "zigbeechannel": conf.get("zigbeechannel"),
        "lights_total": len(lights),
        "sensors_total": len(sensors),
        "total": total,
        "online": online,
        "offline": total - online,
        "groups": out_groups,
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
        old.setdefault("groups", [])
        old.setdefault("total", sum(g.get("total", 0) for g in old["groups"]))
        old.setdefault("online", sum(g.get("online", 0) for g in old["groups"]))
        old.setdefault("offline", old["total"] - old["online"])
        old.setdefault("gateway", "deCONZ")
        old.setdefault("swversion", "")
        old.setdefault("fwversion", "")
        old["ts"] = old.get("ts") or 0
        _write(old)
        print("deCONZ FEHLER: {}".format(e))
        return 1

    _write(out)
    print("deCONZ: {}/{} Geraete verbunden, {} Typen".format(
        out["online"], out["total"], len(out["groups"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
