"""
Client for communicating with the Archer daemon.
Uses D-Bus (preferred) with Unix socket fallback.
"""

import json
import logging
import threading

logger = logging.getLogger("archer-client")

SOCKET_PATH = "/var/run/archer.sock"
TIMEOUT = 5.0
MAX_RETRIES = 3
RETRY_DELAY = 0.5

DBUS_NAME = "io.otectus.Archer1"
DBUS_PATH = "/io/otectus/Archer1"
DBUS_IFACE = "io.otectus.Archer1"

# Maps _send_command names to D-Bus method names + argument packaging
_DBUS_METHOD_MAP = {
    "ping": ("Ping", None),
    "get_all_settings": ("GetAllSettings", None),
    "get_monitoring_data": ("GetMonitoringData", None),
    "get_supported_features": ("GetSupportedFeatures", None),
    "get_fan_curve": ("GetFanCurve", None),
    "get_display_mode": ("GetDisplayMode", None),
    "get_game_mode": ("GetGameMode", None),
    "get_usb_power_policy": ("GetUsbPowerPolicy", None),
    "get_firmware_info": ("GetFirmwareInfo", None),
    "set_thermal_profile": ("SetThermalProfile", lambda p: (p["profile"],)),
    "set_fan_speed": ("SetFanSpeed", lambda p: (p["cpu"], p["gpu"])),
    "set_fan_curve": ("SetFanCurve", lambda p: (json.dumps(p),)),
    "set_battery_calibration": ("SetBatteryCalibration", lambda p: (p["enabled"],)),
    "set_battery_limiter": ("SetBatteryLimiter", lambda p: (p["enabled"],)),
    "set_usb_charging": ("SetUsbCharging", lambda p: (p["level"],)),
    "set_backlight_timeout": ("SetBacklightTimeout", lambda p: (p["enabled"],)),
    "set_lcd_override": ("SetLcdOverride", lambda p: (p["enabled"],)),
    "set_boot_animation_sound": ("SetBootAnimationSound", lambda p: (p["enabled"],)),
    "set_per_zone_mode": ("SetPerZoneMode", lambda p: (json.dumps(p),)),
    "set_four_zone_mode": ("SetFourZoneMode", lambda p: (json.dumps(p),)),
    "set_display_mode": ("SetDisplayMode", lambda p: (p["mode"],)),
    "set_game_mode": ("SetGameMode", lambda p: (p["enabled"],)),
    "set_usb_wake": ("SetUsbWake", lambda p: (p["device"], p["enabled"])),
    "set_audio_enhancement": ("SetAudioEnhancement", lambda p: (json.dumps(p),)),
    "set_modprobe_parameter": ("SetModprobeParameter", lambda p: (p["parameter"],)),
    "remove_modprobe_parameter": ("RemoveModprobeParameter", None),
    "restart_daemon": ("RestartDaemon", None),
    "restart_drivers_and_daemon": ("RestartDriversAndDaemon", None),
}


class ArcherClient:
    """Client for the Archer daemon. Prefers D-Bus, falls back to Unix socket."""

    def __init__(self, socket_path=SOCKET_PATH):
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._features = []
        self._connected = False
        self._use_dbus = False
        self._dbus_iface = None
        self._init_dbus()

    def _init_dbus(self):
        """Try to connect via D-Bus."""
        try:
            import dbus
            bus = dbus.SystemBus()
            proxy = bus.get_object(DBUS_NAME, DBUS_PATH)
            self._dbus_iface = dbus.Interface(proxy, DBUS_IFACE)
            # Test connection
            self._dbus_iface.Ping()
            self._use_dbus = True
            self._connected = True
            logger.info("Connected to Archer daemon via D-Bus")
        except Exception:
            self._use_dbus = False
            logger.info("D-Bus unavailable, using Unix socket fallback")

    def _send_command(self, command, params=None):
        """Send a command and return the response dict."""
        if self._use_dbus:
            return self._send_dbus(command, params)
        return self._send_socket(command, params)

    def _send_dbus(self, command, params=None):
        """Send command via D-Bus."""
        mapping = _DBUS_METHOD_MAP.get(command)
        if not mapping:
            return {"success": False, "error": f"Unknown command: {command}"}

        method_name, args_fn = mapping
        try:
            method = getattr(self._dbus_iface, method_name)
            if args_fn and params:
                result_json = method(*args_fn(params))
            else:
                result_json = method()
            self._connected = True
            return json.loads(str(result_json))
        except Exception as e:
            self._connected = False
            error_str = str(e)
            # Check for polkit denial
            if "not authorized" in error_str.lower() or "authorization" in error_str.lower():
                return {"success": False, "error": "Authorization denied"}
            return {"success": False, "error": error_str}

    def _send_socket(self, command, params=None):
        """Send command via Unix socket (fallback)."""
        import socket as sock_mod
        request = {"command": command}
        if params:
            request["params"] = params

        for attempt in range(MAX_RETRIES):
            try:
                sock = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
                sock.settimeout(TIMEOUT)
                sock.connect(self.socket_path)
                sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

                data = b""
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in data:
                        break

                sock.close()
                self._connected = True
                return json.loads(data.decode("utf-8").strip())

            except (ConnectionRefusedError, FileNotFoundError):
                self._connected = False
                if attempt < MAX_RETRIES - 1:
                    import time
                    time.sleep(RETRY_DELAY)
            except (sock_mod.timeout, json.JSONDecodeError, OSError) as e:
                self._connected = False
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Cannot connect to daemon"}

    @property
    def is_connected(self):
        return self._connected

    @property
    def features(self):
        return self._features

    def has_feature(self, feature):
        return feature in self._features

    # --- High-level API ---
    def ping(self):
        resp = self._send_command("ping")
        return resp.get("success", False)

    def get_all_settings(self):
        resp = self._send_command("get_all_settings")
        if resp.get("success"):
            data = resp.get("data", {})
            self._features = data.get("features", [])
            return data
        return None

    def get_monitoring_data(self):
        resp = self._send_command("get_monitoring_data")
        if resp.get("success"):
            return resp.get("data", {})
        return None

    def get_supported_features(self):
        resp = self._send_command("get_supported_features")
        if resp.get("success"):
            self._features = resp["data"].get("features", [])
            return self._features
        return []

    def set_thermal_profile(self, profile):
        return self._send_command("set_thermal_profile", {"profile": profile})

    def set_fan_speed(self, cpu, gpu):
        return self._send_command("set_fan_speed", {"cpu": cpu, "gpu": gpu})

    def set_battery_calibration(self, enabled):
        return self._send_command("set_battery_calibration", {"enabled": enabled})

    def set_battery_limiter(self, enabled):
        return self._send_command("set_battery_limiter", {"enabled": enabled})

    def set_usb_charging(self, level):
        return self._send_command("set_usb_charging", {"level": level})

    def set_backlight_timeout(self, enabled):
        return self._send_command("set_backlight_timeout", {"enabled": enabled})

    def set_lcd_override(self, enabled):
        return self._send_command("set_lcd_override", {"enabled": enabled})

    def set_boot_animation_sound(self, enabled):
        return self._send_command("set_boot_animation_sound", {"enabled": enabled})

    def set_per_zone_mode(self, zone1, zone2, zone3, zone4, brightness):
        return self._send_command("set_per_zone_mode", {
            "zone1": zone1, "zone2": zone2,
            "zone3": zone3, "zone4": zone4,
            "brightness": brightness,
        })

    def set_four_zone_mode(self, mode, speed, brightness, direction, r, g, b):
        return self._send_command("set_four_zone_mode", {
            "mode": mode, "speed": speed, "brightness": brightness,
            "direction": direction, "red": r, "green": g, "blue": b,
        })

    def set_modprobe_parameter(self, parameter):
        return self._send_command("set_modprobe_parameter", {"parameter": parameter})

    def remove_modprobe_parameter(self):
        return self._send_command("remove_modprobe_parameter")

    def restart_daemon(self):
        return self._send_command("restart_daemon")

    def restart_drivers_and_daemon(self):
        return self._send_command("restart_drivers_and_daemon")
