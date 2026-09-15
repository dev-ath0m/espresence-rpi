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

from espresense_pi.identify import Advertisement

logger = logging.getLogger(__name__)

AdvertisementCallback = Callable[[Advertisement], None]


def _address_type(device) -> Optional[str]:
    """"public" or "random", from the BlueZ device properties.

    The firmware fingerprints public and random addresses differently, so we
    have to ask the backend rather than guess from the address bits.
    """
    details = getattr(device, "details", None)
    props = details.get("props") if isinstance(details, dict) else None
    value = props.get("AddressType") if isinstance(props, dict) else None
    return value if isinstance(value, str) else None


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
            rssi = advertisement_data.rssi
            if rssi is None:
                rssi = getattr(device, "rssi", None)
            if rssi is None:
                return
            self._on_advertisement(Advertisement(
                address=device.address,
                address_type=_address_type(device),
                # Only the advertised name: BlueZ makes device.name fall back to
                # a MAC-derived alias, which would turn into a bogus "name:" id.
                name=advertisement_data.local_name,
                rssi=rssi,
                tx_power=advertisement_data.tx_power,
                manufacturer_data=advertisement_data.manufacturer_data or {},
                service_data={str(k): v for k, v in (advertisement_data.service_data or {}).items()},
                service_uuids=list(advertisement_data.service_uuids or ()),
            ))
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
