#!/usr/bin/env python3
"""Read-only Acer support report. No serials, UUIDs, MACs, users, or full logs."""
import argparse
import json
import platform
import subprocess
import re
import sys
from pathlib import Path

if (Path(__file__).resolve().parents[1] / "gui/archer_inventory.py").is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
from archer_health import report as support_report
from archer_inventory import collect_inventory

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
        if value != "<absent>" and not (value is None or type(value) in (bool, int) and value in (0, 1)):
            value = f"<invalid {type(value).__name__}>"
        report[key] = value
    return report


def daemon_states():
    """Read-only GetAllSettings, reduced to the binary control states. Nonfatal when unavailable."""
    output = command(["busctl", "--system", "--json=short", "call", "io.otectus.Archer1", "/io/otectus/Archer1",
                      "io.otectus.Archer1", "GetAllSettings"])
    try:
        data = json.loads(json.loads(output)["data"][0])["data"]
        states = {}
        for key in BINARY_KEYS:
            state = data.get(f"{key}_state")
            public = {}
            if isinstance(state, dict):
                for field in ("effective", "observed", "configured", "requested", "available"):
                    if field in state and (state[field] is None or type(state[field]) is bool):
                        public[field] = state[field]
                for field, choices in {"source": ("hardware", "last_write", "saved_preference", "unknown"),
                                       "readback_status": ("known", "unrecognized", "invalid", "unreadable", "unavailable")}.items():
                    if state.get(field) in choices:
                        public[field] = state[field]
                raw = state.get("raw_readback")
                if isinstance(raw, str) and re.fullmatch(r"-?\d{1,4}", raw):
                    public["raw_readback"] = raw
            value = data.get(key)
            states[key] = {"value": value if type(value) is bool else None, "state": public}
        return states
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
    live = args.sysfs_root.resolve() == Path("/sys")
    target = Path("/") if live else args.sysfs_root.resolve().parent
    data = support_report(collect_inventory(target, args.sysfs_root), target)
    if live:
        data["binary_controls"] = {"preferences": saved_preferences(SETTINGS_FILE), "daemon": daemon_states()}
    print(json.dumps(data, indent=2))
