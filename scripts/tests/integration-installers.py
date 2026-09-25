"""Exercise archive installers with scratch data retained across init retries."""
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output([
            "helm", "template", "installer-test",
            str(ROOT / "charts/kube4ha/charts/homeassistant"),
            "--set", "customComponents.remote[0].name=fixture",
            "--set", "customComponents.remote[0].url=https://example.invalid/fixture.tar.gz",
            "--set", "customComponents.remote[0].sha256=" + "0" * 64,
            "--set", "customComponents.remote[0].archivePath=fixture",
        ], text=True)
        cls.scripts = {}
        for name in ("hacs-install", "custom-fixture"):
            block = rendered.split(f"- name: {name}\n", 1)[1]
            script = block.split("            - |\n", 1)[1].split("          env:\n", 1)[0]
            cls.scripts[name] = re.sub(r"^              ", "", script, flags=re.M)

    def exercise(self, name):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            # Keep the real checksum and extraction tools; replace only network I/O.
            wget = root / "bin/wget"
            wget.write_text('#!/bin/sh\ncp "$FIXTURE_ARCHIVE" "$3"\n')
            wget.chmod(0o755)
            domain = "hacs" if name == "hacs-install" else "fixture"
            archive = root / ("fixture.zip" if domain == "hacs" else "fixture.tar.gz")

            def pack(manifest=True):
                files = {"component.py": b"verified source"}
                if manifest:
                    files["manifest.json"] = b'{}'
                if domain == "hacs":
                    with zipfile.ZipFile(archive, "w") as handle:
                        for path, data in files.items():
                            handle.writestr(path, data)
                else:
                    with tarfile.open(archive, "w:gz") as handle:
                        for path, data in files.items():
                            info = tarfile.TarInfo("fixture/" + path)
                            info.size = len(data)
                            handle.addfile(info, io.BytesIO(data))

            pack()
            env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}',
                       FIXTURE_ARCHIVE=str(archive), COMPONENT_NAME=domain,
                       COMPONENT_ARCHIVE_PATH="fixture", COMPONENT_URL="https://example.invalid/fixture",
                       HACS_URL="https://example.invalid/fixture")
            script = self.scripts[name].replace("/work", "./work").replace("/config", "./config")
            target = root / "config/custom_components" / domain
            unmanaged = root / "config/custom_components/unmanaged"
            unmanaged.mkdir(parents=True)
            (unmanaged / "keep").write_text("user data")

            def run(success=True, digest=None):
                checksum = digest or hashlib.sha256(archive.read_bytes()).hexdigest()
                env.update(HACS_SHA256=checksum, COMPONENT_SHA256=checksum)
                result = subprocess.run(["sh", "-c", script], cwd=root, env=env,
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                self.assertEqual((unmanaged / "keep").read_text(), "user data")

            run()
            # A reboot retains emptyDir contents, including files from an earlier attempt.
            unpacked = root / "work/unpacked"
            stale = unpacked / ("obsolete.py" if domain == "hacs" else "fixture/obsolete.py")
            stale.write_text("stale source")
            run()
            self.assertFalse((target / "obsolete.py").exists())
            self.assertEqual((target / "component.py").read_bytes(), b"verified source")
            run(success=False, digest="0" * 64)
            self.assertTrue((target / "manifest.json").exists())
            # An old manifest must not make an incomplete new archive appear valid.
            pack(manifest=False)
            run(success=False)
            self.assertTrue((target / "manifest.json").exists())

    def test_hacs_retry(self):
        self.exercise("hacs-install")

    def test_remote_component_retry(self):
        self.exercise("custom-fixture")


if __name__ == "__main__":
    unittest.main()
