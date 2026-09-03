# ESPresense-Pi

An [ESPresense](https://espresense.com/)-compatible BLE presence-detection
node that runs as a native Python service on a Raspberry Pi (using the Pi's
own Bluetooth radio) instead of ESP32 firmware.

It scans BLE advertisements, estimates distance from RSSI, and publishes to
your existing MQTT broker using the **same MQTT topic shape** as a real
ESPresense node, so it can be used as a drop-in additional "room" alongside
(or instead of) ESP32 nodes with Home Assistant's `mqtt_room` integration or
similar. It also ships a small web configuration UI (Network / Settings /
Devices tabs) modeled on the ESPresense device UI, plus a REST API compatible
with a useful subset of `espresense.com`'s [REST API](https://espresense.com/configuration/rest-api/).

> This is a community re-implementation for Linux/Raspberry Pi. It is not
> affiliated with the ESPresense project. See [Differences from upstream ESPresense](#differences-from-upstream-espresense) below.

## Features

- BLE scanning via [BlueZ](https://www.bluez.org/)/[bleak](https://github.com/hbldh/bleak) (no ESP32 required)
- RSSI → distance using the same log-distance path-loss model ESPresense uses
- MQTT publishing compatible with ESPresense's topic shape:
  - `espresense/rooms/<room>/...` (status, name, settings snapshot, `/set` handlers)
  - `espresense/devices/<id>/<room>` (per-device distance JSON, what `mqtt_room` consumes)
  - `espresense/settings/<id>/config` (enrolled device configs, read + written)
  - Basic Home Assistant MQTT discovery (node online/offline binary_sensor)
- Web configuration UI:
  - **Network** — room name, MQTT host/port/credentials, discovery/telemetry/devices toggles
  - **Settings** — calibration (ref_rssi, tx_ref_rssi, absorption), filtering (max_distance, include/exclude, known MACs), counting
  - **Devices** — live view of currently-seen BLE devices + one-click enrollment, and management of enrolled device aliases
- REST API subset: `GET /json`, `GET /json/devices`, `GET/POST/DELETE /json/configs`, `POST /restart`
- Runs as a systemd service with auto-restart

## Requirements

- Raspberry Pi (tested target: Pi 4) running Raspberry Pi OS / Debian with a working built-in or USB Bluetooth adapter
- Python 3.9+
- An existing MQTT broker (defaults to `192.168.178.6:1883` — edit in `config.yaml` or the Network page)

## Installation

Copy/clone this repository onto the Pi, then run the installer:

```bash
sudo ./scripts/install.sh
```

This will:

1. Install `bluetooth`, `bluez`, and Python venv/pip via `apt`
2. Copy the project to `/opt/espresense-pi`
3. Create a virtualenv and install Python dependencies
4. Create `config.yaml` from `config.example.yaml` if it doesn't exist yet
5. Install and start the `espresense-pi` systemd service

The web UI is then available at `http://<pi-ip>:8080`.

To uninstall: `sudo ./scripts/uninstall.sh` (add `--purge` to also delete `/opt/espresense-pi`, including your config).

### Manual / development run

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
sudo ./venv/bin/python -m espresense_pi.main   # sudo/root needed for BLE scanning
```

`ESPRESENSE_PI_HOME` controls where `config.yaml`/`devices.json` are read from
(defaults to the repo root); the systemd unit sets it to `/opt/espresense-pi`.

### Why root?

BlueZ's BLE scanning APIs are normally gated by D-Bus/polkit policy for
system-wide (non-session) processes. Running the service as root avoids
fighting per-distro polkit rules. If you'd rather run as a regular user,
add that user to the `bluetooth` group and adjust the relevant BlueZ polkit
policy, then change `User=` in `systemd/espresense-pi.service`.

## Configuration

All settings live in `config.yaml` (created from `config.example.yaml`) and
can be edited directly or through the web UI — both read/write the same
file, and changes made through MQTT `.../set` topics are persisted back to it
too.

Key defaults (matching ESPresense's own defaults where applicable):

| Setting | Default | Notes |
|---|---|---|
| `mqtt.host` | `192.168.178.6` | Your MQTT broker |
| `mqtt.port` | `1883` | |
| `ble.ref_rssi` | `-65` | RSSI @ 1m for generic (non-iBeacon) devices |
| `ble.absorption` | `2.7` | Path-loss / environmental factor |
| `ble.max_distance` | `16.0` | Meters; farther reports are dropped |
| `ble.skip_ms` / `ble.skip_distance` | `5000` / `0.5` | Report-rate limiting |

See [espresense.com/configuration](https://espresense.com/configuration/settings/)
for the semantics of each field (this project reuses the same names).

## MQTT topic compatibility

```
espresense/rooms/<room>/status              online / offline (retained, LWT)
espresense/rooms/<room>/name                room display name (retained)
espresense/rooms/<room>/telemetry           JSON uptime/memory (non-retained)
espresense/rooms/<room>/<setting>           current value, e.g. max_distance (retained)
espresense/rooms/<room>/<setting>/set       write a setting live
espresense/rooms/*/<setting>/set            fleet-wide write (also honored)
espresense/devices/<id>/<room>              per-device JSON: id, distance, rssi, name, mac
espresense/settings/<id>/config             enrolled device config (also POST /json/configs)
```

Example:

```bash
mosquitto_pub -h 192.168.178.6 -t "espresense/rooms/raspberrypi/max_distance/set" -m "10.0"
mosquitto_sub -h 192.168.178.6 -v -t "espresense/devices/#"
```

## Differences from upstream ESPresense

This is a from-scratch Python implementation guided by the public
[espresense.com](https://espresense.com/) documentation (topic names, config
semantics, REST shapes) — no ESP32 firmware source was copied. Notable gaps
versus the real firmware:

- **No Apple continuity-protocol fingerprinting** (the real firmware derives
  ids like `apple:iphone15-3`). This implementation identifies devices as
  `ibeacon:<uuid>_<major>_<minor>`, `eddy:<namespace>_<instance>`,
  `known:<mac>` (if enrolled by MAC), or `generic:<mac>` as a fallback.
- **No IRK-based private-address resolution** for Apple devices.
- **No active BLE/GATT querying** (`query`/`requery_ms` settings, Mi Flora, etc.).
- **No captive Wi-Fi portal** — the Pi already has network connectivity, so
  there's no "Network" onboarding flow; the Network page only configures the
  room name and MQTT.
- Home Assistant discovery is minimal (node status only), not the full set
  of entities the ESP32 firmware publishes.
- No LED/GPIO/PIR/radar/switch hardware support (not applicable to a Pi 4
  running headless).

If you need full protocol parity (especially for Apple device tracking or
indoor positioning with [ESPresense-companion](https://github.com/ESPresense/ESPresense-companion)),
an actual ESP32 node will track more precisely. This project is best suited
as a simple, no-extra-hardware presence sensor for one room (e.g. detecting
"is my phone's BLE beacon nearby").

## Service management

```bash
sudo systemctl status espresense-pi
sudo systemctl restart espresense-pi
sudo journalctl -u espresense-pi -f
```

## Project layout

```
espresense_pi/
  config.py            # YAML-backed settings store
  store.py             # enrolled device configs (devices.json)
  identify.py          # BLE advertisement -> ESPresense-style id
  distance.py          # RSSI -> distance model
  fingerprint_tracker.py  # report rate-limiting / forget logic
  live_state.py         # in-memory "currently visible" devices
  ble_scanner.py         # bleak/BlueZ scanning thread
  mqtt_client.py         # MQTT publish/subscribe, ESPresense topic shape
  telemetry.py           # periodic telemetry publisher
  main.py                # wiring + service entry point
  web/
    app.py               # Flask app: config UI + REST API
    templates/           # Network / Settings / Devices pages
    static/style.css
systemd/espresense-pi.service
scripts/install.sh, uninstall.sh
config.example.yaml
```
