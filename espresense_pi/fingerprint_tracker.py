"""Rate-limits and expires per-device reports the same way ESPresense's
BleFingerprint reporting logic does: report immediately on a large jump
in distance, otherwise wait at least skip_ms between reports; forget
devices that haven't been seen in forget_ms.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class _State:
    last_distance: float = -1.0
    last_report_ms: float = 0.0
    last_seen_ms: float = 0.0


class FingerprintTracker:
    def __init__(self, config):
        self.config = config
        self._lock = threading.RLock()
        self._state: Dict[str, _State] = {}

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0

    def note_seen(self, device_id: str) -> None:
        now = self._now_ms()
        with self._lock:
            st = self._state.setdefault(device_id, _State())
            st.last_seen_ms = now

    def should_report(self, device_id: str, distance: float) -> bool:
        ble_cfg = self.config.get_section("ble")
        skip_ms = ble_cfg.get("skip_ms", 5000)
        skip_distance = ble_cfg.get("skip_distance", 0.5)
        now = self._now_ms()
        with self._lock:
            st = self._state.setdefault(device_id, _State())
            st.last_seen_ms = now
            moved = abs(distance - st.last_distance) >= skip_distance
            elapsed = now - st.last_report_ms
            if st.last_report_ms == 0.0 or moved or elapsed >= skip_ms:
                st.last_distance = distance
                st.last_report_ms = now
                return True
            return False

    def purge_stale(self) -> List[str]:
        forget_ms = self.config.get_section("ble").get("forget_ms", 150000)
        now = self._now_ms()
        with self._lock:
            stale = [k for k, v in self._state.items() if now - v.last_seen_ms > forget_ms]
            for k in stale:
                del self._state[k]
        return stale
