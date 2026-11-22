"""Inventory-driven graphics planning and validated mode execution."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import json
import hashlib

from archer_runtime import run_command


MODES = ("hybrid", "integrated", "nvidia")


def _devices(inventory):
    devices = inventory.get("devices", []) if isinstance(inventory, dict) else []
    if isinstance(devices, dict):
        flattened = []
        for group in devices.values():
            if isinstance(group, list):
                flattened.extend(item for item in group if isinstance(item, dict))
        return flattened
    if isinstance(devices, list):
        return [item for item in devices if isinstance(item, dict)]
    return []


def graphics_devices(inventory):
    result = []
    for item in _devices(inventory):
        attributes = item.get("attributes", {}) if isinstance(item.get("attributes"), dict) else {}
        subsystem = str(item.get("kind", item.get("subsystem", item.get("class", "")))).lower()
        class_id = str(attributes.get("class", item.get("class_id", item.get("class_code", "")))).lower().removeprefix("0x")
        if subsystem != "pci" or not class_id.startswith("03"):
            continue
        vendor = _hex(attributes.get("vendor", item.get("vendor_id", item.get("vendor"))))
        device = _hex(attributes.get("device", item.get("device_id", item.get("device"))))
        result.append({"device_key": item.get("key", item.get("device_key", "unknown")),
                       "vendor_id": vendor, "device_id": device,
                       "driver": item.get("driver"),
                       "name": item.get("name", item.get("product_name", "")),
                       "gpu_role": attributes.get("gpu_role", "unknown"),
                       "generation": item.get("generation")})
    return result


def _hex(value):
    value = str(value or "").lower().removeprefix("0x")
    return value.zfill(4) if re.fullmatch(r"[0-9a-f]{1,4}", value) else value


def nvidia_generation(device, support_db=None):
    exact = (support_db or supported_gpu_database()).get(device.get("device_id"))
    if exact:
        branch = str(exact.get("legacybranch", exact.get("legacy_branch", ""))).lower()
        if branch:
            return "legacy"
        if exact.get("open") is False or exact.get("kernelopen") is False:
            return "legacy"
        features = exact.get("features", [])
        if exact.get("open") is True or exact.get("kernelopen") is True or "kernelopen" in features:
            return "open-supported"
        return "unknown"
    explicit = str(device.get("generation") or "").lower()
    if explicit in ("turing", "ampere", "ada", "blackwell", "pascal", "maxwell", "legacy"):
        return explicit
    return "unknown"


def distro_owner(os_release=None, which=shutil.which):
    data = os_release or _os_release()
    ids = {str(data.get("ID", "")).lower(), *str(data.get("ID_LIKE", "")).lower().split()}
    if "manjaro" in ids:
        return "mhwd"
    if "cachyos" in ids:
        return "chwd"
    if str(data.get("ID", "")).lower() in ("arch", "endeavouros"):
        return "pacman"
    return "distribution"


def build_plan(inventory, mode="hybrid", *, os_release=None, which=shutil.which,
               package_available=None, support_db=None):
    if mode not in MODES:
        raise ValueError("unsupported graphics mode")
    gpus = graphics_devices(inventory)
    nvidia = [gpu for gpu in gpus if gpu["vendor_id"] == "10de"]
    integrated = [gpu for gpu in gpus if gpu["gpu_role"] == "integrated" and gpu["vendor_id"] in ("1002", "8086")]
    generation = inventory.get("generation", 0) if isinstance(inventory, dict) else 0
    plan = {"schema_version": 1, "inventory_generation": generation, "requested_mode": mode,
            "gpus": gpus, "owner": distro_owner(os_release, which), "packages": [],
            "backend": None, "available": False, "reason_code": None,
            "reboot_required": False, "verification": ["driver", "render_offload", "outputs", "runtime_pm"]}
    plan["preflight"] = {"kernel_modules": kernel_module_status(),
                         "secure_boot": secure_boot_state(),
                         "active_outputs": active_outputs()}
    plan["required_acknowledgements"] = (
        ["active_output_routing_reviewed"]
        if mode == "integrated" and plan["preflight"]["active_outputs"] else [])
    if not nvidia:
        plan["reason_code"] = "no_nvidia_gpu"
        return plan
    if len(gpus) > 2:
        plan["reason_code"] = "mode_backend_cannot_target_multiple_adapters"
        return plan
    if mode in ("hybrid", "integrated") and not integrated:
        plan["reason_code"] = "no_integrated_gpu"
        return plan
    owner = plan["owner"]
    if owner != "pacman":
        plan.update(available=False, backend=owner, reason_code="distribution_manager_required",
                    reboot_required=True)
        return plan
    generations = {nvidia_generation(gpu, support_db) for gpu in nvidia}
    if len(generations) > 1 and generations.intersection({"legacy", "pascal", "maxwell", "unknown"}):
        plan["reason_code"] = "mixed_driver_requirements"
        return plan
    if "unknown" in generations:
        installed = _installed_nvidia_packages()
        if installed and all(gpu.get("driver") == "nvidia" for gpu in nvidia) and which("envycontrol"):
            plan.update(available=True, backend="envycontrol", packages=installed,
                        reason_code="current_driver_preserved", reboot_required=True)
        else:
            plan["reason_code"] = "nvidia_generation_unknown"
        return plan
    if generations <= {"turing", "ampere", "ada", "blackwell", "open-supported"}:
        candidates = ["nvidia-open-dkms", "nvidia-utils"]
    else:
        # Preserve a working legacy branch. Availability must be resolved from
        # configured repositories; do not assert a universal 580xx package.
        candidates = _installed_nvidia_packages()
        if not candidates or any(p.startswith("nvidia-open") for p in candidates) or any(g["driver"] != "nvidia" for g in nvidia):
            plan["reason_code"] = "legacy_driver_requires_qualified_provider"
            return plan
    package_available = package_available or _package_available
    if any(not package_available(package) for package in candidates):
        plan["reason_code"] = "package_unavailable"
        return plan
    plan["packages"] = candidates
    if which("envycontrol"):
        plan.update(available=True, backend="envycontrol", reason_code=None, reboot_required=True)
    else:
        plan["reason_code"] = "mode_backend_unavailable"
    return plan


def review_token(plan):
    content = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def supported_gpu_database(paths=None):
    """Load NVIDIA's installed exact-device support data, never a web guess."""
    paths = paths or (
        Path("/usr/share/doc/nvidia-utils/supported-gpus.json"),
        Path("/usr/share/doc/nvidia/supported-gpus.json"),
        Path("/usr/share/nvidia/supported-gpus.json"),
    )
    for path in paths:
        try:
            payload = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("chips", payload.get("devices", []))
        else:
            rows = []
        if not isinstance(rows, list):
            continue
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            devid = _hex(row.get("devid", row.get("device_id", row.get("device"))))
            if re.fullmatch(r"[0-9a-f]{4}", devid):
                result[devid] = row
        if result:
            return result
    return {}


def execute_mode_plan(plan, runner=run_command):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not plan.get("available"):
        return {"success": False, "error": "Graphics plan is unavailable or invalid"}
    mode, backend = plan.get("requested_mode"), plan.get("backend")
    if mode not in MODES or backend != "envycontrol":
        return {"success": False, "error": "Graphics plan requires its distribution owner"}
    result = runner(["/usr/bin/envycontrol", "-s", mode], 90)
    if not result.success:
        return {"success": False, "error": result.stderr or result.stdout or "Graphics mode change failed"}
    observed = runner(["/usr/bin/envycontrol", "--query"], 10)
    if not observed.success or observed.stdout.strip() != mode:
        return {"success": False, "error": "Graphics configured-mode readback failed"}
    return {"success": True, "data": {
        "configured_mode": mode, "observed_mode": "unknown",
        "reboot_required": True,
        "inventory_generation": plan.get("inventory_generation")}}


def _installed_nvidia_packages():
    result = run_command(["pacman", "-Qq"], 5)
    if not result.success:
        return []
    packages = sorted(package for package in result.stdout.splitlines()
                      if re.fullmatch(r"nvidia(?:-[a-z0-9]+)*(?:-dkms)?|nvidia-utils", package))
    drivers = [p for p in packages if re.fullmatch(r"nvidia(?:-open)?(?:-lts|-dkms)?|nvidia-\d+xx-dkms", p)]
    userspace = [p for p in packages if re.fullmatch(r"nvidia(?:-\d+xx)?-utils", p)]

    def branch(package):
        match = re.search(r"-(\d+xx)(?:-|$)", package)
        return match[1] if match else "current"

    branches = {branch(package) for package in drivers}
    if len(branches) != 1 or {branch(package) for package in userspace} != branches:
        return []
    return drivers + userspace


def _package_available(package):
    return run_command(["pacman", "-Si", "--", package], 15).success


def verify_kernel_modules():
    rows = kernel_module_status()
    if not rows:
        raise ValueError("No installed kernel targets were found")
    missing = [row["kernel"] for row in rows if not row["module_present"]]
    if missing:
        raise ValueError("NVIDIA module missing for: " + ", ".join(missing))
    secure = secure_boot_state()
    if secure == "unknown":
        raise ValueError("Cannot verify Secure Boot state for this graphics transition")
    if secure == "enabled" or signature_enforced():
        unsigned = [row["kernel"] for row in rows if not row["signer_present"]]
        if unsigned:
            raise ValueError("Signed NVIDIA module unavailable for: " + ", ".join(unsigned))
    return {"kernel_modules": rows, "secure_boot": secure}


def signature_enforced():
    for path, values in (("/sys/module/module/parameters/sig_enforce", ("y", "1")),
                         ("/sys/kernel/security/lockdown", ("[integrity]", "[confidentiality]"))):
        try:
            if any(v in Path(path).read_text().lower().split() for v in values):
                return True
        except OSError:
            continue
    return False


def _os_release(path=Path("/etc/os-release")):
    data = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value.strip().strip('"')
    except OSError:
        pass
    return data


def kernel_module_status(modules_root=Path("/usr/lib/modules")):
    rows = []
    try:
        kernels = sorted(path.name for path in modules_root.iterdir() if path.is_dir())
    except OSError:
        return rows
    for kernel in kernels:
        module = run_command(["modinfo", "-k", kernel, "-F", "filename", "nvidia"], 5)
        signer = run_command(["modinfo", "-k", kernel, "-F", "signer", "nvidia"], 5) if module.success else None
        rows.append({"kernel": kernel, "module_present": module.success,
                     "signer_present": bool(signer and signer.success and signer.stdout.strip())})
    return rows


def secure_boot_state(root=Path("/sys/firmware/efi/efivars")):
    try:
        paths = list(root.glob("SecureBoot-*"))
        if not paths:
            return "unknown" if root.parent.exists() else "disabled_or_legacy_boot"
        data = paths[0].read_bytes()
        if len(data) != 5 or data[4] not in (0, 1):
            return "unknown"
        return "enabled" if data[4] else "disabled"
    except OSError:
        return "unknown"


def active_outputs(root=Path("/sys/class/drm")):
    result = []
    try:
        connectors = sorted(root.glob("card*-*"))
    except OSError:
        return result
    for connector in connectors:
        try:
            if (connector / "status").read_text().strip() == "connected":
                result.append(connector.name)
        except OSError:
            continue
    return result
