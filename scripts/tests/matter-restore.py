"""Recovery must reject unsafe snapshots and preserve an existing fabric."""
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "charts/kube4ha/charts/homeassistant/files/matter/initialize.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("matter_initialize", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = "ghcr.io/matter-js/matterjs-server:1.4.0"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.storage = self.root / "data"
        self.storage.mkdir()
        self.archive = self.root / "latest.tar.gz"

    def make_archive(self, name="server/state", image=IMAGE, symlink=False):
        with tarfile.open(self.archive, "w:gz") as archive:
            for path, data in [("metadata.json", json.dumps({"format": 1, "server_image": image}).encode()), (name, b"fixture")]:
                info = tarfile.TarInfo(path)
                if symlink and path == name:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/outside"
                    archive.addfile(info)
                else:
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))

    def test_recovers_only_empty_volume(self):
        self.make_archive()
        self.assertTrue(module.restore(self.storage, self.archive, IMAGE))
        self.assertEqual((self.storage / "server/state").read_bytes(), b"fixture")
        (self.storage / "server/state").write_bytes(b"newer state")
        self.assertFalse(module.restore(self.storage, self.archive, IMAGE))
        self.assertEqual((self.storage / "server/state").read_bytes(), b"newer state")

    def test_new_install_without_snapshot(self):
        self.assertFalse(module.restore(self.storage, self.archive, IMAGE))
        self.assertFalse((self.storage / "server").exists())

    def test_rejects_paths_links_and_mismatched_images(self):
        for name, image, link in [("../escape", IMAGE, False), ("/server/escape", IMAGE, False), ("server/link", IMAGE, True), ("server/state", "different-image", False)]:
            with self.subTest(name=name, image=image, link=link):
                self.make_archive(name, image, link)
                with self.assertRaises(ValueError):
                    module.restore(self.storage, self.archive, IMAGE)
                self.assertFalse((self.storage / "server").exists())

    def test_corruption_does_not_initialize_new_fabric(self):
        self.make_archive()
        data = self.archive.read_bytes()
        self.archive.write_bytes(data[:-6])
        with self.assertRaises((EOFError, OSError, tarfile.TarError)):
            module.restore(self.storage, self.archive, IMAGE)
        self.assertFalse((self.storage / "server").exists())


class PortReleaseTests(unittest.TestCase):
    """A replacement controller waits for the previous one instead of crash-looping."""

    def test_waits_until_previous_controller_exits(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        polls = []

        def release(_interval):
            polls.append(True)
            if len(polls) == 2:
                listener.close()

        messages = []
        module.wait_for_port(port, interval=0, log=messages.append, sleep=release)
        self.assertEqual(len(polls), 2)
        self.assertEqual(len(messages), 2)
        self.assertIn(str(port), messages[0])

    def test_free_port_does_not_wait(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        module.wait_for_port(port, sleep=lambda _interval: self.fail("must not sleep"), log=lambda _m: self.fail("must not log"))


if __name__ == "__main__":
    unittest.main()
