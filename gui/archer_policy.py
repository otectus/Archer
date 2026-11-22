"""Power-policy ownership discovery and conditional lease restoration."""

from __future__ import annotations

import shutil
import time

from archer_runtime import run_command


PROVIDERS = {
    "tlp": ("tlp.service", "tlp"),
    "tlp-pd": ("tlp-pd.service", "tlp-pd"),
    "power-profiles-daemon": ("power-profiles-daemon.service", "powerprofilesctl"),
    "tuned": ("tuned.service", "tuned-adm"),
    "tuned-ppd": ("tuned-ppd.service", "tuned-ppd"),
    "thermald": ("thermald.service", "thermald"),
    "gamemode": ("gamemoded.service", "gamemoded"),
}


def discover_providers(runner=run_command, which=shutil.which):
    result = []
    for name, (unit, executable) in PROVIDERS.items():
        installed = bool(which(executable))
        active = installed and runner(["systemctl", "is-active", "--quiet", unit], 3).success
        result.append({"id": name, "service": unit, "installed": installed,
                       "active": active, "controls": _controls(name)})
    return result


def _controls(name):
    if name in ("tlp", "tlp-pd", "power-profiles-daemon", "tuned", "tuned-ppd"):
        controls = ["platform_profile", "cpu_policy", "runtime_pm"]
        if name in ("tlp", "tlp-pd"):
            controls.append("charge_threshold")
        return controls
    if name == "thermald":
        return ["thermal_policy"]
    if name == "gamemode":
        return ["application_performance"]
    return []


def owners_by_control(providers):
    owners = {}
    for provider in providers:
        if not provider.get("active"):
            continue
        for control in provider.get("controls", []):
            owners.setdefault(control, []).append(provider["id"])
    return owners


def lease(owner, scope, prior, written, *, token, expiry=None, priority=0):
    if not all(isinstance(value, str) and value for value in (owner, scope, token)):
        raise ValueError("lease owner, scope and token are required")
    return {"schema_version": 1, "token": token, "owner": owner, "scope": scope,
            "priority": int(priority), "started_at": time.time(), "expires_at": expiry,
            "prior": prior, "last_written": written}


def may_restore(current, lease_record):
    """Never overwrite a value deliberately superseded after Archer's write."""
    return current == lease_record.get("last_written")
