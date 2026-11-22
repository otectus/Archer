#!/usr/bin/env python3
"""Install or retire only reviewed GUI payloads whose ownership still matches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

REPO = Path(__file__).resolve().parents[1]
STATE = "var/lib/archer/gui-files.json"
DESKTOP = "io.github.archer.desktop"
LAUNCH = b'#!/bin/bash\nexec python3 /opt/archer/archer_gui.py "$@"\n'


def files(repo):
    gui = repo / "gui"
    mapping = {f"usr/share/applications/{DESKTOP}": gui / DESKTOP, f"etc/xdg/autostart/{DESKTOP}": gui / DESKTOP,
               "etc/systemd/system/archer-daemon.service": gui / "archer-daemon.service",
               "etc/dbus-1/system.d/io.otectus.Archer1.conf": gui / "io.otectus.Archer1.conf",
               "usr/share/polkit-1/actions/io.otectus.Archer1.policy": gui / "io.otectus.Archer1.policy",
               "usr/share/icons/hicolor/scalable/apps/io.github.archer.svg": gui / "assets/archer.svg",
               "usr/local/bin/archer-gui": LAUNCH, "opt/archer/archer-boot.py": repo / "scripts/archer-boot.py"}
    for cmd, source in (("archer", "archer_cli.py"), ("archer-session", "archer_session.py")):
        mapping[f"usr/local/bin/{cmd}"] = f'#!/bin/sh\nexec /usr/bin/python3 /opt/archer/{source} "$@"\n'.encode()
    for path in gui.rglob("*"):
        if path.is_symlink() or "__pycache__" in path.parts or not path.is_file():
            continue
        if path.name in ("VERSION", DESKTOP) or path.suffix in (".py", ".css", ".png", ".svg", ".json"):
            mapping["opt/archer/" + str(path.relative_to(gui))] = path
    return {name: value if isinstance(value, bytes) else value.read_bytes() for name, value in mapping.items()}


def atomic(path, content, mode):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(fd, mode)
            stream.write(content)
            stream.flush()
            os.fsync(fd)
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def accepted_hashes(manifest, target):
    """Return the reviewed hashes for one path, accepting the legacy scalar form."""
    value = manifest.get(target)
    if value is None:
        return set()
    values = [value] if isinstance(value, str) else value
    if (not isinstance(values, list) or not values
            or any(not isinstance(item, str) or len(item) != 64
                   or any(char not in "0123456789abcdef" for char in item)
                   for item in values)):
        raise ValueError(f"Invalid reviewed hash entry for {target}")
    return set(values)


def deploy(action, root=Path("/"), repo=REPO):
    state = root / STATE
    if state.resolve() != state:
        raise ValueError("Refusing symlinked deployment state")
    saved = json.loads(state.read_text()) if state.exists() else {"files": {}, "pending": {}}
    legacy = json.loads((repo / "scripts/gui-2.0.1-hashes.json").read_text())
    payloads = files(repo)
    manifest = root / "var/lib/archer/install-manifest.json"
    old_gui = manifest.is_file() and "gui" in json.loads(manifest.read_text()).get("modules_installed", [])
    old = {}
    targets = set(payloads) | set(legacy) | set(saved["files"]) | set(saved["pending"])
    for target in targets:
        path = root / target
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError(f"Refusing redirected GUI destination {target}")
        if path.exists():
            reviewed = accepted_hashes(legacy, target) if old_gui else set()
            known = reviewed | {saved["files"].get(target), saved["pending"].get(target)}
            actual = digest(path.read_bytes())
            if actual not in known:
                raise ValueError(f"Retaining locally edited or unowned {path}. Review it before continuing.")
            if actual in reviewed:
                old[target] = actual
    if action == "check":
        return
    if action == "install":
        pending = {target: digest(data) for target, data in payloads.items()}
        atomic(state, json.dumps({"files": {**old, **saved["files"]}, "pending": pending}).encode(), 0o644)
        for target, data in payloads.items():
            executable = target.startswith("usr/local/bin/") or target in ("opt/archer/archer_daemon.py", "opt/archer/archer_gui.py")
            atomic(root / target, data, 0o755 if executable else 0o644)
        for stale in targets - payloads.keys():
            (root / stale).unlink(missing_ok=True)
        atomic(state, json.dumps({"files": pending, "pending": {}}).encode(), 0o644)
    else:
        for target in targets:
            (root / target).unlink(missing_ok=True)
        state.unlink(missing_ok=True)
        # Keep local additions and Python caches. Empty application directories are harmless.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "install", "remove"))
    try:
        os.umask(0o022)
        deploy(parser.parse_args().action)
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
