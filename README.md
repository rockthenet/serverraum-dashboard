# Serverraum Dashboard

Ressourcenarmes Terminal-Dashboard für den Serverraum – läuft auf einem alten
Netbook (CLEVO M815P, Intel Atom N450, 2 GB RAM, Debian 10) direkt auf `tty1`,
remote per SSH erreichbar.

Zeigt in einem mehrspaltigen ASCII-Layout:

- **Erreichbarkeit** aller Netzwerk-Geräte (Ping + optionaler HTTP-Check), gruppiert
- **Internet-Status** + regelmäßiger **Speedtest** (Down/Up)
- **Proxmox-Panel**: beide PVE-Nodes mit CPU/RAM/Disk-Auslastung und allen
  VMs/Containern inkl. RAM-Nutzung — Start/Stop direkt aus dem Dashboard

Nur **Python 3 stdlib + `fping`** — kein pip, kein venv, kein Framework.

```
┌──────────────────────────────────────────┬─────────────────────────────────┐
│ ▸ Proxmox / Virtualisierung [3/4]         │ ⬤ Proxmox 1 (pve)  up 233d21h   │
│  ● Proxmox Backend      0ms               │  CPU ███░░░░ RAM ██████░ DSK ███ │
│  ○ Zero Pi (Docker)     DOWN              │  ▶ CT ubuntu      ████░░ 1.3/2.0G│
│ ▸ Netzwerk [5/6]                          │  ▶ VM iobroker2022 ████░ 4.6/6.4G│
│  ● FRITZ!Box 6590       0ms               │  ■ CT pihole           stopped   │
└──────────────────────────────────────────┴─────────────────────────────────┘
```

## Komponenten

| Datei              | Zweck                                                       |
|--------------------|-------------------------------------------------------------|
| `dashboard.py`     | Die TUI (ANSI-Rendering, Tastatur/Maus, Proxmox-Aktionen)   |
| `checker.py`       | Ping- + HTTP-Check aller Geräte → `state.json`              |
| `proxmox.py`       | Fragt PVE-API ab (Nodes, VMs, CTs) → `proxmox.json`         |
| `speedtest.py`     | Minimaler Down/Up-Speedtest → `speed.json`                  |
| `checker_loop.sh`  | Hintergrund-Loop: Ping 15 s, Proxmox 30 s, Speedtest 30 min |
| `config.json`      | Geräteliste + Proxmox-Zugangsdaten (**nicht im Repo**)      |

## Steuerung

| Taste        | Aktion                              |
|--------------|-------------------------------------|
| `q`          | Beenden                             |
| `r`          | Ping-Check sofort                   |
| `p`          | Proxmox sofort neu abfragen         |
| `↑`/`↓`, `j`/`k` | Guest auswählen (Proxmox-Panel) |
| `s`          | Ausgewählten Guest **starten**      |
| `x`          | Ausgewählten Guest **herunterfahren** |
| Maus         | Guest anklicken (SSH/xterm)         |

## Installation

```bash
sudo apt install fping
git clone https://github.com/rockthenet/serverraum-dashboard.git
cd serverraum-dashboard
sudo mkdir -p /opt/netbook-dashboard
sudo cp *.py *.sh /opt/netbook-dashboard/
cp config.example.json /opt/netbook-dashboard/config.json
# config.json anpassen: Geräte + Proxmox-Tokens eintragen
```

### Proxmox-Token erzeugen

Auf jedem PVE-Host:

```bash
pveum user token add root@pam backup-script --privsep=0
```

Den `full-tokenid` + `value` in `config.json` unter `proxmox_nodes[].token`
im Format `root@pam!backup-script=<value>` eintragen.

### Autostart auf tty1 (Kiosk)

`~/.bash_profile`:

```bash
if [ "$(tty)" = "/dev/tty1" ]; then
    export TERM=linux
    clear
    exec /usr/bin/python3 /opt/netbook-dashboard/dashboard.py
fi
```

Datensammler als systemd-Service (`systemd/netbook-checker.service`):

```bash
sudo cp systemd/netbook-checker.service /etc/systemd/system/
sudo systemctl enable --now netbook-checker.service
```

Autologin auf `tty1` via `getty@tty1.service.d/autologin.conf`:

```ini
[Service]
Type=idle
ExecStart=
ExecStart=-/sbin/agetty --autologin bp --noclear %I $TERM
```

## Netzwerklast

`fping` sendet **ein** ICMP-Paket pro Host und Durchlauf. Bei ~42 Geräten alle
15 s sind das knapp 3 Pakete/s im Mittel — vernachlässigbar, stört nichts im LAN.

## Lizenz

MIT
