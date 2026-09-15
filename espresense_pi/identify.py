"""Turn a BLE advertisement into an ESPresense device id.

This is a port of ``BleFingerprint.cpp`` from the ESP32 firmware so that a Pi
node and an ESP32 node agree on the id they publish for the same device.  Every
candidate id carries the firmware's ``ID_TYPE_*`` priority and the strongest one
wins, mirroring ``BleFingerprint::setId()``.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, NamedTuple, Optional

from espresense_pi.irk import resolve_rpa

# ID_TYPE_* priorities from BleFingerprint.h.  The address is always
# fingerprinted first with a positive priority, so the negative types below can
# only ever lose -- they exist to document the firmware's ordering.
ID_TYPE_ECHO_LOST = -10
ID_TYPE_MISC_APPLE = -5
ID_TYPE_RAND_MAC = 1
ID_TYPE_RAND_STATIC_MAC = 5
ID_TYPE_AD = 10
ID_TYPE_SD = 15
ID_TYPE_MD = 20
ID_TYPE_MISC = 30
ID_TYPE_FINDMY = 32
ID_TYPE_NAME = 35
ID_TYPE_MSFT = 40
ID_TYPE_PUBLIC_MAC = 55
ID_TYPE_SONOS = 105
ID_TYPE_GARMIN = 107
ID_TYPE_MITHERM = 110
ID_TYPE_MIFIT = 115
ID_TYPE_EXPOSURE = 120
ID_TYPE_SMARTTAG = 121
ID_TYPE_ITAG = 125
ID_TYPE_ITRACK = 127
ID_TYPE_NUT = 128
ID_TYPE_FLORA = 129
ID_TYPE_TRACKR = 130
ID_TYPE_TILE = 135
ID_TYPE_MEATER = 140
ID_TYPE_TRACTIVE = 142
ID_TYPE_VANMOOF = 145
ID_TYPE_DEXA = 146
ID_TYPE_APPLE_NEARBY = 150
ID_TYPE_EBEACON = 170
ID_TYPE_ABEACON = 175
ID_TYPE_IBEACON = 180
ID_TYPE_KNOWN_IRK = 200
ID_TYPE_KNOWN_MAC = 210
ID_TYPE_ALIAS = 250

# Eddystone reports its power at 0 m; this converts it to power at 1 m.
EDDYSTONE_ADD_1M = -41

_BASE_UUID_TAIL = "-0000-1000-8000-00805f9b34fb"

UUID_EDDYSTONE = "0xfeaa"
UUID_TILE = "0xfeed"
UUID_EXPOSURE = "0xfd6f"
UUID_SMARTTAG = "0xfd5a"
UUID_SONOS = "0xfe07"
UUID_ITAG = "0xffe0"
UUID_MITHERM = "0x181a"
UUID_TRACKR = "0x0f3e"
UUID_DEXA = "0xfebc"
UUID_NUT = "0x1803"
UUID_MIFLORA = "0xfe95"
UUID_VANMOOF = "6acc5540-e631-4069-944d-b8ca7598ad50"
UUID_TRACTIVE = "20130001-0719-4b6e-be5d-158ab92fa5a4"
UUID_MEATER = "a75cc7fc-c956-488f-ac2a-2dbc08b63a04"

# Advertised service UUIDs that identify a product outright.  Checked before
# the generic "ad:" fingerprint, and the first match short-circuits the rest.
_SERVICE_ADV_IDS = {
    UUID_TILE: ("tile:", ID_TYPE_TILE),
    UUID_SONOS: ("sonos:", ID_TYPE_SONOS),
    UUID_ITAG: ("itag:", ID_TYPE_ITAG),
    UUID_TRACKR: ("trackr:", ID_TYPE_TRACKR),
    UUID_TRACTIVE: ("tractive:", ID_TYPE_TRACTIVE),
    UUID_VANMOOF: ("vanmoof:", ID_TYPE_VANMOOF),
    UUID_MEATER: ("meater:", ID_TYPE_MEATER),
    UUID_NUT: ("nut:", ID_TYPE_NUT),
    UUID_MIFLORA: ("flora:", ID_TYPE_FLORA),
    UUID_DEXA: ("dexa:", ID_TYPE_DEXA),
}

# Company ids that map straight onto a "<vendor>:<mac>" id.
_VENDOR_MANUFACTURERS = {
    "05a7": ("sonos:", ID_TYPE_SONOS),
    "0087": ("garmin:", ID_TYPE_GARMIN),
    "4d4b": ("iTrack:", ID_TYPE_ITRACK),
    "0157": ("mifit:", ID_TYPE_MIFIT),
    "0075": ("samsung:", ID_TYPE_MISC),
}


class Advertisement(NamedTuple):
    """A single BLE advertisement, as handed to :func:`identify`."""

    address: str
    address_type: Optional[str]
    name: Optional[str]
    rssi: int
    tx_power: Optional[int]
    manufacturer_data: Dict[int, bytes]
    service_data: Dict[str, bytes]
    service_uuids: List[str]


class Fingerprint(NamedTuple):
    id: str
    name: Optional[str]
    rssi_at_1m: Optional[int]


def _normalize_word_separators(text: str, replacement: str) -> str:
    out: List[str] = []
    last_was_replacement = False
    for char in text:
        if char == "_" or (char.isascii() and char.isalnum()):
            out.append(char.lower())
            last_was_replacement = False
        elif not last_was_replacement:
            out.append(replacement)
            last_was_replacement = True
    return "".join(out).strip(replacement)


def kebabify(text: str) -> str:
    return _normalize_word_separators(text, "-")


def uuid_str(uuid: str) -> str:
    """Render a UUID the way NimBLE's ``ble_uuid_to_str`` does.

    Short UUIDs collapse to ``0xfeaa`` form; everything else stays a full dashed
    string.  The ids we publish have to match the firmware byte for byte, so we
    cannot just use the 128-bit form bleak hands us.
    """
    value = str(uuid).lower()
    if value.endswith(_BASE_UUID_TAIL):
        head = value[: len(value) - len(_BASE_UUID_TAIL)]
        return "0x" + (head[4:] if head.startswith("0000") else head)
    return value


def _int8(value: int) -> int:
    return value - 256 if value > 127 else value


def _beacon_uuid(payload: bytes) -> str:
    """Dashed UUID from an iBeacon/AltBeacon body (company id already stripped)."""
    digits = payload[2:18].hex()
    return f"{digits[0:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:32]}"


def _eddystone_uid(payload: bytes) -> str:
    # The firmware's format string repeats payload[6] when printing the 10-byte
    # namespace.  That quirk is part of the published id, so it is mirrored here
    # on purpose -- "fixing" it would desync us from every ESP32 node.
    namespace = payload[2:7] + payload[6:12]
    return f"eddy:{namespace.hex()}-{payload[12:18].hex()}"


class _Id:
    """Accumulates the winning id, replicating ``BleFingerprint::setId``."""

    def __init__(self) -> None:
        self.value = ""
        self.type = 0

    def set(self, value: str, id_type: int) -> None:
        if self.type < 0 and id_type < 0 and id_type >= self.type:
            return
        if self.type > 0 and id_type <= self.type:
            return
        self.value = value
        self.type = id_type


def _resolve_irk(mac: str, known_irks: Optional[Dict[str, str]]) -> Optional[str]:
    for irk_hex, device_id in (known_irks or {}).items():
        try:
            irk_bytes = bytes.fromhex(irk_hex)
        except ValueError:
            continue
        if len(irk_bytes) == 16 and resolve_rpa(mac, irk_bytes):
            return device_id
    return None


def _fingerprint_address(
    best: _Id,
    mac: str,
    address_type: Optional[str],
    known_macs: Iterable[str],
    known_ids: Optional[Dict[str, str]],
    known_irks: Optional[Dict[str, str]],
) -> None:
    alias = (known_ids or {}).get(mac)
    if alias:
        best.set(alias, ID_TYPE_ALIAS)
        return
    if any(mac.startswith(prefix) for prefix in known_macs if prefix):
        best.set(f"known:{mac}", ID_TYPE_KNOWN_MAC)
        return
    if (address_type or "").lower() == "public":
        best.set(mac, ID_TYPE_PUBLIC_MAC)
        return
    # Random address: static ones (top two bits set) never rotate and are used
    # as-is, the rest may be resolvable against an enrolled IRK.
    if int(mac[0:2], 16) & 0xC0 == 0xC0:
        best.set(mac, ID_TYPE_RAND_STATIC_MAC)
        return
    resolved = _resolve_irk(mac, known_irks)
    if resolved:
        best.set(resolved, ID_TYPE_KNOWN_IRK)
    else:
        best.set(mac, ID_TYPE_RAND_MAC)


def _fingerprint_service_advertisements(
    best: _Id, mac: str, service_uuids: Iterable[str], tx_suffix: str
) -> None:
    uuids = [uuid_str(uuid) for uuid in service_uuids]
    if not uuids:
        return
    for uuid in uuids:
        vendor = _SERVICE_ADV_IDS.get(uuid)
        if vendor:
            best.set(f"{vendor[0]}{mac}", vendor[1])
            return
    best.set("ad:" + "".join(uuids) + tx_suffix, ID_TYPE_AD)


def _fingerprint_service_data(
    best: _Id, mac: str, service_data: Dict[str, bytes], tx_suffix: str
) -> Optional[int]:
    rssi_at_1m = None
    fingerprint = ""
    for raw_uuid, payload in service_data.items():
        uuid = uuid_str(raw_uuid)
        if uuid == UUID_EXPOSURE:
            best.set(f"exp:{len(payload)}", ID_TYPE_EXPOSURE)
        elif uuid == UUID_SMARTTAG:
            best.set(f"smarttag:{len(payload)}", ID_TYPE_SMARTTAG)
        elif uuid == UUID_MITHERM:
            if len(payload) in (13, 15):
                best.set(f"miTherm:{mac}", ID_TYPE_MITHERM)
        elif uuid == UUID_EDDYSTONE and payload:
            if payload[0] == 0x10 and 2 <= len(payload) <= 18:  # URL frame
                rssi_at_1m = EDDYSTONE_ADD_1M + _int8(payload[1])
            elif payload[0] == 0x00 and len(payload) >= 18:  # UID frame
                rssi_at_1m = EDDYSTONE_ADD_1M + _int8(payload[1])
                best.set(_eddystone_uid(payload), ID_TYPE_EBEACON)
        else:
            fingerprint += uuid
    if fingerprint:
        best.set("sd:" + fingerprint + tx_suffix, ID_TYPE_SD)
    return rssi_at_1m


def _fingerprint_manufacturer_data(
    best: _Id, mac: str, manufacturer_data: Dict[int, bytes], tx_suffix: str
) -> Optional[int]:
    rssi_at_1m = None
    for company_id, payload in manufacturer_data.items():
        if not payload:
            continue
        # bleak strips the two company-id bytes that the firmware counts as part
        # of the manufacturer data, so add them back: the length ends up in the
        # published id.
        length = len(payload) + 2
        manuf = f"{company_id:04x}"

        if manuf == "004c":  # Apple
            if length == 25 and payload[0:2] == b"\x02\x15":
                beacon_rssi = _int8(payload[22])
                rssi_at_1m = beacon_rssi
                major = int.from_bytes(payload[18:20], "big")
                minor = int.from_bytes(payload[20:22], "big")
                best.set(
                    f"iBeacon:{_beacon_uuid(payload)}-{major}-{minor}",
                    ID_TYPE_IBEACON if beacon_rssi != 3 else ID_TYPE_ECHO_LOST,
                )
            elif length >= 4 and payload[0] == 0x10:
                best.set(
                    f"apple:{payload[0]:02x}{payload[1]:02x}:{length}" + tx_suffix,
                    ID_TYPE_APPLE_NEARBY,
                )
            elif length == 29 and payload[0] == 0x12:
                best.set("apple:findmy", ID_TYPE_FINDMY)
            elif length >= 4:
                best.set(
                    f"apple:{payload[0]:02x}{payload[1]:02x}:{length}" + tx_suffix,
                    ID_TYPE_MISC_APPLE,
                )
        elif manuf in _VENDOR_MANUFACTURERS:
            prefix, id_type = _VENDOR_MANUFACTURERS[manuf]
            best.set(f"{prefix}{mac}", id_type)
        elif manuf == "0006" and length == 29:  # Microsoft CDP
            best.set(f"msft:cdp:{payload[1]:02x}{payload[3]:02x}", ID_TYPE_MSFT)
        elif manuf == "beac" and length == 26:
            rssi_at_1m = _int8(payload[22])
            major = int.from_bytes(payload[18:20], "big")
            minor = int.from_bytes(payload[20:22], "big")
            best.set(f"altBeacon:{_beacon_uuid(payload)}-{major}-{minor}", ID_TYPE_ABEACON)
        elif manuf != "0000":
            best.set(f"md:{manuf}:{length}" + tx_suffix, ID_TYPE_MD)
    return rssi_at_1m


def identify(
    adv: Advertisement,
    known_macs: Iterable[str],
    known_ids: Optional[Dict[str, str]] = None,
    known_irks: Optional[Dict[str, str]] = None,
) -> Fingerprint:
    """Best id for an advertisement, plus its name and calibrated 1 m RSSI."""
    mac = adv.address.replace(":", "").lower()
    best = _Id()
    tx_suffix = "" if adv.tx_power is None else str(-adv.tx_power)

    _fingerprint_address(best, mac, adv.address_type, known_macs, known_ids, known_irks)

    if adv.name:
        kebab = kebabify(adv.name)
        if kebab:
            best.set(f"name:{kebab}", ID_TYPE_NAME)

    _fingerprint_service_advertisements(best, mac, adv.service_uuids or (), tx_suffix)
    sd_rssi = _fingerprint_service_data(best, mac, adv.service_data or {}, tx_suffix)
    md_rssi = _fingerprint_manufacturer_data(best, mac, adv.manufacturer_data or {}, tx_suffix)

    rssi_at_1m = md_rssi if md_rssi is not None else sd_rssi
    return Fingerprint(best.value or mac, adv.name, rssi_at_1m)
