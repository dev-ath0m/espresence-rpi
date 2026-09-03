"""Periodic telemetry publishing, analogous to ESPresense's <room>/telemetry."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

_START = time.monotonic()


def collect() -> dict:
    data = {"uptime": int(time.monotonic() - _START)}
    if psutil:
        try:
            vm = psutil.virtual_memory()
            data["freeMem"] = vm.available
            data["cpuPct"] = psutil.cpu_percent(interval=None)
        except Exception:
            logger.debug("psutil telemetry collection failed", exc_info=True)
    return data


class TelemetryPublisher:
    def __init__(self, mqtt_client, interval_s: float = 30.0):
        self.mqtt_client = mqtt_client
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.mqtt_client.publish_telemetry(collect())
            except Exception:
                logger.exception("Failed to publish telemetry")
