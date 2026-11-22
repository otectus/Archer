"""Read-only, device-specific fwupd assessment using local metadata only."""
import hashlib
import json
from pathlib import Path
import re
import time

from archer_runtime import run_command


READ_COMMANDS = frozenset(("get-devices", "get-updates", "get-remotes"))


# Official model routes reviewed on 2026-11-21. Opening a link is user initiated.
VENDOR_ROUTES = {
    "ANV16S-41": "ANV16S-41/downloads",
    "PHN16-71": "Predator%20PHN16-71/downloads",
    "PT516-52S": "Predator_PT516-52s/downloads",
}


def vendor_route(vendor, product):
    if not isinstance(vendor, str) or vendor.strip().casefold() not in ("acer", "acer incorporated"):
        return None
    match = re.fullmatch(r"(?:(?:ACER|PREDATOR|NITRO)\s+)*([A-Z]+[0-9A-Z]+-[0-9A-Z]+)", str(product).strip().upper())
    route = VENDOR_ROUTES.get(match[1]) if match else None
    return "https://www.acer.com/us-en/support/product-support/" + route if route else None


def _text(value, limit=512):
    if not isinstance(value, str):
        return None
    # fwupd summaries may contain simple markup. Keep the GUI/report plain and
    # bounded; never expose locations, checksums or raw daemon replies.
    value = re.sub(r"<[^>]{0,100}>", "", value)
    value = " ".join(value.split())
    return value[:limit] or None


def _device_key(device_id):
    if not isinstance(device_id, str) or not device_id:
        return "fwupd-unknown"
    return "fwupd-" + hashlib.sha256(device_id.encode()).hexdigest()[:20]


def _offline(message):
    return bool(re.search(r"(?i)offline|network.*unreachable|name.*resolution|timed out|timeout",
                          message or ""))


def firmware_status(run=run_command, now=None, *, local_ids=False):
    now = time.time() if now is None else now

    def query(command, key, empty_ok=False):
        if command not in READ_COMMANDS:
            raise ValueError("Unsupported fwupd read operation")
        result = run(["/usr/bin/fwupdmgr", command, "--json", "--no-unreported-check"], timeout=10)
        if result.timed_out or result.returncode not in (0, 2):
            message = _text(result.stderr or result.stdout, 256) or f"{command} failed"
            return None, {"status": "offline" if _offline(message) else "error",
                          "error": message, "devices": [], "updates": []}
        try:
            payload = json.loads(result.stdout or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid {command} response") from error
        if result.returncode == 2 and empty_ok and payload == {}:
            return [], None
        rows = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {command} response")
        return rows, None

    devices, failure = query("get-devices", "Devices", True)
    if failure:
        return failure
    if not devices:
        return {"status": "no_devices", "devices": [], "updates": []}
    releases, failure = query("get-updates", "Devices", True)
    if failure:
        return failure
    remotes, failure = query("get-remotes", "Remotes")
    if failure:
        return failure
    downloads = [r for r in remotes if r.get("Kind") == "download" and r.get("Enabled") is True]
    freshness = "unknown"
    if downloads and all(type(r.get("Mtime")) is int and type(r.get("RefreshInterval")) is int
                         and r["RefreshInterval"] > 0 for r in downloads):
        freshness = ("fresh" if all(0 <= now - r["Mtime"] <= r["RefreshInterval"] for r in downloads)
                     else "stale")
    by_id = {r["DeviceId"]: r for r in releases
             if isinstance(r.get("DeviceId"), str)}
    rows, updates = [], []
    for device in devices:
        flags = device.get("Flags", [])
        if not isinstance(flags, list):
            raise ValueError("Invalid firmware device flags")
        device_id = device.get("DeviceId")
        row = {
            "device_key": _device_key(device_id),
            "name": _text(device.get("Name"), 120) or "Device",
            "vendor": _text(device.get("Vendor"), 120),
            "version": _text(device.get("Version"), 80),
            "status": "unsupported",
            "reboot": "needs-reboot" in flags,
            "ac_required": "require-ac" in flags,
            "releases": [],
            "rollback": "Firmware rollback is device-specific and is not guaranteed.",
        }
        if local_ids and isinstance(device_id, str) and re.fullmatch(r"[0-9a-fA-F]{40}", device_id):
            row["local_id"] = device_id
        update = by_id.get(device.get("DeviceId"))
        if update:
            available = update.get("Releases")
            if not isinstance(available, list) or not available or not all(isinstance(r, dict) for r in available):
                raise ValueError("Invalid firmware releases")
            for release in available:
                release_flags = release.get("Flags", [])
                if not isinstance(release_flags, list):
                    raise ValueError("Invalid firmware release flags")
                details = {
                    "version": _text(release.get("Version"), 80),
                    "summary": _text(release.get("Summary"), 512),
                    "description": _text(release.get("Description"), 2000),
                    "urgency": _text(release.get("Urgency"), 40),
                    "install_duration": release.get("InstallDuration")
                    if type(release.get("InstallDuration")) is int else None,
                    "reboot": row["reboot"] or "needs-reboot" in release_flags,
                    "ac_required": row["ac_required"],
                    "rollback": row["rollback"],
                }
                row["releases"].append({key: value for key, value in details.items()
                                        if value is not None})
            updates.append({"Name": row["name"], "device_key": row["device_key"],
                            "Releases": [{key: value for key, value in {
                                "Version": release.get("version"),
                                "Summary": release.get("summary"),
                                "Urgency": release.get("urgency"),
                            }.items() if value is not None}
                                for release in row["releases"]]})
            row["status"] = "updates"
        elif "supported" in flags:
            row["status"] = "current" if freshness == "fresh" else "metadata_" + freshness
        problems = device.get("Problems")
        if problems and not isinstance(problems, (str, list)):
            raise ValueError("Invalid firmware device problems")
        if problems or device.get("UpdateError"):
            row["status"] = "blocked"
            if isinstance(problems, list):
                problems = ", ".join(_text(problem, 80) or "unknown" for problem in problems)
            row["reason"] = _text(device.get("UpdateError") or problems, 256)
        rows.append(row)
    states = {row["status"] for row in rows}
    overall = next(state for state in ("blocked", "updates", "metadata_stale", "metadata_unknown", "current",
                                       "unsupported") if state in states)
    return {"status": overall, "devices": rows, "updates": updates, "metadata": freshness}


def reviewed_release(status, device_key, version):
    if status.get("metadata") != "fresh":
        raise ValueError("Refresh firmware metadata with fwupd before reviewing an installation")
    devices = [d for d in status["devices"] if d["device_key"] == device_key]
    if len(devices) != 1 or devices[0]["status"] != "updates" or not devices[0].get("local_id"):
        raise ValueError("This device has no uniquely identified, applicable update")
    device = devices[0]
    releases = [r for r in device["releases"] if r.get("version") == version]
    if len(releases) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,79}", version):
        raise ValueError("Choose one version explicitly offered for this device")
    return {"device": device["name"], "key": device_key, "current": device.get("version"),
            "release": releases[0], "ac_required": device["ac_required"], "device_id": device["local_id"]}


def install_reviewed(device_key, version, *, status=None, confirm=None, execute=None):
    import subprocess
    import sys
    if confirm is None:
        if not sys.stdin.isatty():
            raise ValueError("Review firmware installation in an interactive terminal")
        confirm = input
    status = status or (lambda: firmware_status(local_ids=True))
    plan = reviewed_release(status(), device_key, version)
    print(json.dumps({k: v for k, v in plan.items() if k != "device_id"}, indent=2), flush=True)
    if confirm("Type the exact offered version to continue, or press Enter to cancel: ") != version:
        return {"operation": "cancelled"}
    if reviewed_release(status(), device_key, version) != plan:
        raise ValueError("Firmware or device state changed. Review the current release again.")
    # fwupd can interpret a first argument as a local cabinet filename. Use a
    # fixed directory and permit only the full enumerated device ID.
    if Path("/").joinpath(plan["device_id"]).exists():
        raise ValueError("Device ID conflicts with a local filename")
    args = ["/usr/bin/fwupdmgr", "install", plan["device_id"], version,
            "--no-unreported-check", "--no-reboot-check"]
    result = (execute or subprocess.call)(args, cwd="/")
    return {"operation": "fwupd_finished" if result == 0 else "fwupd_failed", "exit_status": result}
