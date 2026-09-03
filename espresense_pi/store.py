"""Persistent store for enrolled device configs (id/alias/name/rssi@1m).

Mirrors ESPresense's /json/configs REST endpoint and the
espresense/settings/<id>/config MQTT topic.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class DeviceStore:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._configs: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._configs = {c["id"]: c for c in data.get("configs", [])}
            else:
                self._configs = {}
                self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump({"configs": list(self._configs.values())}, fh, indent=2)
            tmp.replace(self.path)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._configs.values())

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._configs.get(device_id)

    def upsert(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        if not entry.get("id"):
            raise ValueError("id is required")
        device_id = entry["id"]
        result: Dict[str, Any] = {
            "id": device_id,
            "alias": entry.get("alias") or device_id,
            "name": entry.get("name", ""),
        }
        mac = entry.get("mac")
        if mac:
            result["mac"] = mac.replace(":", "").lower()
        irk = entry.get("irk")
        if irk:
            result["irk"] = irk.replace(":", "").lower()
        rssi = entry.get("rssi@1m", entry.get("rssi_at_1m"))
        if rssi is not None and rssi != "":
            result["rssi@1m"] = int(rssi)
        with self._lock:
            self._configs[device_id] = result
            self.save()
        return result

    def known_ids(self) -> Dict[str, str]:
        """Return a {mac (no colons, lowercase): id} map for entries that
        were enrolled via Bluetooth pairing (i.e. carry a "mac" field).
        """
        with self._lock:
            return {c["mac"]: c["id"] for c in self._configs.values() if c.get("mac")}

    def known_irks(self) -> Dict[str, str]:
        """Return a {irk (32 lowercase hex chars, no separators): id} map
        for entries that were enrolled via Bluetooth pairing and had an
        Identity Resolving Key captured at bonding time.
        """
        with self._lock:
            return {c["irk"]: c["id"] for c in self._configs.values() if c.get("irk")}

    def delete(self, device_id: str) -> bool:
        with self._lock:
            if device_id in self._configs:
                del self._configs[device_id]
                self.save()
                return True
            return False
