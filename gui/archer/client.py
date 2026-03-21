"""
Unix socket client for communicating with the Archer daemon.
"""

import json
import socket
import threading

SOCKET_PATH = "/var/run/archer.sock"
TIMEOUT = 5.0
MAX_RETRIES = 3
RETRY_DELAY = 0.5


class ArcherClient:
    """Client for the Archer daemon Unix socket."""

    def __init__(self, socket_path=SOCKET_PATH):
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._features = []
        self._connected = False

    def _send_command(self, command, params=None):
        """Send a command and return the response dict."""
        request = {"command": command}
        if params:
            request["params"] = params

        for attempt in range(MAX_RETRIES):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
            except (socket.timeout, json.JSONDecodeError, OSError) as e:
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
