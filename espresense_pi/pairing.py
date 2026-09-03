"""Bluetooth pairing/bonding helper.

Used by the Devices page's "Pair & enroll" action to bond with a specific
device by MAC address via BlueZ (through bleak), instead of only
identifying it passively from advertisements. This is useful for classic
or BLE devices that don't carry a stable manufacturer-data id, where you
want to lock onto one exact physical device by address.
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

logger = logging.getLogger(__name__)


def _to_colon_mac(mac: str) -> str:
    """Normalize a MAC address to colon-separated form (AA:BB:CC:DD:EE:FF).

    The rest of this project stores/passes MACs without colons (see
    identify.py / known_macs), but BlueZ's D-Bus API -- and therefore
    bleak on Linux -- requires the colon-separated form.
    """
    raw = mac.replace(":", "").replace("-", "").strip().upper()
    if len(raw) != 12:
        return mac  # not a bare 12-hex-digit MAC; pass through as-is
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


async def _pair(mac: str, timeout: float) -> bool:
    async with BleakClient(mac, timeout=timeout) as client:
        return await client.pair()


def pair_sync(mac: str, timeout: float = 20.0) -> bool:
    """Pair with a BLE device by MAC address.

    Spins up its own asyncio event loop, so it's safe to call from a plain
    (non-async) request-handling thread such as a Flask view function.
    Raises on failure (BleakError, TimeoutError, etc.) -- callers should
    catch and report to the user.
    """
    return asyncio.run(_pair(_to_colon_mac(mac), timeout))
