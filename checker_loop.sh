#!/bin/bash
# Serverraum Dashboard - Hintergrund-Datensammler
#
# Die eigentliche Ablaufsteuerung steht in scheduler.py: alle Abfragen laufen
# sequenziell, mit eigenen Intervallen, Zeitversatz und Backoff. Diese Huelle
# bleibt der Einstiegspunkt fuer den systemd-Dienst netbook-checker.
cd "$(dirname "$0")" || cd /opt/netbook-dashboard
exec python3 scheduler.py
