#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ntfy-Benachrichtigungen (stdlib-only). Topic aus config.json."""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


def _topic():
    try:
        with open(CONFIG) as f:
            return json.load(f).get("ntfy_topic")
    except Exception:
        return None


def send(message, title=None, tags=None, priority=None):
    """Schickt eine Push-Nachricht an ntfy.sh/<topic>. Fehler werden ignoriert."""
    topic = _topic()
    if not topic:
        return False
    url = "https://ntfy.sh/" + topic
    try:
        data = message.encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        if title:
            req.add_header("Title", title.encode("utf-8"))
        if tags:
            req.add_header("Tags", tags)
        if priority:
            req.add_header("Priority", str(priority))
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "Test"
    print("gesendet" if send(msg, title="Test") else "fehlgeschlagen (kein ntfy_topic?)")