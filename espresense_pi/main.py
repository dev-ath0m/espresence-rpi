"""Entry point for the ESPresense-Pi service."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from espresense_pi.ble_scanner import BleScanner
from espresense_pi.config import Config
from espresense_pi.distance import rssi_to_distance
from espresense_pi.fingerprint_tracker import FingerprintTracker
from espresense_pi.identify import identify
from espresense_pi.live_state import LiveState
from espresense_pi.mqtt_client import EspresenseMqtt
from espresense_pi.store import DeviceStore
from espresense_pi.telemetry import TelemetryPublisher
from espresense_pi.web.app import create_app

BASE_DIR = Path(os.environ.get("ESPRESENSE_PI_HOME", Path(__file__).resolve().parent.parent))
CONFIG_PATH = BASE_DIR / "config.yaml"
STORE_PATH = BASE_DIR / "devices.json"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("espresense_pi")


def restart_process() -> None:
    logger.warning("Restart requested, exiting (systemd will restart the service)")
    os._exit(0)


def matches_prefix_list(device_id: str, raw_list: str) -> bool:
    prefixes = [p.strip() for p in (raw_list or "").split() if p.strip()]
    return any(device_id.startswith(p) for p in prefixes)


def main() -> None:
    config = Config(CONFIG_PATH)
    store = DeviceStore(STORE_PATH)
    tracker = FingerprintTracker(config)
    live_state = LiveState()

    def on_rename(new_name: str) -> None:
        config.set("room", "name", new_name)
        mqtt_client.reconnect()

    mqtt_client = EspresenseMqtt(config, store, on_restart=restart_process, on_rename=on_rename)
    mqtt_client.connect()

    known_macs_cache = {"raw": None, "set": set()}

    def refresh_known_macs():
        raw = config.get("ble", "known_macs", "") or ""
        if raw != known_macs_cache["raw"]:
            known_macs_cache["raw"] = raw
            known_macs_cache["set"] = {m.strip().lower() for m in raw.split() if m.strip()}
        return known_macs_cache["set"]

    def on_advertisement(mac: str, name, rssi: int, manufacturer_data: dict, service_data: dict) -> None:
        service_data_str = {str(k): v for k, v in service_data.items()}
        device_id, friendly_name, rssi_at_1m = identify(
            mac, name, manufacturer_data, service_data_str, refresh_known_macs(), store.known_ids()
        )

        ble_cfg = config.get_section("ble")
        include = ble_cfg.get("include", "")
        exclude = ble_cfg.get("exclude", "")
        if include and not matches_prefix_list(device_id, include):
            return
        if exclude and matches_prefix_list(device_id, exclude):
            return

        enrolled = store.get(device_id)
        publish_id = device_id
        display_name = friendly_name
        ref_rssi = rssi_at_1m if rssi_at_1m is not None else ble_cfg.get("ref_rssi", -65)
        if enrolled:
            publish_id = enrolled.get("alias", device_id)
            display_name = enrolled.get("name") or friendly_name
            if enrolled.get("rssi@1m") is not None:
                ref_rssi = enrolled["rssi@1m"]

        rssi_adj = rssi + int(ble_cfg.get("rx_adj_rssi", 0))
        distance = rssi_to_distance(rssi_adj, ref_rssi, ble_cfg.get("absorption", 2.7))

        visible = 0 <= distance <= ble_cfg.get("max_distance", 16.0)
        live_state.update(device_id, {
            "name": display_name,
            "mac": mac.replace(":", "").lower(),
            "rssi": rssi,
            "distance": distance,
            "visible": visible,
        })
        if not visible:
            return

        tracker.note_seen(device_id)
        if tracker.should_report(device_id, distance):
            mqtt_client.publish_device(publish_id, display_name, mac, rssi, distance, rssi_at_1m)

    scanner = BleScanner(on_advertisement=on_advertisement)
    scanner.start()

    telemetry = TelemetryPublisher(mqtt_client)
    telemetry.start()

    stop_purge = threading.Event()

    def purge_loop():
        while not stop_purge.wait(30):
            tracker.purge_stale()

    threading.Thread(target=purge_loop, daemon=True).start()

    app = create_app(config, store, live_state, restart_process, mqtt_client)

    def handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_purge.set()
        scanner.stop()
        telemetry.stop()
        mqtt_client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    web_cfg = config.get_section("web")
    host = web_cfg.get("host", "0.0.0.0")
    port = int(web_cfg.get("port", 8080))
    logger.info("Starting web UI on %s:%s", host, port)
    from waitress import serve

    serve(app, host=host, port=port)


if __name__ == "__main__":
    main()
