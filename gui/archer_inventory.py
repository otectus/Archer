"""Passive Linux inventory. Only this local view includes kernel device names."""
import argparse
from collections import Counter
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys


BUSES = ("pci", "usb", "hid", "acpi", "i2c", "spi", "platform", "thunderbolt", "wmi", "mmc")
CLASSES = ("power_supply", "hwmon", "drm", "net", "ieee80211", "bluetooth", "rfkill", "wwan",
           "input", "video4linux", "media", "sound", "backlight", "leds", "typec", "platform-profile",
           "iio", "block", "nvme", "tpm", "accel", "scsi_host", "wakeup")
ATTRIBUTES = {
    "pci": ("vendor", "device", "subsystem_vendor", "subsystem_device", "class", "boot_vga"),
    "usb": ("idVendor", "idProduct", "bDeviceClass", "bInterfaceClass", "bInterfaceSubClass"),
    "input": ("id/bustype", "id/vendor", "id/product", "capabilities/sw", "capabilities/abs"),
    "power_supply": ("type", "scope"), "hwmon": ("name",), "rfkill": ("type",),
    "platform-profile": ("name", "choices"), "video4linux": ("index",),
    "drm": ("status",), "block": ("removable", "queue/rotational"), "typec": ("port_type",),
}
INTERFACES = {
    "hwmon": r"(fan\d+_(input|label)|temp\d+_(input|label)|pwm\d+(_enable)?)",
    "power_supply": r"(charge|energy)_(now|full|full_design|control_(start|end)_threshold)|capacity|status|power_now|current_now|voltage_now|online|cycle_count",
    "leds": r"(max_)?brightness|multi_(index|intensity)",
    "backlight": r"(max_|actual_)?brightness",
    "video4linux": r"index|dev", "drm": r"status|modes|enabled", "rfkill": r"hard|soft",
    "platform-profile": r"profile|choices", "nvme": r"state|firmware_rev", "net": r"wireless|operstate|carrier",
}
KNOWN_DISTROS = ("arch", "cachyos", "garuda", "endeavouros", "manjaro", "artix")


_SCAN_ROOT = ContextVar("archer_scan_root", default=None)


def read(path):
    try:
        root = _SCAN_ROOT.get()
        if root is not None and root != Path("/") and not path.resolve().is_relative_to(root):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            return os.read(fd, 1024).decode("utf-8", "replace").strip()
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def distribution(root):
    fields = {}
    for relative in ("etc/os-release", "usr/lib/os-release"):
        try:
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                continue
            with path.open(errors="replace") as file:
                lines = file.read(16384).splitlines()
        except OSError:
            continue
        for line in lines:
            key, sep, value = line.partition("=")
            if sep and key in ("ID", "ID_LIKE", "NAME", "VERSION_ID"):
                fields[key] = value.strip().strip('"').strip("'")
        if fields.get("ID"):
            break
    distro = fields.get("ID", "unknown")
    arch = distro in KNOWN_DISTROS or "arch" in fields.get("ID_LIKE", "").split()
    return {"id": distro, "name": fields.get("NAME", distro), "version": fields.get("VERSION_ID"),
            "arch_family": arch, "adapter": distro if distro in KNOWN_DISTROS else "generic",
            "hardware_manager": {"cachyos": "chwd", "manjaro": "mhwd", "garuda": "distribution"}.get(distro)}


def device_info(kind, node, sysfs):
    physical = node.resolve(strict=True)
    if not physical.is_relative_to(sysfs):
        return None
    target = (node / "device").resolve() if (node / "device").is_symlink() else physical
    if not target.is_relative_to(sysfs):
        return None
    driver = None
    # A PCI root port or USB hub driver does not bind its unclaimed child.
    parents = (physical,) if kind in BUSES else (physical, target, *target.parents)
    for parent in parents:
        if not parent.is_relative_to(sysfs):
            break
        if parent != target and parent != physical and (parent / "subsystem").is_symlink() and (
                (parent / "subsystem").readlink().name in ("pci", "usb")):
            break
        if (parent / "driver").is_symlink():
            driver = (parent / "driver").readlink().name
            break
    attrs = {}
    suspended = kind == "drm" and any(read(parent / "power/runtime_status") == "suspended"
                                      for parent in physical.parents if parent.is_relative_to(sysfs))
    for name in (() if suspended else ATTRIBUTES.get(kind, ())):
        value = read(node / name)
        if value is not None:
            attrs[name] = value
    pattern = INTERFACES.get(kind)
    interfaces = sorted(p.name for p in node.iterdir() if re.fullmatch(pattern, p.name)) if pattern else []
    # Topology is local. Strip allocator numbers when a physical parent identifies the device.
    location = str(physical.relative_to(sysfs))
    location = re.sub(r"(?<=/)card\d+(?=-|/|$)", "card", location)
    location = re.sub(r"/(?:hwmon|input|event|video|media|renderD|hci|phy|rfkill|nvme|accel|wakeup)\d+(?=/|$)",
                      lambda m: re.sub(r"\d+$", "", m[0]), location)
    location = re.sub(r"(/net)/[^/]+$", r"\1", location)
    identity = [kind, location, attrs.get("vendor"), attrs.get("device"), attrs.get("idVendor"),
                attrs.get("idProduct"), attrs.get("name"), attrs.get("index")]
    key = kind + "-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    return {"key": key, "kind": kind, "node": node.name, "topology": str(physical.relative_to(sysfs)),
            "driver": driver, "modalias": read(node / "modalias") if kind in BUSES else None, "attributes": attrs, "interfaces": interfaces}


def gpu_roles(devices, sysfs):
    """Prefer an explicit VGA-switcheroo role or a CPU/GPU topology node."""
    roles = {}
    status = read(sysfs / "kernel/debug/vgaswitcheroo/switch") or ""
    for line in status.splitlines():
        row = re.fullmatch(r"\d+:(IGD|DIS):[^:]*:[^:]*:([0-9a-fA-F:.]+)", line)
        if row:
            roles[row[2].lower()] = "integrated" if row[1] == "IGD" else "discrete"
    for node in (sysfs / "class/kfd/kfd/topology/nodes").glob("*/properties"):
        props = dict(line.split() for line in (read(node) or "").splitlines() if len(line.split()) == 2)
        try:
            if int(props["cpu_cores_count"]) and int(props["simd_count"]) and int(props["vendor_id"]) == 0x1002:
                location = int(props["location_id"])
                address = f'{int(props["domain"]):04x}:{location >> 8:02x}:{(location >> 3) & 31:02x}.{location & 7:x}'
                roles[address] = "integrated"
        except (KeyError, ValueError):
            continue
    for device in devices:
        attrs = device["attributes"]
        if device["kind"] != "pci" or not attrs.get("class", "").startswith("0x03"):
            continue
        node = device["node"]
        role = roles.get(node.lower(), "unknown")
        if (role == "unknown" and attrs.get("vendor") == "0x8086" and device["driver"] in ("i915", "xe")
                and re.fullmatch(r"[0-9a-f]{4}:00:02\.0", node)
                and re.search(r"/pci[0-9a-f]{4}:00/" + re.escape(node) + "$", device["topology"])):
            role = "integrated"
        attrs["gpu_role"] = role


def collect_inventory(root=Path("/"), sysfs=None):
    root = Path(root).resolve()
    token = _SCAN_ROOT.set(root)
    try:
        return _scan(root, sysfs)
    finally:
        _SCAN_ROOT.reset(token)


def _scan(root, sysfs):
    sysfs = Path(sysfs or root / "sys").resolve()
    if not sysfs.is_relative_to(root) or not sysfs.is_dir():
        raise ValueError("Mount the target sysfs tree before scanning")
    devices, missing = [], []
    for prefix, groups in (("bus", BUSES), ("class", CLASSES)):
        for kind in groups:
            directory = sysfs / prefix / kind
            if prefix == "bus":
                directory /= "devices"
            try:
                entries = sorted(directory.iterdir())
            except OSError:
                missing.append(kind)
                continue
            for node in entries:
                if kind == "input" and not re.fullmatch(r"input\d+", node.name):
                    continue
                if kind == "block" and node.name.startswith(("loop", "ram")):
                    continue
                if not node.is_dir():
                    continue
                try:
                    device = device_info(kind, node, sysfs)
                except (OSError, RuntimeError):
                    continue  # disappeared during enumeration
                if device:
                    devices.append(device)
    gpu_roles(devices, sysfs)
    counts = Counter(device["key"] for device in devices)
    for device in devices:
        device["identity_unique"] = counts[device["key"]] == 1
    dmi = {k: read(sysfs / "class/dmi/id" / k) for k in
           ("sys_vendor", "product_name", "board_vendor", "board_name", "bios_version", "bios_date")}
    distro = distribution(root)
    live = root == Path("/") and sysfs == Path("/sys")
    environment = {"distribution": distro, "architecture": platform.machine() if live else None,
                   "kernel": platform.release() if live else None,
                   "init": read(root / "proc/1/comm") if live else None}
    if (root / "run/openrc").is_dir() and environment["init"] in ("init", "openrc-init", None):
        environment["init"] = "openrc"
    # Include interface and binding changes. Ignore allocation names and fluctuating telemetry.
    fingerprint = sorted((d["key"], d["driver"] or "", d["interfaces"], d["attributes"].get("choices", ""),
                          d["attributes"].get("status", ""), d["attributes"].get("gpu_role", "")) for d in devices)
    generation = hashlib.sha256(json.dumps([dmi, environment, fingerprint], sort_keys=True).encode()).hexdigest()
    return {"schema_version": 1, "generation": generation, "environment": environment, "dmi": dmi,
            "devices": devices, "unavailable_groups": missing,
            "sleep": {k: read(sysfs / "power" / k) for k in ("state", "mem_sleep", "disk")}}


def installer_values(data):
    devices = data["devices"]
    dmi = data["dmi"]
    gpus = [d["attributes"] for d in devices if d["kind"] == "pci" and d["attributes"].get("class", "").startswith("0x03")]
    nvidia = [g for g in gpus if g.get("vendor") == "0x10de"]
    radios = [d for d in devices if d["kind"] == "pci" and d["attributes"].get("class", "").startswith("0x0280")]
    vendors = {"0x14c3": "mediatek", "0x8086": "intel", "0x10ec": "realtek", "0x168c": "qualcomm", "0x17cb": "qualcomm"}
    distro = data["environment"]["distribution"]
    return [dmi["product_name"], dmi["board_name"], dmi["bios_version"], dmi["sys_vendor"], int(bool(nvidia)),
            " ".join("NVIDIA " + g.get("device", "") for g in nvidia),
            int(any(g.get("vendor") == "0x8086" for g in gpus)), int(any(g.get("vendor") == "0x1002" for g in gpus)),
            ", ".join(f"{d['attributes'].get('vendor')}/{d['attributes'].get('device')} ({d['driver'] or 'unbound'})" for d in radios),
            " ".join(sorted({vendors.get(d["attributes"].get("vendor"), "unknown") for d in radios})),
            int(any(d["attributes"].get("type") == "Battery" for d in devices if d["kind"] == "power_supply")),
            int(any(d["kind"] == "input" and d["attributes"].get("id/bustype") == "0018" for d in devices)),
            distro["id"], distro["name"], "unsupported-init" if data["environment"]["init"] not in ("systemd", "openrc")
            else "arch" if distro["arch_family"] else "unknown"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", choices=("inventory", "installer", "generation"), default="inventory", nargs="?")
    parser.add_argument("--root", type=Path, default=Path("/"))
    args = parser.parse_args()
    data = collect_inventory(args.root)
    if args.view == "installer":
        for value in installer_values(data):
            sys.stdout.buffer.write(str(value if value is not None else "Unknown").encode() + b"\0")
    elif args.view == "generation":
        print(data["generation"])
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
