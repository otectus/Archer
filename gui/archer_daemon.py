#!/usr/bin/env python3
"""
Archer Compatibility Suite - System Daemon
Runs as root, communicates with Linuwu-Sense driver via sysfs.
Exposes io.otectus.Archer1 on the system D-Bus.
"""

import json
import copy
import logging
import os
import math
import re
from numbers import Integral
import signal
import shutil
import shlex
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import archer_battery
import uuid
from archer_binary import BinaryControl, parse_binary_readback
from archer_capabilities import capability, metric, qualified_driver_overrides
import archer_fans
import archer_graphics
from archer_inventory import collect_inventory
import archer_policy
from archer_firmware import firmware_status
from archer_runtime import run_command
from archer_settings import SettingsStore, validate, EFFECT_IDS, WMI_EFFECT_IDS, EFFECT_LABELS
from archer_version import VERSION

# Direct ENE K5130 backlight backend. On PHN16S-71 the WMI path applies
# brightness but silently discards colour and effect mode, so keyboard
# lighting has to go straight to the LED controller. Optional import: if the
# module or the chip is absent we fall back to the sysfs/WMI path.
try:
    import archer_ene
except Exception:  # pragma: no cover - absence is a supported configuration
    archer_ene = None

# --- Configuration ---
# /run/archer is created by systemd via RuntimeDirectory=archer in the unit
# file. The PID location matches PIDFile= in archer-daemon.service.
PID_FILE = "/run/archer/daemon.pid"
LOG_FILE = "/var/log/archer-daemon.log"
SETTINGS_FILE = "/etc/archer/settings.json"

# Linuwu-Sense sysfs base paths (tried in order)
DRIVER_BASE_PATHS = [
    "/sys/module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi",
    "/sys/devices/platform/acer-wmi",
]

# System paths
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CLASS = "/sys/class/platform-profile"
BATTERY_CALIBRATION_PATH = "/sys/bus/wmi/drivers/acer-wmi-battery/calibration_mode"
CPUFREQ_DIR = "/sys/devices/system/cpu/cpufreq"
AUDIO_CONF = "/etc/pipewire/pipewire.conf.d/archer-noise-suppress.conf"
AUDIO_PLUGIN = "/usr/lib/ladspa/librnnoise_ladspa.so"
BATTERY_HEALTH_PATH = "/sys/bus/wmi/drivers/acer-wmi-battery/health_mode"
PLATFORM_PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"
DRIVER_MODULE_PATH = "/sys/module/linuwu_sense"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"
DMI_BOARD = "/sys/class/dmi/id/board_name"
DMI_VENDOR = "/sys/class/dmi/id/sys_vendor"
HWMON_DIR = "/sys/class/hwmon"
THERMAL_DIR = "/sys/class/thermal"
POWER_SUPPLY_DIR = "/sys/class/power_supply"

# Saved and replayed 0/1 sense controls. Their show() callbacks print -1 for
# firmware replies they cannot decode, so requests and readback stay separate.
BINARY_CONTROLS = {
    "lcd_override": "LCD override",
    "boot_animation_sound": "Boot animation and sound",
    "backlight_timeout": "Keyboard backlight timeout",
}
_BINARY_CONTROLS_LOCK = threading.Lock()

# --- Logging ---
logger = logging.getLogger("archer-daemon")


def setup_logging():
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


# --- Utility Functions ---
def read_sysfs(path):
    """Read a sysfs file, return stripped string or None."""
    try:
        return Path(path).read_text().strip()
    except (OSError, TypeError, ValueError):
        return None


def write_sysfs(path, value):
    """Write a value to a sysfs file. Returns True on success."""
    try:
        # O_CREAT is intentionally absent: a disappearing sysfs node must fail.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC), "w") as stream:
            stream.write(str(value))
        return True
    except (OSError, TypeError, ValueError) as e:
        logger.error(f"Failed to write '{value}' to {path}: {e}")
        return False


def sysfs_children(directory):
    """A class may disappear between probing and enumeration (hotplug/unload)."""
    try:
        return sorted(Path(directory).iterdir())
    except OSError:
        return []


def platform_profile_paths():
    if read_sysfs(PLATFORM_PROFILE) is not None and read_sysfs(PLATFORM_PROFILE_CHOICES):
        return PLATFORM_PROFILE, PLATFORM_PROFILE_CHOICES
    candidates = [(p / "profile", p / "choices") for p in sysfs_children(PLATFORM_PROFILE_CLASS)
                  if read_sysfs(p / "profile") is not None and read_sysfs(p / "choices")]
    # Multiple providers need the kernel's aggregate interface or explicit policy.
    return candidates[0] if len(candidates) == 1 else (None, None)


def battery_supply_paths():
    return [p for p in sysfs_children(POWER_SUPPLY_DIR) if read_sysfs(p / "type") == "Battery"
            or p.name.startswith("BAT")]


# Shell metacharacters we forbid in dynamic strings. Static literal commands
# (no $ interpolation) can still pass shell_meta_ok=True if they need pipes
# or substitutions — see callsites in get_cpu_usage / get_system_info.
_SHELL_META = (";", "&&", "||", "$(", "`")


def run_cmd(cmd, timeout=10, shell_meta_ok=False):
    """Text-only compatibility wrapper for read probes; mutations use run_command.

    Even legacy static pipelines get process-group cleanup and exit-code checks.
    """
    if not shell_meta_ok and any(token in cmd for token in _SHELL_META):
        return ""
    result = run_command(["/bin/sh", "-c", cmd] if shell_meta_ok else shlex.split(cmd), timeout)
    return result.stdout if result.success else ""


class _TtlCache:
    """Tiny single-value-per-key cache with monotonic TTL.

    Used for hot-path probes (nvidia-smi, lspci) that the GUI's 2-second
    monitoring loop would otherwise hit too often.
    """

    def __init__(self, ttl_s):
        self._ttl = ttl_s
        self._values = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key, fn):
        now = time.monotonic()
        with self._lock:
            entry = self._values.get(key)
            if entry is not None:
                ts, val = entry
                if now - ts < self._ttl:
                    return val
        # Compute outside the lock so a slow probe doesn't block other keys.
        val = fn()
        with self._lock:
            self._values[key] = (time.monotonic(), val)
        return val


# 5-second TTL is short enough to feel live and long enough to skip 2/3rds
# of the GUI's 2s polling ticks. Touched by HardwareManager methods below.
_PROBE_CACHE = _TtlCache(ttl_s=5.0)

# hwmon device names that ship a meaningful fan1_input/temp1_input on Acer
# laptops. Order is preference — first match wins. Anything else falls back
# to the legacy "first device with fan1_input" behaviour.
_HWMON_FAN_NAMES = (
    "acer", "linuwu_sense", "acer_wmi", "nct6775", "nct6779", "nct6798",
    "it87", "dell_smm_hwmon",
)
_HWMON_GPU_TEMP_NAMES = ("nvidia", "amdgpu", "nouveau")


# --- Fan Curve Engine ---
class FanCurveEngine:
    """Runs bounded 2Hz curves and restores firmware control on lost evidence."""

    def __init__(self, get_temp_fn, set_fan_fn, restore_auto_fn, on_failure_fn=None,
                 *, paired=True, max_failures=3, clock=time.time):
        self._get_temp = get_temp_fn
        self._set_fan = set_fan_fn
        self._restore_auto = restore_auto_fn
        self._on_failure = on_failure_fn
        self._paired = paired
        self._max_failures = max(1, int(max_failures))
        self._clock = clock
        self._curves = {}
        self._active = {}
        self._bindings = {}
        self._fail_counts = {}
        self._last_error = {}
        self._lock = threading.RLock()
        self._curve_thread = None
        self._running = False

    def start(self, target, points, *, sensor_id=None, expires_at=None,
              owner="archer", resume_after_suspend=False):
        sorted_pts = sorted(points, key=lambda p: p[0])
        with self._lock:
            self._curves[target] = sorted_pts
            self._active[target] = True
            self._fail_counts[target] = 0
            self._last_error.pop(target, None)
            self._bindings[target] = {
                "fan_id": target, "sensor_id": sensor_id,
                "owner": owner, "expires_at": expires_at,
                "resume_after_suspend": bool(resume_after_suspend),
            }
        self._ensure_thread()

    def stop(self, target=None):
        """Stop one independent fan, or every fan for paired/global requests."""
        with self._lock:
            targets = ([key for key, active in self._active.items() if active]
                       if target is None else [target])
            for key in targets:
                self._active[key] = False
                self._fail_counts[key] = 0
            if self._paired:
                restored = self._restore_auto()
            else:
                restored = True
                for key in targets:
                    restored = self._restore_auto(key) and restored
            self._running = any(self._active.values())
            thread = self._curve_thread
        if not self._running and thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return restored

    def get_state(self):
        with self._lock:
            targets = set(self._curves) | set(self._active)
            if self._paired:
                targets.update(("cpu", "gpu"))
            return {target: {
                "active": self._active.get(target, False),
                "points": self._curves.get(target, []),
                **self._bindings.get(target, {}),
                "last_error": self._last_error.get(target),
            } for target in sorted(targets)}

    def _ensure_thread(self):
        if self._running:
            return
        self._running = True
        self._curve_thread = threading.Thread(target=self._loop, daemon=True)
        self._curve_thread.start()

    def _tick(self):
        # Serialize stop/restore with a complete tick; no stale write after Auto.
        with self._lock:
            for target in list(self._active):
                if not self._active.get(target):
                    continue
                binding = self._bindings.get(target, {})
                expires_at = binding.get("expires_at")
                if expires_at is not None and self._clock() >= expires_at:
                    self._last_error[target] = "expired"
                    self._active[target] = False
                    if self._paired:
                        self._restore_auto()
                    else:
                        self._restore_auto(target)
                    continue
                try:
                    sensor_id = binding.get("sensor_id")
                    sample = self._get_temp(target) if sensor_id is None else self._get_temp(target, sensor_id)
                    sampled_at = self._clock()
                    if isinstance(sample, dict):
                        temp = sample.get("celsius")
                        sampled_at = sample.get("sampled_at")
                    else:
                        temp = sample
                    if temp is None or not math.isfinite(temp) or not 0 < temp <= 125:
                        raise ValueError(f"Unavailable/invalid {target} temperature: {temp}")
                    if not isinstance(sampled_at, (int, float)):
                        raise ValueError(f"Invalid {target} temperature sample time")
                    if self._clock() - sampled_at > 3 or sampled_at - self._clock() > 1:
                        raise ValueError(f"Stale {target} temperature sample")
                    pct = max(30, min(100, self._interpolate(self._curves[target], temp)))
                    if self._set_fan(target, pct) is False:
                        raise OSError("Fan write failed")
                    self._fail_counts[target] = 0
                    self._last_error.pop(target, None)
                except Exception as error:
                    logger.error("Fan curve tick failed for %s: %s", target, error)
                    self._fail_counts[target] = self._fail_counts.get(target, 0) + 1
                    if self._paired:
                        self._restore_auto()
                    else:
                        for key, active in self._active.items():
                            if active:
                                self._restore_auto(key)
                    self._last_error[target] = str(error)
                    if self._fail_counts[target] >= self._max_failures:
                        logger.error("Fan curve watchdog: stopping curves and restoring firmware Auto")
                        self._active = {key: False for key in self._active}
                        self._running = False
                        if self._on_failure:
                            self._on_failure()
                    # No second target may overwrite the failsafe in this tick.
                    break
            if not any(self._active.values()):
                self._running = False

    def _loop(self):
        while self._running and self._curve_thread is threading.current_thread():
            self._tick()
            time.sleep(0.5)

    @staticmethod
    def _interpolate(points, temp):
        """Linearly interpolate fan percentage from curve points."""
        if not points:
            return 30
        if temp <= points[0][0]:
            return points[0][1]
        if temp >= points[-1][0]:
            return points[-1][1]
        for i in range(len(points) - 1):
            t0, p0 = points[i]
            t1, p1 = points[i + 1]
            if t0 <= temp <= t1:
                if t1 == t0:
                    return p0
                frac = (temp - t0) / (t1 - t0)
                return p0 + frac * (p1 - p0)
        return points[-1][1]


# --- Hardware Detection ---
class HardwareManager:
    """Manages hardware detection and control via sysfs."""

    def __init__(self, settings_store=None, restore_settings=True):
        self.driver_base = None
        self.sense_base = None  # predator_sense or nitro_sense subdirectory
        self.laptop_type = "unknown"
        self.features = []
        self.driver_status = "not_installed"
        self._fan_lock = threading.RLock()
        self._ene_lock = threading.Lock()
        self._firmware_lock = threading.Lock()
        self._cpu_sample_lock = threading.Lock()
        self.settings = settings_store or SettingsStore()
        self._game_mode_active = False
        self._game_mode_saved = {}
        self._inventory = self._collect_inventory()
        self._native_fans = archer_fans.NativeAcerHwmon.probe(Path(HWMON_DIR))
        hwmon_keys = {item.get("node"): item.get("key") for item in self._inventory.get("devices", [])
                      if item.get("kind") == "hwmon"}
        ambiguous = {item.get("node") for item in self._inventory.get("devices", [])
                     if item.get("kind") == "hwmon" and not item.get("identity_unique", True)}
        self._native_fans = [backend for backend in self._native_fans if backend.path.name not in ambiguous]
        for backend in self._native_fans:
            backend.device_key = hwmon_keys.get(backend.path.name, backend.device_key)
        self._fan_curve_engine = FanCurveEngine(
            get_temp_fn=self._fan_curve_get_temp,
            set_fan_fn=self._fan_curve_set_fan,
            restore_auto_fn=self._fan_curve_restore_auto,
            on_failure_fn=self._disable_saved_curves,
        )
        self._native_curve_engine = FanCurveEngine(
            get_temp_fn=self._native_curve_get_temp,
            set_fan_fn=self._native_curve_set_fan,
            restore_auto_fn=self._native_curve_restore_auto,
            paired=False, max_failures=1,
        )
        self._detect_driver()
        self._detect_laptop_type()
        self._detect_features()
        logger.info(f"Laptop type: {self.laptop_type}")
        logger.info(f"Driver base: {self.driver_base}")
        logger.info(f"Sense base: {self.sense_base}")
        logger.info(f"Available features: {self.features}")
        self._diagnose_driver()
        # Linuwu's paired ABI has no ownership readback, so retain its historical
        # crash recovery. Native fans are touched only when Archer journaled them.
        if self.sense_base and "fan_control" in self.features and not self._fan_curve_restore_auto():
            self.features.remove("fan_control")
            self.driver_status = "auto_restore_failed"
        if self._native_fans and not self._restore_owned_native_fans():
            self.features = [feature for feature in self.features
                             if feature not in ("native_fan_control", "fan_control")]
            self.driver_status = "native_auto_restore_failed"
        if restore_settings:
            try:
                self._restore_saved_settings()
            except (OSError, ValueError, KeyError, TypeError) as error:
                logger.error("Optional settings restoration failed: %s", error)
                self.shutdown_fan_curves()

    @staticmethod
    def _collect_inventory():
        try:
            return collect_inventory()
        except (OSError, RuntimeError, ValueError) as error:
            logger.warning("Inventory collection failed: %s", error)
            return {"schema_version": 1, "generation": "unavailable", "devices": []}

    def _detect_driver(self):
        self.driver_base = None
        for base in DRIVER_BASE_PATHS:
            if not os.path.isdir(base):
                continue
            # /sys/devices/platform/acer-wmi is also created by stock acer_wmi.
            owner = Path(base, "driver/module").resolve().name
            module_path = str(Path(DRIVER_MODULE_PATH) / "drivers") + os.sep
            if owner == "linuwu_sense" or str(base).startswith(module_path):
                self.driver_base = base
                return
        logger.warning("No platform device owned by Linuwu-Sense found")

    def _diagnose_driver(self):
        quirk = read_sysfs(Path(DRIVER_MODULE_PATH) / "parameters/sense_quirk")
        fan_path = self._sense_path("fan_speed")
        if not os.path.isdir(DRIVER_MODULE_PATH):
            self.driver_status = "module_not_loaded" if run_cmd("modinfo -n linuwu_sense") else "not_installed"
        elif not self.sense_base:
            self.driver_status = "dmi_unsupported" if quirk == "none" else "sense_interface_missing"
        elif not fan_path or not os.path.isfile(fan_path):
            self.driver_status = "fan_node_missing"
        elif not os.access(fan_path, os.R_OK | os.W_OK):
            self.driver_status = "permission_denied"
        elif "fan_control" not in self.features:
            self.driver_status = "fan_abi_incompatible"
        else:
            self.driver_status = "supported"
        logger.info("Driver diagnostic: %s; vendor=%r product=%r board=%r quirk=%r driver=%r sense=%r fan=%r",
                    self.driver_status, read_sysfs(DMI_VENDOR), read_sysfs(DMI_PRODUCT),
                    read_sysfs(DMI_BOARD), quirk, self.driver_base, self.sense_base, fan_path)
        if self.driver_status != "supported":
            logger.warning("Fan controls unavailable (%s). Run python3 scripts/archer-diagnose.py; "
                           "DMI family recognition alone does not enable controls.", self.driver_status)

    def _detect_laptop_type(self):
        if self.driver_base:
            predator_path = os.path.join(self.driver_base, "predator_sense")
            nitro_path = os.path.join(self.driver_base, "nitro_sense")
            if os.path.isdir(predator_path):
                self.laptop_type = "predator"
                self.sense_base = predator_path
            elif os.path.isdir(nitro_path):
                self.laptop_type = "nitro"
                self.sense_base = nitro_path
        # Fallback to DMI
        if self.laptop_type == "unknown":
            product = read_sysfs(DMI_PRODUCT) or ""
            product_lower = product.lower()
            if "predator" in product_lower or "helios" in product_lower:
                self.laptop_type = "predator"
            elif "nitro" in product_lower or re.fullmatch(r"anv?\d+[a-z]*-\d+", product_lower):
                self.laptop_type = "nitro"
            elif "triton" in product_lower:
                self.laptop_type = "predator"

    def _detect_features(self):
        self.features = []
        # Thermal profiles
        if platform_profile_paths()[0]:
            self.features.append("thermal_profiles")
        # Keyboard features. The ENE backend is preferred when the controller
        # is present: it is the only path that actually applies colour on this
        # model. The sysfs check stays as the fallback for other hardware.
        self.ene_ready = bool(archer_ene and archer_ene.available())
        if self.ene_ready:
            self.features.append("keyboard_per_zone")
            self.features.append("keyboard_effects")
            logger.info("Keyboard lighting: ENE K5130 backend active")
        elif self.driver_base:
            kb_base = os.path.join(self.driver_base, "four_zoned_kb")
            if os.path.isdir(kb_base):
                if os.path.exists(os.path.join(kb_base, "per_zone_mode")):
                    self.features.append("keyboard_per_zone")
                if os.path.exists(os.path.join(kb_base, "four_zone_mode")):
                    self.features.append("keyboard_effects")
            logger.info("Keyboard lighting: sysfs/WMI fallback "
                        "(colour may not be applied by firmware)")
        # Sense-specific features (under predator_sense/ or nitro_sense/)
        if self.sense_base:
            sense_features = {
                "fan_speed": "fan_control",
                "battery_calibration": "battery_calibration",
                "battery_limiter": "battery_limiter",
                "usb_charging": "usb_charging",
                "lcd_override": "lcd_override",
                "boot_animation_sound": "boot_animation_sound",
                "backlight_timeout": "backlight_timeout",
            }
            for sysfs_file, feature_name in sense_features.items():
                path = os.path.join(self.sense_base, sysfs_file)
                if os.path.isfile(path):
                    if feature_name == "fan_control" and (
                        not os.access(path, os.R_OK | os.W_OK) or self.get_fan_speed() == (None, None)
                    ):
                        continue
                    self.features.append(feature_name)
        if self._native_fans:
            self.features.append("native_fan_control")
            # Compatibility for the existing paired control only when two fans
            # can be represented without silently discarding one side.
            if "fan_control" not in self.features and len(self._native_fans) == 1 and \
                    len(self._native_fans[0].fans()) >= 2:
                self.features.append("fan_control")
        # Battery detection
        bat_paths = battery_supply_paths()
        if bat_paths:
            self.features.append("battery_info")
        if "battery_limiter" not in self.features and self._battery_limit_path():
            self.features.append("battery_limiter")
        if "battery_calibration" not in self.features and self._battery_calibration_path():
            self.features.append("battery_calibration")
        if self.audio_available():
            self.features.append("audio_enhancement")
        # Display mode (envycontrol)
        if _PROBE_CACHE.get_or_compute(
            "which-envycontrol",
            lambda: "/usr/bin/envycontrol" if os.path.isfile("/usr/bin/envycontrol") else "",
        ):
            self.features.append("display_mode")
        if "thermal_profiles" in self.features or self._epp_paths():
            self.features.append("game_mode")
        # USB wake policy
        if os.path.exists("/proc/acpi/wakeup"):
            self.features.append("usb_wake_policy")
        # Firmware info (always available)
        self.features.append("firmware_info")

    def _restore_saved_settings(self):
        """Re-apply any previously saved settings on daemon startup."""
        restored = []
        saved_curves = {t: self.settings.get(f"fan_curve_{t}") for t in ("cpu", "gpu")}

        # Keyboard lighting — restore whichever mode was last applied
        last_kb = self.settings.get("last_keyboard_mode")

        if last_kb == "effect":
            effect = self.settings.get("four_zone_mode")
            if effect and "keyboard_effects" in self.features:
                ok = self.set_four_zone_mode(
                    effect.get("effect", effect.get("mode")), effect["speed"], effect["brightness"],
                    effect["direction"], effect["red"], effect["green"], effect["blue"],
                )
                if ok:
                    restored.append("keyboard_effects")
        else:
            pz = self.settings.get("per_zone_mode")
            if pz and "keyboard_per_zone" in self.features:
                ok = self.set_per_zone_mode(
                    pz["zone1"], pz["zone2"], pz["zone3"], pz["zone4"],
                    pz["brightness"],
                )
                if ok:
                    restored.append("keyboard_per_zone")

        # Backlight timeout. Binary controls validate the saved value, replay it
        # once without saving it again, and record an unverified result honestly.
        if self._binary_control("backlight_timeout").restore("backlight_timeout" in self.features):
            restored.append("backlight_timeout")

        # Thermal profile
        tp = self.settings.get("thermal_profile")
        if tp and "thermal_profiles" in self.features:
            ok, _ = self.set_thermal_profile(tp)
            if ok:
                restored.append("thermal_profile")

        # Manual fan settings are deliberately not replayed across daemon starts.
        # Explicitly enabled curves can resume, after the initial Auto reset.
        try:
            self.settings.remove("fan_speed")
        except OSError as error:
            logger.error("Could not discard saved manual fan speeds: %s", error)

        # Battery limiter
        if "battery_limiter" in self.features:
            policy_path = Path(self.settings.path).with_name("battery.json")
            # GUI-only installs preserve firmware state until a choice was saved.
            bl_lim = self.settings.get("battery_limiter")
            try:
                if policy_path.exists():
                    with archer_battery.policy_lock(policy_path):
                        bl_lim = archer_battery.read_policy(policy_path)
                if bl_lim is not None and self.set_battery_limiter(bl_lim):
                    restored.append("battery_limiter")
            except (OSError, ValueError) as error:
                logger.error("Battery policy could not be restored: %s", error)

        # USB charging
        usb = self.settings.get("usb_charging")
        if usb is not None and "usb_charging" in self.features:
            ok = self.set_usb_charging(usb)
            if ok:
                restored.append("usb_charging")

        # LCD override, then boot animation sound
        for key in ("lcd_override", "boot_animation_sound"):
            if self._binary_control(key).restore(key in self.features):
                restored.append(key)

        # Fan curves
        for target in ("cpu", "gpu"):
            key = f"fan_curve_{target}"
            curve_data = saved_curves[target]
            if curve_data and curve_data.get("enabled") and curve_data.get("points"):
                if self.start_fan_curve(target, curve_data["points"]):
                    restored.append(key)

        # Reconcile interrupted transactions before accepting new Game Mode work.
        self._recover_game_mode()
        self._expire_battery_overrides()

        if restored:
            logger.info(f"Restored saved settings: {restored}")
        else:
            logger.info("No saved settings to restore")

    def _driver_path(self, relative):
        if not self.driver_base:
            return None
        return os.path.join(self.driver_base, relative)

    def _sense_path(self, relative):
        """Path under predator_sense/ or nitro_sense/."""
        if not self.sense_base:
            return None
        return os.path.join(self.sense_base, relative)

    # --- Thermal Profiles ---
    def get_thermal_profile(self):
        return read_sysfs(platform_profile_paths()[0]) or "unknown"

    def get_thermal_profile_choices(self):
        choices = read_sysfs(platform_profile_paths()[1])
        return choices.split() if choices else []

    def set_thermal_profile(self, profile):
        choices = self.get_thermal_profile_choices()
        if profile not in choices:
            return False, f"Invalid profile '{profile}'. Available: {choices}"
        if "fan_control" in self.features:
            self.stop_fan_curve()
            if not self._fan_curve_restore_auto():
                return False, "Could not restore automatic fans before profile change"
        ok = write_sysfs(platform_profile_paths()[0], profile)
        if ok:
            self._sync_button_led(profile)
        return ok, None if ok else "Failed to write profile"

    def _sync_button_led(self, profile):
        if not getattr(self, "ene_ready", False):
            return False
        return self._ene_call("set_button_for_profile", profile, 100, self.settings.get("button_colours", {}))

    @staticmethod
    def _valid_fan_value(value):
        return isinstance(value, Integral) and not isinstance(value, bool) and 0 <= value <= 100

    def get_fan_speed(self):
        path = self._sense_path("fan_speed")
        val = read_sysfs(path) if path else None
        if val and re.fullmatch(r"[0-9]{1,3},[0-9]{1,3}", val):
            values = tuple(map(int, val.split(",")))
            if all(self._valid_fan_value(v) for v in values):
                return values
        if val:
            logger.warning("Incompatible fan_speed ABI at %s: %r", path, val)
        if self._native_fans:
            fans = self._native_fans[0].fans()
            if len(fans) >= 2:
                values = []
                for fan in fans[:2]:
                    raw = read_sysfs(self._native_fans[0].path / f"pwm{fan['index']}")
                    try:
                        values.append(round(int(raw) * 100 / 255))
                    except (TypeError, ValueError):
                        return None, None
                return tuple(values)
        return None, None

    def _write_fan_speed(self, cpu, gpu):
        if not all(self._valid_fan_value(v) for v in (cpu, gpu)):
            return False
        path = self._sense_path("fan_speed")
        if "fan_control" not in self.features:
            return False
        if not path and self._native_fans:
            fans = self._native_fans[0].fans()
            if len(fans) < 2:
                return False
            with self._fan_lock:
                if not self._native_fans[0].set_manual(fans[0]["index"], cpu):
                    return False
                if not self._native_fans[0].set_manual(fans[1]["index"], gpu):
                    self._native_fans[0].set_auto()
                    return False
                return True
        if not path:
            return False
        with self._fan_lock:
            if self.get_fan_speed() == (None, None):
                logger.error("Fan interface disappeared or became incompatible: %s", path)
                return False
            ok = write_sysfs(path, f"{int(cpu)},{int(gpu)}")
            if not ok and (cpu or gpu):
                self._fan_curve_restore_auto()
            return ok

    def set_fan_speed(self, cpu, gpu):
        if "fan_control" not in self.features or not all(self._valid_fan_value(v) for v in (cpu, gpu)):
            return False
        if not self.stop_fan_curve():
            return False
        native = not self._sense_path("fan_speed") and bool(self._native_fans)
        if native and cpu == gpu == 0:
            return True
        claimed = []
        if native:
            for fan in self._native_fans[0].fans()[:2]:
                if not self._claim_native_fan(fan["id"], "manual", "legacy-paired-api"):
                    for fan_id in claimed:
                        self._release_native_fan(fan_id)
                    return False
                claimed.append(fan["id"])
        written = self._write_fan_speed(cpu, gpu)
        if not written and native:
            self._native_curve_restore_auto()
        return written

    def get_fans(self):
        result = []
        for backend in self._native_fans:
            result.extend(backend.fans())
        if self.sense_base and "fan_control" in self.features:
            speeds = self.get_fan_speed()
            for index, label in enumerate(("CPU Fan", "GPU Fan")):
                result.append({"id": f"linuwu:{index + 1}", "index": index + 1,
                               "label": label, "backend": "linuwu_paired",
                               "modes": ["auto", "manual"], "minimum_percent": 0,
                               "maximum_percent": 100, "duty_percent": speeds[index]})
        return result

    def set_fan_device(self, fan_id, percent, owner="authorized-user"):
        if not isinstance(fan_id, str) or type(percent) is not int or not 0 <= percent <= 100:
            return {"success": False, "error": "Invalid fan request"}
        for backend in self._native_fans:
            for fan in backend.fans():
                if fan["id"] == fan_id:
                    native_engine = getattr(self, "_native_curve_engine", None)
                    if native_engine is not None and not native_engine.stop(fan_id):
                        return {"success": False, "error": "Could not stop active curve and restore Auto"}
                    if not self._claim_native_fan(fan_id, "manual", owner):
                        return {"success": False, "error": "Could not persist fan ownership before write"}
                    ok = backend.set_manual(fan["index"], percent)
                    if not ok and native_engine is not None:
                        native_engine.stop()
                    if not ok:
                        self._release_native_fan(fan_id)
                    return {"success": ok, "data": {"fan_id": fan_id, "percent": percent} if ok else None,
                            "error": None if ok else "Native Acer fan write/readback failed"}
        if fan_id in ("linuwu:1", "linuwu:2"):
            if not self.restore_firmware_auto().get("success"):
                return {"success": False, "error": "Could not restore firmware automatic mode"}
            current = self.get_fan_speed()
            if None in current:
                return {"success": False, "error": "Paired fan interface unavailable"}
            values = [*current]
            values[int(fan_id[-1]) - 1] = percent
            ok = self._write_fan_speed(*values)
            return {"success": ok, "data": {"fan_id": fan_id, "percent": percent} if ok else None}
        return {"success": False, "error": "Fan device disappeared or is not writable"}

    def set_fan_mode(self, fan_id, mode, owner="authorized-user"):
        if not isinstance(fan_id, str) or mode not in ("auto", "turbo"):
            return {"success": False, "error": "Invalid fan mode request"}
        for backend in self._native_fans:
            for fan in backend.fans():
                if fan["id"] == fan_id:
                    native_engine = getattr(self, "_native_curve_engine", None)
                    if native_engine is not None and not native_engine.stop(fan_id):
                        return {"success": False, "error": "Could not stop active curve and restore Auto"}
                    if mode == "turbo" and not self._claim_native_fan(
                            fan_id, "turbo", owner):
                        return {"success": False, "error": "Could not persist fan ownership before write"}
                    ok = backend.set_mode(fan["index"], mode)
                    if not ok and native_engine is not None:
                        native_engine.stop()
                    if not ok:
                        self._release_native_fan(fan_id)
                    return {"success": ok,
                            "data": {"fan_id": fan_id, "mode": mode} if ok else None,
                            "error": None if ok else "Native Acer mode write failed"}
        if mode == "auto" and fan_id in ("linuwu:1", "linuwu:2"):
            return self.restore_firmware_auto()
        return {"success": False, "error": "Fan mode is not supported"}

    def restore_firmware_auto(self):
        stopped = self._fan_curve_engine.stop()
        native_engine = getattr(self, "_native_curve_engine", None)
        native_curves = native_engine.stop() if native_engine is not None else True
        native = self._native_curve_restore_auto() if self._native_fans else True
        ok = bool(stopped or not self.sense_base) and bool(native_curves or not self._native_fans) and native
        if ok:
            self._disable_saved_curves()
        return {"success": ok, "data": {"mode": "auto"} if ok else None,
                "error": None if ok else "Firmware automatic fan restoration failed"}

    # --- Battery Features ---
    def _battery_calibration_path(self):
        for path in (self._sense_path("battery_calibration"), BATTERY_CALIBRATION_PATH):
            if path and read_sysfs(path) in ("0", "1"):
                return path
        return None

    def get_battery_calibration(self):
        path = self._battery_calibration_path()
        if not path:
            return None
        return parse_binary_readback(read_sysfs(path))

    def set_battery_calibration(self, enabled):
        if enabled and self.get_power_source() is not True:
            return False
        path = self._battery_calibration_path()
        if not path and not enabled and "battery_calibration" in getattr(self, "features", []):
            # Stopping stays possible while the status is unrecognized; starting does not.
            path = next((p for p in (self._sense_path("battery_calibration"), BATTERY_CALIBRATION_PATH)
                         if p and os.path.isfile(p)), None)
        if not path:
            return False
        return write_sysfs(path, "1" if enabled else "0")

    def _battery_limit_path(self):
        for battery in battery_supply_paths():
            path = battery / "charge_control_end_threshold"
            if read_sysfs(path) is not None:
                return str(path)
        for path in (self._sense_path("battery_limiter"), BATTERY_HEALTH_PATH):
            if path and read_sysfs(path) is not None:
                return path
        return None

    def get_battery_limiter(self):
        path = self._battery_limit_path()
        val = read_sysfs(path)
        if val is None:
            return None
        if path.endswith("charge_control_end_threshold"):
            try:
                return int(val) <= 80
            except ValueError:
                return None
        return val == "1" if val in ("0", "1") else None

    def set_battery_limiter(self, enabled):
        path = self._battery_limit_path()
        if not path:
            return False
        # Lock together with the boot/device-event helper so a stale event cannot
        # replace a newly selected policy. Failed persistence is surfaced to D-Bus.
        policy = Path(self.settings.path).with_name("battery.json")
        with archer_battery.policy_lock(policy):
            value = ("80" if enabled else "100") if path.endswith("charge_control_end_threshold") else ("1" if enabled else "0")
            if not write_sysfs(path, value) or read_sysfs(path) != value:
                return False
            archer_battery.write_policy(bool(enabled), policy)
        return True

    def get_usb_charging(self):
        path = self._sense_path("usb_charging")
        if not path:
            return None
        return read_sysfs(path)

    def set_usb_charging(self, level):
        try:
            validate("usb_charging", level)
        except ValueError:
            return False
        path = self._sense_path("usb_charging")
        if not path:
            return False
        return write_sysfs(path, str(level))

    def get_battery_info(self):
        """Compatibility aggregate; detailed clients use get_battery_devices()."""
        batteries = self.get_battery_devices()
        if not batteries:
            return {"present": False, "percentage": None, "status": "Unknown", "time_remaining": ""}
        available = [b for b in batteries if b.get("present")]
        if not available:
            return {"present": False, "percentage": None, "status": "Unknown", "time_remaining": ""}
        percentage = None
        if len(available) == 1:
            percentage = available[0].get("measurements", {}).get(
                "capacity", {}).get("value")
        else:
            for prefix in ("energy", "charge"):
                now_key, full_key = prefix + "_now", prefix + "_full"
                if all(all((
                    now_key in battery.get("measurements", {}),
                    full_key in battery.get("measurements", {}),
                )) for battery in available):
                    now = sum(battery["measurements"][now_key]["value"]
                              for battery in available)
                    full = sum(battery["measurements"][full_key]["value"]
                               for battery in available)
                    percentage = round(now * 100 / full) if full > 0 else None
                    break
        seconds = [b["time_remaining_seconds"] for b in available if b.get("time_remaining_seconds") is not None]
        status = available[0]["status"].title() if len({b["status"] for b in available}) == 1 else "Mixed"
        total = sum(seconds) if len(seconds) == len(available) else None
        return {"present": True, "percentage": percentage,
                "status": status, "time_remaining": f"{total // 3600}h {(total % 3600) // 60}m" if total is not None else "",
                "battery_count": len(available)}

    def get_battery_devices(self):
        inventory = getattr(self, "_inventory", {"devices": []})
        key_map = {item.get("node"): item.get("key") for item in inventory.get("devices", [])
                   if item.get("kind") == "power_supply"}
        return archer_battery.discover(Path("/sys"), key_map)

    def _battery_hardware_key(self, device_key):
        matches = [d for d in getattr(self, "_inventory", {}).get("devices", [])
                   if d.get("kind") == "power_supply" and d.get("key") == device_key and d.get("identity_unique", True)]
        return "power_supply:" + matches[0]["node"] if len(matches) == 1 else None

    @staticmethod
    def _charge_owner_conflicts():
        owners = archer_policy.owners_by_control(archer_policy.discover_providers(runner=run_command))
        return owners.get("charge_threshold", [])

    def set_battery_threshold(self, device_key, start, end):
        self._inventory = self._collect_inventory()
        conflicts = self._charge_owner_conflicts()
        if conflicts:
            return {"success": False, "error": "charge_threshold_owned", "owners": conflicts}
        hardware_key = self._battery_hardware_key(device_key)
        if not hardware_key:
            return {"success": False, "error": "Battery device disappeared"}
        policy = Path(self.settings.path).with_name("battery.json")
        with archer_battery.policy_lock(policy):
            data = archer_battery.read_policy_data(policy)
            previous = copy.deepcopy(data)
            data["batteries"].setdefault(device_key, {}).update(
                {"start": start, "end": end, "owner": "archer", "node": hardware_key.split(':', 1)[1]})
            archer_battery.write_policy_data(data, policy)
            if not archer_battery.set_threshold(Path("/sys"), hardware_key, start, end):
                archer_battery.write_policy_data(previous, policy)
                return {"success": False, "error": "Threshold is invalid, read-only, or the device disappeared"}
        return {"success": True, "data": {"device_key": device_key, "start": start, "end": end}}

    def charge_full_once(self, device_key, duration):
        if type(duration) is not int or not 300 <= duration <= 172800:
            return {"success": False, "error": "Duration must be between 300 and 172800 seconds"}
        self._inventory = self._collect_inventory()
        conflicts = self._charge_owner_conflicts()
        if conflicts:
            return {"success": False, "error": "charge_threshold_owned", "owners": conflicts}
        batteries = {item["device_key"]: item for item in self.get_battery_devices()}
        item = batteries.get(device_key)
        control = item.get("charge_control", {}) if item else {}
        if control.get("kind") != "variable_threshold" or not control.get("writable") or \
                control.get("constraints", {}).get("maximum") != 100:
            return {"success": False, "error": "Temporary full charge is unavailable for this battery"}
        original = control.get("end")
        hardware_key = self._battery_hardware_key(device_key)
        expires = time.time() + duration
        policy = Path(self.settings.path).with_name("battery.json")
        with archer_battery.policy_lock(policy):
            data = archer_battery.read_policy_data(policy)
            previous = copy.deepcopy(data)
            saved = data["batteries"].setdefault(device_key, {})
            saved.setdefault("end", original)
            saved.update(owner="archer", node=hardware_key.split(':', 1)[1])
            saved["full_override"] = {"expires_at": expires, "original_end": original,
                                      "owner": "archer", "last_written": 100}
            archer_battery.write_policy_data(data, policy)
            if not archer_battery.set_threshold(Path("/sys"), hardware_key, control.get("start"), 100):
                archer_battery.write_policy_data(previous, policy)
                return {"success": False, "error": "Could not apply temporary full charge"}
        return {"success": True, "data": {
            "device_key": device_key, "expires_at": expires,
            "original_end": original}}

    def _expire_battery_overrides(self):
        policy = Path(self.settings.path).with_name("battery.json")
        try:
            with archer_battery.policy_lock(policy):
                data = archer_battery.read_policy_data(policy)
                changed = False
                conflicts = self._charge_owner_conflicts()
                if conflicts:
                    return
                current_devices = {b["device_key"]: b for b in self.get_battery_devices()}
                for key, saved in data["batteries"].items():
                    override = saved.get("full_override")
                    if not isinstance(override, dict) or float(override.get("expires_at", 0)) > time.time():
                        continue
                    current = current_devices.get(key, {})
                    control = current.get("charge_control", {})
                    # Conditional restore: do not overwrite an external change.
                    if control.get("end") == override.get("last_written"):
                        restored = archer_battery.set_threshold(
                            Path("/sys"), "power_supply:" + saved.get("node", current.get("node", "")),
                            control.get("start"), int(override.get("original_end", saved.get("end", 100))))
                        if not restored:
                            continue
                    saved.pop("full_override", None)
                    changed = True
                if changed:
                    archer_battery.write_policy_data(data, policy)
        except (OSError, ValueError, TypeError):
            return

    # --- Keyboard Lighting ---
    # Both setters prefer the ENE backend and keep the sysfs/WMI write as a
    # fallback. Note the sysfs path is not merely less capable: on PHN16S-71 it
    # reports success and reads back the exact colours it was given while the
    # LEDs never change, which is why it cannot be trusted as verification.

    def _ene_call(self, operation, *values):
        if not hasattr(self, "_ene_lock"):
            self._ene_lock = threading.Lock()
        with self._ene_lock:
            result = run_command([sys.executable, str(Path(__file__).with_name("archer_ene.py")),
                                  operation, json.dumps(values)], timeout=30)
        if not result.success:
            logger.error("Lighting operation failed: %s", result.stderr or result.stdout)
            return False
        try:
            return bool(json.loads(result.stdout).get("success"))
        except (ValueError, AttributeError):
            return False

    def keyboard_effects(self):
        ids = EFFECT_IDS if getattr(self, "ene_ready", False) else WMI_EFFECT_IDS
        return [{"id": key, "label": EFFECT_LABELS[EFFECT_IDS.index(key)]} for key in ids]

    def effect_id(self, value):
        ids = EFFECT_IDS if getattr(self, "ene_ready", False) else WMI_EFFECT_IDS
        if isinstance(value, str):
            return value if value in ids else None
        # Compatibility for old D-Bus clients: integer indices use the active backend's ABI.
        return ids[value] if isinstance(value, Integral) and not isinstance(value, bool) and 0 <= value < len(ids) else None

    def set_per_zone_mode(self, zone1, zone2, zone3, zone4, brightness):
        try:
            validate("per_zone_mode", dict(zip(("zone1", "zone2", "zone3", "zone4", "brightness"),
                                               (zone1, zone2, zone3, zone4, brightness))))
        except ValueError:
            return False
        if getattr(self, "ene_ready", False):
            return self._ene_call("set_per_zone", zone1, zone2, zone3, zone4, brightness)
        path = self._driver_path("four_zoned_kb/per_zone_mode")
        return bool(path) and write_sysfs(path, f"{zone1},{zone2},{zone3},{zone4},{brightness}")

    def poll_profile_led(self):
        profile = self.get_thermal_profile()
        if profile and profile != getattr(self, "_led_profile", None):
            if self._sync_button_led(profile):
                self._led_profile = profile
        was_ready = getattr(self, "ene_ready", False)
        ready = bool(archer_ene and archer_ene.available())
        if ready != was_ready:
            self._detect_features()
            if self.driver_status == "auto_restore_failed" and "fan_control" in self.features:
                self.features.remove("fan_control")
            if ready:
                self._lighting_retries = 5
        if getattr(self, "_lighting_retries", 0):
            self._lighting_retries -= 1
            if ready and self.reapply_lighting():
                self._lighting_retries = 0
            elif not self._lighting_retries:
                logger.error("Lighting could not be restored after five attempts; check HID enumeration and retry in Keyboard")
        return True

    def reapply_lighting(self):
        # Called on a bounded background worker. Rediscovery is repeated on resume.
        self.ene_ready = bool(archer_ene and archer_ene.available())
        if not self.ene_ready:
            return False
        if self.settings.get("last_keyboard_mode") == "effect":
            effect = self.settings.get("four_zone_mode")
            if effect and not self.set_four_zone_mode(effect.get("effect", effect.get("mode")), effect["speed"],
                                                      effect["brightness"], effect["direction"], effect["red"], effect["green"], effect["blue"]):
                return False
        else:
            zones = self.settings.get("per_zone_mode")
            if zones and not self.set_per_zone_mode(zones["zone1"], zones["zone2"], zones["zone3"], zones["zone4"], zones["brightness"]):
                return False
        profile = self.get_thermal_profile()
        if profile and not self._sync_button_led(profile):
            return False
        logger.info("Saved lighting restored")
        return True

    def set_four_zone_mode(self, mode, speed, brightness, direction, r, g, b):
        effect = self.effect_id(mode)
        if not effect:
            return False
        try:
            validate("four_zone_mode", {"effect": effect, "speed": speed, "brightness": brightness,
                                        "direction": direction, "red": r, "green": g, "blue": b})
        except ValueError:
            return False
        if getattr(self, "ene_ready", False):
            return self._ene_call("set_effect", EFFECT_IDS.index(effect), brightness, r, g, b, speed, direction)
        path = self._driver_path("four_zoned_kb/four_zone_mode")
        wire_mode = WMI_EFFECT_IDS.index(effect)
        if effect in ("wave", "shifting") and direction == 0:
            return False
        return bool(path) and write_sysfs(path, f"{wire_mode},{speed},{brightness},{direction},{r},{g},{b}")

    # --- LCD override, boot sound and backlight timeout ---
    def _binary_control(self, key):
        # Created lazily (and once) so partially constructed managers in tests and
        # concurrent D-Bus readers share one owner per control.
        with _BINARY_CONTROLS_LOCK:
            controls = self.__dict__.setdefault("_binary_controls", {})
            if key not in controls:
                controls[key] = BinaryControl(key, lambda: self._sense_path(key), lambda: self.settings,
                                              BINARY_CONTROLS[key])
            return controls[key]

    def binary_state(self, key, refresh=True):
        return self._binary_control(key).snapshot(refresh)

    def apply_binary(self, key, value):
        """User request: write the explicit value, save it, verify once and report each outcome."""
        return self._binary_control(key).apply(value, persist=True, origin="user")

    def note_resume(self):
        for key in BINARY_CONTROLS:
            self._binary_control(key).note_resume()

    def get_lcd_override(self):
        """Legacy scalar: the resolved effective value, or None when unknown."""
        return self.binary_state("lcd_override")["effective"]

    def get_boot_animation_sound(self):
        return self.binary_state("boot_animation_sound")["effective"]

    def get_backlight_timeout(self):
        return self.binary_state("backlight_timeout")["effective"]

    # --- System Monitoring ---
    def get_cpu_temp(self):
        return self._read_cpu_temp() or 0

    @staticmethod
    def _temperature(path):
        value = read_sysfs(path)
        try:
            value = int(value) / 1000
            return int(value) if 0 < value <= 125 else None
        except (TypeError, ValueError):
            return None

    def _read_cpu_temp(self):
        """Use identified CPU sensors; an arbitrary ACPI zone is unsafe for curves."""
        values = []
        for hwmon in sorted(Path(HWMON_DIR).glob("hwmon*")):
            name = read_sysfs(hwmon / "name")
            if name in ("k10temp", "zenpower", "coretemp", "acer", "acer_wmi", "linuwu_sense"):
                values.append(self._temperature(hwmon / "temp1_input"))
        for tz in sorted(Path(THERMAL_DIR).glob("thermal_zone*")):
            name = read_sysfs(tz / "type") or ""
            if any(k in name.lower() for k in ("x86_pkg", "coretemp", "k10temp", "cpu")):
                values.append(self._temperature(tz / "temp"))
        return max((v for v in values if v is not None), default=None)

    def get_gpu_temp(self):
        return self._read_gpu_temp() or 0

    def _active_nvidia_sample(self):
        gpu = [d for d in getattr(self, "_inventory", {}).get("devices", [])
               if d["kind"] == "pci" and d["attributes"].get("class", "").startswith("0x03")
               and d["attributes"].get("vendor") == "0x10de"]
        if not gpu:
            return None, None
        for card in gpu:
            if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", card["node"]):
                return None, None
            device = Path("/sys/bus/pci/devices") / card["node"]
            if card["driver"] != "nvidia" or read_sysfs(device / "power/runtime_status") != "active":
                return None, None

        def query():
            reply = run_command(["/usr/bin/nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
                                 "--format=csv,noheader,nounits"], 3)
            rows = [line.split(",") for line in reply.stdout.splitlines()] if reply.success else []
            temp = [int(t) for t, _ in (r for r in rows if len(r) == 2) if t.strip().isdigit() and 0 < int(t) <= 125]
            usage = [int(u) for _, u in (r for r in rows if len(r) == 2) if u.strip().isdigit() and 0 <= int(u) <= 100]
            return max(temp, default=None), max(usage, default=None)

        return _PROBE_CACHE.get_or_compute(("active-nvidia", tuple(g["key"] for g in gpu)), query)

    def _read_gpu_temp(self):
        """Prefer discrete/Nitro GPU readings over the AMD integrated GPU."""
        discrete, integrated = [], []
        for hwmon in sorted(Path(HWMON_DIR).glob("hwmon*")):
            name = read_sysfs(hwmon / "name")
            native_paths = {backend.path for backend in getattr(self, "_native_fans", [])}
            if name in ("nvidia", "nouveau", "amdgpu") and read_sysfs(hwmon / "device/power/runtime_status") in ("suspending", "suspended"):
                continue
            if name == "acer_wmi" or (name == "acer" and hwmon in native_paths):
                # Native acer-wmi mapping: CPU, GPU, external.
                discrete.append(self._temperature(hwmon / "temp2_input"))
            elif name == "linuwu_sense" or (name == "acer" and getattr(self, "sense_base", None)):
                # Linuwu's v4 mapping: CPU, external, GPU.
                discrete.append(self._temperature(hwmon / "temp3_input"))
            elif name in ("nvidia", "nouveau"):
                discrete.append(self._temperature(hwmon / "temp1_input"))
            elif name == "amdgpu":
                integrated.append(self._temperature(hwmon / "temp1_input"))
        if not any(v is not None for v in discrete):
            temp, _ = self._active_nvidia_sample()
            if temp is not None:
                discrete.append(temp)
        readings = [v for v in discrete if v is not None]
        # On a hybrid machine an unavailable NVIDIA sensor must not be replaced
        # by the cooler iGPU. Curves then fail closed through the watchdog.
        if readings:
            return max(readings)
        if Path("/sys/module/nvidia").is_dir() or Path("/sys/module/nouveau").is_dir():
            return None
        return max((v for v in integrated if v is not None), default=None)

    def get_cpu_usage(self):
        return self._read_cpu_usage() or 0

    def _read_cpu_usage(self):
        """Interval CPU time; guest counters are already included in user/nice."""
        try:
            fields = Path("/proc/stat").read_text().splitlines()[0].split()
            if fields[0] != "cpu" or len(fields) < 5:
                return None
            counters = [int(value) for value in fields[1:9]]
            total = sum(counters)
            idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
            if not hasattr(self, "_cpu_sample_lock"):
                self._cpu_sample_lock = threading.Lock()
            with self._cpu_sample_lock:
                previous = getattr(self, "_cpu_sample", None)
                self._cpu_sample = total, idle
            if previous is None or total <= previous[0] or idle < previous[1]:
                return None
            return round(max(0, min(100, 100 * (1 - (idle - previous[1]) / (total - previous[0])))))
        except (OSError, ValueError, IndexError):
            return None

    def get_gpu_usage(self):
        return self._read_gpu_usage() or 0

    def _read_gpu_usage(self):
        """Get GPU usage from nvidia-smi or amdgpu."""
        _, value = self._active_nvidia_sample()
        if value is not None:
            return value
        for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
            if read_sysfs(hwmon / "name") == "amdgpu" and read_sysfs(hwmon / "device/power/runtime_status") == "active":
                raw = read_sysfs(hwmon / "device/gpu_busy_percent")
                if raw and raw.isdigit() and 0 <= int(raw) <= 100:
                    return int(raw)
        return None

    def get_fan_rpm(self):
        return tuple(value or 0 for value in self._read_fan_rpm())

    def _read_fan_rpm(self):
        """Get fan RPM from hwmon. Prefer Acer-relevant chipsets by name."""
        cpu_rpm, gpu_rpm = None, None
        # Pass 1: allowlisted chipsets only.
        for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            name = read_sysfs(hwmon / "name") or ""
            if name.lower() not in _HWMON_FAN_NAMES:
                continue
            fan1 = read_sysfs(hwmon / "fan1_input")
            fan2 = read_sysfs(hwmon / "fan2_input")
            if fan1:
                cpu_rpm = int(fan1)
            if fan2:
                gpu_rpm = int(fan2)
            if cpu_rpm or gpu_rpm:
                return cpu_rpm, gpu_rpm
        # Pass 2 (fallback): the legacy "first device with fan1_input" rule.
        for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            fan1 = read_sysfs(hwmon / "fan1_input")
            fan2 = read_sysfs(hwmon / "fan2_input")
            if fan1:
                cpu_rpm = int(fan1)
            if fan2:
                gpu_rpm = int(fan2)
            if cpu_rpm or gpu_rpm:
                break
        return cpu_rpm, gpu_rpm

    def get_power_source(self):
        """True only for verified external power; None means unavailable."""
        states = []
        for supply in sysfs_children(POWER_SUPPLY_DIR):
            kind = (read_sysfs(supply / "type") or "").lower()
            if kind == "mains" or kind.startswith("usb") or kind == "wireless":
                online = read_sysfs(supply / "online")
                if online in ("0", "1"):
                    states.append(online == "1")
        if any(states):
            return True
        statuses = [read_sysfs(battery / "status") for battery in battery_supply_paths()]
        if "Charging" in statuses:
            return True
        if "Discharging" in statuses or states:
            return False
        return None

    def get_system_info(self):
        """Get general system information."""
        product = read_sysfs(DMI_PRODUCT) or "Unknown"
        vendor = read_sysfs(DMI_VENDOR) or "Unknown"
        cpu_model = ""
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_model = line.split(":")[1].strip()
                        break
        except OSError:
            pass
        gpus = archer_graphics.graphics_devices(getattr(self, "_inventory", {}))
        gpu_model = ", ".join(f'{g["vendor_id"]}:{g["device_id"]} ({g["driver"] or "unbound"})' for g in gpus)
        driver_version = read_sysfs(
            os.path.join(self.driver_base, "version") if self.driver_base else "/dev/null"
        ) or "N/A"
        return {
            "product_name": product,
            "vendor": vendor,
            "cpu_model": cpu_model,
            "gpu_model": gpu_model,
            "laptop_type": self.laptop_type,
            "driver_version": driver_version,
            "daemon_version": VERSION,
            "kernel": os.uname().release,
        }

    def get_all_settings(self):
        """Get all current settings for the GUI to load."""
        cpu_fan, gpu_fan = self.get_fan_speed()
        cpu_rpm, gpu_rpm = self.get_fan_rpm()
        binary = {key: self.binary_state(key) for key in BINARY_CONTROLS}
        return {
            "laptop_type": self.laptop_type,
            "features": self.features,
            "daemon_version": VERSION,
            "thermal_profile": self.get_thermal_profile(),
            "thermal_choices": self.get_thermal_profile_choices(),
            "fan_speed_cpu": cpu_fan,
            "fan_speed_gpu": gpu_fan,
            "fan_rpm_cpu": cpu_rpm,
            "fan_rpm_gpu": gpu_rpm,
            "battery_calibration": self.get_battery_calibration(),
            "battery_limiter": self.get_battery_limiter(),
            "usb_charging": self.get_usb_charging(),
            # Scalars stay boolean/null for older clients and equal "effective";
            # the *_state objects separate saved, requested and observed values.
            **{key: state["effective"] for key, state in binary.items()},
            **{f"{key}_state": state for key, state in binary.items()},
            "battery_info": self.get_battery_info(),
            "power_source_ac": self.get_power_source(),
            "system_info": self.get_system_info(),
            "fan_curve": self.get_fan_curve_state(),
            "display_mode": self.get_display_mode() if "display_mode" in self.features else None,
            "game_mode": self.get_game_mode(),
            "audio_enhancement": self.get_audio_enhancement(),
            "keyboard_effects": self.keyboard_effects(),
            "keyboard_migration_required": self.settings.get("keyboard_migration_required", False),
            "firmware_info": self.get_firmware_info(check=False),
            "driver_status": self.driver_status,
            "sense_base": self.sense_base,
            "driver_override": read_sysfs("/etc/modprobe.d/linuwu-sense.conf") or "Disabled",
            "qualified_driver_overrides": qualified_driver_overrides(
                read_sysfs(DMI_PRODUCT), read_sysfs(DMI_BOARD)),
            "recovery": self.get_recovery_status(),
            "policy": self.get_policy_status(),
            "capability_schema_version": 1,
            "inventory_generation": self._inventory.get("generation"),
            "saved_settings": self.settings.data,
        }

    def get_policy_status(self):
        providers = archer_policy.discover_providers(runner=run_command)
        return {"schema_version": 1, "providers": providers,
                "owners": archer_policy.owners_by_control(providers),
                "game_mode": self.get_game_mode()}

    def get_capabilities(self):
        generation = self._inventory.get("generation", "unavailable")
        items = []
        profile_path, _ = platform_profile_paths()
        items.append(capability("system.platform_profile", "platform-profile", "kernel_platform_profile",
                                availability="available" if profile_path else "absent",
                                readable=bool(profile_path), writable=bool(profile_path and os.access(profile_path, os.W_OK)),
                                value_kind="enum", constraints={"choices": self.get_thermal_profile_choices()},
                                owner=(archer_policy.owners_by_control(archer_policy.discover_providers())
                                       .get("platform_profile", ["archer"])[0]), generation=generation))
        for backend in self._native_fans:
            sensor_ids = [sensor["id"] for sensor in backend.temperatures()]
            for fan in backend.fans():
                items.append(capability("fan." + fan["id"] + ".duty", fan["id"], backend.backend,
                                        readable=True, writable=True, value_kind="integer",
                                        constraints={"minimum": fan["minimum_percent"],
                                                     "maximum": fan["maximum_percent"], "step": 1,
                                                     "modes": fan["modes"]}, generation=generation,
                                        validation="automated-tested"))
                items.append(capability("fan." + fan["id"] + ".curve", fan["id"], backend.backend,
                                        readable=True, writable=bool(sensor_ids), value_kind="object",
                                        constraints={"sensor_ids": sensor_ids, "minimum_percent": 30,
                                                     "maximum_percent": 100, "minimum_duration_seconds": 60,
                                                     "maximum_duration_seconds": 14400,
                                                     "maximum_points": 16}, generation=generation,
                                        reason_code=None if sensor_ids else "temperature_sensor_absent",
                                        validation="automated-tested"))
        for battery in self.get_battery_devices():
            control = battery["charge_control"]
            items.append(capability("battery." + battery["device_key"] + ".charge_limit",
                                    battery["device_key"], "power_supply_threshold",
                                    availability="available" if control["kind"] != "none" else "absent",
                                    readable=control.get("readable", False), writable=control.get("writable", False),
                                    value_kind="integer", constraints=control.get("constraints", {}),
                                    generation=generation))
        fixed_limit = self._battery_limit_path()
        if fixed_limit and not fixed_limit.endswith("charge_control_end_threshold"):
            items.append(capability(
                "battery.controller.health_mode", "acer-battery-controller",
                "fixed_health_mode", readable=True,
                writable=os.access(fixed_limit, os.W_OK), value_kind="boolean",
                constraints={"presets": [False, True]}, generation=generation,
                validation="automated-tested"))
        for key in BINARY_CONTROLS:
            present = key in self.features
            items.append(capability("system." + key, "acer-controller", "linuwu_binary",
                                    availability="available" if present else "absent",
                                    readable=present, writable=present, value_kind="boolean",
                                    generation=generation, reason_code=None if present else "interface_absent"))
        lighting = "ene_k5130" if getattr(self, "ene_ready", False) else "linuwu_wmi"
        present = any(feature in self.features for feature in ("keyboard_per_zone", "keyboard_effects"))
        items.append(capability("keyboard.lighting", "keyboard-controller", lighting,
                                availability="available" if present else "absent", readable=False,
                                writable=present, value_kind="object", generation=generation,
                                validation="hardware-verified" if getattr(self, "ene_ready", False) else "discovered",
                                reason_code=None if present else "qualified_controller_absent"))
        return {"schema_version": 1, "inventory_generation": generation, "capabilities": items}

    def get_monitoring_data(self):
        """Preserve numeric fields and add explicit sensor validity metadata."""
        cpu_rpm, gpu_rpm = self._read_fan_rpm()
        readings = {
            "cpu_temp": self._read_cpu_temp(), "gpu_temp": self._read_gpu_temp(),
            "cpu_usage": self._read_cpu_usage(), "gpu_usage": self._read_gpu_usage(),
            "fan_rpm_cpu": cpu_rpm, "fan_rpm_gpu": gpu_rpm,
        }
        now = time.time()
        telemetry = {
            **{key: value if value is not None else 0 for key, value in readings.items()},
            "metric_validity": {key: value is not None for key, value in readings.items()},
            "metrics": [metric("system", key, value, "celsius" if key.endswith("temp") else
                               "rpm" if key.startswith("fan_rpm") else "percent", "daemon",
                               valid=value is not None, reason=None if value is not None else "unavailable",
                               timestamp=now) for key, value in readings.items()],
            "battery_info": self.get_battery_info(),
            "batteries": self.get_battery_devices(),
            "power_source_ac": self.get_power_source(),
            "thermal_profile": self.get_thermal_profile(),
            "keyboard_backend": "ene" if getattr(self, "ene_ready", False) else "wmi",
            "fan_curves": (self._native_curve_engine.get_state()
                           if hasattr(self, "_native_curve_engine") else {}),
        }
        for backend in getattr(self, "_native_fans", []):
            telemetry.setdefault("fans", []).extend(backend.fans())
            telemetry.setdefault("temperatures", []).extend(backend.temperatures())
        return telemetry

    # --- Fan Curve Methods ---
    def _fan_curve_get_temp(self, target, sensor_id=None):
        return self._read_cpu_temp() if target == "cpu" else self._read_gpu_temp()

    def _fan_curve_set_fan(self, target, pct):
        with self._fan_lock:
            cpu, gpu = self.get_fan_speed()
            if cpu is None or gpu is None:
                raise OSError("Paired fan interface unavailable")
            ok = self._write_fan_speed(int(pct) if target == "cpu" else cpu,
                                       int(pct) if target == "gpu" else gpu)
            if not ok:
                raise OSError("Fan curve write failed")
            return True

    def _fan_curve_restore_auto(self):
        path = self._sense_path("fan_speed")
        if "fan_control" not in self.features:
            return False
        if not path and self._native_fans:
            restored = self._native_curve_restore_auto()
            if not restored:
                logger.error("AUTOMATIC FAN RESTORATION FAILED for native Acer hwmon")
            return restored
        if not path:
            return False
        with self._fan_lock:
            if not write_sysfs(path, "0,0"):
                logger.error("AUTOMATIC FAN RESTORATION FAILED at %s; stop manual control and reboot", path)
                return False
            return True

    def _native_fan(self, fan_id):
        for backend in self._native_fans:
            for fan in backend.fans():
                if fan["id"] == fan_id:
                    return backend, fan
        return None, None

    def _native_owners(self):
        if not hasattr(self, "settings"):
            return {}
        value = self.settings.get("native_fan_owners", {})
        if not isinstance(value, dict):
            return {}
        return {key: item for key, item in value.items()
                if isinstance(key, str) and isinstance(item, dict)}

    def _save_native_owners(self, owners):
        if not hasattr(self, "settings"):
            return not owners
        try:
            if owners:
                self.settings.set("native_fan_owners", owners)
            else:
                self.settings.remove("native_fan_owners")
            return True
        except OSError as error:
            logger.error("Could not persist native fan ownership: %s", error)
            return False

    def _claim_native_fan(self, fan_id, mode, owner):
        owners = self._native_owners()
        owners[fan_id] = {"mode": mode, "owner": owner, "claimed_at": time.time()}
        return self._save_native_owners(owners)

    def _release_native_fan(self, fan_id):
        owners = self._native_owners()
        owners.pop(fan_id, None)
        return self._save_native_owners(owners)

    def _restore_owned_native_fans(self):
        owners = self._native_owners()
        if not owners:
            return True
        remaining = dict(owners)
        restored = True
        for fan_id in owners:
            backend, fan = self._native_fan(fan_id)
            if not backend or not fan:
                remaining.pop(fan_id, None)
                continue
            if backend.set_auto(fan["index"]):
                remaining.pop(fan_id, None)
            else:
                restored = False
        return self._save_native_owners(remaining) and restored

    def _native_sensor(self, sensor_id, backend=None):
        sources = [backend] if backend is not None else self._native_fans
        for candidate in sources:
            for sensor in candidate.temperatures():
                if sensor["id"] == sensor_id:
                    return candidate, sensor
        return None, None

    def _native_curve_get_temp(self, fan_id, sensor_id):
        backend, fan = self._native_fan(fan_id)
        if not backend or not fan:
            return None
        sensor_backend, _ = self._native_sensor(sensor_id, backend)
        return sensor_backend.temperature(sensor_id) if sensor_backend else None

    def _native_curve_set_fan(self, fan_id, percent):
        backend, fan = self._native_fan(fan_id)
        if not backend or not fan:
            return False
        with self._fan_lock:
            return backend.set_manual(fan["index"], int(percent))

    def _native_curve_restore_auto(self, fan_id=None):
        restored = True
        found = False
        with self._fan_lock:
            for backend in self._native_fans:
                if fan_id is None:
                    found = True
                    for fan in backend.fans():
                        ok = backend.set_auto(fan["index"])
                        released = self._release_native_fan(fan["id"]) if ok else False
                        restored = ok and released and restored
                    continue
                for fan in backend.fans():
                    if fan["id"] == fan_id:
                        found = True
                        ok = backend.set_auto(fan["index"])
                        released = self._release_native_fan(fan_id) if ok else False
                        restored = ok and released and restored
        return found and restored

    @staticmethod
    def _valid_curve(points):
        if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= 16:
            return False
        previous_temp = -1
        previous_percent = 0
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return False
            temp, percent = point
            for value in point:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return False
                if not math.isfinite(value):
                    return False
            if not 20 <= temp <= 110 or not 30 <= percent <= 100:
                return False
            if temp <= previous_temp or percent < previous_percent:
                return False
            previous_temp, previous_percent = temp, percent
        return True

    def start_native_fan_curve(self, fan_id, sensor_id, points, duration_seconds,
                               *, owner="authorized-user", resume_after_suspend=True):
        if "native_fan_control" not in self.features:
            return {"success": False, "error": "Invalid native fan curve request"}
        if not isinstance(fan_id, str) or not isinstance(sensor_id, str):
            return {"success": False, "error": "Invalid native fan curve request"}
        if type(duration_seconds) is not int or not 60 <= duration_seconds <= 14400:
            return {"success": False, "error": "Invalid native fan curve request"}
        if type(resume_after_suspend) is not bool or not self._valid_curve(points):
            return {"success": False, "error": "Invalid native fan curve request"}
        backend, fan = self._native_fan(fan_id)
        sensor_backend, sensor = self._native_sensor(sensor_id, backend) if backend else (None, None)
        if not fan or not sensor or sensor_backend is not backend:
            return {"success": False, "error": "Selected fan or sensor disappeared"}
        sample = backend.temperature(sensor_id)
        if not sample or time.time() - sample["sampled_at"] > 3:
            return {"success": False, "error": "Selected temperature sensor is unavailable or stale"}
        if not self._native_curve_engine.stop(fan_id):
            return {"success": False, "error": "Could not establish firmware Auto before curve start"}
        if not self._claim_native_fan(fan_id, "curve", owner):
            return {"success": False, "error": "Could not persist fan ownership before curve start"}
        expires_at = time.time() + duration_seconds
        self._native_curve_engine.start(
            fan_id, points, sensor_id=sensor_id, expires_at=expires_at,
            owner=owner, resume_after_suspend=resume_after_suspend)
        return {"success": True, "data": {
            "fan_id": fan_id, "sensor_id": sensor_id, "expires_at": expires_at,
            "owner": owner, "resume_after_suspend": resume_after_suspend}}

    def stop_native_fan_curve(self, fan_id):
        if not isinstance(fan_id, str):
            return {"success": False, "error": "Invalid fan identifier"}
        restored = self._native_curve_engine.stop(fan_id)
        return {"success": bool(restored), "data": {"fan_id": fan_id, "mode": "auto"} if restored else None,
                "error": None if restored else "Firmware automatic fan restoration failed"}

    def get_cooling_devices(self):
        fans = []
        temperatures = []
        for backend in self._native_fans:
            fans.extend(backend.fans())
            temperatures.extend(backend.temperatures())
        return {"schema_version": 1,
                "inventory_generation": self._inventory.get("generation", "unavailable"),
                "fans": fans, "temperatures": temperatures,
                "curves": self._native_curve_engine.get_state()}

    def start_fan_curve(self, target, points):
        """Reject invalid requests before starting a hardware-control thread."""
        if "fan_control" not in self.features or target not in ("cpu", "gpu"):
            return False
        # Retain the legacy wider temperature/count contract for saved paired curves.
        if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= 64:
            return False
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return False
            for value in point:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return False
                if not math.isfinite(value):
                    return False
            if not 0 <= point[0] <= 125 or not 30 <= point[1] <= 100:
                return False
        if len({point[0] for point in points}) != len(points):
            return False
        native = not self._sense_path("fan_speed") and bool(self._native_fans)
        claimed = []
        if native:
            if not self._fan_curve_engine.stop():
                return False
            for fan in self._native_fans[0].fans()[:2]:
                if not self._claim_native_fan(fan["id"], "curve", "legacy-paired-api"):
                    for fan_id in claimed:
                        self._release_native_fan(fan_id)
                    return False
                claimed.append(fan["id"])
        try:
            self.settings.set(f"fan_curve_{target}", {"enabled": True, "points": points})
        except OSError:
            for fan_id in claimed:
                self._release_native_fan(fan_id)
            raise
        self._fan_curve_engine.start(target, points)
        logger.info("Fan curve started for %s", target)
        return True

    def _disable_saved_curves(self):
        values = {f"fan_curve_{target}": {"enabled": False, "points": self.settings.get(f"fan_curve_{target}", {}).get("points", [])}
                  for target in ("cpu", "gpu")}
        self.settings.update(values)

    def stop_fan_curve(self, target=None):
        if target not in (None, "cpu", "gpu"):
            return False
        restored = self._fan_curve_engine.stop(target)
        self._disable_saved_curves()
        logger.info("Fan curves stopped; requested target=%s, automatic restoration=%s", target, restored)
        return bool(restored)

    def get_fan_curve_state(self):
        native = (self._native_curve_engine.get_state()
                  if hasattr(self, "_native_curve_engine") else {})
        return {**self._fan_curve_engine.get_state(), **native}

    def shutdown_fan_curves(self):
        """Stop all fan curves and restore EC auto control. Called on daemon shutdown."""
        legacy_active = any(item.get("active") for item in self._fan_curve_engine.get_state().values())
        if getattr(self, "sense_base", None) or legacy_active:
            self._fan_curve_engine.stop()
        if hasattr(self, "_native_curve_engine"):
            self._native_curve_engine.stop()
        self._restore_owned_native_fans()

    def prepare_sleep(self):
        """Release userspace fan/performance control before suspend."""
        state = self.get_fan_curve_state()
        self._resume_curves = {
            target: data["points"] for target, data in state.items()
            if target in ("cpu", "gpu") and data.get("active") and data.get("points")}
        self._resume_native_curves = {
            fan_id: dict(data) for fan_id, data in state.items()
            if fan_id not in ("cpu", "gpu") and data.get("active") and data.get("resume_after_suspend")}
        legacy_active = any(state[target].get("active") for target in ("cpu", "gpu"))
        if getattr(self, "sense_base", None) or legacy_active:
            self._fan_curve_engine.stop()
        if hasattr(self, "_native_curve_engine"):
            self._native_curve_engine.stop()
        self._restore_owned_native_fans()
        if self._game_mode_active:
            result = self.deactivate_game_mode()
            if not result.get("success"):
                logger.error("Game Mode suspend cleanup failed: %s",
                             result.get("error"))

    def poll_inventory(self):
        """Coalesce kernel device events; retain a bounded fallback on systems without the counter."""
        now = time.monotonic()
        if now < getattr(self, "_next_device_probe", 0):
            return None
        self._next_device_probe = now + 15
        sequence = read_sysfs("/sys/kernel/uevent_seqnum")
        if sequence and sequence == getattr(self, "_device_sequence", None):
            return None
        self._device_sequence = sequence
        previous = getattr(self, "_announced_generation", self._inventory.get("generation"))
        self._refresh_devices()
        generation = self._inventory.get("generation")
        self._announced_generation = generation
        return generation if previous != generation else None

    def _refresh_devices(self):
        inventory = self._collect_inventory()
        candidates = archer_fans.NativeAcerHwmon.probe(Path(HWMON_DIR))
        hwmon = {item["node"]: item for item in inventory.get("devices", []) if item.get("kind") == "hwmon"}
        backends = []
        for backend in candidates:
            device = hwmon.get(backend.path.name)
            if device is not None:
                if not device.get("identity_unique", True):
                    continue
                backend.device_key = device["key"]
            backends.append(backend)
        self._native_fans = backends
        self._inventory = inventory
        if self._native_fans:
            if "native_fan_control" not in self.features:
                self.features.append("native_fan_control")
            if (not self.sense_base and len(self._native_fans) == 1
                    and len(self._native_fans[0].fans()) >= 2 and "fan_control" not in self.features):
                self.features.append("fan_control")
        else:
            if "native_fan_control" in self.features:
                self.features.remove("native_fan_control")
            if not self.sense_base and "fan_control" in self.features:
                self.features.remove("fan_control")

    def resume_controls(self):
        """Re-enumerate native fan ownership before resuming explicit curves."""
        self._refresh_devices()
        curves = getattr(self, "_resume_curves", {})
        self._resume_curves = {}
        if curves and "fan_control" in self.features and self._fan_curve_restore_auto():
            for target, points in curves.items():
                temperature = self._fan_curve_get_temp(target)
                if temperature is not None and math.isfinite(temperature):
                    self.start_fan_curve(target, points)
                else:
                    logger.warning("%s fan curve remains paused: sensor unavailable",
                                   target)
        elif curves:
            logger.warning("Saved fan curves remain paused after resume: backend unavailable")
        native_curves = getattr(self, "_resume_native_curves", {})
        self._resume_native_curves = {}
        for fan_id, data in native_curves.items():
            remaining = int((data.get("expires_at") or 0) - time.time())
            if remaining < 60:
                logger.info("Native fan curve for %s expired while suspended", fan_id)
                continue
            result = self.start_native_fan_curve(
                fan_id, data.get("sensor_id"), data.get("points", []), remaining,
                owner=data.get("owner", "resume"), resume_after_suspend=True)
            if not result.get("success"):
                logger.warning("Native fan curve for %s remains in Auto after resume: %s",
                               fan_id, result.get("error"))

    # --- Display Mode ---
    def get_display_mode(self):
        mode = _PROBE_CACHE.get_or_compute(
            "envycontrol-query",
            lambda: run_cmd("/usr/bin/envycontrol --query", timeout=3),
        )
        available_modes = ["integrated", "hybrid", "nvidia"]
        boot = read_sysfs("/proc/sys/kernel/random/boot_id")
        pending = bool(boot and self.settings.get("display_change_boot") == boot)
        configured = self.settings.get("display_configured_mode", mode) if pending else mode
        return {"mode": configured if configured in available_modes else "unknown",
                "configured_mode": configured if configured in available_modes else None,
                "observed_render_mode": None, "available_modes": available_modes, "reboot_required": pending}

    def get_graphics_plan(self, mode):
        self._inventory = self._collect_inventory()
        plan = archer_graphics.build_plan(self._inventory, mode)
        return {**plan, "review_token": archer_graphics.review_token(plan)}

    def _system_operation(self, action, value="", deferred=False):
        duration = 450 if action in ("display", "override", "remove-override") else 30 if action == "audio" else 300
        arguments = ["systemd-run", "--quiet", "--collect", "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
                     "--property=TimeoutStopSec=10", f"--property=RuntimeMaxSec={duration}"]
        if deferred:
            value = uuid.uuid4().hex
            arguments += ["--unit=archer-recovery", "--on-active=2s", "--timer-property=RemainAfterElapse=no",
                          "--timer-property=AccuracySec=100ms", "--no-block"]
        else:
            arguments += ["--wait", "--pipe"]
        arguments += ["/usr/bin/python3", "/opt/archer/archer_system.py", action]
        if value:
            arguments.append(value)
        result = run_command(arguments, timeout=15 if deferred else duration + 15)
        if not result.success:
            return {"success": False, "error": result.stderr or result.stdout or "System operation timed out"}
        if deferred:
            return {"success": True, "data": {"scheduled": True, "request": value}}
        try:
            response = json.loads(result.stdout)
            return response if isinstance(response, dict) else {"success": False, "error": "Invalid system helper response"}
        except ValueError:
            return {"success": False, "error": "Invalid system helper response"}

    def get_recovery_status(self):
        try:
            data = json.loads(Path(self.settings.path).with_name("recovery.json").read_text())
            if not isinstance(data, dict) or data.get("boot_id") != read_sysfs("/proc/sys/kernel/random/boot_id"):
                return None
            if data.get("status") == "running" and time.time() - float(data.get("updated", 0)) > 330:
                data.update(status="failed", error="Recovery timed out. Check the service logs before retrying.")
            return data
        except (OSError, ValueError, TypeError):
            return None

    def set_display_mode(self, mode, review_token=None):
        if mode not in ("hybrid", "nvidia", "integrated"):
            return {"success": False, "error": "Invalid graphics mode"}
        if hasattr(self, "_inventory"):
            plan = self.get_graphics_plan(mode)
            if not plan.get("available"):
                return {"success": False, "error": "Graphics plan is unavailable",
                        "reason_code": plan.get("reason_code"), "plan": plan}
            if review_token is not None and review_token != plan.get("review_token"):
                return {"success": False, "reason_code": "plan_changed", "error": "Devices or configuration changed. Review the current plan."}
            if plan.get("required_acknowledgements") and review_token is None:
                return {"success": False,
                        "error": "Graphics output consequences require explicit review",
                        "reason_code": "acknowledgement_required", "plan": plan}
        result = self._system_operation("display", mode)
        if result.get("success"):
            self.settings.update({"display_change_boot": read_sysfs("/proc/sys/kernel/random/boot_id"),
                                  "display_configured_mode": mode})
            result.update(mode=mode, reboot_required=True)
        return result

    @staticmethod
    def audio_available():
        return os.path.isfile(AUDIO_PLUGIN) and (os.path.isfile(AUDIO_CONF) or os.path.isfile(AUDIO_CONF + ".disabled"))

    def get_audio_enhancement(self):
        return {"noise_suppression": self.audio_available() and os.path.isfile(AUDIO_CONF)}

    def set_audio_enhancement(self, enabled):
        result = self._system_operation("audio", "on" if enabled else "off")
        if result.get("success"):
            self.settings.set("audio_enhancement", {"noise_suppression": bool(enabled)})
        return result

    # --- Game Mode ---
    @staticmethod
    def _epp_paths():
        return {path.parent.name: path for path in sorted(Path(CPUFREQ_DIR).glob("policy*/energy_performance_preference"))
                if re.fullmatch(r"policy\d+", path.parent.name)}

    def _recover_game_mode(self):
        saved = self.settings.get("game_mode_saved_state")
        if saved and saved.get("boot_id") == read_sysfs("/proc/sys/kernel/random/boot_id"):
            self._game_mode_saved = saved
            self._game_mode_active = True
            result = self.deactivate_game_mode()
            if not result["success"]:
                logger.error("Game Mode recovery incomplete: %s", result.get("error"))
        else:
            self.settings.update({"game_mode_active": False}, remove=("game_mode_saved_state",))

    def activate_game_mode(self):
        """Transactional profile, per-policy EPP and identified NVIDIA changes."""
        if self._game_mode_active:
            return {"success": True, "data": self.get_game_mode()}
        if run_command(["systemctl", "is-active", "--quiet", "tlp.service"], 3).success:
            return {"success": False, "error": "TLP owns power policy",
                    "reason_code": "control_owned", "owners": ["tlp"]}
        providers = archer_policy.discover_providers(runner=run_command)
        owners = archer_policy.owners_by_control(providers)
        conflicts = sorted(set(owners.get("platform_profile", []) + owners.get("cpu_policy", [])) - {"gamemode"})
        if conflicts:
            label = "TLP owns power policy" if any(owner in ("tlp", "tlp-pd") for owner in conflicts) \
                else "Power policy is externally owned"
            return {"success": False, "error": label,
                    "reason_code": "control_owned", "owners": conflicts}
        token = uuid.uuid4().hex
        saved = {"boot_id": read_sysfs("/proc/sys/kernel/random/boot_id"), "epp": {}, "nvidia": {},
                 "token": token, "leases": {}}
        choices = self.get_thermal_profile_choices()
        if "performance" in choices:
            profile = self.get_thermal_profile()
            if profile in choices:
                saved["platform_profile"] = profile
        for name, path in self._epp_paths().items():
            previous = read_sysfs(path)
            available = (read_sysfs(path.with_name("energy_performance_available_preferences")) or "").split()
            if previous and "performance" in available:
                saved["epp"][name] = previous
        query = run_command(["nvidia-smi", "--query-gpu=uuid,persistence_mode", "--format=csv,noheader"], 3)
        if query.success:
            for line in query.stdout.splitlines():
                fields = [value.strip() for value in line.split(",")]
                if len(fields) == 2 and re.fullmatch(r"GPU-[a-fA-F0-9-]+", fields[0]) and fields[1] in ("Enabled", "Disabled"):
                    saved["nvidia"][fields[0]] = int(fields[1] == "Enabled")
        if not any((saved.get("platform_profile"), saved["epp"], saved["nvidia"])):
            return {"success": False, "error": "No supported Game Mode controls"}
        # Persist the original values before the first write, including crash recovery.
        self.settings.update({"game_mode_saved_state": saved, "game_mode_active": True})
        self._game_mode_saved = saved
        self._game_mode_active = True
        try:
            if saved.get("platform_profile"):
                ok, error = self.set_thermal_profile("performance")
                if not ok:
                    raise OSError(error or "Profile change failed")
                saved["leases"]["platform_profile"] = archer_policy.lease(
                    "archer-gamemode", "platform_profile", saved["platform_profile"], "performance", token=token)
            paths = self._epp_paths()
            for name in saved["epp"]:
                if name not in paths or not write_sysfs(paths[name], "performance") or read_sysfs(paths[name]) != "performance":
                    raise OSError(f"EPP change failed for {name}")
                saved["leases"]["epp:" + name] = archer_policy.lease(
                    "archer-gamemode", "cpu_policy", saved["epp"][name], "performance", token=token)
            for gpu, previous in saved["nvidia"].items():
                if not previous and not run_command(["nvidia-smi", "-i", gpu, "-pm", "1"], 5).success:
                    raise OSError(f"NVIDIA persistence change failed for {gpu}")
                saved["leases"]["nvidia:" + gpu] = archer_policy.lease(
                    "archer-gamemode", "gpu_persistence", previous, 1, token=token)
            self.settings.set("game_mode_saved_state", saved)
        except (OSError, ValueError) as error:
            rollback = self.deactivate_game_mode()
            return {"success": False, "error": str(error), "rollback": rollback}
        return {"success": True, "data": self.get_game_mode()}

    def deactivate_game_mode(self):
        if not self._game_mode_active:
            return {"success": True, "data": self.get_game_mode()}
        saved = self._game_mode_saved
        errors = []
        profile = saved.get("platform_profile")
        if profile and profile in self.get_thermal_profile_choices():
            lease = saved.get("leases", {}).get("platform_profile", {"last_written": "performance"})
            if archer_policy.may_restore(self.get_thermal_profile(), lease):
                ok, error = self.set_thermal_profile(profile)
                if not ok:
                    errors.append(error or "Profile restoration failed")
        paths = self._epp_paths()
        for name, value in saved.get("epp", {}).items():
            # Offline policies disappear; never substitute a different policy.
            lease = saved.get("leases", {}).get("epp:" + name, {"last_written": "performance"})
            if name in paths and archer_policy.may_restore(read_sysfs(paths[name]), lease) and \
                    (not write_sysfs(paths[name], value) or read_sysfs(paths[name]) != value):
                errors.append(f"EPP restoration failed for {name}")
        current_nvidia = {}
        query = None
        if saved.get("leases"):
            query = run_command(["nvidia-smi", "--query-gpu=uuid,persistence_mode", "--format=csv,noheader"], 3)
        if query and query.success:
            for line in query.stdout.splitlines():
                fields = [value.strip() for value in line.split(",")]
                if len(fields) == 2:
                    current_nvidia[fields[0]] = int(fields[1] == "Enabled")
        for gpu, value in saved.get("nvidia", {}).items():
            lease = saved.get("leases", {}).get("nvidia:" + gpu, {"last_written": 1})
            if (not query or not query.success or archer_policy.may_restore(current_nvidia.get(gpu), lease)) and \
                    not run_command(["nvidia-smi", "-i", gpu, "-pm", str(value)], 5).success:
                errors.append(f"NVIDIA persistence restoration failed for {gpu}")
        if errors:
            return {"success": False, "error": "; ".join(errors), "data": self.get_game_mode()}
        self.settings.update({"game_mode_active": False}, remove=("game_mode_saved_state",))
        self._game_mode_active = False
        self._game_mode_saved = {}
        return {"success": True, "data": self.get_game_mode()}

    def get_game_mode(self):
        components = list(self._game_mode_saved.get("epp", {})) + list(self._game_mode_saved.get("nvidia", {}))
        if self._game_mode_saved.get("platform_profile"):
            components.append("platform_profile")
        return {"active": self._game_mode_active, "components": components,
                "token": self._game_mode_saved.get("token"),
                "leases": self._game_mode_saved.get("leases", {})}

    # --- USB Wake Policy ---
    def get_usb_wake_sources(self):
        """Parse /proc/acpi/wakeup for USB controller entries."""
        sources = []
        try:
            with open("/proc/acpi/wakeup") as f:
                lines = f.readlines()
            for line in lines[1:]:  # Skip header line
                parts = line.split()
                if len(parts) >= 3:
                    device = parts[0]
                    sysfs_node = parts[-1] if len(parts) >= 4 else ""
                    enabled = parts[2].strip("*").lower() == "enabled"
                    sources.append({
                        "device": device,
                        "enabled": enabled,
                        "sysfs_node": sysfs_node,
                    })
        except (OSError, FileNotFoundError) as e:
            logger.error(f"Failed to read /proc/acpi/wakeup: {e}")
        return sources

    def set_usb_wake(self, device, enabled):
        """Toggle a wakeup device by writing its name to /proc/acpi/wakeup."""
        # /proc/acpi/wakeup toggles state when device name is written
        # First check current state
        sources = self.get_usb_wake_sources()
        current = None
        for s in sources:
            if s["device"] == device:
                current = s["enabled"]
                break
        if current is None:
            return False
        if current == enabled:
            return True  # already in desired state
        try:
            with open("/proc/acpi/wakeup", "w") as f:
                f.write(device)
            return True
        except OSError as e:
            logger.error(f"Failed to set USB wake for {device}: {e}")
            return False

    # --- Firmware Info ---
    def get_firmware_info(self, check=True):
        """Only explicit checks invoke fwupd; initial settings remain inexpensive."""
        info = {
            "bios_version": read_sysfs("/sys/class/dmi/id/bios_version") or "Unknown",
            "vendor": read_sysfs("/sys/class/dmi/id/sys_vendor") or "Unknown",
            "fwupd_available": bool(shutil.which("fwupdmgr")),
            "updates": [], "status": "not_checked", "checked_at": None,
        }
        info.update(getattr(self, "_firmware_cache", {}))
        if not info["fwupd_available"]:
            info["status"] = "unavailable"
            return info
        if not check:
            return info
        ttl = 10 if info.get("status") == "error" else 60
        if time.monotonic() - getattr(self, "_firmware_checked", -float("inf")) < ttl:
            return info
        if not hasattr(self, "_firmware_lock"):
            self._firmware_lock = threading.Lock()
        if not self._firmware_lock.acquire(blocking=False):
            return {**info, "status": "checking"}
        try:
            info["checked_at"] = time.time()
            try:
                result = firmware_status(run_command)
                info.update(result)
                if "error" not in result:
                    info.pop("error", None)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                info["status"] = "error"
                info["error"] = str(error)
                info["updates"] = []
            self._firmware_cache = dict(info)
            return dict(info)

        finally:
            self._firmware_checked = time.monotonic()
            self._firmware_lock.release()

    # --- Driver Management ---
    def restart_daemon(self):
        return self._system_operation("restart", deferred=True)

    def restart_drivers_and_daemon(self):
        if "fan_control" in self.features and not self.stop_fan_curve():
            return {"success": False, "error": "Could not restore automatic fans"}
        return self._system_operation("reload", deferred=True)

    def set_modprobe_parameter(self, param):
        qualified = qualified_driver_overrides(read_sysfs(DMI_PRODUCT), read_sysfs(DMI_BOARD))
        if param not in qualified:
            return {"success": False, "error": "No qualified driver override for this exact model and board"}
        return self._system_operation("override", param)

    def remove_modprobe_parameter(self):
        return self._system_operation("remove-override")


# --- Main ---
def write_pid():
    Path(PID_FILE).write_text(str(os.getpid()))


def cleanup_pid():
    Path(PID_FILE).unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        print("Error: Archer daemon must run as root.", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1:] == ["--restore-fans"]:
        # Also used by systemd ExecStopPost after SIGKILL / failed startup.
        logging.basicConfig(level=logging.INFO)
        hw = HardwareManager(restore_settings=False)
        sys.exit(1 if hw.driver_status == "auto_restore_failed" else 0)

    setup_logging()
    logger.info(f"Archer Daemon v{VERSION} starting...")

    write_pid()
    settings = SettingsStore()
    hw = HardwareManager(settings_store=settings)

    # D-Bus is mandatory. The previous Unix-socket fallback was unreachable
    # by the user-mode GUI (socket was 0o660 root:root) and only masked real
    # D-Bus startup failures behind a "deprecated" warning.
    try:
        import dbus.mainloop.glib
        from gi.repository import GLib
        from archer_dbus import ArcherDBusService
    except ImportError as e:
        logger.error(
            f"Failed to import D-Bus dependencies: {e}. "
            "Install python-dbus and python-gobject, then restart."
        )
        hw.shutdown_fan_curves()
        cleanup_pid()
        sys.exit(1)

    try:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        _dbus_service = ArcherDBusService(hw)  # noqa: F841 — prevent GC
        main_loop = GLib.MainLoop()
        logger.info("D-Bus service registered (io.otectus.Archer1)")
    except Exception as e:
        logger.error(
            f"Failed to register D-Bus service: {e}. "
            "Check that /etc/dbus-1/system.d/io.otectus.Archer1.conf exists "
            "and 'systemctl reload dbus.service' has been run."
        )
        hw.shutdown_fan_curves()
        cleanup_pid()
        sys.exit(1)

    # Reapply lighting after resume. Neither acer_suspend() nor acer_resume()
    # in the driver touch RGB, and the ENE loses our state across the sleep, so
    # without this the keyboard comes back to whatever the EC decides.
    # Hooking logind's PrepareForSleep keeps this self-contained: no extra
    # systemd unit to install, and the daemon is already running a GLib loop.
    try:
        def _on_prepare_for_sleep(sleeping):
            if sleeping:
                hw.prepare_sleep()
                return
            # The I2C-HID controller re-enumerates a moment after resume, and
            # PrepareForSleep(False) arrives before that. Writing immediately
            # would just fail, so give it a beat. Returning False makes the
            # timeout fire once rather than repeat.
            hw._lighting_retries = 5
            # Firmware may not keep LCD/boot/backlight settings across sleep.
            # Downgrade earlier evidence; no setting is rewritten on resume.
            hw.note_resume()

            def _resume_controls():
                hw.resume_controls()
                return False

            GLib.timeout_add_seconds(2, _resume_controls)

        system_bus = dbus.SystemBus()
        system_bus.add_signal_receiver(
            _on_prepare_for_sleep,
            signal_name="PrepareForSleep",
            dbus_interface="org.freedesktop.login1.Manager",
            bus_name="org.freedesktop.login1",
        )
        logger.info("Listening for resume to reapply lighting")

        # Catch profile changes made outside Archer, so the button LED cannot
        # drift out of step with the machine.
        # The D-Bus telemetry worker polls provider changes and retries lighting.
    except Exception as e:
        # Not fatal: everything else still works, lighting just will not
        # survive a suspend.
        logger.warning(f"Could not subscribe to PrepareForSleep: {e}")

    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        _dbus_service.close(wait=False)
        hw.shutdown_fan_curves()
        main_loop.quit()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        main_loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        _dbus_service.close()
        hw.shutdown_fan_curves()
        hw.deactivate_game_mode()
        cleanup_pid()
        logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
