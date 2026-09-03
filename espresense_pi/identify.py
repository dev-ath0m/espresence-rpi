"""Heuristics for turning a BLE advertisement into an ESPresense-style id.

This is a best-effort approximation of the fingerprinting done by the
original ESP32 firmware. It does NOT do Apple continuity-protocol deep
parsing or IRK (private address) resolution -- see the README for
details on this and other differences from upstream ESPresense.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

from espresense_pi.irk import is_resolvable_private_address, resolve_rpa

APPLE_COMPANY_ID = 0x004C
EDDYSTONE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"


def parse_ibeacon(data: bytes) -> Optional[Dict[str, Any]]:
    if len(data) < 23 or data[0:2] != b"\x02\x15":
        return None
    uuid_bytes = data[2:18]
    major = int.from_bytes(data[18:20], "big")
    minor = int.from_bytes(data[20:22], "big")
    tx_power = data[22] - 256 if data[22] > 127 else data[22]
    uuid_hex = uuid_bytes.hex()
    uuid = f"{uuid_hex[0:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:32]}"
    return {"uuid": uuid, "major": major, "minor": minor, "tx_power": tx_power}


def identify(
    mac: str,
    name: Optional[str],
    manufacturer_data: Dict[int, bytes],
    service_data: Dict[str, bytes],
    known_macs: Set[str],
    known_ids: Optional[Dict[str, str]] = None,
    known_irks: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str], Optional[int]]:
    """Return (id, friendly_name, calibrated_rssi_at_1m_or_None)."""

    mac_key = mac.replace(":", "").lower()

    if known_ids and mac_key in known_ids:
        return known_ids[mac_key], name, None

    if mac_key in known_macs:
        return f"known:{mac_key}", name, None

    if known_irks and is_resolvable_private_address(mac):
        for irk_hex, device_id in known_irks.items():
            try:
                irk_bytes = bytes.fromhex(irk_hex)
            except ValueError:
                continue
            if resolve_rpa(mac, irk_bytes):
                return device_id, name, None

    apple_data = manufacturer_data.get(APPLE_COMPANY_ID)
    if apple_data:
        ibeacon = parse_ibeacon(apple_data)
        if ibeacon:
            ident = f"ibeacon:{ibeacon['uuid']}_{ibeacon['major']}_{ibeacon['minor']}"
            return ident, name, ibeacon["tx_power"]
        return f"apple:{mac_key}", name, None

    for uuid, payload in service_data.items():
        if uuid.lower() == EDDYSTONE_UUID and len(payload) >= 2:
            frame_type = payload[0]
            if frame_type == 0x00 and len(payload) >= 12:  # UID frame
                namespace = payload[2:12].hex()
                instance = payload[12:18].hex() if len(payload) >= 18 else ""
                return f"eddy:{namespace}_{instance}", name, None

    return f"generic:{mac_key}", name, None
