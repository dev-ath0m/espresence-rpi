"""Periodic telemetry publishing, analogous to ESPresense's <room>/telemetry.

ESPresense Companion reads this topic to fill the Nodes view: ``ver`` becomes
the Version column, ``ip`` the IP column, and ``firm`` is looked up against
https://espresense.com/firmware/types.json to derive the CPU and Flavor
columns.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional

from espresense_pi import __version__

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

_START = time.monotonic()


def _local_ip(host: str, port: int) -> Optional[str]:
    """Address of the interface that routes to the broker.

    No packets are sent; connecting a UDP socket only performs a route
    lookup, which avoids guessing wrong on a multi-homed host.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.connect((host, port))
            return sock.getsockname()[0]
    except OSError:
        logger.debug("Could not determine local IP for %s:%s", host, port, exc_info=True)
        return None


def _wifi_rssi() -> Optional[int]:
    """Signal level of the first wireless interface, or None on ethernet."""
    try:
        with open("/proc/net/wireless", "r") as handle:
            for line in handle.readlines()[2:]:
                fields = line.split()
                if len(fields) >= 4:
                    return int(float(fields[3].rstrip(".")))
    except (OSError, ValueError):
        logger.debug("Could not read wireless signal level", exc_info=True)
    return None


def collect(mqtt_host: Optional[str] = None, mqtt_port: int = 1883) -> dict:
    data = {
        "uptime": int(time.monotonic() - _START),
        "ver": f"v{__version__}",
        "firm": "rpi",
    }
    if mqtt_host:
        ip = _local_ip(mqtt_host, mqtt_port)
        if ip:
            data["ip"] = ip
    rssi = _wifi_rssi()
    if rssi is not None:
        data["rssi"] = rssi
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
                mqtt_cfg = self.mqtt_client.config.get_section("mqtt")
                self.mqtt_client.publish_availability()
                self.mqtt_client.publish_telemetry(
                    collect(mqtt_cfg.get("host"), int(mqtt_cfg.get("port", 1883)))
                )
            except Exception:
                logger.exception("Failed to publish telemetry")
