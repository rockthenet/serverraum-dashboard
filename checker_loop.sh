#!/bin/bash
# Serverraum Dashboard - Background Datensammler
# Ping alle 15s, Proxmox alle 30s, Speedtest alle 30min
cd /opt/netbook-dashboard
PROX=0
SPEED=0
# Initial sofort
python3 proxmox.py >/dev/null 2>&1 &
while true; do
    python3 checker.py >/dev/null 2>&1
    PROX=$((PROX + 1))
    if [ $PROX -ge 2 ]; then
        python3 proxmox.py >/dev/null 2>&1 &
        PROX=0
    fi
    SPEED=$((SPEED + 1))
    if [ $SPEED -ge 120 ]; then
        python3 speedtest.py >/dev/null 2>&1 &
        SPEED=0
    fi
    sleep 15
done