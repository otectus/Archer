#!/usr/bin/env python3
"""Read-only Acer support report. No serials, UUIDs, MACs, users, or full logs."""
import argparse
import json
import platform
import subprocess
from pathlib import Path


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


def collect(root=Path("/sys"), commands=True):
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
                report["sense"][str(path)] = {
                    p.name: {"value": read(p), "mode": oct(p.stat().st_mode & 0o777)}
                    for p in sorted(path.iterdir()) if p.is_file()
                }
    for path in sorted((root / "class/hwmon").glob("hwmon*")):
        fans = list(path.glob("fan*_input"))
        if fans:
            report["hwmon"].append({"path": str(path), "name": read(path / "name"),
                                    "fans": {p.name: read(p) for p in sorted(fans)}})
    if commands:
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
