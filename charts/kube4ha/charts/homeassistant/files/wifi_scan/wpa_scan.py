"""Scan-only adapter for existing host wpa_supplicant control sockets."""
import asyncio
import logging
from pathlib import Path
import socket
import uuid

LOGGER = logging.getLogger(__name__)


def decode_ssid(value):
    """Decode wpa_supplicant's printf_encode representation, preserving UTF-8."""
    raw = value.encode("utf-8")
    result = bytearray()
    i = 0
    escaped = {ord("n"): 10, ord("r"): 13, ord("t"): 9,
               ord("e"): 27, ord("\\"): 92, ord('"'): 34}
    while i < len(raw):
        if raw[i] != 92:
            result.append(raw[i])
            i += 1
            continue
        i += 1
        if i >= len(raw):
            raise ValueError("Incomplete SSID escape")
        if raw[i] == ord("x"):
            if i + 2 >= len(raw):
                raise ValueError("Incomplete SSID escape")
            result.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            result.append(escaped[raw[i]])
            i += 1
    if not 1 <= len(result) <= 32:
        raise ValueError("Invalid SSID length")
    return result.decode("utf-8")


def parse_results(response, interface):
    """Allowlist public scan fields; ignore malformed/hidden network names."""
    networks = []
    for row in response.splitlines()[1:]:
        fields = row.split("\t", 4)
        if len(fields) != 5:
            continue
        try:
            _, frequency, strength, security, encoded_ssid = fields
            ssid = decode_ssid(encoded_ssid)
            frequency, strength = int(frequency), int(strength)
            if not 2000 <= frequency <= 7500 or not -127 <= strength <= 0:
                continue
        except (ValueError, KeyError, UnicodeError):
            continue
        networks.append({"ssid": ssid, "frequency": frequency, "signal": strength,
                         "security": security[:256], "interface": interface})
    return networks


class ControlSocket:
    def __init__(self, path):
        self.path = path
        self.sock = None

    async def __aenter__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        try:
            self.sock.bind("\0kube4ha-wifi-" + uuid.uuid4().hex)
            self.sock.connect(str(self.path))
        except BaseException:
            self.sock.close()
            raise
        return self

    async def __aexit__(self, *_args):
        self.sock.close()

    async def receive(self):
        data = await asyncio.get_running_loop().sock_recv(self.sock, 65535)
        return data.decode("utf-8", errors="replace").rstrip("\n")

    async def command(self, command):
        # No caller-supplied commands or parameters can reach the control socket.
        if command not in {"ATTACH", "DETACH", "SCAN TYPE=ONLY", "SCAN_RESULTS"}:
            raise ValueError("Unsupported Wi-Fi operation")
        async def request():
            await asyncio.get_running_loop().sock_sendall(self.sock, command.encode())
            while (response := await self.receive()).startswith("<"):
                pass  # Unsolicited control events can precede command replies.
            return response
        return await asyncio.wait_for(request(), timeout=2)

    async def scan(self):
        if await self.command("ATTACH") != "OK":
            return
        try:
            response = await self.command("SCAN TYPE=ONLY")
            if response not in {"OK", "FAIL-BUSY"}:
                return

            async def wait_for_scan():
                while True:
                    event = await self.receive()
                    if "CTRL-EVENT-SCAN-RESULTS" in event or "CTRL-EVENT-SCAN-FAILED" in event:
                        return
            try:
                await asyncio.wait_for(wait_for_scan(), timeout=8)
            except asyncio.TimeoutError:
                pass  # Retain the host's last results when a fresh scan is slow.
        finally:
            await self.command("DETACH")


class Scanner:
    def __init__(self, directory, interfaces=()):
        self.directory = Path(directory)
        self.interfaces = set(interfaces)
        self.lock = asyncio.Lock()
        self.last_scan = float("-inf")

    def paths(self):
        return [p for p in self.directory.iterdir()
                if p.is_socket() and not p.name.startswith("p2p-")
                and p.name != "global" and (not self.interfaces or p.name in self.interfaces)]

    async def networks(self, refresh=False):
        async with self.lock:
            loop = asyncio.get_running_loop()
            scan = refresh and loop.time() - self.last_scan >= 15
            if scan:
                self.last_scan = loop.time()
            networks = []
            paths = await loop.run_in_executor(None, self.paths)
            for path in paths:
                try:
                    async with ControlSocket(path) as control:
                        if scan:
                            await control.scan()
                        response = await control.command("SCAN_RESULTS")
                        networks.extend(parse_results(response, path.name))
                except (OSError, asyncio.TimeoutError):
                    # Do not log command responses, SSIDs, or unrelated events.
                    LOGGER.warning("Wi-Fi scan interface unavailable")
            # Multiple BSSIDs for one band/SSID are one choice; keep the strongest.
            strongest = {}
            for network in networks:
                key = (network["ssid"], network["frequency"] // 1000)
                if key not in strongest or network["signal"] > strongest[key]["signal"]:
                    strongest[key] = network
            return sorted(strongest.values(), key=lambda n: (-n["signal"], n["ssid"]))

