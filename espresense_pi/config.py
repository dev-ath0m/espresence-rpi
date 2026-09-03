"""Configuration management for ESPresense-Pi.

Loads/saves a YAML config file and provides thread-safe access to
settings, mirroring the option names used by the original ESP32
ESPresense firmware (see https://espresense.com/configuration/).
"""
from __future__ import annotations

import copy
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Union

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "room": {
        "name": socket.gethostname(),
    },
    "mqtt": {
        "host": "192.168.178.6",
        "port": 1883,
        "username": "",
        "password": "",
        "base_topic": "espresense",
        "discovery": True,
        "discovery_prefix": "homeassistant",
        "pub_tele": True,
        "pub_devices": True,
    },
    "ble": {
        "ref_rssi": -65,
        "tx_ref_rssi": -59,
        "absorption": 2.7,
        "rx_adj_rssi": 0,
        "max_distance": 16.0,
        "skip_distance": 0.5,
        "skip_ms": 5000,
        "forget_ms": 150000,
        "include": "",
        "exclude": "",
        "known_macs": "",
        "count_ids": "",
        "count_enter": 2.0,
        "count_exit": 4.0,
        "count_ms": 10000,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """Thread-safe, file-backed configuration store."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    on_disk = yaml.safe_load(fh) or {}
                self._data = _deep_merge(DEFAULT_CONFIG, on_disk)
            else:
                self._data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(self._data, fh, sort_keys=False)
            tmp.replace(self.path)

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(section, {}).get(key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data.get(section, {}))

    def set(self, section: str, key: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            self._data.setdefault(section, {})[key] = value
            if persist:
                self.save()

    def update_section(self, section: str, values: Dict[str, Any], persist: bool = True) -> None:
        with self._lock:
            self._data.setdefault(section, {}).update(values)
            if persist:
                self.save()

    @property
    def room_name(self) -> str:
        return self.get("room", "name", socket.gethostname())
