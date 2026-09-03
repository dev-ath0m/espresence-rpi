"""BLE Resolvable Private Address (RPA) resolution.

Implements the Bluetooth Core Spec Vol 3, Part H "ah" function, used to
check whether a Resolvable Private Address (RPA) was generated from a
given Identity Resolving Key (IRK). This mirrors the exact algorithm
upstream ESPresense's firmware uses in BleFingerprint.cpp's
`ble_ll_resolv_rpa()`, so a device bonded once (and whose IRK we captured
at bonding time, see pairing.get_device_irk()) can keep being recognized
even as its advertised BLE address rotates every few minutes.

Byte-order note: per the BLE spec, an RPA's most significant 3 octets are
"prand" (with the top 2 bits fixed to 0b01) and its least significant 3
octets are "hash" = ah(IRK, prand). The `irk` bytes here are expected in
the same raw order BlueZ stores them in
/var/lib/bluetooth/<adapter>/<device>/info's [IdentityResolvingKey] Key=
field (see pairing.get_device_irk()) -- both BlueZ and the ESP32
firmware's NimBLE stack store the IRK octets in on-the-wire SMP order
with no extra reversal, so a direct byte-for-byte read is expected to be
compatible.
"""
from __future__ import annotations

from Crypto.Cipher import AES


def ah(irk: bytes, prand: bytes) -> bytes:
    """Bluetooth Core Spec "ah" function: hash = e(IRK, prand'), where
    prand' is the 3-byte prand zero-padded on the most-significant side
    to a 16-byte AES block. Returns the 3-byte hash.
    """
    plaintext = bytes(13) + prand
    ciphertext = AES.new(irk, AES.MODE_ECB).encrypt(plaintext)
    return ciphertext[13:16]


def is_resolvable_private_address(mac: str) -> bool:
    """True if mac's most significant octet has its top 2 bits set to
    0b01, marking it as a Resolvable Private Address (RPA) per the BLE
    spec (as opposed to a non-resolvable private, static random, or
    public address).
    """
    first_byte = int(mac.split(":")[0], 16)
    return (first_byte & 0xC0) == 0x40


def resolve_rpa(mac: str, irk: bytes) -> bool:
    """Return True if the RPA `mac` (colon-separated hex, e.g.
    "4F:BB:CC:DD:EE:FF") was generated from `irk` (16 raw bytes).
    """
    addr = bytes.fromhex(mac.replace(":", ""))
    if len(addr) != 6 or len(irk) != 16:
        return False
    prand, expected_hash = addr[0:3], addr[3:6]
    return ah(irk, prand) == expected_hash
