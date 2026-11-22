"""Read-only update and suspend preflight. Never export command lines or crypttab entries."""
import datetime as dt
import json
import os
import stat
import tempfile
from pathlib import Path
import re
import subprocess

from archer_inventory import read
from archer_runtime import run_command


def system_file(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return ""
    try:
        with path.open(errors="replace") as source:
            return source.read(128 * 1024)
    except OSError:
        return ""


def advisory_catalog(root, verify=run_command):
    bundled = json.loads(Path(__file__).with_name("data").joinpath("advisories.json").read_text())
    directory = root / "var/lib/archer/advisories"
    if not directory.exists():
        return bundled, "bundled"
    try:
        if directory.resolve() != directory:
            raise ValueError("redirected advisories directory")
        keys = root / "etc/archer/advisory-keys.gpg"
        if keys.resolve() != keys or not keys.is_file():
            raise ValueError("missing trusted publisher keyring")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, limit in (("catalog.json", 128 * 1024), ("catalog.json.sig", 16 * 1024)):
                fd = os.open(directory / name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError("invalid advisory input")
                    data = stream.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("advisory input too large")
                (work / name).write_bytes(data)
            result = verify(["/usr/bin/gpgv", "--homedir", str(work), "--keyring", str(keys),
                             str(work / "catalog.json.sig"), str(work / "catalog.json")], timeout=5)
            if not result.success:
                raise ValueError("publisher signature could not be verified")
            catalog = json.loads((work / "catalog.json").read_bytes())
        if (not isinstance(catalog, dict) or catalog.get("schema_version") != 1
                or not isinstance(catalog.get("entries"), list) or len(catalog["entries"]) > 256
                or dt.date.fromisoformat(catalog["reviewed"]) < dt.date.fromisoformat(bundled["reviewed"])):
            raise ValueError("invalid advisory catalog")
        common = {"id", "condition", "package", "transition_to", "symptom", "mitigation", "retire_when", "source"}
        for entry in catalog["entries"]:
            if (not isinstance(entry, dict) or not common.issubset(entry)
                    or entry.keys() - (common | {"last_affected"})
                    or not all(isinstance(v, str) and 0 < len(v) <= 2048 for v in entry.values())
                    or not entry["source"].startswith("https://")
                    or (entry["condition"] == "installed-firmware-before-split" and "last_affected" not in entry)):
                raise ValueError("invalid advisory entry")
        return catalog, "publisher_signature_verified"
    except (OSError, KeyError, TypeError, ValueError):
        return bundled, "external_catalog_rejected_using_bundled"


def matching_advisories(root, packages, distro, catalogue=None):
    if distro != "arch":
        return []
    if catalogue is None:
        catalogue, _ = advisory_catalog(root)
    results = []
    for entry in catalogue["entries"]:
        version = packages.get(entry["package"])
        if not version:
            continue
        if entry["condition"] == "installed-firmware-before-split":
            try:
                match = int(subprocess.check_output(["/usr/bin/vercmp", version, entry["last_affected"]],
                                                    text=True, timeout=2)) <= 0
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            if match:
                results.append({**entry, "applicability": "review_when_crossing_version"})
        elif entry["condition"] == "systemd-hook-and-tpm2-unlock":
            try:
                below = int(subprocess.check_output(["/usr/bin/vercmp", version, entry["transition_to"]],
                                                    text=True, timeout=2)) < 0
            except (OSError, ValueError, subprocess.SubprocessError):
                below = None
            if below is False:
                continue
            hooks = re.findall(r"^\s*HOOKS=\(([^)]*)\)", system_file(root, "etc/mkinitcpio.conf"), re.M)
            encrypted = [s.split("#", 1)[0] for p in ("etc/crypttab", "etc/crypttab.initramfs")
                         for s in system_file(root, p).splitlines()]
            if hooks and "systemd" in hooks[-1].split() and any("tpm2-device=" in line for line in encrypted):
                results.append({**entry, "applicability": "verify_package_transition_and_enrolled_pcrs"})
    return results


def target_attribute(root, relative):
    path = (root / relative).resolve()
    return read(path) if path.is_relative_to(root) else None


def readiness(root, inventory, packages):
    bootable = []
    for kernel in sorted((root / "usr/lib/modules").glob("*")):
        if not (kernel / "kernel").is_dir():
            continue
        marker = target_attribute(root, kernel.relative_to(root) / "build/include/config/kernel.release")
        bootable.append({"release": kernel.name, "headers_match": marker == kernel.name,
                         "has_module_tree": True})
    sysfs = root / "sys"
    secure_boot = None
    path = sysfs / "firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    if path.resolve().is_relative_to(root):
        try:
            with path.open("rb") as stream:
                value = stream.read(6)
            if len(value) == 5 and value[-1] in (0, 1):
                secure_boot = bool(value[-1])
        except OSError:
            pass
    swaps = [s.split() for s in system_file(root, "proc/swaps").splitlines()[1:]]
    disk_swap = sum(max(0, int(s[2]) - int(s[3])) for s in swaps if len(s) >= 4 and s[2].isdigit()
                    and s[3].isdigit() and not s[0].startswith("/dev/zram"))
    resume = target_attribute(root, "sys/power/resume")
    disk = "disk" in (inventory["sleep"]["state"] or "").split()
    hibernate = ("no_disk_sleep" if not disk else "no_disk_swap" if not disk_swap else
                 "resume_target_unverified" if not resume or resume == "0:0" else "check_capacity_encryption_and_resume_offset")
    catalogue, provenance = advisory_catalog(root)
    return {"kernels": bootable, "secure_boot": secure_boot,
            "module_signature_enforcement": target_attribute(root, "sys/module/module/parameters/sig_enforce"),
            "pacman_locked": (root / "var/lib/pacman/db.lck").exists(),
            "hibernation": {"assessment": hibernate, "free_disk_swap_kib": disk_swap},
            "advisory_source": provenance,
            "advisories": matching_advisories(root, packages, inventory["environment"]["distribution"]["id"], catalogue),
            "transactions": transaction_status(root)}


def transaction_status(root):
    directory = root / "var/lib/archer/transactions"
    pointer = target_attribute(root, "var/lib/archer/transactions/current")
    if not pointer:
        return {"state": "unknown" if directory.exists() else "none_recorded"}
    key = pointer.strip('"')
    if not re.fullmatch(r"[0-9TZ.\-]{5,50}", key):
        return {"state": "unknown"}
    try:
        data = json.loads(system_file(root, directory.relative_to(root) / (key + ".json")))
        if data.get("state") in ("planned", "preflight-complete", "applying", "pending-restart", "verifying",
                                 "committed", "failed", "recovery-required"):
            return {"state": data["state"], "step_count": len(data["steps"])}
    except (TypeError, KeyError, ValueError):
        pass
    return {"state": "unknown"}
