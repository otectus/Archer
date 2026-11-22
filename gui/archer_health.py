"""Passive device outcomes and allowlisted support reports for the CLI and desktop."""
from collections import Counter
import datetime as dt
import fcntl
import hashlib
import getpass
import json
import os
from pathlib import Path
import re
import tempfile

from archer_inventory import read
from archer_readiness import readiness, system_file
from archer_version import VERSION


PACKAGES = ("linux", "linux-lts", "linux-firmware", "pipewire", "pipewire-audio", "wireplumber", "pulseaudio",
            "tlp", "power-profiles-daemon", "tuned", "fwupd", "gtk4", "libadwaita", "python", "systemd",
            "linux-cachyos", "linux-cachyos-lts", "mhwd", "chwd", "linux-firmware-nvidia", "nvidia-utils",
            "libfprint", "fprintd", "libcamera", "modemmanager", "booster", "dracut", "mkinitcpio")
KINDS = {
    "Connectivity": ("net", "ieee80211", "bluetooth", "rfkill", "wwan"),
    "Input": ("input", "iio"), "Camera": ("video4linux", "media"),
    "Graphics and displays": ("drm", "backlight", "typec", "thunderbolt", "pci"),
    "Sound": ("sound",), "Battery": ("power_supply",),
    "Cooling": ("hwmon", "platform-profile"),
    "Storage and security": ("nvme", "block", "mmc", "tpm", "accel"),
}
TEST_HINTS = {
    "Camera": "Preview a selected capture device in the Camera panel.",
    "Sound": "Select a physical microphone and check the input meter in your session.",
    "Connectivity": "Test local link, authentication, address assignment, and DNS separately in your network manager.",
    "Input": "Test typing, pointing, pen pressure, and rotation in your desktop input settings.",
    "Graphics and displays": "Connect the display or dock and rescan. Check the active mode in desktop settings.",
    "Storage and security": "Use the device-specific system utility for media, enrollment, or runtime checks.",
    "Battery": "Compare readings on AC and battery. Firmware may expose telemetry without a charge control.",
    "Cooling": "Review fan and temperature sources in Performance.",
}
CONFIG_HASHES = (
    "etc/archer/settings.json", "etc/archer/battery.json",
    "etc/modprobe.d/linuwu-sense.conf", "etc/tlp.d/01-acer-optimize.conf",
    "etc/gamemode.ini",
)
BOOT_FILES = ("boot/grub/grub.cfg", "boot/loader/loader.conf", "etc/kernel/cmdline")
GOALS = {
    "battery_life": ("Review charge controls", "Review the current power-policy owner",
                     "Use a temporary travel charge only when requested"),
    "cooling": ("Inspect labelled temperature and fan sensors",
                "Restore firmware Auto before any manual test"),
    "gaming": ("Review graphics routing and active outputs",
               "Use an application-scoped performance lease"),
    "conferencing": ("Test a selected microphone", "Preview a selected camera",
                     "Verify the original media route can be restored"),
    "troubleshooting": ("Record a passive baseline", "Run only the affected functional check",
                        "Compare the after snapshot and preview a redacted report"),
}

GOAL_PAGES = {
    "battery_life": ("battery", "session", "battery"),
    "cooling": ("performance", "performance"),
    "gaming": ("display", "session"),
    "conferencing": ("microphone", "camera", "microphone"),
    "troubleshooting": ("checks", "session", "checks"),
}


def package_versions(root):
    # pacman's local database is available offline and needs no lock for read-only discovery.
    folder = root / "var/lib/pacman/local"
    versions = {}
    for entry in folder.glob("*"):
        match = re.fullmatch(r"(.+)-([^-]+-[^-]+)", entry.name)
        if match and match[1] in PACKAGES:
            versions[match[1]] = match[2]
    return versions


def boot_targets(root):
    """Return local, non-sensitive boot evidence without exporting arguments or paths."""
    fixed = {}
    for relative in BOOT_FILES:
        content = system_file(root, relative)
        if content:
            fixed[relative] = hashlib.sha256(content.encode()).hexdigest()
    entries = []
    for path in sorted((root / "boot/loader/entries").glob("*.conf")):
        try:
            if not path.resolve().is_relative_to(root):
                continue
            with path.open("rb") as entry:
                content = entry.read(128 * 1024)
        except OSError:
            continue
        entries.append(hashlib.sha256(content).hexdigest())
    return {"configuration_hashes": fixed, "loader_entry_hashes": sorted(entries),
            "unified_kernel_image_count": len(list((root / "boot/EFI/Linux").glob("*.efi")))}


def _finding(symptom, device, observed, guidance, *, state="observed",
             confidence="discovered", cause=None, changes="None (read-only)",
             interruption="None", verification="Run the listed functional check",
             recovery="No change was made", support=None):
    return {"state": state, "symptom": symptom, "device": device,
            "observed": observed, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "confidence": confidence, "likely_cause": cause,
            "proposed_action": guidance, "changes": changes,
            "interruption": interruption, "verification": verification,
            "recovery": recovery, "support": support}


def health(inventory, readiness_data=None, firmware=None):
    groups = {}
    for name, kinds in KINDS.items():
        devices = [d for d in inventory["devices"] if d["kind"] in kinds]
        if name == "Graphics and displays":
            devices = [d for d in devices if any((
                d["kind"] != "pci",
                d.get("attributes", {}).get("class", "").startswith("0x03")))]
        summary = Counter(d["kind"] for d in devices)
        guidance = TEST_HINTS[name]
        finding = (_finding("Interfaces discovered", f"{len(devices)} local device(s)",
                            ", ".join(f"{key} × {value}" for key, value in sorted(summary.items())),
                            guidance) if devices else
                   _finding("No interface detected", name, "No matching kernel interface was present",
                            guidance, state="optional_unavailable", confidence="discovered",
                            support="This may be expected when the hardware is absent or disabled."))
        groups[name] = {"counts": dict(sorted(summary.items())), "validation": "discovered",
                        "functional_test": "untested", "guidance": guidance,
                        "status": finding["state"], "findings": [finding]}
    sleep = inventory.get("sleep", {})
    sleep_available = "mem" in (sleep.get("state") or "").split()
    groups["Sleep"] = {"counts": {}, "validation": "discovered",
                       "functional_test": "untested",
                       "guidance": "Run the user-initiated sleep check after saving work.",
                       "status": "observed" if sleep_available else "unavailable",
                       "findings": [_finding(
                           "Sleep interface " + ("detected" if sleep_available else "not detected"),
                           "system sleep", str(sleep.get("state") or "unavailable"),
                           "Run the user-initiated sleep check after saving work.",
                           state="observed" if sleep_available else "unavailable")]}
    firmware = firmware or {}
    fw_status = firmware.get("status", "not_checked")
    groups["Firmware"] = {"counts": {}, "validation": "discovered",
                          "functional_test": "passive",
                          "guidance": "Open Firmware for device-specific local fwupd results.",
                          "status": fw_status, "findings": [_finding(
                              "Firmware status: " + fw_status.replace("_", " "),
                              "fwupd", firmware.get("error") or ("No firmware check supplied" if fw_status == "not_checked" else f"{len(firmware.get('devices', []))} device result(s) returned"),
                              "Open Firmware for device-specific local fwupd results.",
                              state="attention" if fw_status in ("blocked", "error") else "observed")]}
    checked = readiness_data is not None
    readiness_data = readiness_data or {}
    attention = any((readiness_data.get("pacman_locked"), readiness_data.get("advisories"),
                     readiness_data.get("advisory_source") == "external_catalog_rejected_using_bundled",
                     readiness_data.get("transactions", {}).get("state") in ("failed", "recovery-required", "pending-restart"),
                     any(not row.get("headers_match") for row in readiness_data.get("kernels", []))))
    groups["Update readiness"] = {"counts": {}, "validation": "discovered",
                                  "functional_test": "passive",
                                  "guidance": "Review kernels, headers, signing, boot and transactions before updating.",
                                  "status": "attention" if attention else "observed" if checked else "not_checked",
                                  "findings": [_finding(
                                      "Update preflight needs review" if attention else "Update preflight collected" if checked else "Update preflight not collected",
                                      "installed system", "Read-only readiness evidence; not an update guarantee",
                                      "Review kernels, headers, signing, boot and transactions before updating.",
                                      state="attention" if attention else "observed")]}
    return groups


def snapshot(inventory, root, capabilities=None, firmware=None):
    packages = package_versions(root)
    ready = readiness(root, inventory, packages)
    hashes = {}
    for relative in CONFIG_HASHES:
        content = system_file(root, relative)
        if content:
            hashes[relative] = hashlib.sha256(content.encode()).hexdigest()
    capability_rows = []
    source = capabilities.get("capabilities", []) if isinstance(capabilities, dict) else (capabilities or [])
    for item in source:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            capability_rows.append({key: item.get(key) for key in
                                    ("id", "device_key", "backend", "availability", "readable", "writable")})
    return {"schema_version": 1, "archer_version": VERSION,
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "environment": inventory["environment"], "packages": packages,
            "devices": sorted([{"key": d["key"], "driver": d["driver"], "interfaces": d["interfaces"]}
                               for d in inventory["devices"] if d["identity_unique"]], key=lambda d: d["key"]),
            "capabilities": sorted(capability_rows, key=lambda item: item["id"]),
            "config_hashes": hashes,
            "boot_targets": boot_targets(root),
            "readiness": ready,
            "health": health(inventory, ready, firmware)}


def compare(previous, current):
    if previous.get("schema_version") != 1 or not isinstance(previous.get("devices"), list):
        raise ValueError("Unsupported snapshot schema")

    def by_key(value):
        return {d["key"]: (d["driver"], d["interfaces"]) for d in value["devices"]}

    old, new = by_key(previous), by_key(current)
    old_caps = {item["id"]: item for item in previous.get("capabilities", [])}
    new_caps = {item["id"]: item for item in current.get("capabilities", [])}
    package_keys = previous["packages"].keys() | current["packages"].keys()
    config_keys = previous.get("config_hashes", {}).keys() | current.get("config_hashes", {}).keys()
    changes = {
        "added": sorted(new.keys() - old.keys()),
        "lost": sorted(old.keys() - new.keys()),
        "binding_or_interface_changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
        "package_changes": {
            k: [previous["packages"].get(k), current["packages"].get(k)]
            for k in sorted(package_keys)
            if previous["packages"].get(k) != current["packages"].get(k)},
        "lost_capabilities": sorted(old_caps.keys() - new_caps.keys()),
        "capability_changes": sorted(key for key in old_caps.keys() & new_caps.keys()
                                     if old_caps[key] != new_caps[key]),
        "config_changes": sorted(
            key for key in config_keys
            if previous.get("config_hashes", {}).get(key) != current.get("config_hashes", {}).get(key)),
        "boot_targets_changed": previous.get("boot_targets", {}) != current.get("boot_targets", {}),
        "correlation_notice": "Changes occurred between snapshots; timing alone does not prove causation.",
    }
    return changes


class HealthHistory:
    """Bounded, local-only before/after evidence. No upload method exists."""

    def __init__(self, path=None, limit=20):
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.path = Path(path or state_home / "archer/health-history.json")
        self.limit = max(2, min(int(limit), 100))

    def load(self):
        if self.path.is_symlink():
            raise ValueError("Health history must not be a symlink")
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Health history is too large")
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get("schema_version") != 1 or \
                not isinstance(data.get("snapshots"), list):
            raise ValueError("Invalid health history")
        if not all(isinstance(item, dict) and item.get("schema_version") == 1
                   for item in data["snapshots"]):
            raise ValueError("Invalid health history")
        return data["snapshots"][-self.limit:]

    def record(self, value):
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Invalid health snapshot")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._append(value)

    def _append(self, value):
        existing = self.load()
        comparison = compare(existing[-1], value) if existing else None
        rows = (existing + [value])[-self.limit:]
        fd, temporary = tempfile.mkstemp(prefix=".health-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"schema_version": 1, "snapshots": rows}, stream,
                          sort_keys=True)
                stream.flush()
                if stream.tell() > 10 * 1024 * 1024:
                    raise ValueError("Health history exceeds size limit")
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return comparison


def guided_plan(goal, groups):
    if goal not in GOALS:
        raise ValueError("Unknown onboarding goal")
    unavailable = sorted(name for name, group in groups.items()
                         if group.get("status") in ("unavailable", "optional_unavailable"))
    return {"schema_version": 1, "goal": goal, "read_only_scan": True,
            "steps": [{"title": title, "page": page, "authorization": "review before any change"}
                      for title, page in zip(GOALS[goal], GOAL_PAGES[goal], strict=True)],
            "unavailable_outcomes": unavailable,
            "firmware_updates_included": False,
            "notice": "This review does not authorize firmware updates, driver replacement, or package removal."}


def public_text(text, private):
    if not isinstance(text, str):
        return text
    for value in private:
        if value:
            text = re.sub(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", "[redacted]", text, flags=re.I)
    for pattern in (r"(?i)[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}",
                    r"(?i)(?:[a-f0-9]{2}:){5}[a-f0-9]{2}", r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                    r"/(?:home|Users)/[^/\s]+", r"(?i)\b(?:token|password|ssid|serial|key)=[^\s]+"):
        text = re.sub(pattern, "[redacted]", text)
    return "".join(ch for ch in text[:128] if ch.isprintable())


def report(inventory, root):
    # Device aliases are local to this report. Never export paths, modaliases,
    # bus addresses, device node names, raw D-Bus replies or arbitrary logs.
    devices = []
    for device in inventory["devices"]:
        if device["kind"] in ("wakeup", "acpi", "wmi", "platform"):
            continue
        attrs = {k: v for k, v in device["attributes"].items()
                 if k in ("vendor", "device", "subsystem_vendor", "subsystem_device", "class", "idVendor",
                          "idProduct", "id/vendor", "id/product", "id/bustype") and re.fullmatch(r"(0x)?[a-fA-F0-9]{1,8}", v)}
        driver = device["driver"]
        devices.append({"device": len(devices) + 1, "kind": device["kind"], "ids": attrs,
                        "driver": driver if driver and re.fullmatch(r"[a-zA-Z0-9_.-]{1,60}", driver) else None,
                        "interfaces": device["interfaces"]})
    packages = package_versions(root)
    private = [system_file(root, "etc/hostname").strip(), system_file(root, "etc/machine-id").strip()]
    try:
        private.append(getpass.getuser())
    except (KeyError, OSError):
        pass
    model = {k: public_text(v, private) for k, v in inventory["dmi"].items()}
    environment = dict(inventory["environment"])
    environment["distribution"] = {k: public_text(v, private) for k, v in environment["distribution"].items()}
    environment["kernel"] = public_text(environment["kernel"], private)
    ready = readiness(root, inventory, packages)
    return {"report_version": 1, "archer_version": VERSION,
            "build_id": build_id(Path(__file__).parent), "environment": environment,
            "model": model, "readiness": ready, "packages": packages, "devices": devices,
            "sleep": inventory["sleep"], "health": health(inventory, ready)}


def sleep_sample(sysfs):
    batteries = {}
    for p in sorted((sysfs / "class/power_supply").glob("*")):
        if not p.resolve().is_relative_to(sysfs.resolve()):
            continue
        if read(p / "type") != "Battery":
            continue
        for attr in ("energy_now", "charge_now", "capacity"):
            raw = read(p / attr)
            if raw and raw.isdecimal():
                batteries[p.name] = {"value": int(raw), "unit": {"energy_now": "µWh", "charge_now": "µAh",
                                                                 "capacity": "%"}[attr]}
                break
    return {"sampled_at": dt.datetime.now(dt.timezone.utc).isoformat(), "batteries": batteries,
            "wake_irq": read(sysfs / "power/pm_wakeup_irq")}


def sleep_comparison(before, after):
    elapsed = (dt.datetime.fromisoformat(after["sampled_at"]) - dt.datetime.fromisoformat(before["sampled_at"])).total_seconds()
    return {**after, "elapsed_seconds": elapsed, "changes": {
        key: {"delta": value["value"] - before["batteries"][key]["value"], "unit": value["unit"]}
        for key, value in after["batteries"].items()
        if before["batteries"].get(key, {}).get("unit") == value["unit"]}}


def build_id(directory):
    digest = hashlib.sha256()
    for source in sorted(directory.glob("archer_*.py")):
        digest.update(source.name.encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()
