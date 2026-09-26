"""Keep the chart-managed Matter controller on Home Assistant's timezone."""
import asyncio
import logging

_LOGGER = logging.getLogger(__name__)


async def send_timezone(timezone: str) -> None:
    import aiohttp

    connector = aiohttp.UnixConnector(path="/run/kube4ha-matter/control.sock")
    # A timezone change may need the controller's 60s graceful shutdown.
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=90)
    ) as session:
        async with session.post(
            "http://localhost/timezone", json={"timezone": timezone}
        ) as response:
            response.raise_for_status()
            result = await response.json()
            if not isinstance(result, dict) or not isinstance(result.get("timezone"), str):
                raise ValueError("Invalid Matter timezone response")


class TimezoneBridge:
    """Reconcile on startup, config changes, and after controller outages."""

    def __init__(self, get_timezone, send=send_timezone, retry_seconds=60):
        self._get_timezone = get_timezone
        self._send = send
        self._retry_seconds = retry_seconds
        self._changed = asyncio.Event()

    def config_changed(self, _event=None):
        self._changed.set()

    async def run(self):
        failed = False
        while True:
            self._changed.clear()
            try:
                await self._send(self._get_timezone())
                if failed:
                    _LOGGER.info("Matter timezone synchronization recovered")
                failed = False
            except Exception as err:
                # Keep HA startup independent of Matter; retry after an outage,
                # including a snapshot's temporary 409 or controller replacement.
                if not failed:
                    _LOGGER.warning("Could not synchronize Matter timezone; retrying: %s", err)
                failed = True
            try:
                await asyncio.wait_for(self._changed.wait(), self._retry_seconds)
            except asyncio.TimeoutError:
                pass
