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

| Taste                | Aktion                                                |
|----------------------|-------------------------------------------------------|
| `Tab` / `←`/`→`, `h`/`l` | Panel wechseln (**Geraete** <-> **Proxmox**)       |
| `↑`/`↓`, `j`/`k`     | Element im aktiven Panel auswaehlen                   |
| `Enter`              | **Lightbox-Modal oeffnen** (Aktionen & Details)       |
| `1`..`5`             | Im Proxmox-Modal: Start / Shutdown / Reboot / Reset / Backup |
| `q` / `ESC`          | Modal schliessen (wenn offen) bzw. **Dashboard beenden** (zu Bash) |
| `s` / `x`            | Quick-Start / Quick-Stop fuer gewaehlten Proxmox-Guest |
| `r` / `p`            | Ping-Check bzw. Proxmox-Status sofort aktualisieren   |

### Lightbox Modals

- **Proxmox Guest Modal (`Enter`)**:
  Zeigt VMID, Node, Status, Uptime, CPU- & RAM-Balken.
  Aktionen direkt per Tastendruck:
  - `[1]` Start
  - `[2]` Shutdown (sauber per ACPI)
  - `[3]` Reboot
  - `[4]` Stop / Reset (Force Kill)
  - `[5]` **Backup erstellen** (Snapshot vzdump ueber PVE-API)
  - `[q]` / `[ESC]` Schliessen

- **Geraete Modal (`Enter`)**:
  Zeigt IP, Gruppe, Typ, Ping-RTT und HTTP-Port-Status.
  - `[r]` Ping sofort wiederholen
  - `[q]` / `[ESC]` Schliessen

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
    /usr/bin/python3 /opt/netbook-dashboard/dashboard.py
fi
```

*(Wichtig: **kein** `exec`, damit `q` sauber zur Bash-Shell beendet. In `~/.bashrc` kann ein Alias `alias dash='python3 /opt/netbook-dashboard/dashboard.py'` angelegt werden).*

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
