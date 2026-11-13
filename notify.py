#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ntfy-Benachrichtigungen (stdlib-only). Topic aus config.json."""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


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