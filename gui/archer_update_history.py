"""Store a bounded pre/post package observation and compare it after reboot."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile

from archer_health import compare, snapshot
from archer_inventory import collect_inventory

DIRECTORY = Path("/var/lib/archer/update-health")


def read_record(path):
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Invalid update snapshot")
    with path.open() as saved:
        return json.load(saved)


def record(stage, directory=DIRECTORY, observe=None):
    if stage not in ("before", "after"):
        raise ValueError("Unknown snapshot stage")
    if directory.resolve() != directory:
        raise ValueError("Refusing redirected system snapshot directory")
    directory.mkdir(parents=True, mode=0o755, exist_ok=True)
    fd = os.open(directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = (observe or (lambda: snapshot(collect_inventory(), Path("/"))))()
        encoded = json.dumps(data).encode()
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError("Snapshot exceeds size limit")
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                os.fchmod(stream.fileno(), 0o644)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, directory / (stage + ".json"))
        finally:
            Path(tmp).unlink(missing_ok=True)


def regression(root, current):
    directory = root / DIRECTORY.relative_to("/")
    before = directory / "before.json"
    if not before.exists() or before.resolve() != before:
        return None
    return compare(read_record(before), current)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("before", "after"))
    try:
        record(parser.parse_args().stage)
    except (ValueError, OSError) as error:
        raise SystemExit(f"Update observation unavailable: {error}")
