"""Bluetooth "incoming pairing" helper.

Mirrors upstream ESPresense's enrollment flow: instead of the Pi actively
connecting out to a target device by MAC address, the Pi's own Bluetooth
adapter is made discoverable/pairable for a short window and a permissive
pairing agent is registered. Whichever device the user then pairs *from*
(e.g. via their phone's or watch's Bluetooth settings) is captured and its
MAC address is returned, ready to be enrolled under a user-chosen id.

This talks to BlueZ directly over D-Bus using dbus-fast (already a
dependency of bleak on Linux).
"""
from __future__ import annotations

import asyncio
import glob
import logging
import re
from pathlib import Path
from typing import Optional

from dbus_fast import BusType, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.message import Message
from dbus_fast.service import ServiceInterface, method

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
AGENT_PATH = "/espresense/pi/agent"

_DEV_PATH_RE = re.compile(r"/dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})$")


def get_device_irk(mac: str) -> Optional[str]:
    """Best-effort read of a bonded device's Identity Resolving Key from
    BlueZ's local key storage, returned as a lowercase hex string (no
    separators), or None if the device has no stored IRK (e.g. it was
    only classic-BR/EDR-bonded with no LE privacy key exchanged).
    """
    device_mac = mac.replace("-", ":").upper()
    for info_path in glob.glob(f"/var/lib/bluetooth/*/{device_mac}/info"):
        section = None
        try:
            lines = Path(info_path).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section == "IdentityResolvingKey" and line.startswith("Key="):
                return line.split("=", 1)[1].strip().lower()
    return None


class _AutoAcceptAgent(ServiceInterface):
    """A BlueZ pairing agent (capability "NoInputNoOutput") that accepts
    every incoming pairing request without prompting. Only registered for
    the duration of a short, user-initiated enrollment window.
    """

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        return "0000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        return  # returning normally (no DBusError) = accept

    @method()
    def RequestAuthorization(self, device: "o"):
        return

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        return

    @method()
    def Cancel(self):
        pass


def _mac_from_device_path(path: str) -> Optional[str]:
    m = _DEV_PATH_RE.search(path)
    if not m:
        return None
    return m.group(1).replace("_", ":").upper()


async def _enter_pairing_mode(timeout: float) -> str:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    found: "asyncio.Future[str]" = asyncio.get_event_loop().create_future()
    agent_mgr = None
    adapter_props = None

    def _maybe_resolve(path: str) -> None:
        if not found.done():
            mac = _mac_from_device_path(path)
            if mac:
                found.set_result(mac)

    def on_message(message: Message) -> None:
        if (
            message.message_type == MessageType.SIGNAL
            and message.interface == "org.freedesktop.DBus.Properties"
            and message.member == "PropertiesChanged"
        ):
            interface_name, changed, _invalidated = message.body
            if interface_name == "org.bluez.Device1":
                paired = changed.get("Paired")
                if paired is not None and paired.value:
                    _maybe_resolve(message.path)

    def on_interfaces_added(path: str, interfaces) -> None:
        dev = interfaces.get("org.bluez.Device1")
        if dev and dev.get("Paired"):
            _maybe_resolve(path)

    bus.add_message_handler(on_message)
    agent = _AutoAcceptAgent()
    bus.export(AGENT_PATH, agent)

    try:
        await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                interface="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                member="AddMatch",
                signature="s",
                body=["type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'"],
            )
        )

        agent_mgr_intro = await bus.introspect(BLUEZ_SERVICE, "/org/bluez")
        agent_mgr = bus.get_proxy_object(BLUEZ_SERVICE, "/org/bluez", agent_mgr_intro).get_interface(
            "org.bluez.AgentManager1"
        )
        await agent_mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
        await agent_mgr.call_request_default_agent(AGENT_PATH)

        adapter_intro = await bus.introspect(BLUEZ_SERVICE, ADAPTER_PATH)
        adapter_props = bus.get_proxy_object(BLUEZ_SERVICE, ADAPTER_PATH, adapter_intro).get_interface(
            "org.freedesktop.DBus.Properties"
        )

        timeout_s = max(1, int(timeout))
        await adapter_props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", True))
        await adapter_props.call_set("org.bluez.Adapter1", "PairableTimeout", Variant("u", timeout_s))
        await adapter_props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", True))
        await adapter_props.call_set("org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", timeout_s))

        object_mgr_intro = await bus.introspect(BLUEZ_SERVICE, "/")
        object_mgr = bus.get_proxy_object(BLUEZ_SERVICE, "/", object_mgr_intro).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        object_mgr.on_interfaces_added(on_interfaces_added, unpack_variants=True)

        try:
            return await asyncio.wait_for(found, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"No device paired within {timeout_s}s. On the device you want to "
                "enroll, open its Bluetooth settings, find this Pi, and pair with it."
            )
    finally:
        if adapter_props is not None:
            try:
                await adapter_props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", False))
                await adapter_props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", False))
            except Exception:
                logger.debug("Failed to reset adapter discoverable/pairable state", exc_info=True)
        if agent_mgr is not None:
            try:
                await agent_mgr.call_unregister_agent(AGENT_PATH)
            except Exception:
                logger.debug("Failed to unregister pairing agent", exc_info=True)
        bus.remove_message_handler(on_message)
        try:
            bus.unexport(AGENT_PATH)
        except Exception:
            pass
        bus.disconnect()


def enter_pairing_mode_sync(timeout: float = 60.0) -> str:
    """Block the calling thread while the Pi is made Bluetooth
    discoverable/pairable, returning the MAC address (colon-separated,
    uppercase) of the first device that successfully pairs.

    Spins up its own asyncio event loop, so it's safe to call from a plain
    (non-async) request-handling thread such as a Flask view function.
    Raises TimeoutError if no device pairs within ``timeout`` seconds.
    """
    return asyncio.run(_enter_pairing_mode(timeout))

