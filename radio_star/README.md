# Radio-Star

Sonos-FM-Aggregator als Home Assistant App (ehem. Add-on): hält die stündliche
Wetter-/Nachrichten-/Titel-TTS-Kette an einer Stelle und bietet dazu eine
Timeline für die nächste Stunde.

![Radio-Star](icon.png)

## Funktionen

| Section | Was sie macht |
|---|---|
| **Timeline** | Alle aktiven Timer der nächsten Stunde mit Countdown, Status der verknüpften Automation (on/off, letzter Lauf) + Player-Karte (aktueller Titel/Stream auf `media_player.sonos_fm`) |
| **Timer** | Die gefundenen Ketten-Timer (belegbar, editierbar, neue anlegbar, Zurücksetzen). Timer sind die Quelle für die Timeline. |
| **Automationen** | Alle 177+ HA-Automationen (aus `automations.yaml` + Live-States): Toggle, Detail (konfig), löschen (nur `radio_star_*`), **neue Automation anlegen** (Trigger: stündlich / täglich / Intervall, Aktion: Shell-Command / Medien abspielen / Service, optional „nur wenn Player läuft“, optional gleichzeitig als Timer) |
| **Skripte** | Alle `.py`-Dateien im HA-Config-Verzeichnis (root + 1 Ebene tiefer) mit Editor: lesen, speichern (mit automatischem `.bak-<Zeitstempel>`), anlegen, löschen. Path-Traversal-geschützt, nur `.py`. |
| **MP3s** | Liste der fest installierten MP3s vom Webserver (`.50`, `/audio/tts` + Unterordner) mit Größe/Änderungszeit + ▶ Abspielen auf dem gewählten Player |
| **TTS** | Text → TTS (wählbare Stimme + optionaler Player) und „Runner jetzt ausführen“-Karten für alle MP3-Generatoren (Nachrichten, Badewetter, Sonnenauf/-untergang, Wetter-Intro, Tageslänge, Stundenansage, Run.mp3) inkl. letztem MP3-Änderungszeitpunkt |

## Belegte Standard-Timer (Seed)

| Zeitplan | Timer | Automation | Shell-Command |
|---|---|---|---|
| :25 / :55 | ORF-Nachrichten TTS | `sonos_fm_news` | `sonos_fm_news` |
| :26 / :56 | Badewetter + Nachrichten MP3 Update | `badewetter_news_updater_25_55` | `update_wetter_nachrichten` |
| :00 | Stundenansage (Uhrzeit + Wochentag) | `stunden_ansage_sonos_fm` | `stunden_ansage` |
| :00 | Zufalls-Stream | `play_random_stream_hourly` | `pick_random_stream` |
| :15 / :45 | Titel-Announcement (Run.mp3) | `run_titel_on_sonos_fm` | `run_titel` |
| 23:55 | Sonnenaufgang TTS | `sonnenaufgang_tts_updater` | `update_sonnenaufgang` |
| 23:55 | Sonnenuntergang TTS | `sonnenuntergang_tts_updater` | `update_sonnenuntergang` |
| 23:57 | Wetter-Intro TTS | `wetter_intro_tts_updater` | `update_wetter_intro` |
| 02:00 | Tageslänge TTS | `tageslaenge_tts_updater` | `update_tageslaenge` |
| alle 5 min | Sonos FM Watchdog | `sonos_fm_watchdog_15` | — |

## Architektur

- **App-Container** (S6-Overlay-Base-Image `homeassistant/{arch}-base-python`) mit
  Python-3-Webserver auf **Port 8099**, nur erreichbar über **Ingress**
  (Supervisor `172.30.32.2`); kein offener Host-Port.
- **HA-Zugriff** über den Supervisor-Core-API-Proxy (`homeassistant_api: true`,
  `SUPERVISOR_TOKEN`) — kein eigener Access Token nötig.
  Verwendet werden nur: `GET /api/states`, `GET /api/states/<id>`,
  `POST /api/services/...`, `GET|POST|DELETE /api/config/automation/config/<id>`
  (die in HA 2026.x die einzigen Automation-Endpoints sind; Reload passiert
  automatisch).
- **HA-Config-Ordner** wird als `homeassistant_config` (rw) nach
  `/homeassistant` gemappt → Skript-Editor und `automations.yaml`-Lesen
  direkt von der Datei (inkl. Trigger-Zusammenfassung).
- **Eigene Daten** in `/data` (`timers.json`).
- **MP3-Webserver** (192.168.0.50) wird nur per HTTP abgefragt
  (Directory-Listing + HEAD), 120 s Cache.

Getestet gegen HA **2026.10.4** und **2026.11.0** (inkl. CRUD-Roundtrip einer
Test-Automation, die danach wieder gelöscht wurde).

## Installation

### A) Lokale App (HAOS, einfachste Variante)

```sh
# auf dem HAOS-Host (SSH)
sudo mkdir -p /usr/share/hassio/app/radio_star
sudo cp -r <pfad-zu>/radio_star/. /usr/share/hassio/app/radio_star/
```

Die App erscheint im App-Store als `local_radio_star` (der Supervisor baut
das Image beim ersten Start aus der Dockerfile).

### B) Docker-Image bauen

```sh
cd radio-star
docker build --build-arg ARCH=amd64 -t radio-star:1.0.0 .
# aarch64: --build-arg ARCH=aarch64
# danach auf einen Registry pushen oder `docker save`/`load` auf die HA
```

### C) Als Repository freigeben

Ordner in ein Git-Repo legen (config.yaml auf jeder Ebene findbar), im
App-Store Repository-URL hinterlegen.

### Optionen (nach der Installation)

| Option | Default | Bedeutung |
|---|---|---|
| `webserver_url` | `http://192.168.0.50` | MP3-Webserver |
| `mp3_dir` | `/audio/tts` | Basisverzeichnis (Unterordner werden automatisch mitgelistet) |
| `player` | `media_player.sonos_fm` | Standard-Player für ▶ und TTS |

## Lokal entwickeln

```sh
pip install pyyaml
export HA_API_URL="https://<host>:8123/api"
export HA_TOKEN="<long-lived-token>"        # oder HA_API_TOKEN
export HA_CONFIG_DIR=/pfad/zum/config       # z.B. eine KOPFIE
export RADIO_STAR_DATA=$PWD/data
export RADIO_STAR_PORT=8099
export RADIO_STAR_ALLOW_ALL=1               # ohne Supervisor
python3 radio_star/server.py
```

⚠️ Zum Testen den Config-Ordner **kopieren** — der Skript-Editor schreibt
wirklich (mit Backup).

## Sicherheit

- Webserver akzeptiert nur `172.30.32.2` (Supervisor) bzw. localhost;
  Auth übernimmt HA (Ingress).
- `homeassistant_api` = Admin-Prärogative: die App kann alles, was ein Admin
  kann (bewusst so, da sie Automationen/Services verwalten soll).
- Skript-Editor: nur `.py` unterhalb des Config-Dirs, jeder Save legt ein
  `.bak-<Zeitstempel>` an.
- Der `SUPERVISOR_TOKEN` verbleibt im Container (Supervisor-geprüft).

## Dateien

```
config.yaml                     App-Manifest (Supervisor)
Dockerfile                      Image-Build (base-python + PyYAML)
icon.png
etc/services.d/radio-star/run  S6-Startskript
radio_star/server.py            Backend (reines Python-Stdlib + PyYAML)
radio_star/static/              index.html / app.js / style.css (SPA, kein Build-Schritt)
```
