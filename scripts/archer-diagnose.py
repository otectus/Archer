#!/usr/bin/env python3
"""Read-only Acer support report. No serials, UUIDs, MACs, users, or full logs."""
import argparse
import json
import platform
import subprocess
from pathlib import Path

SETTINGS_FILE = Path("/etc/archer/settings.json")
# Only these saved preferences are reported; other user settings never are.
BINARY_KEYS = ("lcd_override", "boot_animation_sound", "backlight_timeout")


def read(path):
    try:
        return path.read_text().strip()
    except PermissionError:
        return "<permission denied>"
    except OSError:
        return "<unavailable>"


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return result.stdout.strip() if result.returncode == 0 else "<unavailable>"
    except (OSError, subprocess.TimeoutExpired):
        return "<unavailable>"


def saved_preferences(path):
    """The saved LCD override, boot sound and backlight timeout values, bounded."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return "<absent>"
    except PermissionError:
        return "<permission denied>"
    except (OSError, ValueError) as error:
        return f"<unreadable: {type(error).__name__}>"
    if not isinstance(data, dict):
        return "<unreadable: not an object>"
    report = {}
    for key in BINARY_KEYS:
        value = data.get(key, "<absent>")
        if not (value is None or isinstance(value, (bool, int)) or (isinstance(value, str) and len(value) <= 32)):
            value = f"<invalid {type(value).__name__}>"
        report[key] = value
    return report


def daemon_states():
    """Read-only GetAllSettings, reduced to the binary control states. Nonfatal when unavailable."""
    output = command(["busctl", "--system", "--json=short", "call", "io.otectus.Archer1", "/io/otectus/Archer1",
                      "io.otectus.Archer1", "GetAllSettings"])
    try:
        data = json.loads(json.loads(output)["data"][0])["data"]
        return {key: {"value": data.get(key), "state": data.get(f"{key}_state", "<not reported by this service>")}
                for key in BINARY_KEYS}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return "<unavailable>"


def collect(root=Path("/sys"), commands=True, settings=None):
    dmi = root / "class/dmi/id"
    report = {
        "dmi": {key: read(dmi / key) for key in
                ("sys_vendor", "product_name", "board_vendor", "board_name", "bios_vendor", "bios_version", "bios_date")},
        "kernel": platform.release(),
        "modules": {}, "wmi_guids": [], "sense": {}, "hwmon": [], "profile_providers": [],
        "platform_profile": read(root / "firmware/acpi/platform_profile"),
        "platform_profile_choices": read(root / "firmware/acpi/platform_profile_choices"),
    }
    for name in ("acer_wmi", "linuwu_sense", "acer_wmi_battery", "amd_pmf"):
        path = root / "module" / name
        report["modules"][name] = {"loaded": path.is_dir()}
        if path.is_dir():
            report["modules"][name]["parameters"] = {p.name: read(p) for p in (path / "parameters").glob("*")}
    for path in sorted((root / "bus/wmi/devices").glob("*")):
        report["wmi_guids"].append(path.name)
    for path in sorted((root / "class/platform-profile").glob("*")):
        report["profile_providers"].append({"path": str(path), "name": read(path / "name"),
                                            "choices": read(path / "choices"), "profile": read(path / "profile")})
    for base in (root / "module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi", root / "devices/platform/acer-wmi"):
        if not base.is_dir():
            continue
        for group in ("nitro_sense", "predator_sense", "four_zoned_kb"):
            path = base / group
            if path.is_dir():
                entries = {}
                report["sense"][str(path)] = entries
                try:
                    children = sorted(path.iterdir())
                except OSError as error:
                    entries["unavailable"] = str(error)
                    continue
                for node in children:
                    try:
                        entries[node.name] = {"value": read(node), "mode": oct(node.stat().st_mode & 0o777)}
                    except OSError as error:
                        entries[node.name] = {"value": None, "unavailable": str(error)}
    for path in sorted((root / "class/hwmon").glob("hwmon*")):
        fans = list(path.glob("fan*_input"))
        if fans:
            report["hwmon"].append({"path": str(path), "name": read(path / "name"),
                                    "fans": {p.name: read(p) for p in sorted(fans)}})
    if settings is None and commands:
        settings = SETTINGS_FILE
    if settings is not None:
        report["archer"] = {"saved_preferences": saved_preferences(Path(settings))}
    if commands:
        report["archer"]["daemon"] = daemon_states()
        report["dkms"] = command(["dkms", "status", "-m", "linuwu-sense"])
        report["module_file"] = command(["modinfo", "-n", "linuwu_sense"])
        report["module_version"] = command(["modinfo", "-F", "version", "linuwu_sense"])
        report["module_vermagic"] = command(["modinfo", "-F", "vermagic", "linuwu_sense"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysfs-root", type=Path, default=Path("/sys"), help="Mock sysfs tree for tests")
    args = parser.parse_args()
    print(json.dumps(collect(args.sysfs_root, commands=args.sysfs_root == Path("/sys")), indent=2))
