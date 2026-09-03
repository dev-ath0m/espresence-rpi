# How this repo was created & where things came from

This document explains the provenance of everything in this repository:
what was researched, what was written from scratch, and which third-party
packages the running service depends on.

## 1. The request

Build an [ESPresense](https://espresense.com/)-style BLE presence node that
runs natively on a Raspberry Pi 4 (using the Pi's own Bluetooth radio)
instead of on ESP32 firmware, publishing to an existing MQTT broker
(`192.168.178.6`) with the same MQTT topic shape, and offering a web
configuration page equivalent to the one on a real ESPresense node.

## 2. Research phase — what was read, and why

No ESPresense source code (firmware C++/C, or the Svelte/TypeScript web UI)
was copied, read line-by-line, or transcribed. Only the **public
documentation site** and the **top-level GitHub README** were fetched, to
learn the *protocol* (topic names, JSON shapes, config field names/semantics)
so this implementation could be wire-compatible:

| Source | What was taken from it |
|---|---|
| https://espresense.com/ (home) | High-level architecture, "requirements" (ESP32 node + trackable device + MQTT broker) |
| https://espresense.com/configuration/mqtt/ | Exact MQTT topic shape (`espresense/rooms/<room>/...`, `espresense/devices/<id>/<room>`, `espresense/settings/<id>/config`), settings reference table (names/types/defaults/ranges for `max_distance`, `absorption`, `ref_rssi`, `tx_ref_rssi`, `rx_adj_rssi`, `skip_distance`, `skip_ms`, `forget_ms`, `include`/`exclude`, `known_macs`, `count_ids`/`count_enter`/`count_exit`/`count_ms`, etc.), subscribed/published topic lists, example `mosquitto_pub`/`mosquitto_sub` commands |
| https://espresense.com/configuration/rest-api/ | REST endpoint shapes: `GET /json`, `GET /json/devices`, `GET/POST/DELETE /json/configs`, `POST /restart`, example JSON payloads |
| https://espresense.com/configuration/settings/ | Human-readable descriptions of each settings-page field (used to write equivalent field labels/help text in this project's own HTML templates) |
| https://espresense.com/configuration/network/ | Network/MQTT config page field list (room name, MQTT server/port/user/pass, discovery toggle, telemetry/devices publish toggles) |
| https://github.com/ESPresense/ESPresense (README + repo file listing only) | Confirmed project purpose/license (AGPL-3.0) and high-level repo layout; **no source files were opened or copied** |

Everything else — the actual Python code, the BLE-advertisement parsing
logic (iBeacon/Eddystone/generic heuristics), the Flask routes, the HTML
templates/CSS for the Network/Settings/Devices pages, the systemd unit, and
the install/uninstall scripts — was **written from scratch** for this
project. The web UI is visually and structurally an independent, original
implementation; it reuses the same *field names and tab groupings* documented
publicly above (so it's functionally familiar to anyone who has used a real
ESPresense node) but none of ESPresense's actual UI code/assets were copied.

### Why this matters (licensing)

The real ESPresense firmware is AGPL-3.0 licensed. Because this project
reimplements the documented protocol independently rather than copying or
adapting ESPresense's source, it is not a derivative work of that codebase.
If you plan to redistribute this repo publicly, it's still worth adding your
own license file reflecting your intentions.

## 3. Implementation phase — how the code was assembled

1. Modeled the on-disk config (`config.yaml`) and enrolled-device store
   (`devices.json`) after the settings/REST semantics above.
2. Wrote `espresense_pi/identify.py` to derive an ESPresense-style id
   (`ibeacon:...`, `eddy:...`, `known:<mac>`, `generic:<mac>`) from raw BLE
   advertisement fields exposed by `bleak` — this is an original, simplified
   heuristic, not a port of ESPresense's Apple continuity-protocol
   fingerprinting (that part of the real firmware is significantly more
   involved and is called out as a known gap in the README).
3. Wrote `espresense_pi/distance.py` implementing the standard log-distance
   path-loss formula `distance = 10 ^ ((ref_rssi - rssi) / (10 * absorption))`,
   which is the same general model described in ESPresense's calibration
   docs (a widely-used, generic RF propagation formula, not proprietary to
   ESPresense).
4. Wrote `espresense_pi/mqtt_client.py` to reproduce the documented topic
   shape and `/set` handling using `paho-mqtt`.
5. Wrote `espresense_pi/ble_scanner.py` using `bleak` (BlueZ D-Bus backend)
   to do the actual scanning that an ESP32's native BLE stack would do in
   the real firmware.
6. Wrote the Flask app (`espresense_pi/web/app.py`) and templates
   (`espresense_pi/web/templates/*.html`) as an original UI covering the same
   configuration categories as the real device's Network/Settings/Devices
   pages, plus the REST endpoints listed in section 2.
7. Wrote `systemd/espresense-pi.service` and `scripts/install.sh` /
   `scripts/uninstall.sh` to package it as a standard Linux service.
8. Verified the result by byte-compiling every module and running an
   in-process smoke test (config load/save, distance math, device
   identification, all Flask routes) in a throwaway virtualenv before
   committing.

## 4. Third-party libraries (the actual "external code" this project runs on)

| Package | Purpose | Project home |
|---|---|---|
| [`bleak`](https://github.com/hbldh/bleak) | Cross-platform BLE scanning (uses BlueZ over D-Bus on Linux) | github.com/hbldh/bleak |
| [`paho-mqtt`](https://github.com/eclipse-paho/paho.mqtt.python) | MQTT client | github.com/eclipse-paho/paho.mqtt.python |
| [`Flask`](https://github.com/pallets/flask) | Web framework for the config UI + REST API | github.com/pallets/flask |
| [`waitress`](https://github.com/Pylons/waitress) | Production WSGI server used to serve the Flask app | github.com/Pylons/waitress |
| [`PyYAML`](https://github.com/yaml/pyyaml) | Reads/writes `config.yaml` | github.com/yaml/pyyaml |
| [`psutil`](https://github.com/giampaolo/psutil) | System stats for the telemetry topic | github.com/giampaolo/psutil |

These are installed via `pip` per `requirements.txt`; none of their source
was vendored into this repo.

## 5. File map (everything under version control here was authored for this project)

```
espresense_pi/            Python service source (see README.md "Project layout")
systemd/espresense-pi.service   systemd unit
scripts/install.sh, uninstall.sh   deployment scripts
config.example.yaml       default config (includes your MQTT broker 192.168.178.6)
README.md                 usage docs
SOURCES.md                this file
```
