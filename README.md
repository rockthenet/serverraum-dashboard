# Serverraum Dashboard

Ressourcenarmes Terminal-Dashboard für den Serverraum – läuft auf einem alten
Netbook (CLEVO M815P, Intel Atom N450, 2 GB RAM, Debian 10) direkt auf `tty1`,
remote per SSH erreichbar.

Zeigt in einem mehrspaltigen ASCII-Layout:

- **Erreichbarkeit** aller Netzwerk-Geräte (Ping + optionaler HTTP-Check), gruppiert
- **Internet-Status** inkl. Verbindungsart (LAN/WLAN, IP, SSID) + **maximale
  WAN-Rate (`WAN max`) und aktuelle Auslastung direkt von der FRITZ!Box**
  (Speedtest nur als Fallback)
- **Proxmox-Panel**: beide PVE-Nodes mit CPU/RAM/Disk-Auslastung und allen
  VMs/Containern inkl. RAM-Nutzung und Uptime je Gast - Start/Stop direkt aus dem Dashboard;
  `Enter` auf dem Node-Kopf fährt den **gesamten Server** kontrolliert herunter oder startet ihn neu
- **UniFi-Block** unter dem Proxmox-Panel: Status und Qualitätswert (`satisfaction`)
  der Access Points; Klick auf einen AP zeigt Qualität pro Funkband, Link, CPU/RAM
  und dessen WLAN-Geräte mehrspaltig im Lightbox-Modal
- **deCONZ/Phoscon-Block**: Anzahl der **konsolidierten** Zigbee-Geräte (mehrere
  Sensor-Ressourcen je Gerät werden zusammengefasst); Klick bzw. `Enter` zeigt
  Typen, online/offline und die **Akku-Übersicht** (schwach/leer/ohne Akku)
- **HomeMatic-Block**: Geräte der CCU über die XML-API — live, Akku leer/schwach
  und Funkverbindung sehr schlecht/mäßig; Klick bzw. `m` zeigt die Details
  nach Problemgruppe und Gerätetyp
- **Freie Pfeilnavigation**: ↑/↓/←/→ springen räumlich zum nächsten Element
  (Geräte-Subspalten, Proxmox-Guests, APs, deCONZ)
- **Verwaltung im TUI**: Geräte anlegen / pausieren / löschen, Intervalle
  für Ping und Speedtest einstellen, Statusdauer je Gerät
- **ntfy-Meldungen pro Gerät**: auf Wunsch bei DOWN, UP oder beidem
- **Uptime**, **CPU-** und **RAM-Auslastung** des Dashboards-Rechners in der Titelzeile

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
| `homematic.py`     | Fragt die HomeMatic-CCU über die XML-API ab → `homematic.json` |
| `fritzbox.py`      | Liest WAN-/Leitungsdaten per TR-064 von der FRITZ!Box → `fritzbox.json` |
| `speedtest.py`     | Minimaler Down/Up-Speedtest (Fallback) → `speed.json`       |
| `scheduler.py`     | Sequenzieller Poller: startet Ping/Proxmox/UniFi/deCONZ/HomeMatic/FRITZ!Box/Speedtest nacheinander |
| `checker_loop.sh`  | Dünne Hülle für den systemd-Dienst `netbook-checker` → `scheduler.py` |
| `boot_notify.py`   | Meldet nach dem Boot per ntfy, sobald Dashboard + Internet laufen |
| `notify.py`        | ntfy-Versand (Topic aus `config.json`)                      |
| `bin/dashboard`    | Shell-Befehl `dashboard` zum Starten der TUI                |
| `config.json`      | Geräteliste + Proxmox-/UniFi-/deCONZ-/FRITZ!Box-Zugangsdaten + Intervalle (**nicht im Repo**) |

## Steuerung

| Taste                | Aktion                                                |
|----------------------|-------------------------------------------------------|
| `Tab`                | Panel wechseln (**Geraete** → **Proxmox** → **UniFi** → **deCONZ** → **HomeMatic**) |
| `↑`/`↓`/`←`/`→`, `j`/`k`/`h`/`l` | **Frei räumlich** zum nächsten Element in der Richtung (Geräte-Subspalten, Proxmox-Nodes/Guests, APs, deCONZ, HomeMatic) |
| `Enter`              | **Lightbox-Modal oeffnen** (Aktionen & Details)       |
| Mausklick            | Auf eine AP-Zeile: **Geraete dieses APs**; auf den deCONZ-Block: **Typ-/Akku-Übersicht**; auf den HomeMatic-Block: **Akku-/Funk-Details**; auf Node/Gast: **Server-/Gast-Modal**; auf die Statuszeile: **FRITZ!Box-Modal** |
| `m`                  | **HomeMatic-Modal** (live, Akku leer/schwach, Funk schlecht) |
| `a`                  | **Geraet anlegen** (Name, IP/Host, optional HTTP-Port, Gruppe) |
| `Space`              | **Geraet pausieren / wieder aktivieren** (kein Ping, zaehlt nicht mit) |
| `d`                  | **Geraet loeschen** (mit Rueckfrage)                  |
| `c`                  | **Intervalle + Meldungen einstellen** (Ping, Speedtest, Offline/Online ab) |
| `f`                  | **FRITZ!Box-Modal** (Leitung, aktuelle Auslastung, Peak, Traffic) |
| `n`                  | Im Geräte-Modal: **ntfy-Meldung** umschalten (aus → down → up → beides) |
| `1`..`5`             | Im Proxmox-Guest-Modal: Start / Shutdown / Reboot / Reset / Backup |
| `s` / `r`            | Im Proxmox-Server-Modal: **ganzen Node herunterfahren / neu starten** (mit `j` bestaetigen) |
| `q` / `ESC`          | Modal schliessen (wenn offen) bzw. **Dashboard beenden** (zu Bash) |
| `s` / `x`            | Quick-Start / Quick-Stop fuer gewaehlten Proxmox-Guest |
| `r` / `p`            | Ping-Check bzw. Proxmox-Status sofort aktualisieren   |

### Lightbox Modals

- **Proxmox Server Modal (`Enter` auf dem Node-Kopf `⬤ Proxmox 1/2`)**:
  Zeigt Node-Name, Uptime, CPU-/RAM-/Disk-Auslastung und die Anzahl laufender
  Gäste. Für Stromausfall/Elektrikarbeiten lässt sich der **gesamte Server**
  kontrolliert herunterfahren:
  - `[s]` Gesamten Server herunterfahren (alle VMs/CTs werden sauber beendet)
  - `[r]` Gesamten Server neu starten
  - Danach Sicherheitsabfrage mit `[j]` Ja / `[n]` Abbrechen
  - `[q]` / `[ESC]` Schliessen

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
  Zeigt Qualitaet, Link-Geschwindigkeit, CPU/RAM, pro Funkband Kanal,
  Kanalauslastung und alle WLAN-Geraete des Access Points in mehreren Spalten.
  - `[q]` / `[ESC]` Schliessen

- **FRITZ!Box Modal (`f` oder Klick auf die Statuszeile)**:
  Zeigt Modell, Firmware, Link-Status, Uptime, die vom Anbieter bereitgestellte
  Leitungsrate, die aktuelle Auslastung mit Balken, Peak der letzten Messwerte,
  Traffic seit Neustart und die Sync-Gruppe (z. B. `sync_cable`).
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
| `speedtest_interval_min` | 30 | Minuten zwischen Speedtests (~500 KB pro Lauf); entfällt, solange die FRITZ!Box Leitungsdaten liefert |
| `fritzbox_interval_sec` | 60 | Sekunden zwischen TR-064-Abfragen der FRITZ!Box |
| `proxmox_interval_sec` | 30 | Sekunden zwischen PVE-Abfragen (Nodes, VMs, CTs) |
| `unifi_interval_sec` | 60 | Sekunden zwischen UniFi-Abfragen (APs, WLAN-Clients) |
| `deconz_interval_sec` | 60 | Sekunden zwischen deCONZ-Abfragen (Lights, Sensoren) |
| `homematic_interval_sec` | 60 | Sekunden zwischen HomeMatic-CCU-Abfragen (XML-API) |
| `fping_timeout` | 800 | ms pro Host |
| `offline_after` | 3 | Fehlversuche in Folge, bis „Internet offline“ gilt |
| `online_after` | 2 | Erfolge in Folge, bis „wieder online“ gilt |

`checker_loop.sh` ist nur noch eine Hülle um **`scheduler.py`**. Der Scheduler
liest die Werte vor jedem Job neu — Änderungen greifen sofort, ohne
Dienst-Neustart.

### Sequenzieller Scheduler (Performance)

Früher liefen alle Abfragen gleichzeitig; bei mehreren geöffneten Dashboards
kam die Zero 2 W schnell an ihre Grenzen. `scheduler.py` arbeitet deshalb so:

- **immer nur ein Kindprozess** — keine parallelen HTTP-/SSH-Abfragen
- **feste Reihenfolge/Priorität**: `checker` → `proxmox` → `fritzbox` →
  `unifi` → `deconz` → `homematic` → `speedtest`
- **Phasenversatz** je Job (0/4/12/28/44 s), damit die Abfragen nicht bündeln
- **Timeout** pro Job (30–90 s); ein hängender Job blockiert die anderen nicht
- **Backoff** bei Fehlern: 2×, 4×, maximal 4× Intervall
- **`nice`** für Scheduler und Kinder, dazu ein **Lastschutz**: ist der
  Load-Schnitt über `max(1.5, Kerne/2)`, warten unkritische Jobs 3 s
- **Speedtest-Guard**: liefert `fritzbox.json` gültige Leitungsdaten
  (`ok=true`, `down_bps>0`), wird der eigene Speedtest übersprungen
- **flock** auf `scheduler.lock` — es läuft nie mehr als ein Scheduler

Das Dashboard selbst zeichnet nur noch **einmal pro Sekunde** neu und liest die
JSON-Dateien über einen **mtime/Größen-Cache**; unveränderte Dateien werden
nicht erneut geparst.

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

## Uptime, CPU und RAM in der Titelzeile

```
 SERVERRAUM DASHBOARD  ⠋  zero2 / Zero 2 W / Debian 13  CPU 12  RAM 55  up 3d4h   18:49:42
```

- **CPU** kommt aus `/proc/stat` (Differenz seit dem letzten Redraw, also die
  Auslastung des Dashboard-Rechners, nicht der überwachten Server)
- **RAM** aus `/proc/meminfo` (`MemTotal` − `MemAvailable`)
- Farbe: grün < 70 %, gelb < 85 %, orange < 95 %, sonst rot
- bei schmalem Terminal wird zuerst die Hardware-/OS-Info gekürzt, dann
  `CPU`/`RAM` zu `C`/`R`; die Uptime bleibt stehen

Gelesen wird `/proc/uptime`; im Proxmox-Panel steht hinter jedem laufenden
CT/VM ebenfalls die Uptime (`up 86d5h`), im Detail-Modal zusätzlich
ausgeschrieben.

## Zwei Instanzen: Kiosk + eine Shell

Es dürfen **zwei** Dashboards gleichzeitig laufen: der Kiosk auf dem
physischen Bildschirm (`/dev/tty1`) und **eine** Sitzung über SSH.

- eine **neue Shell-Sitzung** beendet die vorherige Shell-Sitzung
  (`SIGTERM`, sauberes Aufräumen des Terminals); der Kiosk läuft weiter
- der **Kiosk** wird von einer Shell-Sitzung nie beendet
- ein **Kiosk-Neustart** ersetzt nur einen alten Kiosk-Prozess, nie die
  externe Sitzung
- registriert wird das in `dashboard.lock` als `PID TTY ROLLE` (`k` = Kiosk,
  `e` = extern); abgestürzte Einträge werden per `kill(pid, 0)` aussortiert
- lässt sich eine alte externe Instanz nicht beenden, startet die neue nicht
  (Meldung mit PID)

Damit bleibt der Bildschirm im Serverraum immer aktuell, ohne dass sich
mehrere Shell-Sitzungen gegenseitig die CPU wegnehmen.

## FRITZ!Box-Block (WAN/Leitung)

Statt eines eigenen Speedtests liest `fritzbox.py` die Daten direkt per
**TR-064** (SOAP über HTTP, Digest-Auth) von der FRITZ!Box. In der Statuszeile
steht damit die vom Anbieter bereitgestellte Leitungsrate plus die aktuelle
Auslastung:

```
● INTERNET ONLINE 22h48 · WLAN WITZ!Box2 192.168.178.67   36/40 Geraete
  WAN max ↓50.5 / ↑3.8 Mbps jetzt 0.03/0.07   next ░░░░░
```

- `WAN max ↓/↑` = `Newmax_ds`/`Newmax_us` aus `X_AVM-DE_GetOnlineMonitor` (die
  tatsächlich provisionierte Kabel-/DSL-Rate des Tarifs, **nicht** die
  physikalische Layer-1-Rate)
- `jetzt` = neuester Wert aus `Newds_current_bps`/`Newus_current_bps`
  (20 kommagetrennte Messwerte, Index 0 ist der neueste)
- bei schmalem Terminal entfallen zuerst `jetzt …`, dann die Nachkommastellen,
  dann das Wort `max`
- **`f`** oder Klick auf die Statuszeile öffnet das FRITZ!Box-Modal mit Modell,
  Firmware, Uptime, **maximaler Anbieter-Rate**, DOCSIS-/Layer-1-Kapazität,
  aktueller Auslastung (Balken + Prozent relativ zur Maximalrate), Peak der
  letzten 20 Werte, Traffic seit Neustart und Sync-Gruppe
- Liefert die Box keine Daten (`ok=false`), bleibt die letzte Anzeige erhalten
  und der eigene Speedtest übernimmt wieder (`speed.json`)

Zugang in `config.json` (Modus 600, **nicht ins Repo**):

```json
"fritzbox_interval_sec": 60,
"fritzbox": {
  "enabled": true,
  "host": "192.168.178.1",
  "port": 49000,
  "user": "fritz3219",
  "password": "…",
  "timeout": 8
}
```

Abgefragt werden `DeviceInfo:GetInfo`, `WANCommonInterfaceConfig:
GetCommonLinkProperties`, `X_AVM-DE_GetOnlineMonitor` (SyncGroup 0),
`GetTotalBytesReceived` und `GetTotalBytesSent`. Die FRITZ!Box muss TR-064 im
Web-UI zulassen (*Heimnetz → Netzwerk → Netzwerkeinstellungen → Zugriff für
Anwendungen*). Ein eingebauter Speedtest ist über TR-064 nicht verfügbar
(`X_AVM-DE_Speedtest` liefert auf der 6590 Cable nur eine leere Statistik).

`scheduler.py` startet den Poller alle `fritzbox_interval_sec`; der eigene
`speedtest.py` läuft nur noch, wenn `fritzbox.json` fehlt oder `ok != true` ist.

## UniFi-Block

Unter dem Proxmox-Panel steht ein kompakter UniFi-Block. Er zeigt **nur die
Access Points**; die Geräte erscheinen erst im AP-Modal:

```
⬤ UniFi  2/2 APs · 44 WLAN · 12 LAN
  ▲ oben  up 345d22h  Q96%                AC Pro 16 Ger
  ▲ unten  up 345d22h  Q97%               AC Pro 28 Ger
```

- Kopfzeile: online/gesamt APs, Anzahl WLAN-Clients, dazu LAN-Clients
- AP-Zeilen: Uptime hinter dem Namen, **Qualitaetswert `Q…%`** (UniFi
  `satisfaction`) und Modell + Clientzahl rechts; ausgefallene APs erscheinen
  rot mit `offline`
- Qualitaetsfarben: grün ≥ 90, gelb ≥ 75, orange ≥ 60, rot darunter. Ein
  dauerhaft niedriger Wert ist ein Hinweis auf ein Problem am AP (ggf. Neustart)
- **Klick auf eine AP-Zeile** (oder `Tab` bis `PROXMOX + UNIFI`, dann `↑`/`↓`
  und `Enter`) öffnet das AP-Modal
- Ist der Controller nicht erreichbar, bleiben die letzten Daten sichtbar und
  der Block zeigt `Controller nicht erreichbar (Daten vor …)`

### AP-Modal (Qualitaet + Geräte am Access Point)

Das Modal zeigt zuerst die Qualitaetswerte des APs (gesamt und pro Funkband)
sowie Link, CPU und RAM. Darunter alle WLAN-Geräte als **mehrspaltige
Übersicht** (bis zu 4 Spalten, je nach Terminalbreite), alphabetisch sortiert
und mit farbiger Signalstärke:

```
╔══════════════════════════════════════════════════════════════════╗
║ UNIFI AP: unten  (AC Pro)                                        ║
╠══════════════════════════════════════════════════════════════════╣
║ Status: ONLINE  ·  28 Geraete  ·  up 345d22h  ·  192.168.178.40  ║
║ Modell: AC Pro  ·  Firmware: 6.6.77.15402                        ║
║ Qualitaet: 97% (sehr gut) · Link: 1000 Mbit/s · CPU 8% · RAM 44% ║
║   2.4 GHz Kanal 1    Q99%  21 Ger  Last 32%                      ║
║   5 GHz   Kanal 48   Q92%   8 Ger  Last 5%                       ║
║──────────────────────────────────────────────────────────────────║
║ GERAETE AN DIESEM AP                                             ║
║ · 192.168.178.139 -77dBm · amazon-60d196882 -52dBm · carport ... ║
║ ...                                                              ║
║──────────────────────────────────────────────────────────────────║
║ [ESC] oder [q] schliessen                                        ║
╚══════════════════════════════════════════════════════════════════╝
```

- `Qualitaet` = UniFi-`satisfaction` des APs (gleiche Farbskala wie oben)
- `Link` zeigt die Uplink-Geschwindigkeit; ist sie **kleiner als die
  Maximalgeschwindigkeit**, erscheint sie orange mit `(max …)` – typischer
  Hinweis auf ein Kabel-/Port-Problem
- pro Radio: Band, Kanal, Qualitaet (`Q…%`), Clientzahl und Kanalauslastung
  (`Last …%`)
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
deCONZ-/Phoscon-Block. Im Hauptbildschirm zeigt er **nur die Anzahl** der
physischen Zigbee-Geräte (konsolidiert, siehe unten):

```
⬡ deCONZ  73 Geraete
```

- Farbe: grün = alle verbunden, gelb = einzelne offline, rot = keine Verbindung
- **Klick auf den Block** (oder `Tab` bis `PROXMOX + UNIFI`, dann `↓` bis
  deCONZ und `Enter`) öffnet die Detailübersicht
- Ist das Gateway nicht erreichbar, bleiben die letzten Zahlen sichtbar
  (`nicht erreichbar (Daten vor …)`)

### deCONZ-Modal (Typen, Akku, Konsolidierung)

```
╔════════════════════════════════════════════════════════════════════╗
║ DECONZ / PHOSCON: Phoscon-GW                                       ║
╠════════════════════════════════════════════════════════════════════╣
║ 73 Geraete   63 verbunden  ·  10 offline (9 Typen)                 ║
║ Gateway 2.32.1 · Firmware 0x26780700 · Kanal 20                    ║
║ Akku: 39 mit Akku · 3 schwach (<=25%) · 0 leer · 34 ohne           ║
║ Konsolidiert: 104 Eintraege -> 73 Geraete                          ║
║────────────────────────────────────────────────────────────────────║
║ TYP                                           GES  ONLINE OFFLINE  ║
║ ▸ Bewegung/Praesenz                             15      14       1 ║
║ ▸ Steckdose                                     18      15       3 ║
║ ...                                                                ║
║────────────────────────────────────────────────────────────────────║
║ AKKU SCHWACH (<=25%)                                               ║
║   Bett-schalter-Sarah-2025                                     16% ║
║   OpenClose 80                                                 17% ║
║   TRADFRI on/off switch 2                                      21% ║
╚════════════════════════════════════════════════════════════════════╝
```

- **Konsolidierung:** deCONZ legt pro Funktion oft eine eigene Ressource an
  (z. B. Bewegung + Licht + Temperatur desselben Melders). `deconz.py`
  gruppiert `lights` + `sensors` über die MAC aus der `uniqueid` zu **einem
  physischen Gerät**. Aus 104 Ressourcen werden so 73 Geräte (74 inkl. Gateway)
- „verbunden“ kommt aus `state.reachable` (Lights) bzw. `config.reachable`
  (Sensoren); virtuelle Einträge (`Daylight`, `CLIPDaylightOffset`) zählen
  weder als Gerät noch als offline
- mehrere Roh-Typen werden zu lesbaren Gruppen zusammengefasst, z. B.
  `On/Off plug-in unit` + `Smart plug` → **Steckdose**
- **Akku:** gezählt wird pro physischem Gerät (niedrigster Wert, falls mehrere
  Ressourcen einen Batteriewert melden):
  - `mit Akku` = Geräte mit Batteriewert
  - `schwach` = ≤ 25 % (orange, Liste darunter mit Prozentwerten)
  - `leer` = 0 %
  - `ohne` = kein Batteriewert (Steckdosen, Lampen, virtuelle Geräte)
- bei wenig Platz entfallen zuerst Gateway-Details und Akku-Liste, dann
  `+N weitere Typen`

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

## HomeMatic-Block

Unter dem deCONZ-Block steht – wieder durch eine Leerzeile abgesetzt – der
HomeMatic-Block. Er fasst die physischen Geräte der CCU zusammen:

```
⌂ HomeMatic  92 Geraete  88 live
  Akku 2 leer / 5 schwach · Funk 11 schlecht
```

- Farbe: grün = keine Auffälligkeit, gelb = mindestens ein Problem
- **Klick auf den Block** oder Taste `m` öffnet das Detail-Modal; alternativ
  `Tab` bis `HOMEMATIC [AKTIV]` und `Enter`
- Reicht der Platz nur für eine Zeile (z. B. 100×30), wird kompakt
  zusammengefasst: `⌂ HM 92 · 88 live · Akku 2 · Funk 11`
- Ist die CCU nicht erreichbar, bleiben die letzten Zahlen mit Zeitstempel
  stehen (`HomeMatic nicht erreichbar (vor 5m)`)

### HomeMatic-Modal

```
╔══════════════════════════════════════════════════════════════════════╗
║ HOMEMATIC CCU: 92 Geraete                                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ live 88   4 offline                                                  ║
║ Akku: 2 leer, 5 schwach, 13 ok                                       ║
║ Funk: 11 sehr schlecht, 6 maessig (≤ -95 / -85 dBm)                  ║
║──────────────────────────────────────────────────────────────────────║
║ BATTERIE LEER (2)                                                    ║
║   Heiz Esszimmer (2.0 V)                                             ║
║   Rauch 3 - Büro                                                     ║
║──────────────────────────────────────────────────────────────────────║
║ NICHT ERREICHBAR (4)                                                 ║
║   Heiz Esszimmer                                                     ║
║   … +3 weitere                                                       ║
╚══════════════════════════════════════════════════════════════════════╝
```

- **live** = Geräte mit `UNREACH=false`; virtuelle Geräte (z. B. `HM-RCV-50`,
  `HmIP-CCU3`) haben keinen `UNREACH`-Datenpunkt und werden nicht mitgezählt
- **Akku:** HomeMatic liefert keine Prozentwerte, nur `LOWBAT`/`LOW_BAT` und
  `OPERATING_VOLTAGE`
  - `leer` = `LOWBAT`/`LOW_BAT` aktiv **oder** ≤ 2,2 V
  - `schwach` = ≤ 2,5 V
  - `ok` = Gerät mit Akku, aber über den Schwellwerten
- **Funk:** schlechtester Wert aus `RSSI_DEVICE` und `RSSI_PEER`
  - `sehr schlecht` = ≤ -95 dBm
  - `mäßig` = ≤ -85 dBm
  - Rohwerte > 127 sind unsigned Bytes (`194` → -62 dBm); `0`, `1`, `128`
    und `-65535` bedeuten „kein Messwert“
- Abschnitte in Prioritätsreihenfolge (leer → offline → schwach → Funk);
  Kopf und Zusammenfassung bleiben stehen, die Listen darunter sind
  **scrollbar**:
  - `↑`/`↓` oder `j`/`k` zeilenweise
  - `PgUp`/`PgDn`, `Leertaste` / `b` seitenweise
  - `g` / `G` an den Anfang / ans Ende
  - in der Titelzeile steht der Ausschnitt, z. B. `[1-25/48]`
- unten werden die Geräte **nach Typ gruppiert** (`15x HM-LC-Dim1T-DR · …`)

Voraussetzung ist das Addon **XML-API** auf der CCU
(`http://<ccu>/addons/xmlapi/`). Abgefragt werden `devicelist.cgi` (Name, Typ,
Interface) und `statelist.cgi` (Zustände). Zugang in `config.json`
(Modus 600, **nicht ins Repo**):

```json
"homematic_interval_sec": 60,
"homematic": {
  "enabled": true,
  "url": "http://192.168.178.6/addons/xmlapi",
  "timeout": 8,
  "battery_empty_volt": 2.2,
  "battery_weak_volt": 2.5,
  "rssi_weak": -85,
  "rssi_bad": -95
}
```

Die Abfrage ist mit ~430 KB nicht ganz klein; der Job läuft deshalb nur alle
60 s und braucht auf der Zero 2 W etwa 5 s (davon < 1 s CPU).

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
# config.json anpassen: Geräte + Proxmox-Tokens + UniFi-/deCONZ-/FRITZ!Box-Zugang eintragen
```

### Proxmox-Token erzeugen

Auf jedem PVE-Host:

```bash
pveum user token add root@pam backup-script --privsep=0
```

Den `full-tokenid` + `value` in `config.json` unter `proxmox_nodes[].token`
im Format `root@pam!backup-script=<value>` eintragen.

### Backup-Ziel (Storage)

Die Backup-Funktion (`[5]` im Proxmox-Guest-Modal) nutzt den Storage, der in
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
