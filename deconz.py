#!/usr/bin/env python3
"""deCONZ/Phoscon-Poller: liest Lights + Sensoren und schreibt deconz.json.

   Mehrere Sensor-Ressourcen desselben physischen Geraets (z. B. Bewegung +
   Licht + Temperatur) werden anhand der uniqueid/MAC zu einem Geraet
   zusammengefasst. Virtuelle Daylight-Offsets zaehlen nicht als Geraet.
"""
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "deconz.json")

BATTERY_WEAK = 25

# Lesbare deutsche Namen fuer die deCONZ-Typen (ASCII, Netbook-tauglich)
TYPE_LABELS = {
    "On/Off plug-in unit": "Steckdose",
    "Smart plug": "Steckdose",
    "On/Off light": "Lampe",
    "Dimmable light": "Dimmbare Lampe",
    "Color temperature light": "Weisslicht",
    "Extended color light": "Farblampe",
    "Color light": "Farblampe",
}

# Reihenfolge = Prioritaet fuer die Hauptfunktion eines Geraets
SENSOR_CLASSES = (
    ("ZHAOpenClose", "Tuer/Fenster"),
    ("ZHAWater", "Wassersensor"),
    ("ZHASwitch", "Schalter/Fernbedienung"),
    ("ZHAPresence", "Bewegung/Praesenz"),
    ("ZHATemperature", "Klimasensor"),
    ("ZHAHumidity", "Klimasensor"),
    ("ZHAPressure", "Klimasensor"),
    ("ZHAPower", "Verbrauchsmessung"),
    ("ZHAConsumption", "Verbrauchsmessung"),
    ("ZHAVibration", "Vibrationssensor"),
    ("ZHACarbonMonoxide", "CO-Sensor"),
    ("ZHASmokeDetector", "Rauchmelder"),
)

# Reihenfolge = Prioritaet fuer den Anzeigenamen eines Multisensors
NAME_PRIORITY = (
    "ZHAOpenClose", "ZHAWater", "ZHASwitch", "ZHAPresence", "ZHAHumidity",
    "ZHATemperature", "ZHALightLevel", "ZHAPressure", "ZHAPower",
    "ZHAConsumption", "ZHAVibration",
)

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


def _battery(obj):
    config = obj.get("config") or {}
    if isinstance(config, dict) and config.get("battery") is not None:
        return _int(config.get("battery"))
    return None


def _sensor_class(types):
    for typ, label in SENSOR_CLASSES:
        if typ in types:
            return label
    return "Sonstige"


def _pick_name(entries):
    """Bevorzugt den Namen der Hauptfunktion (z. B. Multisensor)."""
    by_type = {}
    for kind, obj in entries:
        by_type.setdefault(obj.get("type"), obj.get("name"))
    for typ in NAME_PRIORITY:
        name = by_type.get(typ)
        if name:
            return name
    return entries[0][1].get("name") or "?"


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


def poll(cfg):
    dc_cfg = cfg.get("deconz") or {}
    base = (dc_cfg.get("url") or "").strip().rstrip("/")
    key = (dc_cfg.get("apikey") or "").strip()
    if not base or not key:
        raise RuntimeError("deconz nicht konfiguriert (url/apikey)")
    timeout = _int(dc_cfg.get("timeout")) or 6

    api = "{}/api/{}".format(base, key)
    data = _fetch(api, timeout)

    # ─── Ressourcen pro physischem Geraet (MAC aus der uniqueid) sammeln ───
    physical = {}
    resources = 0
    for kind, bucket in (("light", data.get("lights") or {}),
                         ("sensor", data.get("sensors") or {})):
        if not isinstance(bucket, dict):
            continue
        for rid, obj in bucket.items():
            if not isinstance(obj, dict):
                continue
            resources += 1
            typ = obj.get("type") or "?"
            if typ in VIRTUAL_TYPES:
                continue
            uid = obj.get("uniqueid") or rid
            mac = uid.split("-")[0]
            rec = physical.setdefault(mac, {
                "mac": mac,
                "entries": [],
                "types": set(),
                "light_types": [],
                "batteries": [],
                "reachable": [],
            })
            rec["entries"].append((kind, obj))
            rec["types"].add(typ)
            if kind == "light":
                rec["light_types"].append(typ)
            bat = _battery(obj)
            if bat is not None:
                rec["batteries"].append(bat)
            rec["reachable"].append(_reachable(obj))

    groups = {}
    total = online = 0
    battery_total = battery_weak = battery_empty = 0

    for rec in physical.values():
        entries = rec["entries"]
        types = rec["types"]
        if rec["light_types"]:
            label = TYPE_LABELS.get(rec["light_types"][0], rec["light_types"][0])
            kind = "sensor" if "sensor" in [k for k, _ in entries] else "light"
            name = next((o.get("name") for k, o in entries if k == "light"), None)
        else:
            label = _sensor_class(types)
            kind = "sensor"
            name = _pick_name(entries)

        battery = min(rec["batteries"]) if rec["batteries"] else None
        reachable = all(rec["reachable"])
        main = next((o for k, o in entries if k == "light"), entries[0][1])
        device = {
            "name": name or "?",
            "model": main.get("modelid") or "",
            "manufacturer": main.get("manufacturername") or "",
            "types": sorted(types),
            "kind": kind,
            "reachable": reachable,
            "battery": battery,
            "functions": len(types),
        }

        grp = groups.setdefault(label, {
            "type": "/".join(sorted(types)),
            "label": label,
            "total": 0,
            "online": 0,
            "battery": 0,
            "weak": 0,
            "devices": [],
        })
        grp["total"] += 1
        if reachable:
            grp["online"] += 1
        if battery is not None:
            grp["battery"] += 1
            if battery <= BATTERY_WEAK:
                grp["weak"] += 1
        grp["devices"].append(device)

        total += 1
        if reachable:
            online += 1
        if battery is not None:
            battery_total += 1
            if battery == 0:
                battery_empty += 1
            if battery <= BATTERY_WEAK:
                battery_weak += 1

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

    empty_names = []
    weak_names = []
    for grp in out_groups:
        for dev in grp.get("devices") or []:
            bat = dev.get("battery")
            if bat is None:
                continue
            if int(bat) == 0:
                empty_names.append(dev.get("name") or "?")
            elif int(bat) <= BATTERY_WEAK:
                weak_names.append(dev.get("name") or "?")

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
        "resources_total": resources,
        "total": total,
        "online": online,
        "offline": total - online,
        "battery_total": battery_total,
        "battery_weak": battery_weak,
        "battery_empty": battery_empty,
        "battery_none": total - battery_total,
        "battery_weak_limit": BATTERY_WEAK,
        "groups": out_groups,
        "problems": {
            "empty": sorted(empty_names),
            "weak": sorted(weak_names),
        },
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


def _notify_problems(prev, out):
    """Meldet Akku-Probleme mit 24-h-Sperre pro Geraet (kein 5-Minuten-Spam)."""
    state = prev.get("battery_state")
    state = dict(state) if isinstance(state, dict) else {}
    out["battery_state"] = state
    # Ohne vorherigen, vollstaendigen Stand ist das der Basis-Lauf: kein Alarm.
    if not prev.get("ok") or "problems" not in prev:
        return
    try:
        import notify as ntfy
    except Exception:
        return
    pv = prev.get("problems") or {}
    nv = out.get("problems") or {}
    all_cur = set(nv.get("empty") or []) | set(nv.get("weak") or [])
    for key, label, tags, prio in (
            ("empty", "Akku leer", "battery,warning", "high"),
            ("weak", "Akku schwach", "battery", None)):
        added, gone = ntfy.diff(pv.get(key), nv.get(key))
        alerts = []
        # Alle aktuellen Probleme durch die 24-h-Sperre schicken, damit
        # bestehende Auffaelligkeiten eine Erinnerung bekommen koennen.
        for name in (nv.get(key) or []):
            due, kind = ntfy.battery_decide(state, name, key, True)
            if due and kind == "alert":
                alerts.append(name)
        recover = []
        for name in gone:
            if name in all_cur:
                continue          # Kategoriewechsel, kein "behoben"
            due, kind = ntfy.battery_decide(state, name, key, False)
            if due and kind == "recover":
                recover.append(name)
        if alerts:
            ntfy.send("deCONZ {}: {}".format(label, ntfy.join_names(alerts)),
                      title="deCONZ: {}".format(label), tags=tags, priority=prio)
        if recover:
            ntfy.send("deCONZ {} wieder ok: {}".format(
                label, ntfy.join_names(recover)),
                      title="deCONZ: {} behoben".format(label),
                      tags="battery,white_check_mark")


def main():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    if (cfg.get("deconz") or {}).get("paused"):
        print("deCONZ: pausiert")
        return 0

    prev = _previous()

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
        old.setdefault("battery_total", 0)
        old.setdefault("battery_weak", 0)
        old.setdefault("battery_empty", 0)
        old.setdefault("battery_none", old["total"] - old["battery_total"])
        old.setdefault("resources_total", old["total"])
        old.setdefault("gateway", "deCONZ")
        old.setdefault("swversion", "")
        old.setdefault("fwversion", "")
        old["ts"] = old.get("ts") or 0
        _write(old)
        print("deCONZ FEHLER: {}".format(e))
        return 1

    if (cfg.get("deconz") or {}).get("notify"):
        _notify_problems(prev, out)
    elif isinstance(prev.get("battery_state"), dict):
        out["battery_state"] = prev["battery_state"]
    _write(out)
    print("deCONZ: {}/{} Geraete verbunden, {} Typen, Akku {}/{}".format(
        out["online"], out["total"], len(out["groups"]),
        out["battery_total"], out["total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
