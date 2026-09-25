"""Expose only Wi-Fi scan results from the host's existing wpa_supplicant.

The host retains ownership of its radio and connection. No network profiles or
credentials are read. Linux abstract client sockets work across the read-only
control-socket mount because the scan workload uses the host network namespace.
"""
import asyncio
import logging
import os
import signal

from dbus_fast import BusType, NameFlag, RequestNameReply, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

from wpa_scan import Scanner

BUS_NAME = "io.github.fiam.Kube4ha.Wifi"
OBJECT_PATH = "/io/github/fiam/Kube4ha/Wifi"
INTERFACE = "io.github.fiam.Kube4ha.Wifi1"
LOGGER = logging.getLogger(__name__)


def variants(networks):
    types = {"ssid": "s", "frequency": "u", "signal": "n", "security": "s", "interface": "s"}
    return [{key: Variant(types[key], value) for key, value in n.items()} for n in networks]


class WifiService(ServiceInterface):
    def __init__(self, scanner):
        super().__init__(INTERFACE)
        self.scanner = scanner

    @method()
    async def ListNetworks(self) -> "aa{sv}":
        return variants(await self.scanner.networks())

    @method()
    async def Scan(self) -> "aa{sv}":
        return variants(await self.scanner.networks(refresh=True))


async def main():
    scanner = Scanner(os.environ.get("WPA_CONTROL_DIRECTORY", "/run/wpa_supplicant"),
                      os.environ.get("WIFI_INTERFACES", "").split())
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        bus.export(OBJECT_PATH, WifiService(scanner))
        if await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE) != RequestNameReply.PRIMARY_OWNER:
            raise RuntimeError("A kube4ha Wi-Fi service already runs on this host")
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, bus.disconnect)
        LOGGER.info("Wi-Fi listing service ready")
        await bus.wait_for_disconnect()
    finally:
        bus.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
