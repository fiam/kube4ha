"""Install only the scan service's named policy; never restart the host bus."""
import asyncio
import os
from pathlib import Path
import tempfile

NAME = "io.github.fiam.Kube4ha.Wifi.conf"
MARKER = b"<!-- Managed by kube4ha Wi-Fi listing. -->"


def install(directory, policy):
    path = Path(directory) / NAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Refusing to replace a non-regular host policy")
    if path.exists():
        current = path.read_bytes()
        if current == policy:
            return
        if MARKER not in current:
            raise RuntimeError("Refusing to replace an unmanaged host policy")
    fd, temporary = tempfile.mkstemp(prefix=".kube4ha-wifi-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(policy)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def main():
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    install("/host-dbus-policy", Path("/opt/kube4ha-wifi/policy.conf").read_bytes())
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        async with asyncio.timeout(10):
            reply = await bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="ReloadConfig"))
        if reply.message_type != MessageType.METHOD_RETURN:
            raise RuntimeError("Unable to reload the host D-Bus policy")
    finally:
        bus.disconnect()
        await bus.wait_for_disconnect()


if __name__ == "__main__":
    asyncio.run(main())
