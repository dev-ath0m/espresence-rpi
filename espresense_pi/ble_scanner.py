"""BLE advertisement scanner backed by bleak/BlueZ.

Runs its own asyncio event loop in a background thread and forwards
every advertisement to a callback on that same thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable, Optional

from bleak import BleakScanner

logger = logging.getLogger(__name__)

AdvertisementCallback = Callable[[str, Optional[str], int, dict, dict], None]


class BleScanner:
    def __init__(self, on_advertisement: AdvertisementCallback):
        self._on_advertisement = on_advertisement
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ble-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._scan_forever())
        except Exception:
            logger.exception("BLE scanner crashed")
        finally:
            self._loop.close()

    def _detection_callback(self, device, advertisement_data) -> None:
        try:
            manufacturer_data = advertisement_data.manufacturer_data or {}
            service_data = advertisement_data.service_data or {}
            name = advertisement_data.local_name or device.name
            rssi = advertisement_data.rssi
            if rssi is None:
                rssi = getattr(device, "rssi", None)
            if rssi is None:
                return
            self._on_advertisement(device.address, name, rssi, manufacturer_data, service_data)
        except Exception:
            logger.exception("Error handling BLE advertisement")

    async def _scan_forever(self) -> None:
        self._stop_event = asyncio.Event()
        while not self._stop_event.is_set():
            scanner = None
            try:
                scanner = BleakScanner(detection_callback=self._detection_callback)
                await scanner.start()
                logger.info("BLE scanning started")
                await self._stop_event.wait()
            except Exception:
                logger.exception("BLE scan loop error, retrying in 5s")
                await asyncio.sleep(5)
            finally:
                if scanner is not None:
                    try:
                        await scanner.stop()
                    except Exception:
                        pass
