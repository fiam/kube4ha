"""Exercise the HA bridge without importing a full Home Assistant runtime."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
import sys

sys.dont_write_bytecode = True

path = Path(__file__).resolve().parents[2] / "charts/kube4ha/charts/homeassistant/files/kube4ha_matter_backup/timezone.py"
spec = importlib.util.spec_from_file_location("matter_timezone", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_change_retry_and_cancellation(self):
        current = "Europe/Lisbon"
        attempts = []
        ready = asyncio.Event()

        async def send(zone):
            attempts.append(zone)
            if len(attempts) == 1:
                raise OSError("controller unavailable")
            ready.set()

        bridge = module.TimezoneBridge(lambda: current, send, retry_seconds=0.01)
        task = asyncio.create_task(bridge.run())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            self.assertEqual(attempts[:2], ["Europe/Lisbon"] * 2)
            ready.clear()
            current = "America/New_York"
            bridge.config_changed()
            await asyncio.wait_for(ready.wait(), 1)
            self.assertEqual(attempts[-1], current)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        count = len(attempts)
        bridge.config_changed()
        await asyncio.sleep(0.02)
        self.assertEqual(len(attempts), count)

    async def test_change_during_inflight_request_is_not_lost(self):
        current = "Europe/Lisbon"
        entered = asyncio.Event()
        release = asyncio.Event()
        applied = asyncio.Event()
        attempts = []

        async def send(zone):
            attempts.append(zone)
            if len(attempts) == 1:
                entered.set()
                await release.wait()
            else:
                applied.set()

        bridge = module.TimezoneBridge(lambda: current, send, retry_seconds=60)
        task = asyncio.create_task(bridge.run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            current = "Asia/Tokyo"
            bridge.config_changed()
            release.set()
            await asyncio.wait_for(applied.wait(), 1)
            self.assertEqual(attempts, ["Europe/Lisbon", "Asia/Tokyo"])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
