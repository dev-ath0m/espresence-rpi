"""MQTT integration replicating ESPresense's topic shape.

See https://espresense.com/configuration/mqtt/ for the topic reference
this mirrors.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

SETTABLE_BLE_KEYS = {
    "max_distance": float,
    "absorption": float,
    "ref_rssi": int,
    "tx_ref_rssi": int,
    "rx_adj_rssi": int,
    "skip_distance": float,
    "skip_ms": int,
    "include": str,
    "exclude": str,
    "known_macs": str,
    "count_ids": str,
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "room"


class EspresenseMqtt:
    def __init__(
        self,
        config,
        store,
        on_restart: Callable[[], None],
        on_rename: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.store = store
        self.on_restart = on_restart
        self.on_rename = on_rename
        self._lock = threading.RLock()
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self._connected = False

    @property
    def base_topic(self) -> str:
        return self.config.get("mqtt", "base_topic", "espresense")

    @property
    def room_slug(self) -> str:
        return slugify(self.config.room_name)

    def _room_topic(self, key: str = "") -> str:
        base = f"{self.base_topic}/rooms/{self.room_slug}"
        return f"{base}/{key}" if key else base

    def connect(self) -> None:
        with self._lock:
            mqtt_cfg = self.config.get_section("mqtt")
            username = mqtt_cfg.get("username") or None
            password = mqtt_cfg.get("password") or None
            if username:
                self.client.username_pw_set(username, password)
            self.client.will_set(self._room_topic("status"), "offline", qos=1, retain=True)
            host = mqtt_cfg.get("host", "192.168.178.6")
            port = int(mqtt_cfg.get("port", 1883))
            logger.info("Connecting to MQTT broker %s:%s", host, port)
            self.client.connect_async(host, port, keepalive=30)
            self.client.loop_start()

    def disconnect(self) -> None:
        try:
            self.client.publish(self._room_topic("status"), "offline", qos=1, retain=True)
        except Exception:
            pass
        self.client.loop_stop()
        try:
            self.client.disconnect()
        except Exception:
            pass

    def reconnect(self) -> None:
        """Reconnect, e.g. after MQTT host/credentials/room name changed."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.connect()

    # -- callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc != 0:
            logger.error("MQTT connect failed rc=%s", rc)
            return
        self._connected = True
        logger.info("MQTT connected as room '%s'", self.room_slug)
        client.subscribe(f"{self.base_topic}/rooms/*/+/set")
        client.subscribe(f"{self._room_topic()}/+/set")
        client.subscribe(f"{self.base_topic}/settings/+/config")
        self.publish_snapshot()

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        logger.warning("MQTT disconnected rc=%s", rc)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            self._handle_message(msg.topic, msg.payload.decode("utf-8", "replace"))
        except Exception:
            logger.exception("Error handling MQTT message on %s", msg.topic)

    def _handle_message(self, topic: str, payload: str) -> None:
        parts = topic.split("/")

        if len(parts) >= 4 and parts[0] == self.base_topic and parts[1] == "settings" and parts[-1] == "config":
            device_id = parts[2]
            try:
                data = json.loads(payload)
                data["id"] = data.get("id", device_id)
                if device_id.startswith("irk:") and not data.get("irk"):
                    data["irk"] = device_id.split(":", 1)[1].strip().lower()
                elif re.fullmatch(r"[0-9a-fA-F]{12}", device_id) and not data.get("mac"):
                    data["mac"] = device_id.lower()
                self.store.upsert(data)
                logger.info("Updated enrolled device config for %s via MQTT", device_id)
            except Exception:
                logger.exception("Invalid device config payload on %s", topic)
            return

        if len(parts) >= 4 and parts[0] == self.base_topic and parts[1] == "rooms" and parts[-1] == "set":
            room = parts[2]
            key = parts[3]
            if room not in ("*", self.room_slug):
                return
            self._handle_set(key, payload)

    def _handle_set(self, key: str, payload: str) -> None:
        payload = payload.strip()
        if key in ("restart", "reboot"):
            logger.info("Restart requested via MQTT")
            self.on_restart()
            return
        if key == "name":
            if payload and self.on_rename:
                self.on_rename(payload)
            return
        if key in SETTABLE_BLE_KEYS:
            caster = SETTABLE_BLE_KEYS[key]
            try:
                value = caster(payload)
            except ValueError:
                logger.warning("Ignoring bad value for %s: %r", key, payload)
                return
            self.config.set("ble", key, value)
            self.publish_snapshot()
            return
        logger.debug("Ignoring unsupported set topic key=%s", key)

    # -- publishing ------------------------------------------------------
    def publish_snapshot(self) -> None:
        if not self._connected:
            return
        ble_cfg = self.config.get_section("ble")
        self.client.publish(self._room_topic("status"), "online", qos=1, retain=True)
        self.client.publish(self._room_topic("name"), self.config.room_name, qos=1, retain=True)
        for key in (
            "max_distance", "absorption", "ref_rssi", "tx_ref_rssi", "rx_adj_rssi",
            "skip_distance", "skip_ms", "include", "exclude", "known_macs", "count_ids",
        ):
            self.client.publish(self._room_topic(key), str(ble_cfg.get(key, "")), qos=0, retain=True)
        if self.config.get("mqtt", "discovery", True):
            self._publish_discovery()

    def publish_device(
        self,
        device_id: str,
        name: Optional[str],
        mac: str,
        rssi: int,
        distance: float,
        rssi_at_1m: Optional[int] = None,
    ) -> None:
        if not self._connected or not self.config.get("mqtt", "pub_devices", True):
            return
        payload = {
            "id": device_id,
            "distance": distance,
            "rssi": rssi,
            "mac": mac.replace(":", "").lower(),
        }
        if name:
            payload["name"] = name
        if rssi_at_1m is not None:
            payload["rssi@1m"] = rssi_at_1m
        topic = f"{self.base_topic}/devices/{device_id}/{self.room_slug}"
        self.client.publish(topic, json.dumps(payload), qos=0, retain=False)

    def publish_telemetry(self, telemetry: dict) -> None:
        if not self._connected or not self.config.get("mqtt", "pub_tele", True):
            return
        self.client.publish(self._room_topic("telemetry"), json.dumps(telemetry), qos=0, retain=False)

    def _publish_discovery(self) -> None:
        prefix = self.config.get("mqtt", "discovery_prefix", "homeassistant")
        room = self.room_slug
        node_id = f"espresense_pi_{room}"
        topic = f"{prefix}/binary_sensor/{node_id}/status/config"
        payload = {
            "name": f"{self.config.room_name} ESPresense-Pi",
            "unique_id": f"{node_id}_status",
            "state_topic": self._room_topic("status"),
            "payload_on": "online",
            "payload_off": "offline",
            "device_class": "connectivity",
            "device": {
                "identifiers": [node_id],
                "name": f"ESPresense-Pi ({self.config.room_name})",
                "manufacturer": "espresense-pi (community project)",
                "model": "Raspberry Pi BLE presence node",
            },
        }
        self.client.publish(topic, json.dumps(payload), qos=0, retain=True)
