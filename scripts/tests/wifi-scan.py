"""Scan parsing, no-radio behavior and isolation from host network settings."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).resolve().parents[2] / "charts/kube4ha/charts/homeassistant/files/wifi_scan/wpa_scan.py"
spec = importlib.util.spec_from_file_location("wpa_scan", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy_spec = importlib.util.spec_from_file_location("install_policy", SOURCE.with_name("install_policy.py"))
policy_module = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy_module)

HEADER = "bssid / frequency / signal level / flags / ssid\n"


class ParserTests(unittest.TestCase):
    def test_unicode_spaces_and_escaped_backslashes(self):
        rows = (HEADER + "aa\t2412\t-40\t[WPA2-PSK-CCMP][ESS]\tCaf\\xc3\\xa9 \\x5c\n"
                + "bb\t5180\t-70\t[ESS]\t Open network \n")
        result = module.parse_results(rows, "wlan0")
        self.assertEqual([r["ssid"] for r in result], ["Café \\", " Open network "])
        self.assertEqual(set(result[0]), {"ssid", "frequency", "signal", "security", "interface"})

    def test_rejects_hidden_malformed_and_non_utf8_networks(self):
        rows = HEADER + "\n".join([
            "aa\t2412\t-40\t[ESS]\t", "aa\t2412\t-40\t[ESS]\t\\xff",
            "aa\t2412\t-40\t[ESS]\t\\xZ1", "aa\t2412\t-40\t[ESS]\t\\",
            "aa\tnan\t-40\t[ESS]\tBad", "aa\t2412\t400\t[ESS]\tBad",
        ])
        self.assertEqual(module.parse_results(rows, "wlan0"), [])

    def test_policy_only_grants_root_the_named_scan_service(self):
        policy = ET.parse(SOURCE.with_name("policy.conf")).getroot().find("policy")
        self.assertEqual(policy.attrib, {"user": "root"})
        self.assertEqual([rule.attrib for rule in policy], [
            {"own": "io.github.fiam.Kube4ha.Wifi"},
            {"send_destination": "io.github.fiam.Kube4ha.Wifi"},
        ])

    def test_policy_install_preserves_unmanaged_files_and_refuses_symlinks(self):
        content = SOURCE.with_name("policy.conf").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / policy_module.NAME
            policy_module.install(directory, content)
            policy_module.install(directory, content)
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            path.write_text("User policy")
            with self.assertRaises(RuntimeError):
                policy_module.install(directory, content)
            self.assertEqual(path.read_text(), "User policy")
            path.unlink()
            target = Path(directory) / "unrelated.conf"
            target.write_text("Unrelated")
            path.symlink_to(target)
            with self.assertRaises(RuntimeError):
                policy_module.install(directory, content)
            self.assertEqual(target.read_text(), "Unrelated")


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_radio_is_empty_and_does_not_start_one(self):
        with tempfile.TemporaryDirectory() as directory:
            scanner = module.Scanner(directory)
            self.assertEqual(await scanner.networks(refresh=True), [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_socket_command_allowlist(self):
        client = module.ControlSocket("unused")
        for command in ("ADD_NETWORK", "RECONFIGURE", "SAVE_CONFIG", "DISCONNECT", "STATUS"):
            with self.assertRaises(ValueError):
                await client.command(command)

    async def test_scan_does_not_roam_and_detaches_before_reading_results(self):
        client = module.ControlSocket("unused")
        client.command = AsyncMock(return_value="OK")
        client.receive = AsyncMock(side_effect=["<3>CTRL-EVENT-IGNORED", "<3>CTRL-EVENT-SCAN-RESULTS"])
        await client.scan()
        self.assertEqual([c.args[0] for c in client.command.call_args_list],
                         ["ATTACH", "SCAN TYPE=ONLY", "DETACH"])

    async def test_scan_rate_limit_deduplicates_without_losing_other_band(self):
        scanner = module.Scanner("unused")
        control = AsyncMock()
        control.__aenter__.return_value = control
        control.command.return_value = (HEADER + "aa\t2412\t-80\t[ESS]\tHome\n"
            + "bb\t2437\t-40\t[ESS]\tHome\ncc\t5180\t-70\t[ESS]\tHome\n")
        with patch.object(scanner, "paths", return_value=[Path("wlan0")]), \
                patch.object(module, "ControlSocket", return_value=control):
            result, again = await asyncio.gather(scanner.networks(True), scanner.networks(True))
        self.assertEqual(result, again)
        self.assertEqual([r["signal"] for r in result], [-40, -70])
        control.scan.assert_awaited_once()
        self.assertTrue(all(c.args == ("SCAN_RESULTS",) for c in control.command.call_args_list))


if __name__ == "__main__":
    unittest.main()
