#!/usr/bin/env python3
"""Proxmox-Poller: fragt beide PVE-Nodes ab, schreibt proxmox.json.
   Nutzt nur stdlib (urllib + ssl). Laeuft im checker_loop mit."""
import json
import os
import ssl
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
OUT = os.path.join(HERE, "proxmox.json")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api_get(base_url, token, path):
    """GET auf PVE-API, gibt 'data' zurueck oder None."""
    url = base_url.rstrip("/") + "/api2/json" + path
    req = urllib.request.Request(url)
    req.add_header("Authorization", "PVEAPIToken=" + token)
    try:
        resp = urllib.request.urlopen(req, timeout=8, context=CTX)
        payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("data")
    except Exception:
        return None


def poll_node(node_cfg):
    """Fragt einen PVE-Host komplett ab: Node-Ressourcen + VMs + CTs."""
    base = node_cfg["url"]
    token = node_cfg["token"]
    result = {
        "label": node_cfg.get("label", base),
        "online": False,
        "node": None,
        "guests": [],
    }

    nodes = api_get(base, token, "/nodes")
    if not nodes:
        return result

    nd = nodes[0]
    node_name = nd.get("node")
    result["online"] = True
    result["node"] = {
        "name": node_name,
        "cpu": round(nd.get("cpu", 0) * 100, 1),
        "mem": nd.get("mem", 0),
        "maxmem": nd.get("maxmem", 1),
        "disk": nd.get("disk", 0),
        "maxdisk": nd.get("maxdisk", 1),
        "uptime": nd.get("uptime", 0),
    }

    # QEMU VMs
    vms = api_get(base, token, "/nodes/{}/qemu".format(node_name)) or []
    for vm in vms:
        result["guests"].append({
            "vmid": vm.get("vmid"),
            "name": vm.get("name", "vm"),
            "type": "VM",
            "status": vm.get("status", "unknown"),
            "mem": vm.get("mem", 0),
            "maxmem": vm.get("maxmem", 1),
            "cpu": round(vm.get("cpu", 0) * 100, 1),
            "uptime": vm.get("uptime", 0),
        })

    # LXC Container
    cts = api_get(base, token, "/nodes/{}/lxc".format(node_name)) or []
    for ct in cts:
        result["guests"].append({
            "vmid": ct.get("vmid"),
            "name": ct.get("name", "ct"),
            "type": "CT",
            "status": ct.get("status", "unknown"),
            "mem": ct.get("mem", 0),
            "maxmem": ct.get("maxmem", 1),
            "cpu": round(ct.get("cpu", 0) * 100, 1),
            "uptime": ct.get("uptime", 0),
        })

    # Sortieren: running zuerst, dann nach vmid
    result["guests"].sort(key=lambda g: (g["status"] != "running", g["vmid"] or 0))
    return result


def main():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    pve_nodes = cfg.get("proxmox_nodes", [])
    out = {"time": time.time(), "nodes": []}

    for node_cfg in pve_nodes:
        out["nodes"].append(poll_node(node_cfg))

    with open(OUT, "w") as f:
        json.dump(out, f)

    total_guests = sum(len(n["guests"]) for n in out["nodes"])
    online_nodes = sum(1 for n in out["nodes"] if n["online"])
    print("Proxmox: {}/{} Nodes online, {} Guests".format(
        online_nodes, len(pve_nodes), total_guests))


if __name__ == "__main__":
    main()