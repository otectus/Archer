"""
Archer D-Bus Service — exposes HardwareManager over system D-Bus with polkit authorization.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from archer_settings import object_request
from archer_version import VERSION

import dbus
import dbus.service
from gi.repository import GLib

logger = logging.getLogger("archer-daemon")

# How often the daemon pushes a TelemetryUpdated signal. The GUI considers
# data "stale" if it has not seen a signal within ~3x this interval.
TELEMETRY_INTERVAL_S = 2

DBUS_NAME = "io.otectus.Archer1"
DBUS_PATH = "/io/otectus/Archer1"
DBUS_IFACE = "io.otectus.Archer1"

# Polkit action IDs mapped to command groups
POLKIT_ACTIONS = {
    "set_thermal_profile": "io.otectus.archer1.set-profile",
    "set_fan_speed": "io.otectus.archer1.set-fan",
    "set_fan_curve": "io.otectus.archer1.set-fan",
    "set_native_fan_curve": "io.otectus.archer1.set-fan",
    "set_fan_device": "io.otectus.archer1.set-fan",
    "set_fan_mode": "io.otectus.archer1.set-fan",
    "restore_firmware_auto": "io.otectus.archer1.set-fan",
    "set_battery_calibration": "io.otectus.archer1.set-hardware",
    "set_battery_limiter": "io.otectus.archer1.set-hardware",
    "set_battery_threshold": "io.otectus.archer1.set-hardware",
    "charge_full_once": "io.otectus.archer1.set-hardware",
    "set_usb_charging": "io.otectus.archer1.set-hardware",
    "set_backlight_timeout": "io.otectus.archer1.set-hardware",
    "set_lcd_override": "io.otectus.archer1.set-hardware",
    "set_boot_animation_sound": "io.otectus.archer1.set-hardware",
    "set_per_zone_mode": "io.otectus.archer1.set-hardware",
    "set_four_zone_mode": "io.otectus.archer1.set-hardware",
    "set_usb_wake": "io.otectus.archer1.set-hardware",
    "set_audio_enhancement": "io.otectus.archer1.set-hardware",
    "set_display_mode": "io.otectus.archer1.set-display",
    "set_game_mode": "io.otectus.archer1.set-gamemode",
    "restart_daemon": "io.otectus.archer1.system-control",
    "restart_drivers_and_daemon": "io.otectus.archer1.system-control",
    "set_modprobe_parameter": "io.otectus.archer1.system-control",
    "remove_modprobe_parameter": "io.otectus.archer1.system-control",
}


def _boolean_request(value):
    """The 'b' argument as a native bool. dbus.Boolean subclasses int, not bool,
    so it is converted here and nothing else truthy reaches hardware code."""
    if isinstance(value, bool):
        return value
    if isinstance(value, dbus.Boolean):
        return bool(value)
    return None


def _check_polkit(bus, sender, action_id):
    """Check polkit authorization for the calling process."""
    proxy = bus.get_object("org.freedesktop.PolicyKit1",
                           "/org/freedesktop/PolicyKit1/Authority")
    authority = dbus.Interface(proxy, "org.freedesktop.PolicyKit1.Authority")

    subject = ("system-bus-name", {"name": sender})
    result = authority.CheckAuthorization(
        subject, action_id, {}, dbus.UInt32(1), "", timeout=120  # 1 = AllowUserInteraction
    )
    is_authorized = bool(result[0])
    if not is_authorized:
        logger.warning(f"Polkit denied {action_id} for {sender}")
    return is_authorized


class ArcherDBusService(dbus.service.Object):
    """Exports Archer hardware control over D-Bus with polkit checks."""

    def __init__(self, hw_manager, bus=None):
        self.hw = hw_manager
        self._read_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archer-read")
        self._write_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archer-write")
        self._monitor_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archer-monitor")
        self._read_slots = threading.BoundedSemaphore(4)
        self._write_slots = threading.BoundedSemaphore(1)
        self._telemetry_pending = False
        self._maintenance_pending = False
        self._last_profile = None
        self._stopping = False
        self._bus = bus or dbus.SystemBus()
        self._bus_name = dbus.service.BusName(DBUS_NAME, self._bus)
        super().__init__(self._bus, DBUS_PATH)
        logger.info(f"D-Bus service registered: {DBUS_NAME} at {DBUS_PATH}")
        # Push telemetry on a timer so the GUI doesn't have to poll. Returns
        # True so GLib keeps re-arming the timeout.
        self._timer = GLib.timeout_add_seconds(TELEMETRY_INTERVAL_S, self._emit_telemetry)

    def close(self, wait=True):
        self._stopping = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        for pool in (self._read_pool, self._write_pool, self._monitor_pool):
            pool.shutdown(wait=wait, cancel_futures=True)

    def _dispatch(self, work, reply, failure, mutating=False):
        if self._stopping:
            response = self._json_response({"success": False, "error": "Archer is shutting down"})
            return reply(response) if reply is not None else response
        if reply is None:
            return work()  # Direct unit-test calls; exported calls always supply callbacks.
        slots = self._write_slots if mutating else self._read_slots
        pool = self._write_pool if mutating else self._read_pool
        if not slots.acquire(blocking=False):
            reply(self._json_response({"success": False, "error": "Archer is busy; retry when the current operation finishes"}))
            return

        def run():
            try:
                response = work()
            except Exception as error:
                logger.warning("Operation failed: %s", error)
                response = self._json_response({"success": False, "error": str(error)})
            finally:
                slots.release()

            def done():
                reply(response)
                return False
            GLib.idle_add(done)
        pool.submit(run)

    def _emit_telemetry(self):
        if self._stopping:
            return False
        if not self._telemetry_pending:
            self._telemetry_pending = True

            def collect():
                try:
                    data = self.hw.get_monitoring_data()
                except Exception as error:
                    logger.warning("Telemetry collection failed: %s", error)
                    data = None

                def publish():
                    self._telemetry_pending = False
                    if data is not None and not self._stopping:
                        self.TelemetryUpdated(json.dumps(data))
                        profile = data.get("thermal_profile")
                        if profile and profile != self._last_profile:
                            self._last_profile = profile
                            GLib.idle_add(self.ProfileChanged, profile)
                    return False
                GLib.idle_add(publish)
            self._monitor_pool.submit(collect)
        if not self._maintenance_pending and hasattr(self.hw, "poll_profile_led") and self._write_slots.acquire(blocking=False):
            self._maintenance_pending = True

            def maintain():
                try:
                    if hasattr(self.hw, "poll_inventory"):
                        generation = self.hw.poll_inventory()
                        if isinstance(generation, str):
                            GLib.idle_add(self.InventoryChanged, generation)
                    self.hw.poll_profile_led()
                    self.hw._expire_battery_overrides()
                except Exception as error:
                    logger.warning("Lighting maintenance failed: %s", error)
                finally:
                    self._maintenance_pending = False
                    self._write_slots.release()
            self._monitor_pool.submit(maintain)
        return True

    def _authorize(self, command, sender):
        """Check polkit for mutating commands. Returns True if authorized."""
        action_id = POLKIT_ACTIONS.get(command)
        if not action_id:
            return True  # Read-only commands need no auth
        return not self._stopping and _check_polkit(self._bus, sender, action_id) and not self._stopping

    def _json_response(self, data):
        """Serialize response to JSON string for D-Bus transport."""
        return json.dumps(data)

    # --- Read-Only Methods (no polkit) ---

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender")
    def Ping(self, sender=None):
        return self._json_response({"success": True, "data": {"version": VERSION,
                                    "interface": 1, "capability_schema": 1}})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetAllSettings(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetAllSettings(sender=sender), reply, failure, mutating=False)

    def _do_GetAllSettings(self, sender=None):
        return self._json_response({"success": True, "data": self.hw.get_all_settings()})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetMonitoringData(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetMonitoringData(sender=sender), reply, failure, mutating=False)

    def _do_GetMonitoringData(self, sender=None):
        return self._json_response({"success": True, "data": self.hw.get_monitoring_data()})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetSupportedFeatures(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetSupportedFeatures(sender=sender), reply, failure, mutating=False)

    def _do_GetSupportedFeatures(self, sender=None):
        return self._json_response({"success": True, "data": {"features": self.hw.features}})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetCapabilities(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._json_response({"success": True, "data": self.hw.get_capabilities()}),
                              reply, failure, mutating=False)

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetBatteryDevices(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._json_response({"success": True, "data": {
            "schema_version": 1, "batteries": self.hw.get_battery_devices()}}), reply, failure, mutating=False)

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetPolicyStatus(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._json_response({"success": True, "data": self.hw.get_policy_status()}),
                              reply, failure, mutating=False)

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetGraphicsPlan(self, mode, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetGraphicsPlan(mode), reply, failure, mutating=False)

    def _do_GetGraphicsPlan(self, mode):
        try:
            return self._json_response({"success": True, "data": self.hw.get_graphics_plan(str(mode))})
        except ValueError as error:
            return self._json_response({"success": False, "error": str(error)})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetFanCurve(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetFanCurve(sender=sender), reply, failure, mutating=False)

    def _do_GetFanCurve(self, sender=None):
        return self._json_response({"success": True, "data": self.hw.get_fan_curve_state()})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetCoolingDevices(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._json_response({
            "success": True, "data": self.hw.get_cooling_devices()}),
            reply, failure, mutating=False)

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetDisplayMode(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetDisplayMode(sender=sender), reply, failure, mutating=False)

    def _do_GetDisplayMode(self, sender=None):
        return self._json_response({"success": True, "data": self.hw.get_display_mode()})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetGameMode(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetGameMode(sender=sender), reply, failure, mutating=False)

    def _do_GetGameMode(self, sender=None):
        return self._json_response({"success": True, "data": self.hw.get_game_mode()})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetUsbPowerPolicy(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetUsbPowerPolicy(sender=sender), reply, failure, mutating=False)

    def _do_GetUsbPowerPolicy(self, sender=None):
        usb_level = self.hw.get_usb_charging()
        wake = self.hw.get_usb_wake_sources()
        return self._json_response({"success": True, "data": {
            "charging_level": usb_level, "wake_sources": wake,
        }})

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def GetFirmwareInfo(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_GetFirmwareInfo(sender=sender), reply, failure, mutating=False)

    def _do_GetFirmwareInfo(self, sender=None):
        data = self.hw.get_firmware_info()
        return self._json_response({"success": data.get("status") != "error",
                                    "data": data, "error": data.get("error", "")})

    # --- Mutating Methods (polkit-protected) ---

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetThermalProfile(self, profile, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetThermalProfile(profile, sender=sender), reply, failure, mutating=True)

    def _do_SetThermalProfile(self, profile, sender=None):
        if not self._authorize("set_thermal_profile", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok, err = self.hw.set_thermal_profile(profile)
        if ok:
            self.hw.settings.set("thermal_profile", profile)
            GLib.idle_add(self.ProfileChanged, profile)
        return self._json_response({"success": ok, "error": err})

    @dbus.service.method(DBUS_IFACE, in_signature="ii", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetFanSpeed(self, cpu, gpu, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetFanSpeed(cpu, gpu, sender=sender), reply, failure, mutating=True)

    def _do_SetFanSpeed(self, cpu, gpu, sender=None):
        if not self._authorize("set_fan_speed", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok = self.hw.set_fan_speed(cpu, gpu)
        if ok:
            self.hw.settings.set("fan_speed", {"cpu": cpu, "gpu": gpu})
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetFanCurve(self, params_json, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetFanCurve(params_json, sender=sender), reply, failure, mutating=True)

    def _do_SetFanCurve(self, params_json, sender=None):
        if not self._authorize("set_fan_curve", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        try:
            params = json.loads(params_json)
            if not isinstance(params, dict):
                raise ValueError("Expected an object")
            target = params.get("target", "cpu")
            enabled = params.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            ok = self.hw.start_fan_curve(target, params.get("points", [])) if enabled else self.hw.stop_fan_curve(target)
            return self._json_response({"success": ok, "error": None if ok else "Invalid curve or unavailable fan interface"})
        except (ValueError, TypeError) as error:
            return self._json_response({"success": False, "error": str(error)})

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetNativeFanCurve(self, params_json, reply=None, failure=None, sender=None):
        return self._dispatch(
            lambda: self._do_SetNativeFanCurve(params_json, sender=sender),
            reply, failure, mutating=True)

    def _do_SetNativeFanCurve(self, params_json, sender=None):
        if not self._authorize("set_native_fan_curve", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        try:
            params = json.loads(params_json)
            if not isinstance(params, dict) or type(params.get("enabled", True)) is not bool:
                raise ValueError("Expected a native curve object")
            fan_id = params.get("fan_id")
            if params.get("enabled", True) is False:
                return self._json_response(self.hw.stop_native_fan_curve(fan_id))
            result = self.hw.start_native_fan_curve(
                fan_id, params.get("sensor_id"), params.get("points"),
                params.get("duration_seconds"), owner=f"dbus:{sender}",
                resume_after_suspend=params.get("resume_after_suspend", True))
            return self._json_response(result)
        except (ValueError, TypeError) as error:
            return self._json_response({"success": False, "error": str(error)})

    @dbus.service.method(DBUS_IFACE, in_signature="si", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetFanDevice(self, fan_id, percent, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetFanDevice(str(fan_id), percent, sender),
                              reply, failure, mutating=True)

    def _do_SetFanDevice(self, fan_id, percent, sender):
        if not self._authorize("set_fan_device", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.set_fan_device(
            fan_id, int(percent), owner=f"dbus:{sender}"))

    @dbus.service.method(DBUS_IFACE, in_signature="ss", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetFanMode(self, fan_id, mode, reply=None, failure=None, sender=None):
        return self._dispatch(
            lambda: self._do_SetFanMode(str(fan_id), str(mode), sender),
            reply, failure, mutating=True)

    def _do_SetFanMode(self, fan_id, mode, sender):
        if not self._authorize("set_fan_mode", sender):
            return self._json_response({"success": False,
                                        "error": "Authorization denied"})
        return self._json_response(self.hw.set_fan_mode(
            fan_id, mode, owner=f"dbus:{sender}"))

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def RestoreFirmwareAuto(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_RestoreFirmwareAuto(sender), reply, failure, mutating=True)

    def _do_RestoreFirmwareAuto(self, sender):
        if not self._authorize("restore_firmware_auto", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.restore_firmware_auto())

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetBatteryCalibration(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetBatteryCalibration(bool(enabled), sender=sender), reply, failure, mutating=True)

    def _do_SetBatteryCalibration(self, enabled, sender=None):
        if not self._authorize("set_battery_calibration", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok = self.hw.set_battery_calibration(enabled)
        if ok:
            self.hw.settings.set("battery_calibration", enabled)
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetBatteryLimiter(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetBatteryLimiter(bool(enabled), sender=sender), reply, failure, mutating=True)

    def _do_SetBatteryLimiter(self, enabled, sender=None):
        if not self._authorize("set_battery_limiter", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok = self.hw.set_battery_limiter(enabled)
        if ok:
            self.hw.settings.set("battery_limiter", enabled)
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="sii", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetBatteryThreshold(self, device_key, start, end, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetBatteryThreshold(str(device_key), start, end, sender),
                              reply, failure, mutating=True)

    def _do_SetBatteryThreshold(self, device_key, start, end, sender):
        if not self._authorize("set_battery_threshold", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        start, end = int(start), int(end)
        return self._json_response(self.hw.set_battery_threshold(device_key, None if start < 0 else start, end))

    @dbus.service.method(DBUS_IFACE, in_signature="si", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def ChargeFullOnce(self, device_key, duration, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_ChargeFullOnce(str(device_key), duration, sender),
                              reply, failure, mutating=True)

    def _do_ChargeFullOnce(self, device_key, duration, sender):
        if not self._authorize("charge_full_once", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.charge_full_once(device_key, int(duration)))

    @dbus.service.method(DBUS_IFACE, in_signature="i", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetUsbCharging(self, level, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetUsbCharging(level, sender=sender), reply, failure, mutating=True)

    def _do_SetUsbCharging(self, level, sender=None):
        if not self._authorize("set_usb_charging", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok = self.hw.set_usb_charging(level)
        if ok:
            self.hw.settings.set("usb_charging", level)
        return self._json_response({"success": ok})

    # LCD override, backlight timeout and boot sound share one contract. The
    # hardware manager owns the write, its persistence and the readback check;
    # the response reports each outcome with the resulting state, and success
    # means written and saved without a contradicting readback.
    def _do_binary(self, command, key, enabled, sender):
        if not self._authorize(command, sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        result = self.hw.apply_binary(key, _boolean_request(enabled))
        state = (result.get("data") or {}).get(f"{key}_state")
        if state is not None:
            GLib.idle_add(self._binary_changed, key, json.dumps(state))
        return self._json_response(result)

    def _binary_changed(self, key, state_json):
        if not self._stopping:
            self.BinarySettingChanged(key, state_json)
        return False

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetBacklightTimeout(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetBacklightTimeout(enabled, sender=sender), reply, failure, mutating=True)

    def _do_SetBacklightTimeout(self, enabled, sender=None):
        return self._do_binary("set_backlight_timeout", "backlight_timeout", enabled, sender)

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetLcdOverride(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetLcdOverride(enabled, sender=sender), reply, failure, mutating=True)

    def _do_SetLcdOverride(self, enabled, sender=None):
        return self._do_binary("set_lcd_override", "lcd_override", enabled, sender)

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetBootAnimationSound(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetBootAnimationSound(enabled, sender=sender), reply, failure, mutating=True)

    def _do_SetBootAnimationSound(self, enabled, sender=None):
        return self._do_binary("set_boot_animation_sound", "boot_animation_sound", enabled, sender)

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetPerZoneMode(self, params_json, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetPerZoneMode(params_json, sender=sender), reply, failure, mutating=True)

    def _do_SetPerZoneMode(self, params_json, sender=None):
        if not self._authorize("set_per_zone_mode", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        p = object_request(params_json, "per_zone_mode")
        ok = self.hw.set_per_zone_mode(p["zone1"], p["zone2"], p["zone3"], p["zone4"], p["brightness"])
        if ok:
            self.hw.settings.update({"per_zone_mode": p, "last_keyboard_mode": "per_zone"})
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetFourZoneMode(self, params_json, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetFourZoneMode(params_json, sender=sender), reply, failure, mutating=True)

    def _do_SetFourZoneMode(self, params_json, sender=None):
        if not self._authorize("set_four_zone_mode", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        p = object_request(params_json, "four_zone_mode")
        effect = self.hw.effect_id(p.get("effect", p.get("mode")))
        ok = self.hw.set_four_zone_mode(effect, p["speed"], p["brightness"],
                                        p["direction"], p["red"], p["green"], p["blue"])
        if ok:
            p["effect"] = effect
            self.hw.settings.update({"four_zone_mode": p, "last_keyboard_mode": "effect", "keyboard_migration_required": False})
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetDisplayMode(self, mode, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetDisplayMode(mode, sender=sender), reply, failure, mutating=True)

    def _do_SetDisplayMode(self, mode, sender=None):
        if not self._authorize("set_display_mode", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        result = self.hw.set_display_mode(mode)
        return self._json_response(result)

    @dbus.service.method(DBUS_IFACE, in_signature="ss", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def ApplyGraphicsPlan(self, mode, token, reply=None, failure=None, sender=None):
        def apply():
            if not self._authorize("set_display_mode", sender):
                return self._json_response({"success": False, "error": "Authorization denied"})
            return self._json_response(self.hw.set_display_mode(str(mode), str(token)))
        return self._dispatch(apply, reply, failure, mutating=True)

    @dbus.service.method(DBUS_IFACE, in_signature="b", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetGameMode(self, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetGameMode(bool(enabled), sender=sender), reply, failure, mutating=True)

    def _do_SetGameMode(self, enabled, sender=None):
        if not self._authorize("set_game_mode", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        result = self.hw.activate_game_mode() if enabled else self.hw.deactivate_game_mode()
        return self._json_response(result)

    @dbus.service.method(DBUS_IFACE, in_signature="sb", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetUsbWake(self, device, enabled, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetUsbWake(device, bool(enabled), sender=sender), reply, failure, mutating=True)

    def _do_SetUsbWake(self, device, enabled, sender=None):
        if not self._authorize("set_usb_wake", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        ok = self.hw.set_usb_wake(device, enabled)
        return self._json_response({"success": ok})

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetAudioEnhancement(self, params_json, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetAudioEnhancement(params_json, sender=sender), reply, failure, mutating=True)

    def _do_SetAudioEnhancement(self, params_json, sender=None):
        if not self._authorize("set_audio_enhancement", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        params = object_request(params_json, "audio_enhancement")
        noise = params["noise_suppression"]
        result = self.hw.set_audio_enhancement(noise)
        if result.get("success"):
            GLib.idle_add(self.AudioEnhancementChanged, noise)
        return self._json_response(result)

    @dbus.service.method(DBUS_IFACE, in_signature="s", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def SetModprobeParameter(self, param, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_SetModprobeParameter(param, sender=sender), reply, failure, mutating=True)

    def _do_SetModprobeParameter(self, param, sender=None):
        if not self._authorize("set_modprobe_parameter", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        if param not in ("nitro_v4", "predator_v4", "enable_all"):
            return self._json_response({"success": False, "error": f"Invalid parameter: {param}"})
        return self._json_response(self.hw.set_modprobe_parameter(param))

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def RemoveModprobeParameter(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_RemoveModprobeParameter(sender=sender), reply, failure, mutating=True)

    def _do_RemoveModprobeParameter(self, sender=None):
        if not self._authorize("remove_modprobe_parameter", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.remove_modprobe_parameter())

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def RestartDaemon(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_RestartDaemon(sender=sender), reply, failure, mutating=True)

    def _do_RestartDaemon(self, sender=None):
        if not self._authorize("restart_daemon", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.restart_daemon())

    @dbus.service.method(DBUS_IFACE, in_signature="", out_signature="s",
                         sender_keyword="sender", async_callbacks=("reply", "failure"))
    def RestartDriversAndDaemon(self, reply=None, failure=None, sender=None):
        return self._dispatch(lambda: self._do_RestartDriversAndDaemon(sender=sender), reply, failure, mutating=True)

    def _do_RestartDriversAndDaemon(self, sender=None):
        if not self._authorize("restart_drivers_and_daemon", sender):
            return self._json_response({"success": False, "error": "Authorization denied"})
        return self._json_response(self.hw.restart_drivers_and_daemon())

    # --- D-Bus Signals ---

    @dbus.service.signal(DBUS_IFACE, signature="s")
    def InventoryChanged(self, generation):
        """Advertise a new generation after hotplug or a driver binding change."""

    @dbus.service.signal(DBUS_IFACE, signature="s")
    def TelemetryUpdated(self, data_json):
        pass

    @dbus.service.signal(DBUS_IFACE, signature="s")
    def ProfileChanged(self, profile):
        pass

    @dbus.service.signal(DBUS_IFACE, signature="b")
    def AudioEnhancementChanged(self, enabled):
        pass

    # Emitted after any LCD override, backlight timeout or boot sound operation
    # so other clients reconcile. Carries the resulting *_state JSON object.
    @dbus.service.signal(DBUS_IFACE, signature="ss")
    def BinarySettingChanged(self, key, state_json):
        pass
