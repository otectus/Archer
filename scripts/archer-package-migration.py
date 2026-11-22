#!/usr/bin/env python3
"""Transfer the legacy GUI launchers and service to Arch package ownership."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path("/")
STATE = Path("var/lib/archer/package-migration")
MANIFEST = Path("var/lib/archer/install-manifest.json")
DESKTOP = "io.github.archer.desktop"
FILES = {"etc/systemd/system/archer-daemon.service": "archer-daemon.service",
         "etc/dbus-1/system.d/io.otectus.Archer1.conf": "io.otectus.Archer1.conf",
         "usr/share/polkit-1/actions/io.otectus.Archer1.policy": "io.otectus.Archer1.policy",
         "usr/share/applications/" + DESKTOP: DESKTOP,
         "etc/xdg/autostart/" + DESKTOP: DESKTOP,
         "usr/share/icons/hicolor/scalable/apps/io.github.archer.svg": "assets/archer.svg",
         "usr/local/bin/archer-gui": None}


def atomic(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(root, relative):
    path = root / relative
    if path.resolve() != path:
        raise ValueError(f"Resolve symlink before transferring {relative}")
    return path


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=60)


def plan(root):
    manifest = json.loads(checked_path(root, MANIFEST).read_text())
    if "gui" not in manifest["modules_installed"]:
        raise ValueError("No installer-managed GUI in the manifest")
    claimed = set(manifest.get("files_created", []))
    state_path = checked_path(root, "var/lib/archer/gui-files.json")
    deployed = json.loads(state_path.read_text()) if state_path.exists() else {}
    if deployed.get("pending"):
        raise ValueError("Complete the interrupted script deployment before adopting packages")
    legacy = json.loads(Path(__file__).with_name("gui-2.0.1-hashes.json").read_text())
    recorded = deployed.get("files", {})
    candidates = set(legacy) | set(recorded) | set(FILES)
    if not claimed.intersection({"/opt/archer"} | {"/" + f for f in candidates}):
        raise ValueError("Missing GUI ownership evidence in the legacy manifest")
    result = {}
    for name in sorted(candidates):
        if not name.startswith("opt/archer/") and name not in FILES and name not in ("usr/local/bin/archer", "usr/local/bin/archer-session"):
            raise ValueError("Unexpected deployment path")
        path = checked_path(root, name)
        if not path.exists():
            continue
        owned = (name in recorded or "/" + name in claimed
                 or (name.startswith("opt/archer/") and "/opt/archer" in claimed))
        if not owned or run("pacman", "-Qo", str(path), check=False).returncode == 0:
            raise ValueError(f"Conflicting ownership for {name}")
        if digest(path) not in (legacy.get(name), recorded.get(name)):
            raise ValueError(f"Locally edited {name}. Review the file before transfer.")
        result[name] = digest(path)
    if state_path.exists():
        result["var/lib/archer/gui-files.json"] = digest(state_path)
    settings = root / "etc/archer/settings.json"
    if settings.exists():
        data = json.loads(checked_path(root, "etc/archer/settings.json").read_text())
        if not isinstance(data, dict) or data.get("schema_version", 1) not in (1, 2):
            raise ValueError("Unsupported settings file; review it before transfer")
    return {"schema": 1, "stage": "planned", "files": result, "manifest_hash": digest(root / MANIFEST),
            "was_enabled": run("systemctl", "is-enabled", "archer-daemon.service", check=False).stdout.strip() == "enabled",
            "was_active": run("systemctl", "is-active", "archer-daemon.service", check=False).stdout.strip() == "active"}


@contextmanager
def lock(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def transfer(action, root=ROOT):
    state = checked_path(root, STATE)
    if action == "plan":
        return plan(root)
    journal = state / "journal.json"
    with lock(state):
        if journal.exists():
            data = json.loads(journal.read_text())
        elif action == "prepare":
            data = plan(root)
            atomic(journal, data)
        else:
            raise ValueError("Prepare the transfer first")
        if data["stage"] == "restored" or data["stage"] == "complete" and action != "restore":
            return data
        if action == "prepare":
            if data["stage"] == "prepared":
                return data
            if digest(root / MANIFEST) != data["manifest_hash"]:
                raise ValueError("Installer manifest changed during transfer")
            if data["stage"] == "planned":
                run("systemctl", "disable", "--now", "archer-daemon.service")
                data["stage"] = "transferring"
                atomic(journal, data)
            for extra in (MANIFEST, Path("etc/archer/settings.json")):
                source = checked_path(root, extra)
                if source.exists() and not (state / extra.name).exists():
                    shutil.copy2(source, state / extra.name)
            for name, expected in data["files"].items():
                source, backup = checked_path(root, name), state / name
                if source.exists():
                    if digest(source) != expected:
                        raise ValueError(f"{name} changed during transfer")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, backup)
                    with backup.open("rb") as stream:
                        os.fsync(stream.fileno())
                    if digest(backup) != expected:
                        raise ValueError(f"Backup verification failed for {name}")
                    source.unlink()
                elif not backup.is_file() or digest(backup) != expected:
                    raise ValueError(f"Missing backup for {name}")
            run("systemctl", "daemon-reload")
            data["stage"] = "prepared"
        elif action == "finish":
            if data["stage"] != "prepared":
                raise ValueError("Complete the prepare step before adopting packages")
            for package in ("archer-core", "archer-daemon", "archer-gui"):
                run("pacman", "-Q", package)
            manifest = json.loads((root / MANIFEST).read_text())
            if "gui" in manifest["modules_installed"]:
                if digest(root / MANIFEST) != data["manifest_hash"]:
                    raise ValueError("Manifest changed. Review before dropping the GUI ownership record.")
                manifest["modules_installed"].remove("gui")
                retired = {"/" + k for k in data["files"]} | {"/opt/archer"}
                manifest["files_created"] = [f for f in manifest["files_created"] if f not in retired]
                atomic(root / MANIFEST, manifest)
            restore_service(data)
            data["stage"] = "complete"
        elif action == "restore":
            if any(run("pacman", "-Q", p, check=False).returncode == 0 for p in ("archer-daemon", "archer-gui")):
                raise ValueError("Package-managed service or GUI is still installed")
            for name, expected in data["files"].items():
                dest, backup = checked_path(root, name), state / name
                if dest.exists() and digest(dest) == expected:
                    continue
                if dest.exists() or not backup.is_file() or digest(backup) != expected:
                    raise ValueError(f"Intervening edit or missing verified backup for {name}")
                shutil.copy2(backup, dest)
            manifest = json.loads((root / MANIFEST).read_text())
            old = json.loads((state / MANIFEST.name).read_text())
            if "gui" not in manifest["modules_installed"]:
                manifest["modules_installed"].append("gui")
                retained = {"/" + name for name in data["files"]} | {"/opt/archer"}
                for value in old.get("files_created", []):
                    if value in retained and value not in manifest["files_created"]:
                        manifest["files_created"].append(value)
                atomic(root / MANIFEST, manifest)
            restore_service(data)
            data["stage"] = "restored"
        atomic(journal, data)
        return data


def restore_service(data):
    run("systemctl", "daemon-reload")
    if data["was_enabled"]:
        run("systemctl", "enable", "archer-daemon.service")
    if data["was_active"]:
        run("systemctl", "start", "archer-daemon.service")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "finish", "restore"))
    args = parser.parse_args()
    if args.action != "plan" and os.geteuid() != 0:
        parser.error("Run the selected ownership transfer as root")
    try:
        print(json.dumps(transfer(args.action), indent=2))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Migration stopped: {error}")
