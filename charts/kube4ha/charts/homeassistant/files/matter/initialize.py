"""Restore a native-backup snapshot only into an empty Matter volume."""
import json
import gzip
import os
from pathlib import Path, PurePosixPath
import socket
import tarfile
import tempfile
import time


def restore(storage: Path, archive: Path, image: str) -> bool:
    target = storage / "server"
    if target.is_symlink():
        raise ValueError("Matter data directory must not be a symlink")
    if target.exists() and any(target.iterdir()):
        return False
    if not archive.exists():
        return False
    with gzip.open(archive, "rb") as compressed:
        while compressed.read(1024 * 1024):
            pass
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or member.name in names
                    or not (member.isfile() or member.isdir())
                    or not (path.parts and path.parts[0] == "server" or member.name == "metadata.json")):
                raise ValueError("Unsafe Matter snapshot member")
            names.add(member.name)
        metadata = source.getmember("metadata.json")
        if not metadata.isfile() or metadata.size > 4096:
            raise ValueError("Invalid Matter snapshot metadata")
        info = json.load(source.extractfile(metadata))
        if info.get("format") != 1 or info.get("server_image") != image:
            raise ValueError("Matter snapshot format or server image does not match")
        if not any(m.isfile() and m.name.startswith("server/") for m in members):
            raise ValueError("Matter snapshot contains no server data")
        # Fully validate/extract on the same filesystem before replacing even
        # an empty target. A corrupt archive must never initialize a new fabric.
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=storage) as work:
            source.extractall(work, members=[m for m in members if m.name != "metadata.json"], filter="data")
            if target.exists():
                target.rmdir()
            (Path(work) / "server").rename(target)
    return True


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, interval: float = 2.0, log=print, sleep=time.sleep) -> None:
    """Block until nothing listens on the loopback port.

    Matter shares the node's network namespace with Home Assistant, and only one
    controller may own the fabric storage. During a migration from the former
    sidecar, or while a previous pod is still shutting down, wait rather than
    crash-loop against the port and the storage lock.
    """
    waited = False
    while port_in_use(port):
        if not waited:
            log(f"Waiting for the previous Matter controller to release port {port}")
            waited = True
        sleep(interval)
    if waited:
        log(f"Port {port} is free")


def main():
    storage = Path("/data")
    wait_for_port(int(os.environ.get("PORT", "5580")), log=lambda m: print(m, flush=True))
    backup = Path("/config/.kube4ha/matter")
    if os.environ.get("MATTER_BACKUP_ENABLED") == "true":
        if restore(storage, backup / "latest.tar.gz", os.environ["MATTER_SERVER_IMAGE"]):
            print("Restored Matter state from the native Home Assistant backup")
        backup.mkdir(parents=True, exist_ok=True)
        backup.chmod(0o700)
        os.chown(backup, 1000, 1000)
        control = Path("/run/kube4ha-matter")
        control.chmod(0o700)
        os.chown(control, 1000, 1000)
    target = storage / "server"
    target.mkdir(exist_ok=True)
    target.chmod(0o700)
    for directory, dirs, files in os.walk(target):
        os.chown(directory, 1000, 1000)
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("Matter data must not contain symlinks")
            os.chown(path, 1000, 1000)


if __name__ == "__main__":
    main()
