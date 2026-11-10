# Serverraum Dashboard

Ressourcenarmes Terminal-Dashboard für den Serverraum – läuft auf einem alten
Netbook (CLEVO M815P, Intel Atom N450, 2 GB RAM, Debian 10) direkt auf `tty1`,
remote per SSH erreichbar.

Zeigt in einem mehrspaltigen ASCII-Layout:

- **Erreichbarkeit** aller Netzwerk-Geräte (Ping + optionaler HTTP-Check), gruppiert
- **Internet-Status** inkl. Verbindungsart (LAN/WLAN, IP, SSID) + regelmäßiger **Speedtest** (Down/Up)
- **Proxmox-Panel**: beide PVE-Nodes mit CPU/RAM/Disk-Auslastung und allen
  VMs/Containern inkl. RAM-Nutzung und Uptime je Gast - Start/Stop direkt aus dem Dashboard
- **UniFi-Block** unter dem Proxmox-Panel: Status der Access Points; Klick auf
  einen AP zeigt dessen WLAN-Geräte mehrspaltig im Lightbox-Modal
- **deCONZ/Phoscon-Block**: Anzahl aller Zigbee-Geräte; Klick bzw. `Enter`
  zeigt die Geräte nach Typ gruppiert mit online/offline
- **Freie Pfeilnavigation**: ↑/↓/←/→ springen räumlich zum nächsten Element
  (Geräte-Subspalten, Proxmox-Guests, APs, deCONZ)
- **Verwaltung im TUI**: Geräte anlegen / pausieren / löschen, Intervalle
  für Ping und Speedtest einstellen, Statusdauer je Gerät
- **ntfy-Meldungen pro Gerät**: auf Wunsch bei DOWN, UP oder beidem
- **Uptime** des Dashboards-Rechners in der Titelzeile

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
| `unifi.py`         | Fragt UniFi-Controller ab (APs, WLAN-Clients) → `unifi.json` |
| `deconz.py`        | Fragt deCONZ/Phoscon ab (Lights, Sensoren) → `deconz.json`  |
| `speedtest.py`     | Minimaler Down/Up-Speedtest → `speed.json`                  |
| `checker_loop.sh`  | Hintergrund-Loop: Ping/Proxmox/UniFi/deCONZ/Speedtest nach `config.json` |
| `boot_notify.py`   | Meldet nach dem Boot per ntfy, sobald Dashboard + Internet laufen |
| `notify.py`        | ntfy-Versand (Topic aus `config.json`)                      |
| `bin/dashboard`    | Shell-Befehl `dashboard` zum Starten der TUI                |
| `config.json`      | Geräteliste + Proxmox-/UniFi-/deCONZ-Zugangsdaten + Intervalle (**nicht im Repo**) |

## Steuerung

| Taste                | Aktion                                                |
|----------------------|-------------------------------------------------------|
| `Tab`                | Panel wechseln (**Geraete** → **Proxmox** → **UniFi** → **deCONZ**) |
| `↑`/`↓`/`←`/`→`, `j`/`k`/`h`/`l` | **Frei räumlich** zum nächsten Element in der Richtung (Geräte-Subspalten, Guests, APs, deCONZ) |
| `Enter`              | **Lightbox-Modal oeffnen** (Aktionen & Details)       |
| Mausklick            | Auf eine AP-Zeile: **Geraete dieses APs** anzeigen; auf den deCONZ-Block: **Typ-Übersicht** |
| `a`                  | **Geraet anlegen** (Name, IP/Host, optional HTTP-Port, Gruppe) |
| `Space`              | **Geraet pausieren / wieder aktivieren** (kein Ping, zaehlt nicht mit) |
| `d`                  | **Geraet loeschen** (mit Rueckfrage)                  |
| `c`                  | **Intervalle + Meldungen einstellen** (Ping, Speedtest, Offline/Online ab) |
| `n`                  | Im Geräte-Modal: **ntfy-Meldung** umschalten (aus → down → up → beides) |
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

- **UniFi AP Modal (Klick auf AP oder `Enter`)**:
  Zeigt alle WLAN-Geraete des Access Points in mehreren Spalten, inkl.
  Signalstaerke und WLAN-Name.
  - `[q]` / `[ESC]` Schliessen

## Geraeteverwaltung (direkt im Dashboard)

- `a` oeffnet ein Formular (Name, IP/Host, HTTP-Port optional, Gruppe per `<` / `>`).
  Gespeichert wird direkt in `config.json`, der Checker laeuft sofort.
- `Space` pausiert das gewaehlte Geraet: kein Ping mehr, zaehlt nicht in
  online/total und erscheint als `PAUSE` (Header: `⏸N`). Erneutes `Space`
  aktiviert es wieder.
- `d` loescht nach Rueckfrage, `Enter` zeigt Details inkl. derselben Aktionen.

Aenderungen werden atomar in `config.json` geschrieben (Modus 600) - kein
Editor und kein Dienst-Neustart noetig.

## Intervalle & Performance

In `config.json` (oder bequem per `c` im Dashboard):

| Feld | Standard | Bedeutung |
|---|---|---|
| `poll_interval` | 15 | Sekunden zwischen Ping-Runden (`fping`, 1 Paket/Host) |
| `speedtest_interval_min` | 30 | Minuten zwischen Speedtests (~500 KB pro Lauf) |
| `fping_timeout` | 800 | ms pro Host |
| `offline_after` | 3 | Fehlversuche in Folge, bis „Internet offline“ gilt |
| `online_after` | 2 | Erfolge in Folge, bis „wieder online“ gilt |

`checker_loop.sh` liest die Werte vor jedem Durchlauf - Aenderungen greifen
sofort, ohne Dienst-Neustart. Proxmox wird weiterhin alle 30 s abgefragt.

## Internet-Meldungen (entprellt)

Ein einzelner verlorener Ping loest **keine** Meldung mehr aus:

- „offline“ gilt erst nach `offline_after` Fehlversuchen in Folge (Standard 3,
  bei 15 s also ~45 s)
- „wieder online“ gilt erst nach `online_after` Erfolgen in Folge (Standard 2)
- gemeldet wird nur ein echter Zustandswechsel, inkl. Ausfalldauer und
  Verbindungsart (z. B. `WLAN 'WITZ!Box2' 192.168.178.67`)
- `checker.py` sperrt sich per `flock` gegen parallele Laeufe (verhinderte
  doppelte Meldungen)
- nach einem Neustart wird **nicht** sofort „wieder online“ gemeldet - das
  uebernimmt die Boot-Benachrichtigung

Der Rohwert der letzten Messung steht als `internet_raw` in `state.json`,
der entprellte Wert als `internet`.

## Verbindungsart im Kopf

Die Statuszeile zeigt hinter dem Internet-Status das aktive Interface:

```
● INTERNET ONLINE 2h · WLAN WITZ!Box2 192.168.178.67  36/40 Geraete ...
● INTERNET ONLINE 5d · LAN 192.168.178.20             12/12 Geraete ...
```

Ermittelt wird das Default-Route-Interface (`/proc/net/route`), die IP per
ioctl und die SSID ueber `iwgetid` (Fallback `nmcli`).

## Boot-Benachrichtigung

`boot_notify.py` (systemd-Unit `systemd/zero2-boot-notify.service`) wartet nach
dem Boot maximal 180 s, bis

1. `state.json` frisch ist (Checker laeuft),
2. `internet` true ist,
3. der `dashboard.py`-Prozess laeuft (Dashboard geladen ist),

und schickt dann **eine** ntfy-Nachricht mit Hostname, Verbindungsart, IP,
Ladezeit des Dashboards und Geraete-Status. Pro Boot wird das ueber
`/proc/sys/kernel/random/boot_id` nur einmal gesendet.

Die alte `ntfy-boot.service` (starrer 10-s-curl) wird dadurch ersetzt und
sollte deaktiviert werden:

```bash
sudo systemctl disable --now ntfy-boot.service
sudo cp systemd/zero2-boot-notify.service /etc/systemd/system/
sudo systemctl enable --now zero2-boot-notify.service
```

## Dashboard-Befehl

```bash
sudo install -m 755 bin/dashboard /usr/local/bin/dashboard
```

Danach startet `dashboard` die TUI von jeder Konsole aus (beendet mit `q`).
Der alte Alias `dash` kann als `alias dash='dashboard'` weiterlaufen.

## Bildschirm-Standby (Strom sparen)

Der Kernel blankt die Textkonsole nach Inaktivitaet und weckt sie per
Tastendruck wieder auf:

```bash
# /boot/firmware/cmdline.txt: consoleblank=0 entfernen, consoleblank=300 = 5 min
sudo sed -i 's/ *consoleblank=[0-9]*//g; s/$/ consoleblank=300/' /boot/firmware/cmdline.txt
sudo reboot
```

Pruefen: `cat /proc/cmdline` (enthaelt `consoleblank=300`) und
`cat /sys/module/kernel/parameters/consoleblank`.

## Benachrichtigungen pro Gerät

Standardmäßig wird pro Gerät **nichts** gemeldet. Im Geräte-Modal (`Enter`)
schaltet `n` durch:

| Modus | Meldung |
|---|---|
| `aus` | keine Meldung (Standard) |
| `nur DOWN` | „*Gerät* offline – war X online" |
| `nur UP` | „*Gerät* wieder erreichbar – war X offline" |
| `UP + DOWN` | beide Richtungen |

In der Geräteliste markiert ein gelber Punkt `•` die Geräte mit aktiver Meldung.
In `config.json` steht das als `"notify": "down" | "up" | "both"` (fehlt =
aus). Gesendet wird nur bei echtem Statuswechsel; solange das Internet selbst
weg ist, werden Geräte-Meldungen unterdrückt, und Geräte, die während eines
Internet-Ausfalls down gingen, lösen danach keine Einzel-Flut aus (max. 5
Meldungen pro Durchlauf, Rest als Sammelmeldung).

## Uptime in der Titelzeile

```
 SERVERRAUM DASHBOARD  ⠋  zero2 / Zero 2 W / Debian 13  up 3d4h        18:49:42
```

Gelesen wird `/proc/uptime`; bei schmalem Terminal entfällt zuerst die
Hardware-/OS-Info, die Uptime bleibt stehen.

Im Proxmox-Panel steht hinter jedem laufenden CT/VM ebenfalls die Uptime
(`up 86d5h`), im Detail-Modal zusätzlich ausgeschrieben.

## UniFi-Block

Unter dem Proxmox-Panel steht ein kompakter UniFi-Block. Er zeigt **nur die
Access Points**; die Geräte erscheinen erst im AP-Modal:

```
⬤ UniFi  2/2 APs · 44 WLAN · 12 LAN
  ▲ oben  up 345d22h                      AC Pro 16 Ger
  ▲ unten  up 345d22h                     AC Pro 28 Ger
```

- Kopfzeile: online/gesamt APs, Anzahl WLAN-Clients, dazu LAN-Clients
- AP-Zeilen: Uptime hinter dem Namen, Modell + Clientzahl rechts; ausgefallene
  APs erscheinen rot mit `offline`
- **Klick auf eine AP-Zeile** (oder `Tab` bis `PROXMOX + UNIFI`, dann `↑`/`↓`
  und `Enter`) öffnet das AP-Modal
- Ist der Controller nicht erreichbar, bleiben die letzten Daten sichtbar und
  der Block zeigt `Controller nicht erreichbar (Daten vor …)`

### AP-Modal (Geräte am Access Point)

Das Modal zeigt alle WLAN-Geräte des gewählten APs als **mehrspaltige
Übersicht** (bis zu 4 Spalten, je nach Terminalbreite), alphabetisch sortiert
und mit farbiger Signalstärke:

```
╔══════════════════════════════════════════════════════════════════╗
║ UNIFI AP: unten  (AC Pro)                                        ║
╠══════════════════════════════════════════════════════════════════╣
║ Status: ONLINE  ·  28 Geraete  ·  up 345d22h  ·  192.168.178.40  ║
║ Modell: AC Pro  ·  Firmware: 6.6.77.15402                        ║
║ WLAN: WITZ!Box2 23 · WITZ!2.4 5                                  ║
║──────────────────────────────────────────────────────────────────║
║ GERAETE AN DIESEM AP                                             ║
║ · 192.168.178.139 -77dBm · amazon-60d196882 -52dBm · carport ... ║
║ ...                                                              ║
║──────────────────────────────────────────────────────────────────║
║ [ESC] oder [q] schliessen                                        ║
╚══════════════════════════════════════════════════════════════════╝
```

- dBm-Farben: grün ≥ -60, gelb ≥ -70, orange ≥ -78, rot darunter
- Bei wenig Platz wird die Liste mit `+N weitere Geraete` gekürzt

Zugang in `config.json` (Modus 600, **nicht ins Repo**):

```json
"unifi_interval_sec": 60,
"unifi": {
  "url": "https://192.168.178.175:8443",
  "username": "admin",
  "password": "…",
  "site": "default",
  "timeout": 8,
  "verify_tls": false
}
```
`unifi.py` nutzt den klassischen Login (`/api/login`); bei UniFi OS wird
automatisch `/api/auth/login` + `/proxy/network` verwendet. Abgefragt werden
`stat/device` (APs), `stat/sta` (Clients) und `stat/health`.

## deCONZ-Block

Unter dem UniFi-Block steht – durch eine Leerzeile abgesetzt – ein kompakter
deCONZ-/Phoscon-Block. Im Hauptbildschirm zeigt er **nur die Anzahl** aller
Zigbee-Geräte:

```
⬡ deCONZ  104 Geraete
```

- Farbe: grün = alle verbunden, gelb = einzelne offline, rot = keine Verbindung
- **Klick auf den Block** (oder `Tab` bis `PROXMOX + UNIFI`, dann `↓` bis
  deCONZ und `Enter`) öffnet die Detailübersicht
- Ist das Gateway nicht erreichbar, bleiben die letzten Zahlen sichtbar
  (`nicht erreichbar (Daten vor …)`)

### deCONZ-Modal (nach Typ gruppiert)

```
╔════════════════════════════════════════════════════════════════════╗
║ DECONZ / PHOSCON: Phoscon-GW                                       ║
╠════════════════════════════════════════════════════════════════════╣
║ 104 Geraete   91 verbunden  ·  13 offline (16 Typen)               ║
║ Gateway 2.32.1 · Firmware 0x26780700 · Kanal 20                    ║
║────────────────────────────────────────────────────────────────────║
║ TYP                                           GES  ONLINE OFFLINE  ║
║ ▸ Bewegung/Praesenz                             15      14       1 ║
║ ▸ Steckdose                                     18      15       3 ║
║ ...                                                                ║
╚════════════════════════════════════════════════════════════════════╝
```

- gezählt werden deCONZ-`lights` und `sensors`
- „verbunden“ kommt aus `state.reachable` (Lights) bzw. `config.reachable`
  (Sensoren); virtuelle Einträge (`Daylight`, `CLIPDaylightOffset`) gelten als
  verbunden
- mehrere Roh-Typen werden zu lesbaren Gruppen zusammengefasst, z. B.
  `On/Off plug-in unit` + `Smart plug` → **Steckdose**
- bei wenig Platz: `+N weitere Typen`

Zugang in `config.json` (Modus 600, **nicht ins Repo**):

```json
"deconz_interval_sec": 60,
"deconz": {
  "url": "http://192.168.178.247",
  "apikey": "…",
  "timeout": 6
}
```

Den API-Key legt man in Phoscon unter *Einstellungen → Gateway → API-Schlüssel*
an (oder nutzt einen vorhandenen). Abgefragt wird `<url>/api/<apikey>/`.

## Statusdauer

`checker.py` schreibt pro Geraet `since`, `last_down` und `last_up` in
`state.json`:

- Geraeteliste: Dauer des aktuellen Zustands hinter der Latenz (`4d3h`, `5h12`, `45m`)
- Statuszeile: `INTERNET ONLINE` + Dauer des aktuellen Zustands
- Modal (`Enter`): `Online seit 10.11. 18:02 (2h 05m)`, `Zuletzt down ...`, `Zuletzt up ...`

Die Historie bleibt bei einem Dienst-Neustart erhalten (sie wird aus dem alten
`state.json` fortgeschrieben), solange das Geraet in der Config bleibt.

## Installation

```bash
sudo apt install fping
git clone https://github.com/rockthenet/serverraum-dashboard.git
cd serverraum-dashboard
sudo mkdir -p /opt/netbook-dashboard
sudo cp *.py *.sh /opt/netbook-dashboard/
sudo install -m 755 bin/dashboard /usr/local/bin/dashboard
cp config.example.json /opt/netbook-dashboard/config.json
# config.json anpassen: Geräte + Proxmox-Tokens + UniFi-/deCONZ-Zugang eintragen
```

### Proxmox-Token erzeugen

Auf jedem PVE-Host:

```bash
pveum user token add root@pam backup-script --privsep=0
```

Den `full-tokenid` + `value` in `config.json` unter `proxmox_nodes[].token`
im Format `root@pam!backup-script=<value>` eintragen.

### Backup-Ziel (Storage)

Die Backup-Funktion (`[5]` im Proxmox-Modal) nutzt den Storage, der in
Proxmox den **Content-Typ `backup`** hat. `local` scheidet damit automatisch
aus (Fehler: *"can't use storage 'local' for backups"*).

Pro Node kann das Ziel fest vorgegeben werden:

```json
"proxmox_nodes": [
  {
    "label": "Proxmox 1 (pve)",
    "url": "https://192.168.178.171:8006",
    "token": "root@pam!backup-script=...",
    "backup_storage": "backup-nas-smb"
  }
]
```

Ohne `backup_storage` wird automatisch erkannt (Prioritaet: Name enthaelt `nas`,
dann `backup`, `local` nur als letzte Wahl). Ist `snapshot` als Modus nicht
moeglich, faellt das Dashboard automatisch auf `suspend` und dann `stop` zurueck.

### Autostart auf tty1 (Kiosk)

`~/.bash_profile`:

```bash
if [ "$(tty)" = "/dev/tty1" ]; then
    export TERM=linux
    clear
    /usr/bin/python3 /opt/netbook-dashboard/dashboard.py
fi
```

*(Wichtig: **kein** `exec`, damit `q` sauber zur Bash-Shell beendet. Zum erneuten Starten genügt `dashboard`.)*

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
