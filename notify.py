#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ntfy-Benachrichtigungen (stdlib-only). Topic aus config.json."""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")

BATTERY_COOLDOWN = 86400


def battery_decide(state, name, category, active_seen, now=None):
    """Akku-Meldung pro Geraet hoechstens einmal pro Tag (24 h).

    ``state`` ist der persistente Zustand (name -> dict) und wird direkt
    gepflegt. ``category`` ist ``empty`` oder ``weak``, ``active_seen`` sagt,
    ob das Problem im aktuellen Lauf noch besteht.

    Rueckgabe: (melden, art) mit art ``alert``/``recover``/``None``.
    """
    now = time.time() if now is None else now
    rec = state.get(name)
    if not isinstance(rec, dict):
        rec = {}

    if active_seen:
        cat = rec.get("cat")
        due = (now - float(rec.get("ts") or 0)) >= BATTERY_COOLDOWN
        # Verschlechterung (schwach -> leer) einmal sofort melden, sonst
        # hoechstens eine Erinnerung pro 24 h. Erstfeststellung: ts fehlt.
        worse = (cat == "weak" and category == "empty")
        if worse or due:
            rec.update({"cat": category, "ts": now, "active": True,
                        "alerted": True})
            state[name] = rec
            return True, "alert"
        rec["cat"] = category
        rec["active"] = True
        rec.setdefault("alerted", False)
        state[name] = rec
        return False, None

    if rec.get("active"):
        rec["active"] = False
        alerted = bool(rec.get("alerted"))
        rec["alerted"] = False
        state[name] = rec
        if alerted:
            return True, "recover"
    return False, None


def _config():
    try:
        with open(CONFIG) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _topic():
    return _config().get("ntfy_topic")


def diff(prev_names, new_names):
    """(neu dazugekommen, wieder behoben) als sortierte Namenslisten."""
    prev = set(prev_names or [])
    new = set(new_names or [])
    return sorted(new - prev), sorted(prev - new)


def join_names(names, limit=8):
    """Namensliste kompakt fuer eine Meldung (gekuerzt mit '+N weitere')."""
    names = list(names or [])
    head = names[:limit]
    text = ", ".join(head)
    if len(names) > limit:
        text += " (+{} weitere)".format(len(names) - limit)
    return text


def send(message, title=None, tags=None, priority=None):
    """Schickt eine Push-Nachricht an ntfy.sh/<topic>. Fehler werden ignoriert.

    Optionaler Zugang: ``ntfy_token`` in config.json wird als
    ``Authorization: Bearer <token>`` mitgeschickt (ntfy-Access-Token).
    """
    cfg = _config()
    topic = cfg.get("ntfy_topic")
    if not topic:
        return False
    url = "https://ntfy.sh/" + str(topic)
    token = str(cfg.get("ntfy_token") or "").strip()
    try:
        data = message.encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        if title:
            req.add_header("Title", title.encode("utf-8"))
        if tags:
            req.add_header("Tags", tags)
        if priority:
            req.add_header("Priority", str(priority))
        if token:
            req.add_header("Authorization", "Bearer " + token)
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "Test"
    print("gesendet" if send(msg, title="Test") else "fehlgeschlagen (kein ntfy_topic?)")