"""In-memory view of currently visible devices, used by the REST API and
the web UI's Devices tab.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List


class LiveState:
    def __init__(self):
        self._lock = threading.RLock()
        self._devices: Dict[str, Dict[str, Any]] = {}

    def update(self, device_id: str, info: Dict[str, Any]) -> None:
        info = dict(info)
        info["last_seen"] = time.time()
        with self._lock:
            self._devices[device_id] = info

    def snapshot(self, max_age: float = 60.0) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                dict(v, id=k)
                for k, v in self._devices.items()
                if now - v.get("last_seen", 0) <= max_age
            ]

    def remove(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
